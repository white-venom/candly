"""Fyers API v3 over plain httpx: login/token handling, symbol master and candle history.

Endpoints, payloads and limits were checked on 2026-09-23 against FyersDev/fyers-skills
(references/auth.md, endpoints.md, market-data.md, rate-limits.md, symbols.md) and the
fyers-apiv3 SDK source (SessionModel / FyersModel.history).
"""

import hashlib
import json
import logging
import os
import random
import re
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import numpy as np
import pandas as pd

from candly.core.calendar import IST, get_calendar
from candly.core.instruments import Instrument
from candly.core.schema import empty_candles
from candly.core.settings import get_settings
from candly.core.timeframes import validate_tf
from candly.data import clock
from candly.data.clean import clean_candles, closed_only
from candly.data.resample import resample_candles
from candly.data.sources import SourceError, SourceUnavailable

log = logging.getLogger(__name__)

# --- every Fyers URL lives here -------------------------------------------------------------
API_BASE = "https://api-t1.fyers.in/api/v3"
DATA_BASE = "https://api-t1.fyers.in/data"
AUTHCODE_URL = f"{API_BASE}/generate-authcode"
VALIDATE_AUTHCODE_URL = f"{API_BASE}/validate-authcode"
REFRESH_TOKEN_URL = f"{API_BASE}/validate-refresh-token"
HISTORY_URL = f"{DATA_BASE}/history"
SYMBOL_MASTER_URL = "https://public.fyers.in/sym_details/{segment}.csv"

# --- documented limits and conventions ------------------------------------------------------
RESOLUTIONS = {"5m": "5", "1D": "D"}
# Documented maximum per request: 100 days for minute resolutions, 366 for daily. Stay one day inside.
MAX_SPAN = {"5m": pd.Timedelta(days=99), "1D": pd.Timedelta(days=365)}
MINUTE_DATA_START = pd.Timestamp("2017-07-03", tz=IST).tz_convert("UTC")
DERIVED_TFS = ("15m", "1h")
AUTH_ERROR_CODES = {-8, -15, -16, -17}
RATE_LIMITED_CODE = -429
REFRESH_TOKEN_DAYS = 15
REFRESH_RETRY_SECONDS = 180  # after a failed session refresh, wait this long before trying again
ACCESS_EXPIRY_HOUR_IST = 6  # undocumented ("end of trading day"); assume the next 06:00 IST
FRONT_SEGMENTS = {"MCX": "MCX_COM", "NSE": "NSE_FO", "BSE": "BSE_FO"}
# Symbol master CSV (no header): 8 = expiry epoch, 9 = symbol ticker, 13 = underlying symbol.
MASTER_EXPIRY, MASTER_TICKER, MASTER_UNDERLYING = 8, 9, 13
# Fyers rejects some default client user agents (FyersDev notes); their own helper sends a browser UA.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
MAX_RETRIES = 4
TIMEOUT = 30.0

_sleep = time.sleep


class FyersError(SourceError):
    pass


class FyersNotConnected(FyersError):
    def __init__(self, message: str = "Fyers is not connected: use Connect Fyers in the dashboard to log in"):
        super().__init__(message)


_REJECTED_WHAT = re.compile(r"\b(token|pin)\b", re.IGNORECASE)
_REJECTED_HOW = re.compile(r"\b(invalid|expired|incorrect|wrong|revoked)\b", re.IGNORECASE)


