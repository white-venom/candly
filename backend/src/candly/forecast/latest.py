"""The latest forecast per (instrument, tf), kept in memory for the scanner.

The forecast cycle runs in the API process (the scheduler starts in the app's lifespan), so what it
remembers here is what /api/scanner serves; the scanner computes a forecast itself only on a miss. Each
entry also keeps the previous close and a short tail of closed bars, for the change and forming-pattern
columns, so serving a row never re-reads the candle store.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import pandas as pd

from candly.forecast.models import Forecast

TAIL_BARS = 60  # enough for every candlestick pattern's bars plus its ATR(14) context


@dataclass(frozen=True)
class Latest:
    forecast: Forecast
    prev_close: float | None
    tail: pd.DataFrame  # the last TAIL_BARS closed bars up to the reference bar

    @property
    def ref_ts(self) -> pd.Timestamp:
        return pd.Timestamp(self.forecast.ref_time, unit="s", tz="UTC")


_entries: dict[tuple[str, str], Latest] = {}
_lock = threading.Lock()


def remember(forecast: Forecast, candles: pd.DataFrame) -> Latest:
    """Keep `forecast` as the latest for its (instrument, tf), unless a newer reference bar is already
    kept. `candles` are the closed bars it was made from (at least up to its reference bar)."""
    ref_ts = pd.Timestamp(forecast.ref_time, unit="s", tz="UTC")
    upto = candles[candles["ts"] <= ref_ts]
    tail = upto.iloc[-TAIL_BARS:].reset_index(drop=True)
    prev_close = float(upto["close"].iloc[-2]) if len(upto) >= 2 else None
    entry = Latest(forecast, prev_close, tail)
    key = (forecast.instrument, forecast.tf)
    with _lock:
        current = _entries.get(key)
        if current is None or current.forecast.ref_time <= forecast.ref_time:
            _entries[key] = entry
            return entry
        return current


def latest(instrument_id: str, tf: str) -> Latest | None:
    with _lock:
        return _entries.get((instrument_id, tf))


def clear() -> None:
    with _lock:
        _entries.clear()
