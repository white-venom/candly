from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST, get_calendar
from candly.core.schema import CANDLE_COLUMNS, CandleSchemaError, validate_candles
from candly.data import clock
from candly.data.clean import clean_candles, missing_bars
from candly.data.store import (
    candle_path,
    candle_source,
    data_summary,
    load_candles,
    save_candles,
    series_stats,
)

cal = get_calendar()
NOW = pd.Timestamp(datetime(2026, 9, 23, 12, 2), tz=IST).tz_convert("UTC")


def ist(y, m, d, hh, mm) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm), tz=IST).tz_convert("UTC")


def daily(days: list[date], exchange: str = "NSE", close: float = 101.0) -> pd.DataFrame:
    n = len(days)
    return pd.DataFrame(
        {
            "ts": pd.DatetimeIndex([cal.session_times(exchange, d)[0] for d in days]),
            "open": np.full(n, 100.0),
            "high": np.full(n, 102.0),
            "low": np.full(n, 99.0),
            "close": np.full(n, close),
            "volume": np.full(n, 1000.0),
            "oi": np.nan,
        }
    )


@pytest.fixture
def frozen(monkeypatch, tmp_data_dir, no_keys):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    return tmp_data_dir


def test_path_layout(frozen):
    assert candle_path("NSE:RELIANCE", "1D") == frozen / "candles" / "NSE" / "1D" / "RELIANCE.parquet"
    with pytest.raises(ValueError):
        candle_path("XYZ:FOO", "1D")


def test_roundtrip_and_idempotent_upsert(frozen):
    df = daily([date(2026, 9, 21), date(2026, 9, 22)])
    assert save_candles("NSE:RELIANCE", "1D", df, source="yahoo") == 2
    assert save_candles("NSE:RELIANCE", "1D", df, source="yahoo") == 0
    out = load_candles("NSE:RELIANCE", "1D")
    assert list(out.columns) == CANDLE_COLUMNS
    assert str(out["ts"].dtype) == "datetime64[ns, UTC]"
    pd.testing.assert_frame_equal(out, validate_candles(df))
    assert candle_source("NSE:RELIANCE", "1D") == "yahoo"


def test_incoming_rows_win_and_count_changes(frozen):
    save_candles("NSE:RELIANCE", "1D", daily([date(2026, 9, 21), date(2026, 9, 22)]))
    update = daily([date(2026, 9, 22)], close=101.5)
    assert save_candles("NSE:RELIANCE", "1D", update, source="fyers") == 1
    out = load_candles("NSE:RELIANCE", "1D")
    assert len(out) == 2 and out["close"].iloc[-1] == 101.5
    assert candle_source("NSE:RELIANCE", "1D") == "fyers"


def test_forming_bars_are_never_stored(frozen):
    df = daily([date(2026, 9, 22), date(2026, 9, 23)])  # 23 Sep closes at 15:30 IST, after NOW
    assert save_candles("NSE:RELIANCE", "1D", df) == 1
    assert len(load_candles("NSE:RELIANCE", "1D")) == 1


def test_atomic_write_leaves_no_temp_files(frozen):
    save_candles("NSE:RELIANCE", "1D", daily([date(2026, 9, 21)]))
    save_candles("NSE:RELIANCE", "1D", daily([date(2026, 9, 22)]))
    files = sorted(p.name for p in candle_path("NSE:RELIANCE", "1D").parent.iterdir())
    assert files == ["RELIANCE.parquet"]


def test_invalid_frames_are_rejected(frozen):
    bad = daily([date(2026, 9, 21)]).assign(high=90.0)
    with pytest.raises(CandleSchemaError):
        save_candles("NSE:RELIANCE", "1D", bad)
    assert not candle_path("NSE:RELIANCE", "1D").exists()


def test_start_end_filters_and_naive_rejected(frozen):
    save_candles("NSE:RELIANCE", "1D", daily([date(2026, 9, d) for d in (15, 16, 17, 18, 21, 22)]))
    out = load_candles("NSE:RELIANCE", "1D", start=ist(2026, 9, 16, 0, 0), end=ist(2026, 9, 18, 23, 0))
    assert len(out) == 3
    with pytest.raises(ValueError):
        load_candles("NSE:RELIANCE", "1D", start=pd.Timestamp("2026-09-16"))


def test_empty_series(frozen):
    assert load_candles("NSE:TCS", "5m").empty
    assert series_stats("NSE:TCS", "5m") == {"bars": 0, "first": None, "last": None}
    assert candle_source("NSE:TCS", "5m") is None


def test_data_summary_shape(frozen):
    save_candles("NSE:RELIANCE", "1D", daily([date(2026, 9, 21), date(2026, 9, 22)]))
    summary = data_summary()
    assert set(summary["NSE:RELIANCE"]) == {"5m", "15m", "1h", "1D"}
    stats = summary["NSE:RELIANCE"]["1D"]
    assert stats["bars"] == 2
    assert stats["first"] == ist(2026, 9, 21, 9, 15) and stats["last"] == ist(2026, 9, 22, 9, 15)
    assert summary["MCX:GOLD"]["5m"] == {"bars": 0, "first": None, "last": None}


def test_clean_filters_session_dedupes_and_repairs():
    opens = cal.expected_bar_opens("NSE", date(2026, 9, 22), "5m")[:3]
    ts = [ist(2026, 9, 22, 9, 10), *opens, opens[1], ist(2026, 9, 22, 9, 17)]
    df = pd.DataFrame(
        {
            "ts": pd.DatetimeIndex(ts),
            "open": [1.0, 10.0, 11.0, 12.0, 11.5, 13.0],
            "high": [1.0, 10.5, 11.5, 11.0, 12.0, 13.0],
            "low": [1.0, 9.5, 10.5, 11.5, 11.0, 13.0],
            "close": [1.0, 10.2, 11.2, 12.2, 11.8, 13.0],
            "volume": [1.0, 1.0, np.nan, 1.0, 2.0, 1.0],
            "oi": np.nan,
        }
    )
    out = clean_candles(df, "5m", "NSE")
    assert list(out["ts"]) == opens
    assert out["open"].iloc[1] == 11.5  # duplicate: last row wins
    assert out["volume"].iloc[1] == 2.0
    assert out["high"].iloc[2] == 12.2 and out["low"].iloc[2] == 11.5  # repaired to cover the body


def test_missing_bar_report():
    days = [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)]
    opens = [ts for d in days for ts in cal.expected_bar_opens("NSE", d, "15m")]
    opens.remove(ist(2026, 9, 22, 10, 0))
    df = pd.DataFrame({"ts": pd.DatetimeIndex(opens)})
    report = missing_bars(df, "15m", "NSE")
    assert report.to_dict("records") == [
        {"date": date(2026, 9, 22), "expected": 25, "present": 24, "missing": 1}
    ]
    report = missing_bars(df, "15m", "NSE", start=date(2026, 9, 18), end=date(2026, 9, 23))
    assert list(report["date"]) == [date(2026, 9, 18), date(2026, 9, 22)]
