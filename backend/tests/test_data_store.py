import logging
import os
import threading
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
import yaml

from candly.core.calendar import IST, MarketCalendar, get_calendar
from candly.core.schema import CANDLE_COLUMNS, CandleSchemaError, validate_candles
from candly.core.settings import get_settings
from candly.data import clean, clock, store
from candly.data.clean import clean_candles, missing_bars
from candly.data.store import (
    candle_path,
    candle_source,
    clean_existing,
    data_summary,
    load_candles,
    save_candles,
    series_lock,
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
    assert files == ["RELIANCE.parquet", "RELIANCE.parquet.lock"]


def test_concurrent_writers_do_not_lose_rows(frozen, monkeypatch):
    real_read = store._read

    def slow_read(path):
        result = real_read(path)
        threading.Event().wait(0.3)  # widen the read-modify-write window so unlocked writers would collide
        return result

    monkeypatch.setattr(store, "_read", slow_read)
    errors = []

    def writer(day: date) -> None:
        try:
            save_candles("NSE:RELIANCE", "1D", daily([day]))
        except Exception as exc:  # surfaced below; a thread's exception would otherwise vanish
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(date(2026, 9, d),)) for d in (21, 22)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    stored = load_candles("NSE:RELIANCE", "1D")["ts"]
    assert list(stored) == [ist(2026, 9, 21, 9, 15), ist(2026, 9, 22, 9, 15)]


def test_series_lock_times_out_while_another_writer_holds_it(frozen):
    path = candle_path("NSE:RELIANCE", "1D")
    outcome = []

    def contender() -> None:
        try:
            with series_lock(path, timeout=0.2):
                outcome.append("locked")
        except TimeoutError:
            outcome.append("timeout")

    with series_lock(path):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join()
    assert outcome == ["timeout"]
    with series_lock(path, timeout=0.2):  # released again
        pass


def test_corrupt_series_is_quarantined_and_reads_as_empty(frozen, caplog):
    save_candles("NSE:RELIANCE", "1D", daily([date(2026, 9, 21), date(2026, 9, 22)]), source="yahoo")
    path = candle_path("NSE:RELIANCE", "1D")
    path.write_bytes(b"PAR1 truncated garbage")
    with caplog.at_level(logging.ERROR, logger="candly.data.store"):
        assert series_stats("NSE:RELIANCE", "1D") == {"bars": 0, "first": None, "last": None}
    assert not path.exists()
    quarantined = list((frozen / "quarantine").iterdir())
    assert [p.name for p in quarantined] == ["NSE_1D_RELIANCE.20260923T063200Z.parquet"]
    assert quarantined[0].read_bytes() == b"PAR1 truncated garbage"
    assert "corrupt" in caplog.text
    assert load_candles("NSE:RELIANCE", "1D").empty and candle_source("NSE:RELIANCE", "1D") is None

    path.write_bytes(b"garbage again")  # a writer quarantines too, then stores the fresh rows
    assert save_candles("NSE:RELIANCE", "1D", daily([date(2026, 9, 22)])) == 1
    assert len(load_candles("NSE:RELIANCE", "1D")) == 1


def test_replace_retries_while_windows_holds_the_file(frozen, monkeypatch):
    real_replace = os.replace
    attempts, sleeps = [], []

    def flaky_replace(src, dst):
        attempts.append(dst)
        if len(attempts) < 3:
            raise PermissionError("file in use by a virus scanner")
        real_replace(src, dst)

    monkeypatch.setattr(store.os, "replace", flaky_replace)
    monkeypatch.setattr(store, "_sleep", sleeps.append)
    assert save_candles("NSE:RELIANCE", "1D", daily([date(2026, 9, 21)])) == 1
    assert len(attempts) == 3 and sleeps == [0.2, 0.4]
    assert len(load_candles("NSE:RELIANCE", "1D")) == 1


def test_replace_gives_up_and_cleans_its_temp_file(frozen, monkeypatch):
    def locked(src, dst):
        raise PermissionError("still locked")

    monkeypatch.setattr(store.os, "replace", locked)
    monkeypatch.setattr(store, "_sleep", lambda seconds: None)
    with pytest.raises(PermissionError):
        save_candles("NSE:RELIANCE", "1D", daily([date(2026, 9, 21)]))
    assert [p.name for p in candle_path("NSE:RELIANCE", "1D").parent.iterdir()] == ["RELIANCE.parquet.lock"]


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


def bars_on(days: list[date], exchange: str, rows: list[tuple]) -> pd.DataFrame:
    """Daily bars with explicit (open, high, low, close, volume) per day."""
    o, h, lo, c, v = (list(col) for col in zip(*rows, strict=True))
    return pd.DataFrame(
        {
            "ts": pd.DatetimeIndex([cal.session_times(exchange, d)[0] for d in days]),
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "volume": v,
            "oi": np.nan,
        }
    )


