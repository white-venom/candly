from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST, get_calendar
from candly.core.schema import CANDLE_COLUMNS
from candly.data.resample import resample_candles

cal = get_calendar()


def ist(y, m, d, hh, mm) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm), tz=IST).tz_convert("UTC")


def five_minute_day(exchange: str, d: date) -> pd.DataFrame:
    opens = cal.expected_bar_opens(exchange, d, "5m")
    n = len(opens)
    base = np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "ts": pd.DatetimeIndex(opens),
            "open": 100 + base,
            "high": 101 + base,
            "low": 99 + base,
            "close": 100.5 + base,
            "volume": np.full(n, 10.0),
            "oi": np.nan,
        }
    )


def test_nse_hourly_bars_anchor_to_open_and_last_is_short():
    bars = resample_candles(five_minute_day("NSE", date(2026, 9, 23)), "1h", "NSE")
    assert list(bars.columns) == CANDLE_COLUMNS
    assert list(bars["ts"]) == cal.expected_bar_opens("NSE", date(2026, 9, 23), "1h")
    assert bars["ts"].iloc[0] == ist(2026, 9, 23, 9, 15)
    assert bars["ts"].iloc[-1] == ist(2026, 9, 23, 15, 15)
    first, last = bars.iloc[0], bars.iloc[-1]
    assert (first["open"], first["high"], first["low"], first["close"]) == (100.0, 112.0, 99.0, 111.5)
    assert first["volume"] == 120.0
    assert last["volume"] == 30.0  # 15:15, 15:20, 15:25
    assert last["close"] == 100.5 + 74


def test_nse_fifteen_minute_bars():
    bars = resample_candles(five_minute_day("NSE", date(2026, 9, 23)), "15m", "NSE")
    assert len(bars) == 25
    assert (bars["volume"] == 30.0).all()
    assert bars["ts"].iloc[1] == ist(2026, 9, 23, 9, 30)


def test_mcx_hourly_bars_anchor_to_nine():
    bars = resample_candles(five_minute_day("MCX", date(2026, 9, 23)), "1h", "MCX")
    assert bars["ts"].iloc[0] == ist(2026, 9, 23, 9, 0)
    assert bars["ts"].iloc[-1] == ist(2026, 9, 23, 23, 0)
    assert bars["volume"].iloc[-1] == 60.0  # 23:00-23:30 during US daylight saving time


def test_missing_bars_and_multiple_days():
    df = pd.concat([five_minute_day("NSE", date(2026, 9, 22)), five_minute_day("NSE", date(2026, 9, 23))])
    df = df[df["ts"] != ist(2026, 9, 23, 9, 20)]
    bars = resample_candles(df, "15m", "NSE")
    assert len(bars) == 50
    assert bars.loc[bars["ts"] == ist(2026, 9, 23, 9, 15), "volume"].item() == 20.0


def test_out_of_session_bars_are_ignored():
    df = five_minute_day("NSE", date(2026, 9, 23))
    stray = df.iloc[[0]].assign(ts=ist(2026, 9, 23, 9, 5))
    bars = resample_candles(pd.concat([stray, df]), "1h", "NSE")
    assert bars["ts"].iloc[0] == ist(2026, 9, 23, 9, 15)
    assert bars["volume"].iloc[0] == 120.0


def test_daily_aggregate_and_identity():
    df = five_minute_day("NSE", date(2026, 9, 23))
    daily = resample_candles(df, "1D", "NSE")
    assert len(daily) == 1 and daily["ts"].iloc[0] == ist(2026, 9, 23, 9, 15)
    assert daily["volume"].iloc[0] == 750.0
    assert len(resample_candles(df, "5m", "NSE")) == 75


def test_empty_and_bad_tf():
    empty = five_minute_day("NSE", date(2026, 9, 23)).iloc[0:0]
    assert resample_candles(empty, "1h", "NSE").empty
    with pytest.raises(ValueError):
        resample_candles(empty, "2h", "NSE")