class FyersAuthError(FyersError):
    """Fyers answered an auth request with an explicit error (as opposed to an outage)."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code

    @property
    def rejects_session(self) -> bool:
        """Fyers says the token or PIN itself is bad, so retrying the same refresh can't succeed.
        A wrong PIN counts: retrying it every few minutes could get the account locked."""
        text = str(self)
        explicit = bool(_REJECTED_WHAT.search(text) and _REJECTED_HOW.search(text))
        return self.code in AUTH_ERROR_CODES or explicit


# --- rate limiting ---------------------------------------------------------------------------


class RateLimiter:
    """Client-side pacing below the documented 10/s and 200/min. Breaching the per-minute cap
    more than 3 times in a day blocks the user for the rest of the day, so stay well under it."""

    def __init__(self, per_second: int = 8, per_minute: int = 150):
        self.per_second, self.per_minute = per_second, per_minute
        self._stamps: deque[float] = deque()
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                while self._stamps and now - self._stamps[0] >= 60:
                    self._stamps.popleft()
                delay = 0.0
                if len(self._stamps) >= self.per_minute:
                    delay = 60 - (now - self._stamps[0])
                elif len(self._stamps) >= self.per_second:
                    delay = 1 - (now - self._stamps[-self.per_second])
                if delay <= 0:
                    self._stamps.append(now)
                    return
                _sleep(delay)


_limiter = RateLimiter()


def _backoff(attempt: int, response: httpx.Response | None = None) -> float:
    if response is not None:
        if response.headers.get("X-Retry-After-Ms"):
            return float(response.headers["X-Retry-After-Ms"]) / 1000
        if response.headers.get("Retry-After", "").isdigit():
            return float(response.headers["Retry-After"])
    return min(30.0, 2**attempt) + random.uniform(0, 0.5)


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})


def _request(
    client: httpx.Client, method: str, url: str, *, retries: int = MAX_RETRIES, **kwargs
) -> httpx.Response:
    """Throttled request; retries 429, -429, 5xx and transport errors with backoff."""
    for attempt in range(retries + 1):
        _limiter.wait()
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if attempt == retries:
                raise FyersError(f"could not reach Fyers ({type(exc).__name__})") from exc
            _sleep(_backoff(attempt))
            continue
        limited = response.status_code == 429 or _json(response).get("code") == RATE_LIMITED_CODE
        if not (limited or response.status_code >= 500):
            return response
        if attempt == retries:
            raise FyersError(f"Fyers returned HTTP {response.status_code} after {retries} retries")
        log.warning("Fyers HTTP %s; retrying", response.status_code)
        _sleep(_backoff(attempt, response if limited else None))
    raise AssertionError("unreachable")


# --- token storage -------------------------------------------------------------------------


@dataclass
class FyersToken:
    app_id: str
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    created_at: int
    expires_at: int
    refresh_expires_at: int | None

    def access_valid(self, now: int) -> bool:
        return now < self.expires_at - 60

    def refreshable(self, now: int) -> bool:
        return (
            bool(self.refresh_token) and self.refresh_expires_at is not None and now < self.refresh_expires_at
        )


TOKEN_MAGIC = b"candly-dpapi-v1\n"
_DPAPI_ENTROPY = b"candly.fyers.token"
DPAPI = sys.platform == "win32"

if DPAPI:
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _BLOB_P = ctypes.POINTER(_Blob)
    for _fn in (_crypt32.CryptProtectData, _crypt32.CryptUnprotectData):
        _fn.argtypes = [
            _BLOB_P, ctypes.c_void_p, _BLOB_P, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, _BLOB_P
        ]
        _fn.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def _blob(data: bytes) -> tuple["_Blob", ctypes.Array]:
        buffer = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer

    def _dpapi(data: bytes, *, protect: bool) -> bytes:
        """Windows DPAPI, CurrentUser scope: only this Windows account can decrypt the result."""
        blob_in, _keep_in = _blob(data)
        entropy, _keep_entropy = _blob(_DPAPI_ENTROPY)
        blob_out = _Blob()
        call = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
        flags = _CRYPTPROTECT_UI_FORBIDDEN
        ok = call(
            ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None, flags, ctypes.byref(blob_out)
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def token_path() -> Path:
    return get_settings().secrets_dir / "fyers_token.json"


def load_token() -> FyersToken | None:
    """The stored session. A plaintext file from before encryption loads once and is re-saved encrypted."""
    path = token_path()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    encrypted = data.startswith(TOKEN_MAGIC)
    try:
        if encrypted and not DPAPI:
            raise ValueError("encrypted with Windows DPAPI")
        raw = _dpapi(data[len(TOKEN_MAGIC) :], protect=False) if encrypted else data
        token = FyersToken(**json.loads(raw))
    except (OSError, ValueError, TypeError):
        log.warning("ignoring unreadable Fyers token file")
        return None
    if DPAPI and not encrypted:
        try:
            save_token(token)
            log.info("Fyers token file re-saved encrypted")
        except OSError as exc:
            log.warning("could not re-save the Fyers token file encrypted: %s", exc)
    return token


def save_token(token: FyersToken) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(token)).encode()
    if DPAPI:
        payload = TOKEN_MAGIC + _dpapi(payload, protect=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".fyers_token.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        if not DPAPI:
            os.chmod(tmp, 0o600)  # the plaintext fallback must be readable by its owner only
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def access_expiry(created: pd.Timestamp) -> pd.Timestamp:
    local = created.tz_convert(IST)
    expiry = local.normalize() + pd.Timedelta(hours=ACCESS_EXPIRY_HOUR_IST)
    if expiry <= local:
        expiry += pd.Timedelta(days=1)
    return expiry.tz_convert("UTC")


# --- login -----------------------------------------------------------------------------------


def app_id_hash(app_id: str, secret: str) -> str:
    return hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest()


def _require_keys():
    settings = get_settings()
    if not settings.has_fyers:
        raise FyersNotConnected("Fyers keys are not set: add FYERS_APP_ID and FYERS_SECRET_KEY to .env")
    return settings


def login_url(state: str) -> str:
    settings = _require_keys()
    query = urlencode(
        {
            "client_id": settings.fyers_app_id,
            "redirect_uri": settings.fyers_redirect_uri,
            "response_type": "code",
            "state": state,
        }
    )
    return f"{AUTHCODE_URL}?{query}"


def _pasted_query(text: str) -> dict[str, list[str]] | None:
    """Query parameters of a pasted redirect URL (or bare "?a=b" string); None for a raw auth code."""
    text = (text or "").strip()
    if "://" not in text and "auth_code=" not in text and "?" not in text:
        return None
    return parse_qs(urlparse(text).query if "://" in text else text.lstrip("?"), keep_blank_values=True)


def extract_state(text: str) -> str | None:
    """The `state` parameter of a pasted redirect URL, or None when there is none."""
    query = _pasted_query(text)
    return None if query is None or "state" not in query else query["state"][0]


def extract_auth_code(text: str) -> str:
    """Accepts the raw auth_code or the whole redirect URL copied from the browser."""
    text = (text or "").strip()
    if not text:
        raise ValueError("no auth code given")
    query = _pasted_query(text)
    if query is None:
        return text
    status = query.get("s", [""])[0]
    if status and status.lower() != "ok":
        raise ValueError("the Fyers login did not succeed; log in again")
    code = query.get("auth_code", [""])[0]
    if not code:
        # In Fyers' redirect, `code` is usually the HTTP status (e.g. 200), not the auth code.
        fallback = query.get("code", [""])[0]
        code = "" if fallback.isdigit() else fallback
    if not code:
        raise ValueError("no auth_code found in the pasted URL")
    return code


def _scrub(message: str, *secrets: str) -> str:
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    return message


def _post_auth(url: str, payload: dict, *secrets: str) -> dict:
    """Outages (network errors, 429, 5xx, non-JSON pages) raise the retryable FyersError after the
    usual retries; only an explicit JSON refusal raises FyersAuthError."""
    with _client() as client:
        response = _request(client, "POST", url, json=payload)
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise FyersError(f"Fyers returned a non-JSON response (HTTP {response.status_code})")
    if body.get("s") == "ok" and body.get("access_token"):
        return body
    if body.get("s") == "ok":
        raise FyersError("Fyers returned no access token")
    message = _scrub(str(body.get("message") or f"HTTP {response.status_code}"), *secrets)
    code = body.get("code")
    raise FyersAuthError(message, code if isinstance(code, int) else None)


def exchange_auth_code(auth_code: str) -> FyersToken:
    settings = _require_keys()
    secret = settings.fyers_secret_key.get_secret_value()
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": app_id_hash(settings.fyers_app_id, secret),
        "code": auth_code,
    }
    body = _post_auth(VALIDATE_AUTHCODE_URL, payload, auth_code, secret)
    now = clock.utc_now()
    refresh = body.get("refresh_token") or None
    token = FyersToken(
        app_id=settings.fyers_app_id,
        access_token=body["access_token"],
        refresh_token=refresh,
        created_at=clock.epoch_seconds(now),
        expires_at=clock.epoch_seconds(access_expiry(now)),
        refresh_expires_at=clock.epoch_seconds(now + pd.Timedelta(days=REFRESH_TOKEN_DAYS))
        if refresh
        else None,
    )
    save_token(token)
    _clear_refresh_failure()
    log.info("Fyers connected; session valid until %s IST", access_expiry(now).tz_convert(IST))
    return token


def refresh_access_token(token: FyersToken) -> FyersToken:
    """New access token from the ~15-day refresh token plus the PIN, so daily logins are rarely needed."""
    settings = _require_keys()
    pin = settings.fyers_pin.get_secret_value()
    if not pin:
        raise FyersNotConnected("Fyers session expired and FYERS_PIN is not set: log in again")
    secret = settings.fyers_secret_key.get_secret_value()
    payload = {
        "grant_type": "refresh_token",
        "appIdHash": app_id_hash(settings.fyers_app_id, secret),
        "refresh_token": token.refresh_token,
        "pin": pin,
    }
    try:
        body = _post_auth(REFRESH_TOKEN_URL, payload, secret, pin, token.refresh_token or "")
    except FyersAuthError as exc:
        if not exc.rejects_session:
            raise FyersError(f"Fyers session refresh failed ({exc}); will retry") from exc
        log.warning("Fyers rejected the session refresh (code %s): %s", exc.code, exc)
        token.refresh_token, token.refresh_expires_at = None, None
        save_token(token)
        raise FyersNotConnected(f"Fyers session refresh was rejected ({exc}): log in again") from exc
    now = clock.utc_now()
    token.access_token = body["access_token"]
    token.created_at = clock.epoch_seconds(now)
    token.expires_at = clock.epoch_seconds(access_expiry(now))
    save_token(token)
    log.info("Fyers session refreshed")
    return token


_refresh_lock = threading.Lock()
_refresh_failure: tuple[float, str] | None = None  # (time.monotonic() of the failure, reason)


def _clear_refresh_failure() -> None:
    global _refresh_failure
    _refresh_failure = None


def _usable_token(app_id: str) -> tuple[FyersToken, bool]:
    """The stored token and whether its access token is still valid. Raises FyersNotConnected when
    there is none, or when it has expired and can't be refreshed."""
    token = load_token()
    if token is None or token.app_id != app_id:
        raise FyersNotConnected()
    now = clock.epoch_seconds(clock.utc_now())
    if token.access_valid(now):
        return token, True
    if token.refreshable(now):
        return token, False
    raise FyersNotConnected("Fyers session expired: use Connect Fyers in the dashboard to log in again")