REAL = (100.0, 102.0, 99.0, 101.0, 5000.0)
UNTRADED = (100.0, 102.0, 99.0, 101.0, 0.0)  # e.g. a Yahoo index bar: indices always have zero volume
FLAT_ZERO = (101.0, 101.0, 101.0, 101.0, 0.0)
HOLIDAY = date(2026, 9, 14)  # Ganesh Chaturthi, closed on NSE, BSE and MCX


def dates(df: pd.DataFrame) -> list[date]:
    return [ts.tz_convert(IST).date() for ts in df["ts"]]


def test_equity_drops_flat_zero_volume_bars_on_holidays_and_normal_days():
    days = [date(2026, 9, 11), HOLIDAY, date(2026, 9, 15), date(2026, 9, 16)]
    df = bars_on(days, "NSE", [REAL, FLAT_ZERO, FLAT_ZERO, REAL])
    assert dates(clean_candles(df, "1D", "NSE", kind="equity")) == [date(2026, 9, 11), date(2026, 9, 16)]
    assert len(clean_candles(df, "1D", "NSE")) == 4  # no kind (already-cleaned input): untouched


def test_equity_bar_on_a_holiday_without_trading_is_dropped_even_if_not_flat():
    df = bars_on([date(2026, 9, 11), HOLIDAY], "NSE", [REAL, UNTRADED])
    assert len(clean_candles(df, "1D", "NSE", kind="equity")) == 1


def test_index_keeps_flat_bars_except_on_holidays():
    days = [date(2026, 9, 11), HOLIDAY, date(2026, 9, 15), date(2026, 9, 16)]
    df = bars_on(days, "NSE", [UNTRADED, FLAT_ZERO, FLAT_ZERO, UNTRADED])
    out = clean_candles(df, "1D", "NSE", kind="index")
    assert dates(out) == [date(2026, 9, 11), date(2026, 9, 15), date(2026, 9, 16)]


def test_intraday_holiday_bars_are_dropped():
    opens = cal.session_times("NSE", HOLIDAY)[0] + pd.to_timedelta([0, 1, 2], unit="h")
    df = pd.DataFrame(
        {"ts": opens, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 0.0, "oi": np.nan}
    )
    assert clean_candles(df, "1h", "NSE", kind="equity").empty


@pytest.fixture
def nse_only_holiday(monkeypatch):
    """The real calendar plus an NSE-only holiday on 22 Sep 2026 and a special NSE session on 21 Sep."""
    raw = yaml.safe_load((get_settings().config_dir / "markets.yaml").read_text(encoding="utf-8"))
    raw["holidays"] += [
        {"date": date(2026, 9, 22), "name": "test", "exchanges": ["NSE"]},
        {"date": date(2026, 9, 21), "name": "test", "exchanges": ["NSE"]},
    ]
    special = {"date": date(2026, 9, 21), "exchanges": ["NSE"], "open": "09:15", "close": "15:30"}
    raw["special_sessions"] += [special]
    monkeypatch.setattr(clean, "get_calendar", lambda: MarketCalendar(raw))


def test_mcx_bars_are_never_dropped_for_an_nse_holiday(nse_only_holiday):
    days = [date(2026, 9, 21), date(2026, 9, 22)]
    assert len(clean_candles(bars_on(days, "MCX", [REAL, UNTRADED]), "1D", "MCX", kind="future")) == 2
    out = clean_candles(bars_on(days, "NSE", [UNTRADED, UNTRADED]), "1D", "NSE", kind="equity")
    assert dates(out) == [date(2026, 9, 21)]  # the special session is kept, the NSE holiday is not


def test_mcx_holiday_with_real_trading_keeps_its_bars():
    # An MCX evening session can run on a day markets.yaml lists as an MCX holiday; traded volume proves it.
    df = bars_on([date(2026, 9, 11), HOLIDAY], "MCX", [REAL, REAL])
    assert len(clean_candles(df, "1D", "MCX", kind="future")) == 2


def test_clean_existing_rewrites_stored_series(frozen):
    days = [date(2026, 9, 11), HOLIDAY, date(2026, 9, 15)]
    save_candles("NSE:RELIANCE", "1D", bars_on(days, "NSE", [REAL, FLAT_ZERO, REAL]), source="yahoo")
    save_candles("NSE:NIFTY50", "1D", bars_on(days, "NSE", [UNTRADED] * 3), source="yahoo")
    save_candles("MCX:GOLD", "1D", bars_on(days, "MCX", [REAL, FLAT_ZERO, REAL]))
    assert clean_existing("1D") == {"NSE:NIFTY50": 0, "NSE:RELIANCE": 1}
    assert len(load_candles("NSE:RELIANCE", "1D")) == 2 and candle_source("NSE:RELIANCE", "1D") == "yahoo"
    assert len(load_candles("MCX:GOLD", "1D")) == 3  # other exchanges untouched
    mtime = candle_path("NSE:RELIANCE", "1D").stat().st_mtime_ns
    assert clean_existing("1D")["NSE:RELIANCE"] == 0
    assert candle_path("NSE:RELIANCE", "1D").stat().st_mtime_ns == mtime  # nothing to do: no rewrite


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
