"""Derivatives expiry context per bar, from the published expiry schedule (never from price data).

The value at a bar depends only on that bar's IST date, so it is causal. `candly.data.expiries` is the
single entry point (NSE/BSE rules via core, MCX contract dates via the symbol master); until it exists,
`candly.core.expiry` answers for NSE/BSE.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd

from candly.core.calendar import IST, MarketCalendar, get_calendar
from candly.core.instruments import exchange_of

log = logging.getLogger(__name__)
EXPIRY_COLUMNS = ["expiry_day", "days_to_expiry"]
_warned: set[str] = set()


def _entry_point() -> Callable:
    try:
        from candly.data.expiries import expiry_info
    except ImportError:
        from candly.core.expiry import expiry_info
    return expiry_info


@lru_cache(maxsize=200_000)
def _cached(instrument_id: str, d: date):
    return _entry_point()(instrument_id, d)


def expiry_on(instrument_id: str, d: date):
    """ExpiryInfo on IST date `d`, or None when the instrument has no expiry (or the lookup failed).
    NSE/BSE answers come from fixed rules and are memoised; MCX answers change as symbol masters arrive."""
    try:
        if exchange_of(instrument_id) == "MCX":
            return _entry_point()(instrument_id, d)
        return _cached(instrument_id, d)
    except Exception as exc:  # a missing symbol master must not break features
        if instrument_id not in _warned:
            _warned.add(instrument_id)
            log.warning("expiry lookup failed for %s (%s); treated as no expiry", instrument_id, exc)
        return None


def _trading_days(cal: MarketCalendar, exchange: str, a: date, b: date) -> int:
    """Trading days in (a, b]."""
    return sum(cal.is_trading_day(exchange, a + timedelta(days=n)) for n in range(1, (b - a).days + 1))


def _by_day(instrument_id: str, dates: list[date]) -> dict[date, tuple[bool, int] | None]:
    """(is expiry day, trading days to the next expiry) per date. One lookup per expiry: every date up to
    an expiry shares it, and the day counts are walked back from it."""
    cal = get_calendar()
    exchange = exchange_of(instrument_id)
    out: dict[date, tuple[bool, int] | None] = {}
    i = 0
    while i < len(dates):
        info = expiry_on(instrument_id, dates[i])
        if info is None:
            out[dates[i]] = None
            i += 1
            continue
        nxt = info.next_expiry
        j = i
        while j + 1 < len(dates) and dates[j + 1] <= nxt:
            j += 1
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
