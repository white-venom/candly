import json
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST
from candly.research import tsmom_data as td
from candly.research import tsmom_eval as te
from candly.research.causality import check_causal
from candly.research.edge_v2_config import edge_v2_section
from candly.research.synthetic import synthetic_candles

HOLDOUT = date(2025, 10, 1)


def _daily(start: str, n: int, exchange_open_utc: str = "03:45") -> pd.Series:
    days = pd.bdate_range(start, periods=n)
    return pd.Series(pd.to_datetime([f"{d.date()} {exchange_open_utc}" for d in days], utc=True))


def _frame(ts: pd.Series, close, open_=None, volume=None, oi=None) -> pd.DataFrame:
    close = np.asarray(close, float)
    open_ = close if open_ is None else np.asarray(open_, float)
    return pd.DataFrame(
        {
            "ts": ts,
            "open": open_,
            "high": np.maximum(open_, close) * 1.001,
            "low": np.minimum(open_, close) * 0.999,
            "close": close,
            "volume": np.full(len(ts), 1e5) if volume is None else np.asarray(volume, float),
            "oi": np.full(len(ts), np.nan) if oi is None else np.asarray(oi, float),
        }
    )


# --- the registration ------------------------------------------------------------------------------------


def test_registered_section_parses_to_the_implemented_values():
    spec = te.parse_spec(edge_v2_section("tsmom"), HOLDOUT)
    assert spec.universe == te.UNIVERSE
    assert (spec.indices_from, spec.mcx_from, spec.until) == (date(2008, 1, 1), date(2019, 1, 1), HOLDOUT)
    assert spec.sharpe_ci_lower_above == 0.0 and spec.beats_buy_and_hold is True
    assert spec.start_for("MCX:GOLD") == date(2019, 1, 1) and spec.start_for("BSE:SENSEX") == date(2008, 1, 1)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("signal", "sign of the close-to-close return from t-126 to t-21"),
        ("rebalance", "weekly"),
        ("extra", 1),
    ],
)
def test_a_changed_registration_fails_loudly(key, value):
    raw = {**edge_v2_section("tsmom"), key: value}
    with pytest.raises(ValueError):
        te.parse_spec(raw, HOLDOUT)


# --- returns ---------------------------------------------------------------------------------------------


def test_index_futures_proxy_subtracts_carry_per_calendar_day():
    ts = pd.Series(pd.to_datetime(["2024-01-05 03:45", "2024-01-08 03:45", "2024-01-09 03:45"], utc=True))
    r = td.index_futures_returns(_frame(ts, [100.0, 101.0, 101.0]))
    assert math.isnan(r.iloc[0])
    assert r.iloc[1] == pytest.approx(0.01 - 0.05 * 3 / 365)
    assert r.iloc[2] == pytest.approx(-0.05 / 365)


def _mcx_series(months: int = 4, roll_day: int = 20, spread: float = 0.05) -> tuple[pd.DataFrame, list]:
    """Crude-like front month: volume dries up on the two days before each roll, the roll day opens at the
    next contract's price (a `spread` gap) with full volume again."""
    days = pd.bdate_range("2021-01-01", periods=months * 23)
    ts = pd.Series(pd.to_datetime([f"{d.date()} 03:30" for d in days], utc=True))
    close = np.full(len(days), 100.0)
    open_ = close.copy()
    volume = np.full(len(days), 1e5)
    rolls = []
    level = 100.0
    for m in sorted({(d.year, d.month) for d in days}):
        in_month = [i for i, d in enumerate(days) if (d.year, d.month) == m and d.day >= roll_day]
        if not in_month or in_month[0] < 25:
            continue
        i = in_month[0]
        rolls.append(ts[i])
        volume[i - 2 : i] = 1e4
        level *= 1 + spread
        open_[i:] = level
        close[i:] = level
    return _frame(ts, close, open_, volume), rolls


def test_detect_rolls_finds_the_volume_switch_days_and_removes_the_roll_gap():
    df, rolls = _mcx_series()
    found = td.detect_rolls(df, "MCX:CRUDEOIL")
    evidenced = found[found["evidence"] == "volume_switch"]
    assert list(evidenced["ts"]) == rolls
    adj = td.mcx_adjustments(df, "MCX:CRUDEOIL")
    ret = td.mcx_futures_returns(df, adj)
    assert np.allclose(ret.iloc[1:], 0.0)
    raw = td.mcx_futures_returns(df, adj, rolls="none")
    assert raw[df["ts"].isin(rolls)].round(6).tolist() == [0.05] * len(rolls)


