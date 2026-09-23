"""Exchange holidays observed in the stored Fyers daily bars (written by the Fyers sync, merged into the
calendar by candly.core.calendar).

For each exchange, a weekday between the first and last stored Fyers 1D bar of any of its watchlist
instruments is a holiday when none of those instruments has a bar on it and no special session is
configured for it. One instrument missing a day is a gap in its data, not a holiday. Series that aren't
Fyers data are left out (Yahoo misses too many days).
"""

import logging
from collections.abc import Collection
from datetime import date
from pathlib import Path

import pandas as pd

from candly.core.calendar import IST, get_calendar
from candly.core.instruments import EXCHANGES, load_watchlist
from candly.core.settings import get_settings
from candly.data.jsonfile import read_json, write_json
from candly.data.store import candle_source, load_ts

log = logging.getLogger(__name__)


def holidays_path() -> Path:
    return get_settings().derived_dir / "holidays_observed.json"


def observed_holidays(ts: pd.Series, special_days: Collection[date]) -> list[date]:
    """Weekdays from the first to the last bar (IST dates) with no bar and no configured special session."""
    if ts.empty:
        return []
    have = set(ts.dt.tz_convert(IST).dt.date)
    weekdays = (d.date() for d in pd.bdate_range(min(have), max(have)))
    return [d for d in weekdays if d not in have and d not in special_days]


def _fyers_daily(exchange: str) -> list[str]:
    ids = [i.id for i in load_watchlist() if i.exchange == exchange and "1D" in i.timeframes]
    return [i for i in ids if candle_source(i, "1D") == "fyers"]


def derive_observed_holidays() -> dict[str, list[str]]:
    """Writes data/derived/holidays_observed.json: {"NSE": ["YYYY-MM-DD", ...], "BSE": [...], "MCX": [...]}.
    An exchange with no Fyers 1D series keeps its old dates."""
    previous = read_json(holidays_path())
    previous = previous if isinstance(previous, dict) else {}
    cal = get_calendar()
    result: dict[str, list[str]] = {}
    for exchange in EXCHANGES:
        ids = _fyers_daily(exchange)
        if not ids:
            log.warning("%s: no Fyers 1D series; kept old dates", exchange)
            result[exchange] = list(previous.get(exchange) or [])
            continue
        traded = pd.concat([load_ts(i, "1D") for i in ids], ignore_index=True)
        special = cal.spec(exchange).special_sessions.keys()
        days = observed_holidays(traded, special)
        result[exchange] = [d.isoformat() for d in days]
        log.info("%s: %d weekday holidays observed across %d series", exchange, len(days), len(ids))
    write_json(holidays_path(), result)
    return result
