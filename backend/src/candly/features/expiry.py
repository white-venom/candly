"""Derivatives expiry context per bar, from the published expiry schedule (never from price data).

The value at a bar depends only on that bar's IST date, so it is causal. `candly.data.expiries` is the
single entry point: exchange-listed dates from data/expiries/live.json (refreshed daily), else the rules
in config/expiry.yaml. Without it, `candly.core.expiry` answers from the rules.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd

from candly.core.calendar import IST, MarketCalendar, get_calendar
from candly.core.expiry import ExpiryInfo
from candly.core.instruments import exchange_of
from candly.core.settings import get_settings

log = logging.getLogger(__name__)
EXPIRY_COLUMNS = ["expiry_day", "days_to_expiry"]
_warned: set[str] = set()


def _entry_point() -> Callable:
    try:
        from candly.data.expiries import expiry_with_source
    except ImportError:
        from candly.core.expiry import expiry_info

        def expiry_with_source(instrument_id: str, d: date):
            info = expiry_info(instrument_id, d)
            return info, None if info is None else "rules"

    return expiry_with_source


def live_stamp() -> tuple:
    """Identifies the current data/expiries/live.json: (path, mtime, size), or (path, None) without one.
    The daily refresh rewrites it, so answers memoised under an older stamp are never reused."""
    path = get_settings().data_dir / "expiries" / "live.json"
    try:
        stat = path.stat()
    except OSError:
        return (str(path), None)
    return (str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=200_000)
def _cached(instrument_id: str, d: date, stamp: tuple | None) -> tuple[ExpiryInfo | None, str | None]:
    return _entry_point()(instrument_id, d)


def expiry_with_source(
    instrument_id: str, d: date, stamp: tuple | None = None
) -> tuple[ExpiryInfo | None, str | None]:
    """(ExpiryInfo on IST date `d`, "exchange" | "rules"), or (None, None) when the instrument has no
    expiry (or the lookup failed). Pass `stamp` from live_stamp() to look up many dates with one stat."""
    try:
        return _cached(instrument_id, d, live_stamp() if stamp is None else stamp)
    except Exception as exc:  # unreadable expiry data must not break features
        if instrument_id not in _warned:
            _warned.add(instrument_id)
            log.warning("expiry lookup failed for %s (%s); treated as no expiry", instrument_id, exc)
        return None, None


def expiry_on(instrument_id: str, d: date, stamp: tuple | None = None) -> ExpiryInfo | None:
    """ExpiryInfo on IST date `d`, or None when the instrument has no expiry."""
    return expiry_with_source(instrument_id, d, stamp)[0]


def _trading_days(cal: MarketCalendar, exchange: str, a: date, b: date) -> int:
    """Trading days in (a, b]."""
    return sum(cal.is_trading_day(exchange, a + timedelta(days=n)) for n in range(1, (b - a).days + 1))


def _by_day(instrument_id: str, dates: list[date]) -> dict[date, tuple[bool, int] | None]:
    """(is expiry day, trading days to the next expiry) per date, with a few lookups per expiry.

    Dates up to an expiry share it while its source stays the same. The source switches from the rules
    to exchange dates at the first refresh and back when live data goes stale, at most once between two
    expiries, so a segment is cut at its first date with a different answer (found by bisection). Day
    counts are walked back from the expiry."""
    cal = get_calendar()
    exchange = exchange_of(instrument_id)
    stamp = live_stamp()

    def answer(k: int) -> tuple[date, str | None] | None:
        info, source = expiry_with_source(instrument_id, dates[k], stamp)
        return None if info is None else (info.next_expiry, source)

    out: dict[date, tuple[bool, int] | None] = {}
    i = 0
    while i < len(dates):
        first = answer(i)
        if first is None:
            out[dates[i]] = None
            i += 1
            continue
        nxt = first[0]
        j = i
        while j + 1 < len(dates) and dates[j + 1] <= nxt:
            j += 1
        if answer(j) != first:
            lo, hi = i, j
            while hi - lo > 1:
                mid = (lo + hi) // 2
                lo, hi = (mid, hi) if answer(mid) == first else (lo, mid)
            j = lo
        remaining = _trading_days(cal, exchange, dates[j], nxt)
        for k in range(j, i - 1, -1):
            if k < j:
                remaining += _trading_days(cal, exchange, dates[k], dates[k + 1])
            out[dates[k]] = (dates[k] == nxt, remaining)
        i = j + 1
    return out


def expiry_features(instrument_id: str | None, ts: pd.Series) -> pd.DataFrame:
    """Per bar: `expiry_day` (True/False, None without expiry info) and `days_to_expiry` (trading days
    after the bar's IST date up to and including the next expiry, 0 on the day; NaN without info)."""
    expiry_day = pd.Series(None, index=ts.index, dtype=object)
    days_to = pd.Series(np.nan, index=ts.index, dtype="float64")
    if instrument_id is None or ts.empty:
        return pd.DataFrame({"expiry_day": expiry_day, "days_to_expiry": days_to})
    days = ts.dt.tz_convert(IST).dt.date
    by_day = _by_day(instrument_id, sorted(set(days)))
    known = days.map(lambda d: by_day[d] is not None).astype(bool)
    expiry_day[known] = days[known].map(lambda d: by_day[d][0]).astype(object)
    days_to[known] = days[known].map(lambda d: by_day[d][1]).astype("float64")
    return pd.DataFrame({"expiry_day": expiry_day, "days_to_expiry": days_to})
