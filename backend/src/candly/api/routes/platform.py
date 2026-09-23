"""Platform routes: health, instruments, candles, news and the Fyers login flow (docs/CONTRACTS.md §4)."""

import logging
import secrets
import threading
import time

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from candly import __version__
from candly.core.calendar import get_calendar
from candly.core.instruments import EXCHANGES, Instrument, UnknownInstrument, get_instrument, load_watchlist
from candly.core.settings import get_settings
from candly.core.timeframes import TIMEFRAMES
from candly.data import clock
from candly.data.live import get_forming
from candly.data.sources import fyers
from candly.data.store import candle_source, data_summary, load_candles, series_stats
from candly.news.store import NewsStore

log = logging.getLogger(__name__)
router = APIRouter(tags=["platform"])

MAX_CANDLES = 5000
MAX_NEWS = 500
LOGIN_STATE_SECONDS = 15 * 60
_login_states: dict[str, float] = {}
_state_lock = threading.Lock()


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def _instrument(instrument_id: str | None) -> Instrument:
    if not instrument_id:
        raise _bad("instrument is required, e.g. NSE:RELIANCE")
    try:
        return get_instrument(instrument_id)
    except UnknownInstrument:
        raise HTTPException(status_code=404, detail=f"unknown instrument {instrument_id}") from None


