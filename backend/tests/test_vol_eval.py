import dataclasses
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import reload_calendar
from candly.core.schema import validate_candles
from candly.core.settings import get_settings
from candly.research import vol_eval as ve
from candly.research.config import load_costs_config
from candly.research.edge_config import load_edge_config
from candly.research.synthetic import bar_times
from candly.research.vol_model import build_vol_frame

NIFTY, VIX = "NSE:NIFTY50", "NSE:INDIAVIX"
H = 5


# --- statistics ------------------------------------------------------------------------------------------


def test_qlike_is_zero_when_exact_and_punishes_under_forecasts_more():
    assert ve.qlike([0.04], [0.04])[0] == 0.0
    under, over = ve.qlike(2.0, 1.0), ve.qlike(1.0, 2.0)
    assert under == pytest.approx(2.0 - np.log(2.0) - 1.0)
    assert over == pytest.approx(0.5 + np.log(2.0) - 1.0)
    assert under > over > 0


def test_sharpe_and_drawdown_known_values():
    assert ve.sharpe([1.0, 2.0, 3.0], periods_per_year=4) == pytest.approx(4.0)
    assert ve.sharpe([0.0, 0.0, 0.0], 4) == 0.0
    assert ve.sharpe([1.0, 1.0], 4) is None
    assert ve.sharpe([1.0], 4) is None
    assert ve.max_drawdown([1.0, -2.0, 1.0, -3.0, 5.0]) == pytest.approx(4.0)
    assert ve.max_drawdown([-1.0, 2.0]) == pytest.approx(1.0)
    assert ve.max_drawdown([1.0, 2.0]) == 0.0


def _slow_draws(values_by_block, counts_row):
    return np.concatenate([values_by_block[b] for b in np.repeat(np.arange(len(counts_row)), counts_row)])


def test_block_bootstrap_means_resample_whole_blocks():
    rng = np.random.default_rng(1)
    values, blocks = rng.normal(size=40), np.arange(40) // 4
    samples = ve.bootstrap_means(values, blocks, resamples=30, seed=7)
    counts = np.random.default_rng(7).multinomial(10, np.full(10, 0.1), size=30)
    by_block = [values[blocks == b] for b in range(10)]
    for j in range(30):
        assert samples[j] == pytest.approx(_slow_draws(by_block, counts[j]).mean())


def test_bootstrap_sharpe_difference_matches_a_direct_computation():
    rng = np.random.default_rng(2)
    a, b = rng.normal(0.3, 1.0, 24), rng.normal(0.1, 1.0, 24)
    blocks = np.arange(24) // 3
    samples = ve.bootstrap_sharpe_diff(a, b, blocks, periods_per_year=12, resamples=25, seed=9)
    counts = np.random.default_rng(9).multinomial(8, np.full(8, 1 / 8), size=25)
    by_a, by_b = [a[blocks == k] for k in range(8)], [b[blocks == k] for k in range(8)]
    for j in range(25):
        expected = ve.sharpe(_slow_draws(by_a, counts[j]), 12) - ve.sharpe(_slow_draws(by_b, counts[j]), 12)
        assert samples[j] == pytest.approx(expected)


def test_percentile_ci_and_one_sided_p():
    samples = np.arange(-10, 91, dtype=float)
    assert ve.percentile_ci(samples, 0.9) == pytest.approx([-5.0, 85.0])
    assert ve.p_not_above(samples) == pytest.approx(11 / 101)
    assert ve.percentile_ci(np.array([]), 0.95) is None


# --- positions, P&L, costs ----------------------------------------------------------------------------


def test_conditional_positions_use_the_margin_in_vol_points():
    vix_var = np.full(4, 0.20**2)
    model_var = np.array([0.15, 0.15, 0.26, 0.19]) ** 2
    margin = np.array([4.0, 6.0, 4.0, 0.5])
    assert ve.conditional_positions(vix_var, model_var, margin).tolist() == [1, 0, -1, 1]


def test_variance_swap_legs_and_pnl():
    K, R, cost = ve.variance_swap_legs([0.04], [0.0225], h=5, cost_rate=0.004)
    assert K[0] == pytest.approx(1e4 * 0.04 * 5 / 252)
    assert R[0] == pytest.approx(1e4 * 0.0225 * 5 / 252)
    assert cost[0] == pytest.approx(2 * 0.004 * K[0])
    net, gross = ve.strategy_pnl(np.array([1, -1, 0]), np.repeat(K, 3), np.repeat(R, 3), np.repeat(cost, 3))
    edge = K[0] - R[0]
    assert gross.tolist() == pytest.approx([edge, -edge, 0.0])
    assert net.tolist() == pytest.approx([edge - cost[0], -edge - cost[0], 0.0])