def test_open_interest_switch_beats_a_volume_jump_and_a_hole_beats_both():
    df, rolls = _mcx_series(months=2)
    first = df.index[df["ts"] == rolls[0]][0]
    oi = np.full(len(df), 1000.0)
    oi[first + 1 :] = 3000.0  # OI doubles the day after the volume switch
    df["oi"] = oi
    found = td.detect_rolls(df, "MCX:CRUDEOIL").set_index("window")
    month = rolls[0].tz_convert(IST).strftime("%Y-%m")
    assert found.loc[month, "ts"] == df["ts"][first + 1]
    assert found.loc[month, "evidence"] == "oi_switch"
    holed = df.drop(index=range(first - 3, first + 5)).reset_index(drop=True)
    found = td.detect_rolls(holed, "MCX:CRUDEOIL")
    after_hole = holed["ts"][holed["ts"].diff().dt.days > td.HOLE_DAYS].iloc[0]
    assert after_hole in set(found["ts"])
    assert found.loc[found["ts"] == after_hole, "evidence"].item() == "data_hole"


def test_weak_windows_fall_back_to_the_days_where_evidenced_rolls_happen():
    df, rolls = _mcx_series(months=9)
    last = df.index[df["ts"] == rolls[-1]][0]
    df.loc[last - 2 : last - 1, "volume"] = 1e5  # no dry-up before the last roll: weak evidence
    df.loc[df["ts"].dt.tz_convert(IST).dt.day == 15, "volume"] = 2e5  # a jump outside the usual days
    found = td.detect_rolls(df, "MCX:CRUDEOIL")
    weak = found[found["evidence"] == "weak_volume_jump"]
    assert len(weak) >= 1
    days = weak["ts"].dt.tz_convert(IST).dt.day
    evidenced_days = found.loc[found["evidence"] == "volume_switch", "ts"].dt.tz_convert(IST).dt.day
    assert days.between(evidenced_days.min(), evidenced_days.max()).all()


def test_off_market_print_and_invalid_price_repairs():
    ts = _daily("2021-01-04", 80)
    close = np.full(80, 100.0)
    open_ = close.copy()
    volume = np.full(80, 1e5)
    close[40], open_[40], volume[40] = 120.0, 120.0, 100.0  # a stale print from another contract
    close[60] = 1.0  # vendor floor
    open_[60] = 100.0
    open_[61] = 90.0
    close[61:] = 90.0
    df = _frame(ts, close, open_, volume)
    df["high"] = df[["open", "close"]].max(axis=1)
    df["low"] = df[["open", "close"]].min(axis=1)
    assert list(td.glitch_rows(df)) == [40, 41]
    assert list(td.invalid_rows(df)) == [60]
    adj = td.mcx_adjustments(df, "MCX:CRUDEOIL")
    ret = td.mcx_futures_returns(df, adj)
    assert ret[40] == 0.0 and ret[41] == 0.0
    assert ret[60] == 0.0 and ret[61] == 0.0
    kept = td.mcx_futures_returns(df, adj, zero_invalid=False)
    assert kept[60] == pytest.approx(1.0 / 100.0 - 1.0)


# --- signal, sizing, P&L ---------------------------------------------------------------------------------


def test_rebalance_mask_marks_the_first_bar_of_each_ist_month():
    ts = pd.Series(
        pd.to_datetime(
            ["2024-01-30 03:45", "2024-01-31 03:45", "2024-02-01 03:45", "2024-02-02 03:45"], utc=True
        )
    )
    assert te.rebalance_mask(ts).tolist() == [True, False, True, False]


def test_weights_follow_the_12_1_sign_and_target_ten_percent_vol():
    ts = _daily("2020-01-01", 400)
    up = pd.Series(np.r_[np.nan, np.full(399, 0.001)])
    w = te.strategy_weights(ts, up)
    rebal = te.rebalance_mask(ts)
    late = np.flatnonzero(rebal & (np.arange(400) > te.LOOKBACK))
    early = np.flatnonzero(rebal & (np.arange(400) < te.LOOKBACK))
    sigma = math.sqrt(252 * 0.001**2)
    assert w["w_tsmom"].iloc[late].to_numpy() == pytest.approx(0.10 / sigma)
    assert (w["w_tsmom"].iloc[early] == 0).all()
    assert w["w_buy_and_hold"].iloc[late].to_numpy() == pytest.approx(0.10 / sigma)
    down = te.strategy_weights(ts, -up)
    assert down["w_tsmom"].iloc[late].to_numpy() == pytest.approx(-0.10 / sigma)
    assert w["w_tsmom"][~rebal].isna().all()


def test_signal_skips_the_last_month():
    ts = _daily("2020-01-01", 300)
    r = np.full(300, 0.001)
    r[0] = np.nan
    r[-te.SKIP :] = -0.05  # a crash inside the skipped month must not flip the signal
    w = te.strategy_weights(ts, pd.Series(r))
    assert w["signal"].iloc[-1] == 1.0


def test_sleeve_pnl_known_answer():
    target = pd.Series([np.nan, 1.0, np.nan, np.nan, -1.0, np.nan])
    ret = pd.Series([np.nan, 0.01, 0.02, -0.01, 0.03, 0.01])
    out = te.sleeve_pnl(ret, target, round_trip_cost=0.001)
    assert out["position"].tolist() == [0.0, 0.0, 1.0, 1.0, 1.0, -1.0]
    assert out["gross"].tolist() == pytest.approx([0.0, 0.0, 0.02, -0.01, 0.03, -0.01])
    assert out["cost"].tolist() == pytest.approx([0.0, 0.002, 0.0, 0.0, 0.003, 0.0])


