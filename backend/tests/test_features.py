import copy
import json
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST, get_calendar, reload_calendar
from candly.core.expiry import expiry_info
from candly.features.context import CONTEXT_COLUMNS, compute_context, session_phases
from candly.features.expiry import expiry_with_source
from candly.features.levels import LEVEL_KINDS, key_levels, level_frame, levels_as_of, swing_points
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


def test_intraday_vol_regime_ranks_within_the_time_of_day():
    df = synthetic_candles("15m", "2024-01-01", "2024-03-29", seed=8, vol=0.003)
    local = df["ts"].dt.tz_convert(IST)
    opening = ((local.dt.hour == 9) & (local.dt.minute == 15)).to_numpy()
    spread = (df["high"] - df["low"]).to_numpy()
    wide = df.copy()
    wide.loc[opening, "high"] += 4.0 * spread[opening]
    wide.loc[opening, "low"] -= 4.0 * spread[opening]
    regime = compute_context(wide, "15m", "NSE", small_vol_window_config())["vol_regime"]
    labelled = regime.notna().to_numpy()
    high_at_open = (regime[opening & labelled] == "high").mean()
    high_later = (regime[~opening & labelled] == "high").mean()
    # ranking against every recent bar put ~70% of opening bars in "high" on this data
    assert 0.2 < high_at_open < 0.5 and 0.2 < high_later < 0.5
    assert abs(high_at_open - high_later) < 0.1


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


def test_levels_as_of_names_the_source_session():
    daily = synthetic_candles("1D", "2024-01-01", "2024-03-01", seed=4)
    assert levels_as_of(daily, "1D", "NSE") == daily["ts"].iloc[-1]
    intraday = synthetic_candles("1h", "2024-01-01", "2024-01-03", seed=5)
    day = intraday["ts"].dt.tz_convert(IST).dt.date
    last_day, prev_day = sorted(day.unique())[-1], sorted(day.unique())[-2]
    mid = intraday[(day < last_day) | (intraday["ts"] < intraday["ts"][day == last_day].iloc[2])]
    assert levels_as_of(mid, "1h", "NSE") == intraday["ts"][day == prev_day].iloc[0]
    assert levels_as_of(intraday, "1h", "NSE") == intraday["ts"][day == last_day].iloc[0]
    first_hours = intraday[day == sorted(day.unique())[0]].iloc[:3]
    assert levels_as_of(first_hours, "1h", "NSE") == first_hours["ts"].iloc[-1]
    assert key_levels(first_hours, "1h", "NSE") and not any(
        lv.kind in {"pdh", "pdl", "pdc"} for lv in key_levels(first_hours, "1h", "NSE")
    )


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


def test_expiry_columns_match_the_expiry_function():
    df = synthetic_candles("1D", "2026-08-20", "2026-10-10", seed=11)
    ctx = compute_context(df, "1D", "NSE", instrument_id="NSE:NIFTY50")
    days = df["ts"].dt.tz_convert(IST).dt.date
    for d, flag, left in zip(days, ctx["expiry_day"], ctx["days_to_expiry"], strict=True):
        info = expiry_info("NSE:NIFTY50", d)
        assert flag is info.is_expiry_day and left == info.days_to_expiry, d
    by_day = dict(zip(days, ctx["days_to_expiry"], strict=True))
    assert by_day[date(2026, 9, 22)] == 0 and by_day[date(2026, 9, 21)] == 1  # weekly Tuesday
    assert by_day[date(2026, 9, 23)] == 4  # to the monthly on Tue 29 Sep
    assert ctx.loc[days == date(2026, 9, 29), "expiry_day"].item() is True


def write_live_expiries(data_dir, instrument: str, expiries: dict[str, str], since: str, last_ok: str):
    """data/expiries/live.json as data.expiries.refresh_expiries() writes it, for one instrument."""
    path = data_dir / "expiries" / "live.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    listed = {d: {"kind": kind, "first_seen": since} for d, kind in expiries.items()}
    record = {"since": since, "last_ok": last_ok, "expiries": listed}
    path.write_text(json.dumps({"instruments": {instrument: record}, "check": {}}), encoding="utf-8")


