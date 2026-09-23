import copy
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST, get_calendar
from candly.features.context import CONTEXT_COLUMNS, bars_per_session, compute_context, session_phases
from candly.features.levels import LEVEL_KINDS, key_levels, level_frame, swing_points
from candly.patterns import load_pattern_config
from candly.research.causality import check_causal
from candly.research.synthetic import bar_times, synthetic_candles


def small_vol_window_config(days: int = 10) -> dict:
    cfg = copy.deepcopy(load_pattern_config())
    cfg["context"]["vol_window_days"] = days
    return cfg


def frame_from_closes(closes, ts=None, spread=0.5) -> pd.DataFrame:
    closes = np.asarray(closes, float)
    opens = np.r_[closes[0], closes[:-1]]
    ts = ts if ts is not None else pd.date_range("2023-01-02 03:45", periods=len(closes), freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": opens,
            "high": np.maximum(opens, closes) + spread,
            "low": np.minimum(opens, closes) - spread,
            "close": closes,
            "volume": 1000.0,
            "oi": np.nan,
        }
    )


def test_trend_up_down_sideways():
    up = compute_context(frame_from_closes(np.linspace(100, 200, 120)), "1D", "NSE")
    down = compute_context(frame_from_closes(np.linspace(200, 100, 120)), "1D", "NSE")
    flat = compute_context(frame_from_closes(100 + np.tile([0.0, 1.0], 60)), "1D", "NSE")
    assert up["trend"].iloc[:40].isna().all()
    assert up["trend"].iloc[-1] == "up"
    assert down["trend"].iloc[-1] == "down"
    assert flat["trend"].iloc[-1] == "sideways"
    assert list(up.columns) == CONTEXT_COLUMNS
    assert up["trend_strength"].iloc[-1] > 50


def test_vol_regime_from_trailing_atr_percentile():
    rng = np.random.default_rng(0)
    closes = 100 + np.cumsum(rng.normal(0, 0.1, 400))
    calm = frame_from_closes(closes, spread=0.2)
    wild = calm.copy()
    wild.loc[wild.index[-30:], "high"] += 5.0
    wild.loc[wild.index[-30:], "low"] -= 5.0
    ctx_calm = compute_context(calm, "1D", "NSE")
    ctx_wild = compute_context(wild, "1D", "NSE")
    assert ctx_calm["vol_regime"].iloc[:120].isna().all()
    assert ctx_wild["vol_regime"].iloc[-1] == "high"
    assert ctx_wild["vol_pct"].iloc[-1] == pytest.approx(1.0)
    assert set(ctx_calm["vol_regime"].dropna()) <= {"low", "normal", "high"}


def test_bars_per_session():
    assert bars_per_session("NSE", "1D") == 1
    assert bars_per_session("NSE", "5m") == 75
    assert bars_per_session("NSE", "1h") == 7


def test_session_phases_match_calendar():
    ts = pd.Series(bar_times("15m", "2026-09-21", "2026-09-23", "MCX"))
    cal = get_calendar()
    expected = [cal.session_phase("MCX", t) for t in ts]
    assert session_phases(ts, "MCX").tolist() == expected
    df = synthetic_candles("15m", "2026-09-21", "2026-09-22", seed=1)
    assert compute_context(df, "15m", "NSE")["session_phase"].iloc[0] == "open"
    assert (
        compute_context(synthetic_candles("1D", "2024-01-01", "2024-03-01"), "1D", "NSE")["session_phase"]
        .isna()
        .all()
    )


def test_daily_levels_use_previous_bar():
    df = synthetic_candles("1D", "2024-01-01", "2024-03-01", seed=2)
    lv = level_frame(df, "1D", "NSE")
    assert list(lv.columns) == LEVEL_KINDS
    h, lo, c = df["high"].iloc[9], df["low"].iloc[9], df["close"].iloc[9]
    p = (h + lo + c) / 3
    row = lv.iloc[10]
    assert (row["pdh"], row["pdl"], row["pdc"]) == (h, lo, c)
    assert row["pivot"] == pytest.approx(p)
    assert row["r1"] == pytest.approx(2 * p - lo)
    assert row["s2"] == pytest.approx(p - (h - lo))
    assert row["cpr_top"] >= row["cpr_bottom"]
    assert sorted([row["cpr_bottom"], row["cpr_top"]]) == pytest.approx(
        sorted([(h + lo) / 2, 2 * p - (h + lo) / 2])
    )
    assert lv["vwap"].isna().all()