def _refresh(app_id: str, *, wait: bool) -> FyersToken:
    """One refresh at a time, and none for REFRESH_RETRY_SECONDS after a failed one, so the health
    poll and the ingest jobs don't hammer Fyers during an outage."""
    global _refresh_failure
    if not _refresh_lock.acquire(blocking=wait):
        raise FyersError("a Fyers session refresh is already running")
    try:
        token, valid = _usable_token(app_id)  # another caller may have refreshed meanwhile
        if valid:
            return token
        if _refresh_failure is not None and time.monotonic() - _refresh_failure[0] < REFRESH_RETRY_SECONDS:
            raise FyersError(f"{_refresh_failure[1]}; retrying in a few minutes")
        try:
            token = refresh_access_token(token)
        except FyersNotConnected:
            raise
        except FyersError as exc:
            _refresh_failure = (time.monotonic(), str(exc))
            raise
        _refresh_failure = None
        return token
    finally:
        _refresh_lock.release()


def ensure_token(*, wait: bool = True) -> FyersToken:
    """A usable token, refreshing it when the access token has expired. Raises FyersNotConnected when
    a login is needed, or FyersError when a refresh failed for now (it is retried later).
    wait=False gives up at once instead of waiting for a refresh another thread is running."""
    settings = _require_keys()
    token, valid = _usable_token(settings.fyers_app_id)
    return token if valid else _refresh(settings.fyers_app_id, wait=wait)


