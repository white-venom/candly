import json

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

from candly.research.costs import round_trip_cost
from candly.research.regime import load_pivot_config
from candly.research.regime_eval import (
    METHODS,
    RegimeEvaluation,
    _gates,
    bootstrap_mean_ci,
    bootstrap_skills,
    calibration_wald,
    markdown_summary,
    score_method,
    top_decile,
    trade_costs,
    validation_report,
    write_validation_report,
)
from candly.research.stats import calibration_self_consistency, clustered_bootstrap_skill_ci


def test_block_bootstrap_matches_the_go_no_go_1_bootstrap():
    rng = np.random.default_rng(0)
    brier, base = rng.uniform(0, 0.3, 400), rng.uniform(0, 0.3, 400)
    cluster = np.repeat(np.arange(80), 5)
    samples = bootstrap_skills(brier, base, cluster, resamples=500, seed=3)
    expected = clustered_bootstrap_skill_ci(brier, base, cluster, 0.95, resamples=500, seed=3)
    assert tuple(np.quantile(samples, [0.025, 0.975])) == pytest.approx(expected)


def test_bootstrap_mean_ci_resamples_whole_clusters():
    values = np.r_[np.full(50, 1.0), np.full(50, -1.0)]
    lo, hi = bootstrap_mean_ci(values, np.r_[np.zeros(50), np.ones(50)], 0.95, resamples=400)
    assert lo == -1.0 and hi == 1.0
    lo, hi = bootstrap_mean_ci(values, np.arange(100), 0.95, resamples=400)
    assert -0.4 < lo < 0 < hi < 0.4


def test_top_decile_trades_the_called_side_after_costs():
    p = np.r_[np.full(90, 0.52), np.full(5, 0.80), np.full(5, 0.20)]
    y = np.r_[np.zeros(90), np.ones(5), np.zeros(5)]
    trade_ret = np.r_[np.zeros(90), np.full(5, 0.04), np.full(5, -0.02)]
    cost_long, cost_short = np.full(100, 0.003), np.full(100, 0.001)
    out = top_decile(p, y, np.full(100, 0.55), trade_ret, cost_long, cost_short, np.arange(100), 0.95)
    assert (out["n"], out["n_long"], out["n_short"]) == (10, 5, 5)
    assert out["mean_gross_return"] == pytest.approx(0.03)
    assert out["expectancy"] == pytest.approx(0.03 - 0.002)
    assert out["hit_rate"] == 1.0 and out["base_hit_rate"] == pytest.approx(0.5)
    assert 0.02 < out["conviction_threshold"] <= 0.30


def test_trade_costs_come_from_the_cost_table():
    long, short = trade_costs(pd.Series(["equity", "index", "equity"]))
    assert long[0] == round_trip_cost("equity", "multi_day", side="long")
    assert short[0] == round_trip_cost("equity", "multi_day", side="short")
    assert long[1] == short[1] == round_trip_cost("index", "multi_day")
    assert long[0] != short[0]


def correlated_outcomes(seed: int, clusters: int = 200, size: int = 20, rho: float = 0.3):
    """Calibrated by construction (P(y=1 | p) = p), with a shared shock per cluster, like every stock on
    one date, and forecasts that are nearly equal within a cluster."""
    rng = np.random.default_rng(seed)
    cluster = np.repeat(np.arange(clusters), size)
    p = np.clip(rng.uniform(0.35, 0.75, clusters)[cluster] + rng.normal(0, 0.01, cluster.size), 0.01, 0.99)
    z = np.sqrt(rho) * rng.normal(size=clusters)[cluster] + np.sqrt(1 - rho) * rng.normal(size=cluster.size)
    return p, (z < sps.norm.ppf(p)).astype(float), cluster


