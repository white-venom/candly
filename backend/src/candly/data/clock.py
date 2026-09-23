from datetime import date, datetime

import pandas as pd

from candly.core.calendar import IST

EPOCH = pd.Timestamp(0, tz="UTC")


def utc_now() -> pd.Timestamp:
    """The single source of "now" for the data platform, so tests can freeze time."""
    return pd.Timestamp.now(tz="UTC")


def to_utc(value: pd.Timestamp | datetime | str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        raise ValueError(f"naive timestamp {value!r}: pass a timezone-aware value")
    return ts.tz_convert("UTC")


def ist_midnight(d: date) -> pd.Timestamp:
    return pd.Timestamp(datetime(d.year, d.month, d.day), tz=IST).tz_convert("UTC")


def epoch_seconds(ts: pd.Timestamp) -> int:
    return int((ts - EPOCH) // pd.Timedelta(seconds=1))


def epoch_series(ts: pd.Series) -> pd.Series:
    return (ts - EPOCH) // pd.Timedelta(seconds=1)