def test_expiry_answers_follow_the_daily_exchange_refresh(tmp_data_dir):
    day = date(2026, 9, 23)
    df = synthetic_candles("1D", "2026-08-20", "2026-09-28", seed=15)
    ruled = compute_context(df, "1D", "NSE", instrument_id="NSE:NIFTY50")
    info, source = expiry_with_source("NSE:NIFTY50", day)
    assert (info.next_expiry, source) == (date(2026, 9, 29), "rules")

    # the exchange lists only a monthly expiry, a day before the rules' date; the memoised rule answers
    # must not survive the refresh
    write_live_expiries(tmp_data_dir, "NSE:NIFTY50", {"2026-09-28": "monthly"}, "2026-09-20", "2026-09-28")
    info, source = expiry_with_source("NSE:NIFTY50", day)
    assert (info.next_expiry, info.days_to_expiry, source) == (date(2026, 9, 28), 3, "exchange")
    moved = compute_context(df, "1D", "NSE", instrument_id="NSE:NIFTY50")
    days = df["ts"].dt.tz_convert(IST).dt.date
    assert_matches_lookups(moved, days)
    at = {d: (days == d).to_numpy() for d in (date(2026, 9, 22), date(2026, 9, 28))}
    assert moved.loc[at[date(2026, 9, 28)], "expiry_day"].item() is True
    assert ruled.loc[at[date(2026, 9, 28)], "expiry_day"].item() is False
    assert moved.loc[at[date(2026, 9, 22)], "expiry_day"].item() is False  # the rules' weekly
    assert ruled.loc[at[date(2026, 9, 22)], "expiry_day"].item() is True
    before = (days < date(2026, 9, 20)).to_numpy()  # before the first refresh the rules still answer
    pd.testing.assert_frame_equal(moved[before], ruled[before])

    write_live_expiries(
        tmp_data_dir,
        "NSE:NIFTY50",
        {"2026-09-29": "monthly", "2026-10-27": "monthly"},
        "2026-09-20",
        "2026-09-24",
    )
    assert expiry_with_source("NSE:NIFTY50", day)[0].next_expiry == date(2026, 9, 29)

    # refreshes stopped on 1 Sep: three days on, the rules take over again mid-way to the listed expiry
    write_live_expiries(tmp_data_dir, "NSE:NIFTY50", {"2026-09-29": "monthly"}, "2026-08-24", "2026-09-01")
    assert expiry_with_source("NSE:NIFTY50", date(2026, 9, 4))[1] == "exchange"
    assert expiry_with_source("NSE:NIFTY50", date(2026, 9, 7))[1] == "rules"
    assert_matches_lookups(compute_context(df, "1D", "NSE", instrument_id="NSE:NIFTY50"), days)


def test_reloading_the_calendar_forgets_memoised_expiry_answers(tmp_data_dir):
    day = date(2024, 4, 8)
    reload_calendar()
    assert expiry_with_source("NSE:NIFTY50", day)[0].next_expiry == date(2024, 4, 11)
    path = tmp_data_dir / "derived" / "holidays_observed.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"NSE": ["2024-04-11"]}), encoding="utf-8")  # Id-ul-Fitr
    try:
        reload_calendar()
        assert expiry_with_source("NSE:NIFTY50", day)[0].next_expiry == date(2024, 4, 10)
    finally:
        path.unlink()
        reload_calendar()


def assert_matches_lookups(ctx: pd.DataFrame, days: pd.Series) -> None:
    for d, flag, left in zip(days, ctx["expiry_day"], ctx["days_to_expiry"], strict=True):
        expected = expiry_with_source("NSE:NIFTY50", d)[0]
        assert flag is expected.is_expiry_day and left == expected.days_to_expiry, d


def test_expiry_columns_are_empty_without_an_expiry():
    df = synthetic_candles("1D", "2026-08-20", "2026-09-30", seed=12)
    vix = compute_context(df, "1D", "NSE", instrument_id="NSE:INDIAVIX")
    anonymous = compute_context(df, "1D", "NSE")
    for ctx in (vix, anonymous):
        assert ctx["expiry_day"].isna().all() and ctx["days_to_expiry"].isna().all()
    stock = compute_context(df, "1D", "NSE", instrument_id="NSE:TCS")
    assert stock["expiry_day"].sum() == 2  # monthly only: 25 Aug and 29 Sep 2026
    assert stock["expiry_day"].notna().all()


def test_index_relative_volume_is_left_out():
    df = synthetic_candles("1D", "2024-01-01", "2024-06-30", seed=13)
    stock = compute_context(df, "1D", "NSE", instrument_id="NSE:RELIANCE")
    index = compute_context(df, "1D", "NSE", instrument_id="NSE:NIFTY50")
    assert stock["rel_volume"].notna().any()
    assert index["rel_volume"].isna().all()
    pd.testing.assert_frame_equal(stock.drop(columns=EXCLUDED), index.drop(columns=EXCLUDED))


EXCLUDED = ["rel_volume", "expiry_day", "days_to_expiry"]


@pytest.mark.parametrize(("tf", "instrument"), [("1D", "NSE:NIFTY50"), ("15m", "NSE:BANKNIFTY")])
def test_expiry_context_is_causal(tf, instrument):
    start, end = ("2023-06-01", "2025-06-30") if tf == "1D" else ("2024-10-01", "2024-12-31")
    df = synthetic_candles(tf, start, end, seed=14, vol=0.004)
    cfg = small_vol_window_config() if tf != "1D" else load_pattern_config()
    n = len(df)
    ctx = compute_context(df, tf, "NSE", cfg, instrument_id=instrument)
    assert ctx["expiry_day"].eq(True).any() and ctx["days_to_expiry"].notna().all()
    check_causal(
        lambda d: compute_context(d, tf, "NSE", cfg, instrument_id=instrument), df, [n // 3, n // 2, n - 30]
    )


def test_key_levels_ignore_future_bars():
    df = synthetic_candles("1h", "2024-01-01", "2024-01-10", seed=7)
    cut = len(df) - 10
    ts = df["ts"].iloc[cut]
    assert ts < pd.Timestamp(datetime(2024, 1, 11), tz=IST)
    first = key_levels(df.iloc[: cut + 1], "1h", "NSE")
    altered = df.copy()
    altered.loc[altered.index[cut + 1 :], ["open", "high", "low", "close"]] *= 1.5
    assert key_levels(altered.iloc[: cut + 1], "1h", "NSE") == first
