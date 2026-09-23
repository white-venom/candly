import json
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST, get_calendar
from candly.data import clock, quality
from candly.data.store import save_candles

cal = get_calendar()
NOW = pd.Timestamp(datetime(2026, 9, 23, 12, 2), tz=IST).tz_convert("UTC")  # NSE session in progress


def frame(opens: list[pd.Timestamp], prices: list[tuple[float, float]] | None = None) -> pd.DataFrame:
    prices = prices or [(100.0, 101.0)] * len(opens)
    o = np.array([p[0] for p in prices])
    c = np.array([p[1] for p in prices])
    return pd.DataFrame(
        {
            "ts": pd.DatetimeIndex(opens),
            "open": o,
            "high": np.maximum(o, c) + 1,
            "low": np.minimum(o, c) - 1,
            "close": c,
            "volume": np.full(len(opens), 10.0),
            "oi": np.nan,
        }
    )


def day_open(d: date) -> pd.Timestamp:
    return cal.session_times("NSE", d)[0]


def hours(d: date, n: int | None = None) -> list[pd.Timestamp]:
    return cal.expected_bar_opens("NSE", d, "1h")[:n]


def september(*days: int) -> set[date]:
    return {date(2026, 9, d) for d in days}


@pytest.fixture(autouse=True)
def frozen(monkeypatch, tmp_data_dir):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)


SPIKE = [(290.0, 291.0), (292.0, 291.03), (146.67, 143.87), (299.31, 300.0), (301.0, 302.0), (303.0, 304.0)]
SPIKE_DAYS = [date(2026, 9, d) for d in (15, 16, 17, 18, 21, 22)]


def test_suspicious_bars_flags_a_half_price_bar_only():
    df = frame([day_open(d) for d in SPIKE_DAYS], SPIKE)
    assert quality.suspicious_bars(df, "equity").tolist() == [False, False, True, False, False, False]

    crash = frame(
        [day_open(d) for d in SPIKE_DAYS],
        [(290.0, 291.0), (210.0, 205.0), (215.0, 214.0), (180.0, 170.0), (200.0, 201.0), (202.0, 203.0)],
    )
    assert not quality.suspicious_bars(crash, "equity").any()  # a -28% gap that doesn't reverse is real


def test_index_limits_are_tighter():
    prices = [(100.0, 100.0), (116.0, 117.0), (103.0, 102.0), (102.0, 102.0)]  # +16% then -12%
    df = frame([day_open(d) for d in SPIKE_DAYS[:4]], prices)
    assert quality.suspicious_bars(df, "index").tolist() == [False, True, False, False]
    assert not quality.suspicious_bars(df, "equity").any()


def test_missing_daily_sessions_ignores_the_session_in_progress():
    save_candles("NSE:RELIANCE", "1D", frame([day_open(date(2026, 9, 21))]))
    one_hour = hours(date(2026, 9, 21)) + hours(date(2026, 9, 22)) + hours(date(2026, 9, 23), 2)
    save_candles("NSE:RELIANCE", "1h", frame(one_hour))
    save_candles("NSE:RELIANCE", "5m", frame([day_open(date(2026, 9, 18))]))
    assert quality.missing_daily_sessions("NSE:RELIANCE") == [date(2026, 9, 18), date(2026, 9, 22)]
    assert quality.missing_daily_sessions("NSE:TCS") == []


def test_bar_count_anomalies():
    own = pd.Series(
        hours(date(2026, 1, 30))
        + hours(date(2026, 9, 17))
        + [day_open(date(2026, 9, 20))]  # Sunday
        + hours(date(2026, 9, 21))
        + hours(date(2026, 9, 22), 6)  # 15:15 bar missing
        + hours(date(2026, 9, 23), 2)  # session still running
    )
    peers = [september(17, 18, 19, 21, 22, 23), september(17, 18, 21, 22), september(17, 18, 21, 22)]
    found = quality.bar_count_anomalies(own, "1h", "NSE", peers)
    assert found["partial"] == [(date(2026, 9, 22), 7, 6)]
    assert found["unexpected"] == [date(2026, 9, 20)]
    # 18 Sep: all peers have it; 19 Sep: only one of three; 1 Feb: the Budget special session.
    assert found["missing"] == [date(2026, 2, 1), date(2026, 9, 18)]
    assert quality.bar_count_anomalies(pd.Series([], dtype="datetime64[ns, UTC]"), "1h", "NSE") == {
        "partial": [],
        "missing": [],
        "unexpected": [],
    }


def test_report_cli(tmp_data_dir, monkeypatch, capsys):
    monkeypatch.setattr(quality, "setup_logging", lambda: None)
    save_candles("NSE:LT", "1D", frame([day_open(d) for d in SPIKE_DAYS], SPIKE), source="yahoo")
    lt_hours = hours(date(2026, 9, 21)) + hours(date(2026, 9, 22), 6)
    save_candles("NSE:LT", "1h", frame(lt_hours), source="yahoo")
    save_candles("NSE:TCS", "1D", frame([day_open(d) for d in SPIKE_DAYS if d != date(2026, 9, 18)]))
    save_candles("NSE:TCS", "1h", frame(hours(date(2026, 9, 18))))

    assert quality.main(["--report"]) == 0
    report = json.loads((tmp_data_dir / "quality" / "report.json").read_text(encoding="utf-8"))
    summary = report["summary"]
    assert summary["instruments_with_data"] == 2 and len(summary["instruments_without_data"]) == 16
    assert summary["suspicious_bars"] == 1 and summary["missing_daily_sessions"] == 1

    lt = report["instruments"]["NSE:LT"]
    assert lt["source"] == {"1h": "yahoo", "1D": "yahoo"} and lt["bars"] == {"1h": 13, "1D": 6}
    assert lt["suspicious_bars"] == [
        {
            "tf": "1D",
            "date": "2026-09-17",
            "time": clock.epoch_seconds(day_open(date(2026, 9, 17))),
            "prev_close": 291.03,
            "open": 146.67,
            "close": 143.87,
            "next_open": 299.31,
            "gap_pct": -49.6,
            "back_pct": 108.04,
        }
    ]
    assert lt["bar_counts"]["1h"]["partial_sessions"] == {
        "count": 1,
        "missing_bars": 1,
        "recent": [{"date": "2026-09-22", "expected": 7, "present": 6}],
    }
    tcs = report["instruments"]["NSE:TCS"]
    assert tcs["missing_daily_sessions"] == {"count": 1, "recent": ["2026-09-18"]}
    assert tcs["bar_counts"]["1D"]["missing_sessions"] == {"count": 1, "recent": ["2026-09-18"]}
    assert "suspicious: NSE:LT 1D 2026-09-17 gap -49.6% then +108.0%" in capsys.readouterr().out


def test_cli_without_report_flag_prints_help(capsys):
    assert quality.main([]) == 2
    assert "--report" in capsys.readouterr().out
