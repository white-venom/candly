from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST, get_calendar
from candly.data import clock, live
from candly.data.sources import SourceError

cal = get_calendar()
NOW = pd.Timestamp(datetime(2026, 9, 23, 10, 2), tz=IST).tz_convert("UTC")


def ist(y, m, d, hh, mm) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm), tz=IST).tz_convert("UTC")


def today_5m() -> pd.DataFrame:
    opens = [
        ts for ts in cal.expected_bar_opens("NSE", date(2026, 9, 23), "5m") if ts <= ist(2026, 9, 23, 10, 0)
    ]
    n = len(opens)
    base = np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "ts": pd.DatetimeIndex(opens),
            "open": 100 + base,
            "high": 102 + base,
            "low": 99 + base,
            "close": 101 + base,
            "volume": np.full(n, 10.0),
            "oi": np.nan,
        }
    )


@pytest.fixture
def source(monkeypatch, tmp_data_dir, no_keys):
    calls = []

    def fake_fetch(source, instrument, tf, start=None, end=None, *, include_forming=False):
        calls.append((source, instrument.id, tf, start, include_forming))
        return today_5m()

    live._cache.clear()
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    monkeypatch.setattr(live, "fetch_candles", fake_fetch)
    yield calls
    live._cache.clear()


def test_forming_bar_per_timeframe(source):
    five = live.get_forming("NSE:RELIANCE", "5m")
    assert five["ts"] == ist(2026, 9, 23, 10, 0) and five["open"] == 109.0
    fifteen = live.get_forming("NSE:RELIANCE", "15m")
    assert fifteen["ts"] == ist(2026, 9, 23, 10, 0)
    hour = live.get_forming("NSE:RELIANCE", "1h")
    assert hour["ts"] == ist(2026, 9, 23, 9, 15)
    assert hour["open"] == 100.0 and hour["close"] == 110.0 and hour["volume"] == 100.0
    day = live.get_forming("NSE:RELIANCE", "1D")
    assert day["ts"] == ist(2026, 9, 23, 9, 15) and day["high"] == 111.0
    assert list(five.index) == ["ts", "open", "high", "low", "close", "volume", "oi"]
    assert source == [
        ("yahoo", "NSE:RELIANCE", "5m", ist(2026, 9, 23, 9, 15), True)
    ]  # cached after one fetch


def test_closed_market_returns_none(source, monkeypatch):
    monkeypatch.setattr(clock, "utc_now", lambda: ist(2026, 9, 26, 11, 0))  # Saturday
    assert live.get_forming("NSE:RELIANCE", "5m") is None
    assert source == []


def test_no_forming_bar_when_data_lags(source, monkeypatch):
    monkeypatch.setattr(clock, "utc_now", lambda: ist(2026, 9, 23, 10, 12))  # 10:10 bar missing
    assert live.get_forming("NSE:RELIANCE", "5m") is None


def test_source_failure_returns_none(source, monkeypatch):
    def broken(*args, **kwargs):
        raise SourceError("down")

    monkeypatch.setattr(live, "fetch_candles", broken)
    assert live.get_forming("NSE:RELIANCE", "15m") is None
    assert live.get_forming("MCX:GOLD", "5m") is None