def _int_param(name: str, raw: str | None, default: int, low: int, high: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise _bad(f"{name} must be an integer") from None
    if not low <= value <= high:
        raise _bad(f"{name} must be between {low} and {high}")
    return value


def _epoch(ts: pd.Timestamp | None) -> int | None:
    return None if ts is None else clock.epoch_seconds(ts)


def _candles_json(df: pd.DataFrame) -> list[dict]:
    times = clock.epoch_series(df["ts"]).tolist()
    return [
        {"time": int(t), "open": o, "high": h, "low": lo, "close": c, "volume": v}
        for t, o, h, lo, c, v in zip(
            times, df["open"], df["high"], df["low"], df["close"], df["volume"], strict=True
        )
    ]


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    now = clock.utc_now()
    cal = get_calendar()
    markets = []
    for exchange in EXCHANGES:
        trading_day = cal.is_trading_day(exchange, cal.local_date(now))
        markets.append(
            {
                "exchange": exchange,
                "open": cal.is_open(exchange, now),
                "phase": cal.session_phase(exchange, now) if trading_day else "closed",
            }
        )
    return {
        "status": "ok",
        "version": __version__,
        "time": clock.epoch_seconds(now),
        "data_source": settings.resolved_data_source(),
        "keys": {
            "fyers": settings.has_fyers,
            "fyers_connected": fyers.connection_status()["connected"],
            "kotak_neo": settings.has_kotak,
            "anthropic": settings.has_anthropic,
            "telegram": settings.has_telegram,
        },
        "markets": markets,
    }


@router.get("/instruments")
def instruments() -> list[dict]:
    summary = data_summary()
    return [
        {
            "id": inst.id,
            "exchange": inst.exchange,
            "symbol": inst.symbol,
            "name": inst.name,
            "kind": inst.kind,
            "tradable": inst.tradable,
            "timeframes": list(inst.timeframes),
            "data": {
                tf: {"bars": s["bars"], "first": _epoch(s["first"]), "last": _epoch(s["last"])}
                for tf, s in summary.get(inst.id, {}).items()
            },
        }
        for inst in load_watchlist()
    ]


@router.get("/candles")
def candles(
    instrument: str | None = None, tf: str | None = None, limit: str | None = None, end: str | None = None
) -> dict:
    inst = _instrument(instrument)
    if tf not in TIMEFRAMES or tf not in inst.timeframes:
        raise _bad(f"tf must be one of {list(inst.timeframes)}")
    count = _int_param("limit", limit, 500, 1, MAX_CANDLES)
    now = clock.utc_now()
    end_ts = None
    if end is not None:
        end_ts = pd.Timestamp(_int_param("end", end, 0, 0, 2**40), unit="s", tz="UTC")
    if series_stats(inst.id, tf)["bars"] == 0:
        raise HTTPException(status_code=503, detail=f"no {tf} data for {inst.id} yet: run ingest")
    df = load_candles(inst.id, tf, end=end_ts).tail(count)
    forming = None
    if end_ts is None or end_ts >= now:
        bar = get_forming(inst.id, tf)
        if bar is not None and (df.empty or bar["ts"] > df["ts"].iloc[-1]):
            forming = {"time": clock.epoch_seconds(bar["ts"])} | {
                k: float(bar[k]) for k in ("open", "high", "low", "close", "volume")
            }
    return {
        "instrument": inst.id,
        "tf": tf,
        "source": candle_source(inst.id, tf) or get_settings().resolved_data_source(),
        "candles": _candles_json(df),
        "forming": forming,
    }


@router.get("/news")
def news(instrument: str | None = None, limit: str | None = None) -> list[dict]:
    if instrument is not None:
        _instrument(instrument)
    count = _int_param("limit", limit, 50, 1, MAX_NEWS)
    return [item.to_api() for item in NewsStore().query(instrument_id=instrument, limit=count)]


# --- Fyers login --------------------------------------------------------------------------


def _new_state() -> str:
    state = secrets.token_urlsafe(16)
    now = time.monotonic()
    with _state_lock:
        for old in [s for s, issued in _login_states.items() if now - issued > LOGIN_STATE_SECONDS]:
            del _login_states[old]
        _login_states[state] = now
    return state


def _take_state(state: str | None) -> bool:
    with _state_lock:
        issued = _login_states.pop(state or "", None)
    return issued is not None and time.monotonic() - issued <= LOGIN_STATE_SECONDS


def _exchange(text: str) -> dict:
    """Exchange a pasted auth code / redirect URL for a session. Error messages never echo the code."""
    try:
        auth_code = fyers.extract_auth_code(text)
    except ValueError as exc:
        raise _bad(str(exc)) from None
    try:
        token = fyers.exchange_auth_code(auth_code)
    except fyers.FyersNotConnected as exc:
        raise _bad(str(exc)) from None
    except fyers.FyersAuthError as exc:
        raise _bad(f"Fyers rejected the code: {exc}") from None
    except fyers.FyersError as exc:
        raise _bad(f"could not complete the Fyers login: {exc}") from None
    return {"connected": True, "expires_at": token.expires_at}


@router.get("/auth/fyers/login")
def fyers_login() -> RedirectResponse:
    try:
        url = fyers.login_url(_new_state())
    except fyers.FyersNotConnected as exc:
        raise _bad(str(exc)) from None
    return RedirectResponse(url, status_code=307)


@router.post("/auth/fyers/code")
async def fyers_code(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise _bad('body must be JSON: {"code": "<auth_code or redirect URL>"}') from None
    code = body.get("code") if isinstance(body, dict) else None
    if not isinstance(code, str) or not code.strip():
        raise _bad('body must be JSON: {"code": "<auth_code or redirect URL>"}')
    return await run_in_threadpool(_exchange, code)


@router.get("/auth/fyers/callback")
def fyers_callback(request: Request) -> RedirectResponse:
    frontend = get_settings().frontend_url.rstrip("/")
    outcome = "error"
    if _take_state(request.query_params.get("state")):
        try:
            _exchange(str(request.url))
            outcome = "connected"
        except HTTPException as exc:
            log.warning("Fyers callback failed: %s", exc.detail)
    else:
        log.warning("Fyers callback with an unknown or expired state; ignored")
    return RedirectResponse(f"{frontend}/?fyers={outcome}", status_code=307)


@router.get("/auth/fyers/status")
def fyers_status() -> dict:
    return fyers.connection_status()