def connection_status() -> dict:
    """{"connected": bool, "expires_at": epoch | None}. Cheap enough for every health poll: it reads the
    stored token, and only an expired session triggers a (throttled, non-waiting) refresh."""
    if not get_settings().has_fyers:
        return {"connected": False, "expires_at": None}
    try:
        token = ensure_token(wait=False)
    except FyersError as exc:
        log.debug("Fyers not connected: %s", exc)
        return {"connected": False, "expires_at": None}
    return {"connected": True, "expires_at": token.expires_at}


def _expire(token: FyersToken) -> None:
    token.expires_at = clock.epoch_seconds(clock.utc_now())
    save_token(token)


# --- symbols ---------------------------------------------------------------------------------

_master_cache: dict[Path, tuple[str, pd.DataFrame]] = {}


def _today_ist() -> str:
    return clock.utc_now().tz_convert(IST).date().isoformat()


def master_path(segment: str) -> Path:
    return get_settings().data_dir / "cache" / "fyers" / f"{segment}.csv"


def master_day(path: Path) -> str:
    """IST date (YYYY-MM-DD) the cached master file was downloaded."""
    return datetime.fromtimestamp(path.stat().st_mtime, IST).date().isoformat()


def read_master(path: Path) -> pd.DataFrame:
    """Columns expiry (epoch), ticker, underlying, from a downloaded symbol master CSV."""
    try:
        raw = pd.read_csv(path, header=None, dtype=str, keep_default_na=False, on_bad_lines="skip")
        return pd.DataFrame(
            {
                "expiry": pd.to_numeric(raw[MASTER_EXPIRY], errors="coerce"),
                "ticker": raw[MASTER_TICKER].str.strip(),
                "underlying": raw[MASTER_UNDERLYING].str.strip(),
            }
        )
    except (ValueError, KeyError) as exc:  # empty file, an HTML error page, too few columns
        raise FyersError(f"unreadable symbol master ({type(exc).__name__}: {exc})") from exc