def test_intraday_levels_use_previous_session():
    df = synthetic_candles("1h", "2024-01-01", "2024-01-03", seed=3)
    lv = level_frame(df, "1h", "NSE")
    day = df["ts"].dt.tz_convert(IST).dt.date
    first, second = sorted(day.unique())[:2]
    prev = df[day == first]
    today = lv[(day == second).to_numpy()]
    assert (today["pdh"] == prev["high"].max()).all()
    assert (today["pdl"] == prev["low"].min()).all()
    assert (today["pdc"] == prev["close"].iloc[-1]).all()
    assert lv[(day == first).to_numpy()]["pdh"].isna().all()


def test_swing_points_confirm_after_k_bars():
    highs = np.array([1, 2, 3, 9, 3, 2, 1, 1, 1, 1], float)
    df = pd.DataFrame({"high": highs, "low": highs - 1})
    swing_h, _ = swing_points(df, 3)
    assert swing_h.notna().sum() == 1
    assert swing_h.iloc[6] == 9.0


def test_near_level_finds_the_previous_high():
    df = frame_from_closes(np.r_[np.full(30, 100.0), 103.0, 103.1], spread=1.0)
    ctx = compute_context(df, "1D", "NSE")
    assert ctx["near_level"].iloc[-1] in {"pdh", "pdc"}
    far = frame_from_closes(np.r_[np.full(30, 100.0), 130.0], spread=0.1)
    assert compute_context(far, "1D", "NSE")["near_level"].iloc[-1] is None


def test_key_levels_daily_and_intraday():
    daily = synthetic_candles("1D", "2024-01-01", "2024-03-01", seed=4)
    levels = {lv.kind: lv.price for lv in key_levels(daily, "1D", "NSE")}
    assert levels["pdh"] == daily["high"].iloc[-1]
    assert "vwap" not in levels
    assert sum(lv.kind == "swing_high" for lv in key_levels(daily, "1D", "NSE")) <= 3

    intraday = synthetic_candles("1h", "2024-01-01", "2024-01-03", seed=5)
    day = intraday["ts"].dt.tz_convert(IST).dt.date
    last_day = day.iloc[-1]
    mid = intraday[(day < last_day) | (intraday["ts"] < intraday["ts"][day == last_day].iloc[2])]
    mid_levels = {lv.kind: lv.price for lv in key_levels(mid, "1h", "NSE")}
    prev_day = intraday[day == sorted(day.unique())[-2]]
    assert mid_levels["pdh"] == prev_day["high"].max()
    assert "vwap" in mid_levels
    done_levels = {lv.kind: lv.price for lv in key_levels(intraday, "1h", "NSE")}
    assert done_levels["pdh"] == intraday[day == last_day]["high"].max()


@pytest.mark.parametrize(
    "tf,start,end", [("1D", "2021-01-01", "2022-12-31"), ("15m", "2024-01-01", "2024-02-29")]
)
def test_context_and_levels_are_causal(tf, start, end):
    df = synthetic_candles(tf, start, end, seed=6, vol=0.004)
    cfg = small_vol_window_config() if tf != "1D" else load_pattern_config()
    n = len(df)
    cuts = [n // 3, n // 2, n - 30]
    ctx = compute_context(df, tf, "NSE", cfg)
    assert ctx["vol_regime"].notna().any() and ctx["near_level"].notna().any()
    check_causal(lambda d: compute_context(d, tf, "NSE", cfg), df, cuts)
    check_causal(lambda d: level_frame(d, tf, "NSE", cfg), df, cuts)


def test_key_levels_ignore_future_bars():
    df = synthetic_candles("1h", "2024-01-01", "2024-01-10", seed=7)
    cut = len(df) - 10
    ts = df["ts"].iloc[cut]
    assert ts < pd.Timestamp(datetime(2024, 1, 11), tz=IST)
    first = key_levels(df.iloc[: cut + 1], "1h", "NSE")
    altered = df.copy()
    altered.loc[altered.index[cut + 1 :], ["open", "high", "low", "close"]] *= 1.5
    assert key_levels(altered.iloc[: cut + 1], "1h", "NSE") == first