def _index_weights(df: pd.DataFrame) -> pd.DataFrame:
    return te.strategy_weights(df["ts"], td.index_futures_returns(df))


def test_index_weights_are_causal():
    df = synthetic_candles("1D", "2018-01-01", "2020-06-30", seed=3)
    check_causal(_index_weights, df, cuts=[300, 400, len(df) - 30])


def test_mcx_weights_are_causal_given_the_roll_calendar():
    df = synthetic_candles("1D", "2018-01-01", "2020-06-30", exchange="MCX", seed=4)
    adj = td.mcx_adjustments(df, "MCX:GOLD")

    def weights(frame: pd.DataFrame) -> pd.DataFrame:
        n = len(frame)
        cut = td.ReturnAdjustments(
            rolls=adj.rolls,
            roll_rows=adj.roll_rows[adj.roll_rows < n],
            weak_roll_rows=adj.weak_roll_rows[adj.weak_roll_rows < n],
            repair_rows=adj.repair_rows[adj.repair_rows < n],
            zero_rows=adj.zero_rows[adj.zero_rows < n],
            repairs=adj.repairs,
        )
        return te.strategy_weights(frame["ts"], td.mcx_futures_returns(frame, cut))

    assert len(adj.roll_rows) > 5
    check_causal(weights, df, cuts=[300, 450, len(df) - 20])


# --- statistics ------------------------------------------------------------------------------------------


def test_bootstrap_sharpe_matches_a_direct_resample_and_p_counts_ties():
    rng = np.random.default_rng(5)
    values, blocks = rng.normal(0.01, 0.02, 36), np.arange(36)
    draws = te.bootstrap_sharpe(values, blocks, 12, resamples=20, seed=9)
    counts = np.random.default_rng(9).multinomial(36, np.full(36, 1 / 36), size=20)
    for j in range(20):
        x = np.repeat(values, counts[j])
        assert draws[j] == pytest.approx(x.mean() / x.std(ddof=1) * math.sqrt(12))
    assert te.p_one_sided(np.array([-1.0, 0.0, 1.0, 2.0])) == pytest.approx(3 / 5)
    assert te.p_one_sided(np.array([1.0, 2.0])) == pytest.approx(1 / 3)


def test_portfolio_averages_the_sleeves_whose_evaluation_started():
    spec = te.parse_spec(edge_v2_section("tsmom"), HOLDOUT)
    a = pd.DataFrame(
        {"date": [date(2018, 12, 31), date(2019, 1, 1), date(2019, 1, 2)], "x": [0.01, 0.02, 0.03]}
    )
    b = pd.DataFrame({"date": [date(2018, 12, 31), date(2019, 1, 2)], "x": [0.5, 0.06]})
    out = te.portfolio_daily({"NSE:NIFTY50": a, "MCX:GOLD": b}, spec, "x")
    assert out.tolist() == pytest.approx([0.01, 0.01, 0.045])


# --- end to end on synthetic data ------------------------------------------------------------------------


def _synthetic_datas(drift: float) -> dict:
    out = {}
    for k, iid in enumerate(te.UNIVERSE):
        exchange = iid.split(":")[0]
        start = "2017-01-01" if exchange == "MCX" else "2006-01-01"
        df = synthetic_candles(
            "1D", start, "2025-09-30", exchange=exchange, seed=10 + k, gap=0.0, drift=drift
        )
        adj = td.mcx_adjustments(df, iid) if exchange == "MCX" else None
        rt, parts = te.round_trip(iid)
        out[iid] = te.InstrumentData(iid, df, adj, rt, parts)
    return out


def test_evaluate_end_to_end_on_a_trending_market(tmp_path):
    result = te.evaluate(datas=_synthetic_datas(drift=0.25))
    rep = result.report
    assert rep["primary"]["value"] > 0
    assert rep["primary"]["p_one_sided"] < 0.05
    assert set(rep["per_instrument"]) == set(te.UNIVERSE)
    assert rep["costs"]["index_futures"]["round_trip_pct"] > 0
    assert rep["costs"]["mcx_futures"]["round_trip_inr_at_reference_notional"] > 0
    json_path, md_path = te.write_report(rep, tmp_path / "tsmom.json")
    assert json.loads(json_path.read_text(encoding="utf-8"))["test"] == "tsmom_v1"
    assert "Own pass rule" in md_path.read_text(encoding="utf-8")


def test_evaluate_refuses_holdout_bars():
    datas = _synthetic_datas(drift=0.0)
    gold = datas["MCX:GOLD"]
    extra = synthetic_candles("1D", "2025-10-01", "2025-10-10", exchange="MCX", seed=1)
    gold.candles = pd.concat([gold.candles, extra], ignore_index=True)
    with pytest.raises(PermissionError):
        te.evaluate(datas=datas)
