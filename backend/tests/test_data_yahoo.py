from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST
from candly.core.instruments import get_instrument
from candly.data import clock
from candly.data.sources import SourceUnavailable, yahoo

NOW = pd.Timestamp(datetime(2026, 9, 23, 12, 2), tz=IST).tz_convert("UTC")


def ist(y, m, d, hh, mm) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm), tz=IST).tz_convert("UTC")


def yahoo_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    n = len(index)
    base = np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "Open": 100 + base,
            "High": 102 + base,
            "Low": 99 + base,
            "Close": 101 + base,
            "Adj Close": 90 + base,
            "Volume": np.full(n, 1000.0),
        },
        index=index,
    )


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)


def test_daily_rows_move_to_session_open_and_forming_day_is_dropped():
    index = pd.DatetimeIndex(["2026-09-21", "2026-09-22", "2026-09-23"]).tz_localize(IST)
    out = yahoo.to_candles(yahoo_frame(index), "NSE", "1D", NOW)
    assert list(out["ts"]) == [ist(2026, 9, 21, 9, 15), ist(2026, 9, 22, 9, 15)]
    assert out["close"].tolist() == [101.0, 102.0]  # raw close, not "Adj Close"
    assert out["oi"].isna().all()


def test_intraday_drops_out_of_session_and_forming_bars():
    index = pd.DatetimeIndex(
        [ist(2026, 9, 23, 11, 50), ist(2026, 9, 23, 11, 55), ist(2026, 9, 23, 12, 0), ist(2026, 9, 23, 8, 0)]
    ).tz_convert(IST)
    raw = yahoo_frame(index)
    closed = yahoo.to_candles(raw, "NSE", "5m", NOW)
    assert list(closed["ts"]) == [ist(2026, 9, 23, 11, 50), ist(2026, 9, 23, 11, 55)]
    with_forming = yahoo.to_candles(raw, "NSE", "5m", NOW, include_forming=True)
    assert with_forming["ts"].iloc[-1] == ist(2026, 9, 23, 12, 0)


def test_empty_download():
    assert yahoo.to_candles(pd.DataFrame(), "NSE", "1D", NOW).empty


def test_mcx_is_not_on_yahoo():
    with pytest.raises(SourceUnavailable, match="not available on yahoo"):
        yahoo.fetch_candles(get_instrument("MCX:CRUDEOIL"), "1D")


def test_interval_mapping_and_lookback_clamp(monkeypatch):
    calls = []

    def fake_download(symbol, interval, start, end):
        calls.append((symbol, interval, start))
        return pd.DataFrame()

    monkeypatch.setattr(yahoo, "_download", fake_download)
    reliance = get_instrument("NSE:RELIANCE")
    yahoo.fetch_candles(reliance, "5m", start=ist(2020, 1, 1, 9, 15))
    yahoo.fetch_candles(reliance, "1h")
    yahoo.fetch_candles(reliance, "1D")
    assert calls[0] == ("RELIANCE.NS", "5m", NOW - yahoo.LOOKBACK["5m"])
    assert calls[1] == ("RELIANCE.NS", "60m", NOW - yahoo.LOOKBACK["1h"])
    assert calls[2] == ("RELIANCE.NS", "1d", None)