def symbol_master(segment: str, *, retries: int = MAX_RETRIES) -> pd.DataFrame:
    """Columns expiry (epoch), ticker, underlying. Cached on disk and in memory for the IST day."""
    today = _today_ist()
    path = master_path(segment)
    cached = _master_cache.get(path)
    if cached and cached[0] == today:
        return cached[1]
    if path.exists() and master_day(path) == today:
        table = read_master(path)
    else:
        with _client() as client:
            response = _request(client, "GET", SYMBOL_MASTER_URL.format(segment=segment), retries=retries)
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(response.content)
            table = read_master(Path(tmp))  # a broken download never replaces the last good copy
            os.replace(tmp, path)
        finally:
            Path(tmp).unlink(missing_ok=True)
    _master_cache[path] = (today, table)
    return table


def front_month_symbol(exchange: str, underlying: str) -> str:
    """The nearest-expiry future that hasn't expired yet, e.g. MCX:CRUDEOIL26OCTFUT."""
    segment = FRONT_SEGMENTS.get(exchange)
    if segment is None:
        raise SourceUnavailable(f"no futures segment known for {exchange}")
    table = symbol_master(segment)
    now = clock.epoch_seconds(clock.utc_now())
    futures = table[
        (table["underlying"] == underlying) & table["ticker"].str.endswith("FUT") & (table["expiry"] >= now)
    ]
    if futures.empty:
        raise FyersError(f"no live {underlying} future found in the {segment} symbol master")
    return str(futures.sort_values("expiry").iloc[0]["ticker"])


