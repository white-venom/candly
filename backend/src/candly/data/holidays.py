"""Exchange holidays observed in the stored Fyers daily bars (written by the Fyers sync, merged into the
calendar by candly.core.calendar).

For each exchange, a weekday between the first and last stored 1D bar of its reference instrument is a
holiday when that instrument has no bar on it and no special session is configured for it.
"""

import logging
from collections.abc import Collection
from datetime import date
from pathlib import Path

import pandas as pd

from candly.core.calendar import IST, get_calendar
from candly.core.settings import get_settings
from candly.data.jsonfile import read_json, write_json
from candly.data.store import candle_source, load_ts

log = logging.getLogger(__name__)

REFERENCE = {"NSE": "NSE:NIFTY50", "BSE": "BSE:SENSEX", "MCX": "MCX:CRUDEOIL"}


def holidays_path() -> Path:
    return get_settings().derived_dir / "holidays_observed.json"


def observed_holidays(ts: pd.Series, special_days: Collection[date]) -> list[date]:
    """Weekdays from the first to the last bar (IST dates) with no bar and no configured special session."""
    if ts.empty:
        return []
    have = set(ts.dt.tz_convert(IST).dt.date)
    weekdays = (d.date() for d in pd.bdate_range(min(have), max(have)))
    return [d for d in weekdays if d not in have and d not in special_days]


def derive_observed_holidays() -> dict[str, list[str]]:
    """Writes data/derived/holidays_observed.json: {"NSE": ["YYYY-MM-DD", ...], "BSE": [...], "MCX": [...]}.
    An exchange whose reference series isn't Fyers data keeps its old dates (Yahoo misses too many days)."""
    previous = read_json(holidays_path())
    previous = previous if isinstance(previous, dict) else {}
    cal = get_calendar()
    result: dict[str, list[str]] = {}
    for exchange, instrument_id in REFERENCE.items():
        source = candle_source(instrument_id, "1D")
        if source != "fyers":
            log.warning("%s: %s 1D is not Fyers data (%s); kept old dates", exchange, instrument_id, source)
            result[exchange] = list(previous.get(exchange) or [])
            continue
        special = cal.spec(exchange).special_sessions.keys()
        days = observed_holidays(load_ts(instrument_id, "1D"), special)
        result[exchange] = [d.isoformat() for d in days]
        log.info("%s: %d weekday holidays observed in %s", exchange, len(days), instrument_id)
    write_json(holidays_path(), result)
    return result
