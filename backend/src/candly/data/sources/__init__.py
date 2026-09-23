"""Candle sources. Each returns a validated candle frame with `ts` = bar open time (UTC)."""

import pandas as pd

from candly.core.instruments import Instrument
from candly.core.settings import get_settings

SOURCES = ("fyers", "yahoo")


def resolve_source(source: str = "auto") -> str:
    resolved = get_settings().resolved_data_source() if source == "auto" else source
    if resolved not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected auto or one of {SOURCES}")
    return resolved


class SourceError(RuntimeError):
    pass


class SourceUnavailable(SourceError):
    """The source doesn't offer this instrument or timeframe."""


def fetch_candles(
    source: str,
    instrument: Instrument,
    tf: str,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    *,
    include_forming: bool = False,
) -> pd.DataFrame:
    if source == "yahoo":
        from candly.data.sources import yahoo as module
    elif source == "fyers":
        from candly.data.sources import fyers as module
    else:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
    return module.fetch_candles(instrument, tf, start, end, include_forming=include_forming)