def resolve_symbol(instrument: Instrument) -> str:
    symbol = instrument.source_symbol("fyers")
    if not symbol:
        raise SourceUnavailable(f"{instrument.id} has no Fyers symbol")
    if symbol.endswith("@FRONT"):
        exchange, _, underlying = symbol.removesuffix("@FRONT").partition(":")
        return front_month_symbol(exchange, underlying)
    return symbol


# --- history ---------------------------------------------------------------------------------


def _get_data(client: httpx.Client, url: str, params: dict) -> dict:
    for _ in range(2):
        token = ensure_token()
        headers = {"Authorization": f"{token.app_id}:{token.access_token}"}
        response = _request(client, "GET", url, params=params, headers=headers)
        body = _json(response)
        if response.status_code == 401 or body.get("code") in AUTH_ERROR_CODES:
            log.info("Fyers rejected the access token; trying a session refresh")
            _expire(token)
            continue
        if body.get("s") in ("ok", "no_data"):
            return body
        raise FyersError(
            f"Fyers history error {body.get('code')}: {body.get('message') or response.status_code}"
        )
    raise FyersNotConnected("Fyers rejected the session: use Connect Fyers in the dashboard to log in again")


def history(symbol: str, tf: str, start: pd.Timestamp, end: pd.Timestamp, *, oi: bool = False) -> list[list]:
    """Raw [epoch, o, h, l, c, v(, oi)] rows, requested in chunks within the documented maximum."""
    rows: list[list] = []
    span = MAX_SPAN[tf]
    chunk_start = start
    with _client() as client:
        while chunk_start < end:
            chunk_end = min(chunk_start + span, end)
            params = {
                "symbol": symbol,
                "resolution": RESOLUTIONS[tf],
                "date_format": "0",
                "range_from": str(clock.epoch_seconds(chunk_start)),
                "range_to": str(clock.epoch_seconds(chunk_end)),
                "cont_flag": "1",
            }
            if oi:
                params["oi_flag"] = "1"
            rows.extend(_get_data(client, HISTORY_URL, params).get("candles") or [])
            chunk_start = chunk_end
    return rows


def to_candles(rows: list[list], tf: str, exchange: str, kind: str | None = None) -> pd.DataFrame:
    if not rows:
        return empty_candles()
    raw = pd.DataFrame([row[:7] for row in rows])
    ts = pd.to_datetime(raw[0].astype("int64"), unit="s", utc=True)
    if tf == "1D":
        # Daily epochs mark the trading date; the canonical ts is that day's session open.
        cal = get_calendar()
        ts = pd.Series([cal.session_times(exchange, d)[0] for d in ts.dt.tz_convert(IST).dt.date])
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": raw[1].astype("float64"),
            "high": raw[2].astype("float64"),
            "low": raw[3].astype("float64"),
            "close": raw[4].astype("float64"),
            "volume": raw[5].astype("float64"),
            "oi": raw[6].astype("float64") if 6 in raw.columns else np.nan,
        }
    )
    return clean_candles(df, tf, exchange, kind=kind)


def fetch_candles(
    instrument: Instrument,
    tf: str,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    *,
    include_forming: bool = False,
) -> pd.DataFrame:
    """5m and 1D come straight from Fyers; 15m and 1h are resampled from 5m."""
    tf = validate_tf(tf)
    now = clock.utc_now()
    end = now if end is None else min(clock.to_utc(end), now)
    base_tf = "5m" if tf in DERIVED_TFS else tf
    if start is None:
        start = MINUTE_DATA_START if base_tf == "5m" else end - timedelta(days=365 * 20)
    start = clock.to_utc(start)
    if base_tf == "5m":
        start = max(start, MINUTE_DATA_START)
    symbol = resolve_symbol(instrument)
    rows = history(symbol, base_tf, start, end, oi=instrument.kind == "future")
    df = to_candles(rows, base_tf, instrument.exchange, instrument.kind)
    if tf in DERIVED_TFS:
        df = resample_candles(df, tf, instrument.exchange)
    return df if include_forming else closed_only(df, instrument.exchange, tf, now)
