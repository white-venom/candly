"""Platform routes: health, instruments, candles, news, the Fyers login flow and the Fyers data sync
(docs/CONTRACTS.md §4)."""

import logging
import secrets
import threading
import time

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from candly import __version__
from candly.core.calendar import IST, get_calendar
from candly.core.expiry import ExpiryInfo
from candly.core.instruments import EXCHANGES, Instrument, UnknownInstrument, get_instrument, load_watchlist
from candly.core.settings import get_settings
from candly.core.timeframes import TIMEFRAMES
from candly.data import clock
from candly.data.expiries import expiry_check, expiry_with_source
from candly.data.live import get_forming
from candly.data.sources import fyers
from candly.data.store import candle_source, data_summary, load_candles, series_stats
from candly.jobs import sync
from candly.news.store import NewsStore

log = logging.getLogger(__name__)
router = APIRouter(tags=["platform"])

MAX_CANDLES = 5000
MAX_NEWS = 500
LOGIN_STATE_SECONDS = 15 * 60
INGEST_NOT_CONNECTED = "Fyers not connected — log in to resume data updates"
INGEST_NO_KEYS = "Fyers keys are not set — add FYERS_APP_ID and FYERS_SECRET_KEY to .env"
BAD_STATE = "login link expired or not from this app — click Connect Fyers again"
SYNC_RUNNING = "a Fyers sync is already running"
SYNC_NOT_FYERS = "DATA_SOURCE is set to yahoo in .env: set it to auto or fyers to sync Fyers data"
SYNC_HEALTH_FIELDS = ("status", "step", "progress", "message", "started_at", "finished_at", "error")
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


def _ingest_status(data_source: str, has_fyers: bool, fyers_connected: bool) -> dict:
    """Once Fyers keys exist ingest uses Fyers only, so a missing session stalls every update."""
    if data_source != "fyers" or fyers_connected:
        return {"status": "ok", "reason": None}
    return {"status": "blocked", "reason": INGEST_NOT_CONNECTED if has_fyers else INGEST_NO_KEYS}


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
    data_source = settings.resolved_data_source()
    fyers_connected = fyers.connection_status()["connected"]
    return {
        "status": "ok",
        "version": __version__,
        "time": clock.epoch_seconds(now),
        "data_source": data_source,
        "keys": {
            "fyers": settings.has_fyers,
            "fyers_connected": fyers_connected,
            "kotak_neo": settings.has_kotak,
            "anthropic": settings.has_anthropic,
            "telegram": settings.has_telegram,
        },
        "markets": markets,
        "ingest": _ingest_status(data_source, settings.has_fyers, fyers_connected),
        "expiry_check": expiry_check(),
        "sync": {key: value for key, value in sync.get_sync_status().items() if key in SYNC_HEALTH_FIELDS},
    }


def _expiry_json(info: ExpiryInfo | None, source: str | None) -> dict | None:
    if info is None:
        return None
    return {
        "next": info.next_expiry.isoformat(),
        "kind": info.kind,
        "days_to_expiry": info.days_to_expiry,
        "is_expiry_day": info.is_expiry_day,
        "source": source,
    }


@router.get("/instruments")
def instruments() -> list[dict]:
    summary = data_summary()
    today = clock.utc_now().tz_convert(IST).date()
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
            "expiry": _expiry_json(*expiry_with_source(inst.id, today)),
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


def _require_json(request: Request) -> None:
    # Requiring JSON makes a POST CORS-preflighted, so other sites can't send a "simple" form request here.
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")


def _start_sync_after_login() -> None:
    """Never lets a sync problem fail the login itself."""
    try:
        if sync.start_if_needed():
            log.info("Fyers connected: data sync started in the background")
    except Exception:
        log.exception("could not start the Fyers sync after login")


@router.post("/auth/fyers/code")
async def fyers_code(request: Request) -> dict:
    _require_json(request)
    try:
        body = await request.json()
    except ValueError:
        raise _bad('body must be JSON: {"code": "<auth_code or redirect URL>"}') from None
    code = body.get("code") if isinstance(body, dict) else None
    if not isinstance(code, str) or not code.strip():
        raise _bad('body must be JSON: {"code": "<auth_code or redirect URL>"}')
    state = fyers.extract_state(code)
    if state is not None and not _take_state(state):
        raise _bad(BAD_STATE)
    result = await run_in_threadpool(_exchange, code)
    await run_in_threadpool(_start_sync_after_login)
    return result


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
            _start_sync_after_login()
    else:
        log.warning("Fyers callback with an unknown or expired state; ignored")
    return RedirectResponse(f"{frontend}/?fyers={outcome}", status_code=307)


@router.get("/auth/fyers/status")
def fyers_status() -> dict:
    return fyers.connection_status()


# --- Fyers data sync ------------------------------------------------------------------------


@router.post("/sync/fyers", status_code=202)
async def sync_fyers(request: Request) -> dict:
    """Starts (or resumes) the Fyers sync in the background. The body may be empty or any JSON."""
    _require_json(request)
    if (await request.body()).strip():
        try:
            await request.json()
        except ValueError:
            raise _bad("body must be JSON, e.g. {}") from None
    if sync.is_running():
        raise HTTPException(status_code=409, detail=SYNC_RUNNING)
    settings = get_settings()
    if not settings.has_fyers:
        raise _bad(INGEST_NO_KEYS)
    if settings.resolved_data_source() != "fyers":
        raise _bad(SYNC_NOT_FYERS)
    if not (await run_in_threadpool(fyers.connection_status))["connected"]:
        raise _bad(INGEST_NOT_CONNECTED)
    if not await run_in_threadpool(sync.start_fyers_sync):
        raise HTTPException(status_code=409, detail=SYNC_RUNNING)
    return sync.get_sync_status()


@router.get("/sync/status")
def sync_status() -> dict:
    return sync.get_sync_status()