def test_period_starts_do_not_overlap():
    day_idx = np.arange(10, 31)
    assert day_idx[ve.period_starts(day_idx, 10, 5)].tolist() == [10, 15, 20, 25, 30]
    assert day_idx[ve.period_starts(day_idx, 10, 5, phase=2)].tolist() == [12, 17, 22, 27]


def test_option_round_trip_comes_from_costs_yaml():
    cfg = load_costs_config()
    seg, broker = cfg["segments"]["options"], cfg["brokerage"]["fyers"]
    brokerage = 2 * min(broker["pct_cap"], broker["per_order_inr"] / cfg["reference_notional_inr"])
    exchange, sebi = 2 * seg["exchange_txn"]["both"], 2 * cfg["sebi_fee"]
    expected = (
        brokerage + exchange + sebi + seg["stt"]["sell"] + seg["stamp_duty"]["buy"]
        + cfg["gst_rate"] * (brokerage + exchange + sebi) + 2 * cfg["slippage"]["default"]
    )
    rate, parts = ve.option_round_trip()
    assert rate == pytest.approx(expected)
    assert parts["transaction_tax"] == pytest.approx(seg["stt"]["sell"])


def test_conditional_equals_always_short_when_it_always_shorts():
    n = 60
    rng = np.random.default_rng(3)
    rows = pd.DataFrame(
        {
            "ts": pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC"),
            "day_idx": np.arange(n),
            "vix_var": 0.04 * rng.uniform(0.8, 1.2, n),
            "y": 0.03 * rng.lognormal(0, 0.5, n),
            "model_var": np.full(n, 0.01),
            "margin_vol": np.zeros(n),
        }
    )
    pnl = ve.variance_swap_evaluation(
        rows, h=5, first_day=0, cost_rate=0.004, level=0.95, resamples=200, seed=1
    )
    assert pnl["n_periods"] == 12
    assert pnl["conditional"]["n_short"] == 12
    assert pnl["sharpe_diff"] == pytest.approx(0.0)
    assert pnl["sharpe_diff_ci"] == pytest.approx([0.0, 0.0])
    assert pnl["always_short"]["mean_net"] == pytest.approx(pnl["conditional"]["mean_net"])


# --- walk-forward on synthetic markets with a known answer -----------------------------------------------


def vol_world(seed: int, premium: float, start: str = "2010-01-01", end: str = "2016-06-30"):
    """Log-AR(1) daily volatility; VIX = premium x the exact expected realised vol of the next H days (an
    oracle that knows the future volatility path, which no forecaster can see)."""
    ts = bar_times("1D", start, end)
    n = len(ts)
    rng = np.random.default_rng(seed)
    x, eps = np.zeros(n), rng.normal(0.0, 0.12, n)
    for i in range(1, n):
        x[i] = 0.985 * x[i - 1] + eps[i]
    sigma = 0.01 * np.exp(x)
    night = np.sqrt(0.2) * sigma * rng.normal(size=n)
    night[0] = 0.0
    day = np.sqrt(0.8) * sigma * rng.normal(size=n)
    log_close = np.log(10_000.0) + np.cumsum(night + day)
    o, c = np.exp(log_close - day), np.exp(log_close)
    wick = np.abs(rng.normal(size=(2, n))) * 0.5 * sigma
    nifty = pd.DataFrame(
        {
            "ts": ts,
            "open": o,
            "high": np.maximum(o, c) * np.exp(wick[0]),
            "low": np.minimum(o, c) * np.exp(-wick[1]),
            "close": c,
            "volume": 0.0,
            "oi": np.nan,
        }
    )
    expected = pd.Series(sigma**2).rolling(H).mean().shift(-H).to_numpy()
    level = premium * 100.0 * np.sqrt(252.0 * expected)
    keep = np.isfinite(level)
    vix = pd.DataFrame(
        {"ts": ts[keep], "open": level[keep], "high": level[keep], "low": level[keep], "close": level[keep],
         "volume": 0.0, "oi": np.nan}
    )
    return validate_candles(nifty), validate_candles(vix)


def loader(nifty: pd.DataFrame, vix: pd.DataFrame):
    series, calls = {NIFTY: nifty, VIX: vix}, []

    def load(instrument_id, tf, start=None, end=None):
        calls.append((instrument_id, tf, end))
        df = series[instrument_id]
        return df if end is None else df[df["ts"] <= end]

    return load, calls


