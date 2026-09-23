"""The live, still-forming bar: built from today's 5m history of the active source."""

import logging
import threading
import time

import pandas as pd

from candly.core.calendar import get_calendar
from candly.core.instruments import get_instrument
from candly.core.timeframes import validate_tf
from candly.data import clock
from candly.data.resample import resample_candles
from candly.data.sources import fetch_candles, resolve_source

log = logging.getLogger(__name__)

CACHE_SECONDS = 15.0
_cache: dict[tuple[str, str], tuple[float, pd.DataFrame | None]] = {}
_lock = threading.Lock()


def _today_5m(instrument_id: str, now: pd.Timestamp) -> pd.DataFrame | None:
    inst = get_instrument(instrument_id)
    source = resolve_source()
    key = (instrument_id, source)
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
    cal = get_calendar()
    session_open = cal.session_times(inst.exchange, cal.local_date(now))[0]
    try:
        bars = fetch_candles(source, inst, "5m", session_open, include_forming=True)
    except Exception as exc:  # any source failure just means "no live bar"
        log.debug("no live bars for %s from %s: %s", instrument_id, source, exc)
        bars = None
    with _lock:
        _cache[key] = (time.monotonic(), bars)
    return bars


def get_forming(instrument_id: str, tf: str) -> pd.Series | None:
    """The current partial bar (index: ts, open, high, low, close, volume, oi), or None when the
    market is closed or no source can supply today's bars."""
    tf = validate_tf(tf)
    inst = get_instrument(instrument_id)
    now = clock.utc_now()
    cal = get_calendar()
    if not cal.is_open(inst.exchange, now):
        return None
    bars = _today_5m(instrument_id, now)
    if bars is None or bars.empty:
        return None
    bars = resample_candles(bars, tf, inst.exchange)
    last = bars.iloc[-1]
    if cal.bar_close_time(inst.exchange, last["ts"], tf) <= now:
        return None
    return last.rename(None)