def test_clustered_calibration_diagnostic():
    rng = np.random.default_rng(5)
    p = rng.uniform(0.3, 0.7, 5000)
    cluster = np.repeat(np.arange(500), 10)
    calibrated = calibration_wald(p, (rng.uniform(size=p.size) < p).astype(float), cluster)
    assert calibrated["p_value"] > 0.05 and calibrated["slope"] == pytest.approx(1.0, abs=0.2)
    biased = calibration_wald(p, (rng.uniform(size=p.size) < p + 0.08).astype(float), cluster)
    assert biased["p_value"] < 1e-6 and biased["intercept"] > 0.05


def test_self_consistency_gate_rejects_calibrated_but_clustered_outcomes():
    """Why the clustered diagnostic is reported: the pre-registered gate assumes independent outcomes."""
    p, y, cluster = correlated_outcomes(seed=1)
    _, gate_p = calibration_self_consistency(p, y, 10, "quantile", sims=1000)
    assert gate_p < 0.01
    assert calibration_wald(p, y, cluster)["p_value"] > 0.05


def scored_frame(n_blocks: int = 120, per_block: int = 10, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = n_blocks * per_block
    p_model = rng.uniform(0.3, 0.8, n)
    y = (rng.uniform(size=n) < p_model).astype(float)
    return pd.DataFrame(
        {
            "p_model": p_model,
            "p_base": np.full(n, 0.55),
            "p_trend": np.clip(p_model + rng.normal(0, 0.1, n), 0.05, 0.95),
            "p_mom": np.full(n, 0.5),
            "y": y,
            "trade_ret": np.where(y == 1, 0.02, -0.02),
            "kind": np.where(np.arange(n) % 3 == 0, "index", "equity"),
            "day_idx": np.arange(n) // 2,
            "block": np.arange(n) // per_block,
        }
    )


def test_gates_follow_the_pivot_thresholds():
    pivot = load_pivot_config()
    g = pivot.regime_model.go_no_go_2
    metrics = {
        "skill_ci": [0.001, 0.02],
        "calibration_p": g.calibration_min_p,
        "top_decile": {"expectancy": 0.0005},
    }
    gates = _gates(metrics, g.min_scored, pivot)
    assert all(gate["pass"] for gate in gates.values())
    failing = _gates({**metrics, "skill_ci": [0.0, 0.02]}, g.min_scored - 1, pivot)
    assert not failing["brier_skill_ci_lower"]["pass"] and not failing["min_scored"]["pass"]
    assert not _gates({**metrics, "top_decile": {"expectancy": -1e-6}}, 10**6, pivot)[
        "top_decile_expectancy_after_costs"
    ]["pass"]


def test_report_round_trip(tmp_path):
    pivot = load_pivot_config()
    scored = scored_frame()
    methods = {name: score_method(scored, col, 5, 0.95) for name, col in METHODS.items()}
    for m in methods.values():
        m["gates"] = _gates(m, len(scored), pivot)
        m["pass"] = all(x["pass"] for x in m["gates"].values())
    assert methods["regime_v1"]["skill"] > 0 and methods["base_rate"]["skill"] == 0.0
    ev = RegimeEvaluation(
        horizon=5, period="validation", start="s", end="e", instruments=["NSE:RELIANCE"], gap_bars=20,
        n_folds=1, n_rows=len(scored), n_no_features=0, n_flat=0, n_unresolved=0, n_unfitted=0,
        n_scored=len(scored), n_dates=scored["day_idx"].nunique(), n_blocks=scored["block"].nunique(),
        methods=methods, gates=methods["regime_v1"]["gates"], passed=methods["regime_v1"]["pass"],
        runtime_s=0.0,
    )
    report = validation_report({5: ev}, pivot, 1.0)
    assert report["n_hypotheses"] == 1 and report["pivot_sha256"] == pivot.sha256
    assert report["pass"] == ev.passed and report["horizons"]["5"]["skill_q"] is not None
    json_path, md_path = write_validation_report(report, tmp_path / "regime.json")
    assert json.loads(json_path.read_text(encoding="utf-8"))["horizons"]["5"]["n_scored"] == len(scored)
    text = md_path.read_text(encoding="utf-8")
    assert "5-day horizon" in text and "trend_rule_ema200" in text
    assert markdown_summary(report) == text