def small_edge():
    edge = load_edge_config()
    return dataclasses.replace(
        edge,
        common=dataclasses.replace(edge.common, holdout_start=date(2016, 1, 1)),
        volatility=dataclasses.replace(edge.volatility, train_end=date(2013, 1, 1), horizons_days=(H,)),
    )


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    """Evaluated once, on a calendar without the real data dir's observed holidays."""
    edge = small_edge()
    out = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(tmp_path_factory.mktemp("data")))
        get_settings.cache_clear()
        reload_calendar()
        for name, premium in (("biased", 1.5), ("oracle", 1.0)):
            load, calls = loader(*vol_world(31, premium))
            frame = build_vol_frame(edge, load=load)
            out[name] = (ve.evaluate_vol(H, edge=edge, frame=frame), frame, calls)
        load, _ = loader(*vol_world(31, 1.5))
        out["holdout"] = ve.evaluate_vol(H, "holdout", allow_holdout=True, edge=edge, load=load)
    get_settings.cache_clear()
    reload_calendar()
    return edge, out


def test_validation_never_sees_holdout_bars(worlds):
    edge, out = worlds
    result, frame, _ = out["biased"]
    assert frame["ts"].max() < edge.common.holdout_start_utc
    preds = result.predictions
    assert preds["ts"].min() >= edge.volatility.train_end_utc
    assert preds["ts"].max() < edge.common.holdout_start_utc
    assert result.n_folds == 3 and result.n_scored > 650


def test_every_fold_trains_only_on_labels_that_end_before_it(worlds):
    _, out = worlds
    for fold in out["biased"][0].folds:
        assert fold["fitted"]
        assert pd.Timestamp(fold["last_label_end_ts"]) < pd.Timestamp(fold["test_start"])
        assert fold["margin_vol"] > 0


def test_a_vix_carrying_a_risk_premium_is_beaten(worlds):
    _, out = worlds
    result = out["biased"][0]
    gate = result.gates["qlike_improvement_vs_vix"]
    assert gate["pass"] and gate["ci"][0] > 0
    assert result.qlike["india_vix"]["realized_over_forecast_variance"] == pytest.approx(1 / 1.5**2, rel=0.15)
    assert result.model_vs["india_vix_rescaled"]["improvement"] < gate["value"]
    pnl = result.variance_swap
    assert pnl["always_short"]["mean_net"] > 0
    assert pnl["conditional"]["n_short"] + pnl["conditional"]["n_long"] + pnl["conditional"]["n_flat"] == pnl[
        "n_periods"
    ]


def test_an_oracle_vix_is_not_beaten(worlds):
    _, out = worlds
    gate = out["oracle"][0].gates["qlike_improvement_vs_vix"]
    assert not gate["pass"]
    assert gate["value"] < 0 and gate["ci"][0] < 0


def test_holdout_is_locked_without_the_explicit_flag(worlds):
    edge, out = worlds
    _, frame, _ = out["biased"]
    with pytest.raises(PermissionError):
        ve.evaluate_vol(H, "holdout", edge=edge, frame=frame)
    leaky = pd.concat([frame, frame.tail(1).assign(ts=edge.common.holdout_start_utc)], ignore_index=True)
    with pytest.raises(PermissionError):
        ve.evaluate_vol(H, "validation", edge=edge, frame=leaky)
    with pytest.raises(ValueError, match="not pre-registered"):
        ve.evaluate_vol(3, edge=edge, frame=frame)


def test_the_holdout_replay_continues_the_walk_forward(worlds):
    edge, out = worlds
    result = out["holdout"]
    assert result.period == "holdout" and result.n_scored > 50
    assert result.predictions["ts"].min() >= edge.common.holdout_start_utc
    assert pd.Timestamp(result.folds[0]["last_label_end_ts"]) < edge.common.holdout_start_utc


def test_report_json_and_markdown(worlds, tmp_path):
    edge, out = worlds
    report = ve.validation_report({H: out["biased"][0]}, edge, runtime_s=1.0)
    assert report["n_hypotheses"] == 2
    gates = report["horizons"][str(H)]["gates"]
    assert set(gates) == set(ve.GATE_KEYS)
    assert all(0.0 <= g["q_value"] <= 1.0 for g in gates.values())
    assert report["caveats"][0] == edge.volatility.caveat
    json_path, md_path = ve.write_validation_report(report, tmp_path / "vol.json")
    assert json.loads(json_path.read_text(encoding="utf-8"))["test"] == "vol_v1"
    text = md_path.read_text(encoding="utf-8")
    assert "a pass is a lead" in text and f"## {H}-day horizon" in text
