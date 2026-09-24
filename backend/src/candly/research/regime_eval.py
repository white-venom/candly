"""Go/no-go #2 evaluation of regime_v1 (config/pivot.yaml regime_model.go_no_go_2).

Every forecast comes from the walk-forward in candly.research.regime: a refit at the start of each
test window on data before it. Scored forecasts are those with a resolved, non-flat outcome and a
base-rate forecast; every method is scored on the same rows.

Statistics. Labels overlap for h bars and all instruments share a date, so resampling units are blocks
of h consecutive trading days (every instrument's forecasts in them together). The Brier-skill gate uses
the percentile CI of that block bootstrap; the one-date-per-cluster CI (go/no-go #1's method) is reported
alongside. The one-sided bootstrap p-value of skill <= 0 gets a Benjamini-Hochberg q across horizons.
The calibration gate is the pre-registered self-consistency test (outcomes simulated independently from
the forecasts). Two diagnostics that never change the gate are reported with it: the same test on dates
spaced h bars apart, and a block-clustered Wald test of calibration intercept 0 and slope 1.

Top decile: the 10% of scored forecasts with the largest |p_up - 0.5|, each traded in its called
direction: a long enters at the next open and exits at close[t+h] (equity delivery costs; index futures
for an index), a short the reverse (futures costs), all from config/costs.yaml via research/costs.py.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

from candly.research.config import load_costs_config, load_research_config
from candly.research.costs import round_trip_cost
from candly.research.data import CandleLoader
from candly.research.regime import (
    MODEL_NAME,
    PivotConfig,
    build_panel,
    config_hashes,
    fold_windows,
    gap_bars,
    hyperparameters,
    load_pivot_config,
    model_dir,
    regime_universe,
    train_production_model,
    validation_report_path,
    walk_forward_predictions,
)
from candly.research.stats import (
    benjamini_hochberg,
    calibration_self_consistency,
    cluster_robust_z,
    z_pvalue,
)

METHODS = {
    MODEL_NAME: "p_model",
    "base_rate": "p_base",
    "trend_rule_ema200": "p_trend",
    "momentum_sign_20d": "p_mom",
}
BOOTSTRAP_RESAMPLES = 2000
CALIBRATION_SIMS = 2000
SEED = 20260924
TOP_SHARE = 0.10
HOLDING = "multi_day"


def bootstrap_skills(brier, baseline, cluster, resamples: int = BOOTSTRAP_RESAMPLES, seed: int = SEED):
    """Brier skill 1 - sum(brier) / sum(baseline) over `resamples` cluster-bootstrap draws (whole clusters
    resampled with replacement; the same draws as stats.clustered_bootstrap_skill_ci)."""
    brier, baseline = np.asarray(brier, float), np.asarray(baseline, float)
    _, inverse = np.unique(np.asarray(cluster), return_inverse=True)
    b, b0 = np.bincount(inverse, weights=brier), np.bincount(inverse, weights=baseline)
    k = b.size
    counts = np.random.default_rng(seed).multinomial(k, np.full(k, 1.0 / k), size=resamples)
    with np.errstate(divide="ignore", invalid="ignore"):
        skill = 1.0 - (counts @ b) / (counts @ b0)
    return skill[np.isfinite(skill)]


def bootstrap_mean_ci(values, cluster, level: float, resamples: int = BOOTSTRAP_RESAMPLES, seed: int = SEED):
    values = np.asarray(values, float)
    if values.size == 0:
        return None
    _, inverse = np.unique(np.asarray(cluster), return_inverse=True)
    sums, n = np.bincount(inverse, weights=values), np.bincount(inverse).astype(float)
    k = sums.size
    counts = np.random.default_rng(seed).multinomial(k, np.full(k, 1.0 / k), size=resamples)
    with np.errstate(divide="ignore", invalid="ignore"):
        means = (counts @ sums) / (counts @ n)
    means = means[np.isfinite(means)]
    tail = (1.0 - level) / 2.0
    lo, hi = np.quantile(means, [tail, 1.0 - tail])
    return float(lo), float(hi)


def _ci(samples: np.ndarray, level: float) -> list[float] | None:
    if samples.size == 0:
        return None
    tail = (1.0 - level) / 2.0
    return [float(x) for x in np.quantile(samples, [tail, 1.0 - tail])]


def calibration_wald(p, y, cluster) -> dict:
    """Diagnostic, not a gate: cluster-robust Wald test that y - p has zero intercept and zero slope on
    (p - mean p), i.e. calibration in the large and a reliability slope of 1. Unlike the self-consistency
    test it allows outcomes to be correlated within a cluster."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    r = y - p
    X = np.column_stack([np.ones_like(p), p - p.mean()])
    bread = np.linalg.pinv(X.T @ X)
    beta = bread @ X.T @ r
    _, inverse = np.unique(np.asarray(cluster), return_inverse=True)
    g = int(inverse.max()) + 1
    scores = np.zeros((g, 2))
    np.add.at(scores, inverse, X * (r - X @ beta)[:, None])
    cov = bread @ (scores.T @ scores) @ bread * (g / max(g - 1, 1))
    df = int(np.linalg.matrix_rank(cov))
    wald = float(beta @ np.linalg.pinv(cov) @ beta)
    return {
        "intercept": float(beta[0]),
        "slope": float(1.0 + beta[1]),
        "wald": wald,
        "df": df,
        "p_value": float(sps.chi2.sf(wald, df)) if df else None,
        "n_clusters": g,
    }


def trade_costs(kinds: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Round-trip cost fraction per row for a long and for a short multi-day trade."""
    costs = load_costs_config()
    table = {
        kind: (
            round_trip_cost(kind, HOLDING, costs=costs, side="long"),
            round_trip_cost(kind, HOLDING, costs=costs, side="short"),
        )
        for kind in kinds.unique()
    }
    return (
        kinds.map(lambda k: table[k][0]).to_numpy(dtype=float),
        kinds.map(lambda k: table[k][1]).to_numpy(dtype=float),
    )


def top_decile(p, y, p_base, trade_ret, cost_long, cost_short, blocks, level: float) -> dict:
    """Expectancy after costs of the TOP_SHARE most confident calls (ties at the threshold included)."""
    p, y, p_base, trade_ret = (np.asarray(a, float) for a in (p, y, p_base, trade_ret))
    conviction = np.abs(p - 0.5)
    threshold = float(np.quantile(conviction, 1.0 - TOP_SHARE))
    sel = (conviction >= threshold) & (p != 0.5)
    if not sel.any():
        return {"n": 0, "conviction_threshold": threshold, "expectancy": None}
    long = p[sel] > 0.5
    gross = np.where(long, trade_ret[sel], -trade_ret[sel])
    cost = np.where(long, np.asarray(cost_long)[sel], np.asarray(cost_short)[sel])
    net = gross - cost
    hit = np.where(long, y[sel], 1.0 - y[sel])
    expected_hit = np.where(long, p_base[sel], 1.0 - p_base[sel])
    z, n_clusters = cluster_robust_z(hit - expected_hit, np.asarray(blocks)[sel])
    return {
        "n": int(sel.sum()),
        "n_long": int(long.sum()),
        "n_short": int((~long).sum()),
        "conviction_threshold": threshold,
        "mean_p_up": float(p[sel].mean()),
        "hit_rate": float(hit.mean()),
        "base_hit_rate": float(expected_hit.mean()),
        "hit_vs_base_z": None if np.isnan(z) else float(z),
        "hit_vs_base_p": float(z_pvalue(z, False)),
        "n_clusters": n_clusters,
        "mean_gross_return": float(gross.mean()),
        "mean_cost": float(cost.mean()),
        "expectancy": float(net.mean()),
        "expectancy_ci": bootstrap_mean_ci(net, np.asarray(blocks)[sel], level),
    }


def score_method(scored: pd.DataFrame, col: str, h: int, level: float) -> dict:
    research = load_research_config().go_no_go_1
    p, y, base = (scored[c].to_numpy(dtype=float) for c in (col, "y", "p_base"))
    brier_i, base_i = (p - y) ** 2, (base - y) ** 2
    brier, brier_base = float(brier_i.mean()), float(base_i.mean())
    blocks, days = scored["block"].to_numpy(), scored["day_idx"].to_numpy()
    block_skills = bootstrap_skills(brier_i, base_i, blocks)
    ece, cal_p = calibration_self_consistency(
        p, y, research.ece_bins, research.ece_binning, sims=CALIBRATION_SIMS, seed=SEED
    )
    thin = days % h == 0
    ece_thin, cal_p_thin = calibration_self_consistency(
        p[thin], y[thin], research.ece_bins, research.ece_binning, sims=CALIBRATION_SIMS, seed=SEED
    )
    cost_long, cost_short = trade_costs(scored["kind"])
    return {
        "brier": brier,
        "brier_base_rate": brier_base,
        "skill": 1.0 - brier / brier_base if brier_base > 0 else None,
        "skill_ci": _ci(block_skills, level),
        "skill_ci_method": f"block bootstrap, blocks of {h} trading days",
        "skill_ci_by_date": _ci(bootstrap_skills(brier_i, base_i, days), level),
        "skill_p_one_sided": float((block_skills <= 0).mean()) if block_skills.size else None,
        "ece": ece,
        "calibration_p": cal_p,
        "calibration_p_thinned": cal_p_thin,
        "ece_thinned": ece_thin,
        "n_thinned": int(thin.sum()),
        "calibration_clustered": calibration_wald(p, y, blocks),
        "mean_p_up": float(p.mean()),
        "up_rate": float(y.mean()),
        "hit_rate": float(np.where(p > 0.5, y, 1.0 - y).mean()),
        "top_decile": top_decile(
            p, y, base, scored["trade_ret"], cost_long, cost_short, blocks, level
        ),
    }


def _gates(m: dict, n_scored: int, pivot: PivotConfig) -> dict:
    g = pivot.regime_model.go_no_go_2
    lower = m["skill_ci"][0] if m["skill_ci"] else None
    expectancy = m["top_decile"]["expectancy"]
    return {
        "brier_skill_ci_lower": {
            "value": lower,
            "above": g.brier_skill_ci_lower_above,
            "ci": m["skill_ci"],
            "pass": lower is not None and lower > g.brier_skill_ci_lower_above,
        },
        "calibration": {
            "p_value": m["calibration_p"],
            "min_p": g.calibration_min_p,
            "pass": m["calibration_p"] is not None and m["calibration_p"] >= g.calibration_min_p,
        },
        "min_scored": {"value": n_scored, "required": g.min_scored, "pass": n_scored >= g.min_scored},
        "top_decile_expectancy_after_costs": {
            "value": expectancy,
            "required_positive": g.top_decile_expectancy_after_cost_positive,
            "pass": (expectancy is not None and expectancy > 0)
            if g.top_decile_expectancy_after_cost_positive
            else True,
        },
    }


@dataclass
class RegimeEvaluation:
    horizon: int
    period: str
    start: str
    end: str
    instruments: list[str]
    gap_bars: int
    n_folds: int
    n_rows: int  # panel rows inside the test windows
    n_no_features: int  # warm-up rows without every required feature
    n_flat: int
    n_unresolved: int  # outcome bars fall after the last available bar
    n_unfitted: int  # rows whose fold had too little data to fit
    n_scored: int
    n_dates: int
    n_blocks: int
    methods: dict[str, dict]
    gates: dict[str, dict]
    passed: bool
    runtime_s: float
    predictions: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)

    def to_dict(self) -> dict:
        out = {k: v for k, v in self.__dict__.items() if k != "predictions"}
        out["pass"] = out.pop("passed")
        return out


def evaluate_regime(
    horizon: int,
    period: str = "validation",
    allow_holdout: bool = False,
    *,
    instruments=None,
    load: CandleLoader | None = None,
    pivot: PivotConfig | None = None,
    params: dict | None = None,
    panel: pd.DataFrame | None = None,
) -> RegimeEvaluation:
    """Walk-forward replay of regime_v1 and its baselines at `horizon` days over `period`, scored against
    go/no-go #2. "validation" is [train_end, holdout); "holdout" continues the same walk-forward from the
    holdout start and needs `allow_holdout=True`, which only the official go/no-go run passes."""
    started = time.perf_counter()
    pivot = pivot or load_pivot_config()
    if horizon not in pivot.regime_model.horizons_days:
        raise ValueError(f"horizon {horizon} is not pre-registered: {pivot.regime_model.horizons_days}")
    if period not in ("validation", "holdout"):
        raise ValueError("period must be 'validation' or 'holdout'")
    if period == "holdout" and not allow_holdout:
        raise PermissionError("the holdout is locked; an official go/no-go run passes allow_holdout=True")
    level = load_research_config().ci_level
    ids = list(instruments) if instruments is not None else regime_universe()
    if panel is None:
        panel = build_panel(
            ids, pivot.regime_model.horizons_days, allow_holdout=period == "holdout", load=load, pivot=pivot
        )
    elif period == "validation" and (panel["ts"] >= pivot.holdout_start_utc).any():
        raise PermissionError("the panel holds holdout bars; build it with allow_holdout=False")
    months = pivot.walk_forward.retrain_every_months
    if period == "validation":
        start, end = pivot.train_end_utc(), pivot.holdout_start_utc
    else:
        start, end = pivot.holdout_start_utc, panel["ts"].max() + pd.Timedelta(days=1)
    windows = fold_windows(start, end, months)
    preds = walk_forward_predictions(panel, horizon, windows, pivot, params)
    preds["block"] = preds["day_idx"] // horizon
    ok = preds["features_ok"].astype(bool)
    resolved = preds["y"].notna()
    fitted = preds["p_model"].notna() & preds["p_base"].notna()
    scored = preds[ok & resolved & fitted]
    methods: dict[str, dict] = {}
    if len(scored):
        methods = {name: score_method(scored, col, horizon, level) for name, col in METHODS.items()}
        for name, col in METHODS.items():
            if name == MODEL_NAME:
                continue
            p_model, p_other, y = (scored[c].to_numpy(dtype=float) for c in ("p_model", col, "y"))
            samples = bootstrap_skills((p_model - y) ** 2, (p_other - y) ** 2, scored["block"])
            other = ((p_other - y) ** 2).mean()
            methods[MODEL_NAME].setdefault("skill_vs", {})[name] = {
                "skill": float(1.0 - ((p_model - y) ** 2).mean() / other) if other > 0 else None,
                "ci": _ci(samples, level),
            }
        for m in methods.values():
            m["gates"] = _gates(m, len(scored), pivot)
            m["pass"] = all(g["pass"] for g in m["gates"].values())
    model = methods.get(MODEL_NAME)
    return RegimeEvaluation(
        horizon=horizon,
        period=period,
        start=start.isoformat(),
        end=end.isoformat(),
        instruments=sorted(panel["instrument"].unique().tolist()),
        gap_bars=gap_bars(horizon, pivot),
        n_folds=int(preds["fold"].nunique()) if len(preds) else 0,
        n_rows=len(preds),
        n_no_features=int((~ok).sum()),
        n_flat=int((ok & preds["flat"].astype(bool)).sum()),
        n_unresolved=int((ok & ~resolved & ~preds["flat"].astype(bool)).sum()),
        n_unfitted=int((ok & resolved & ~fitted).sum()),
        n_scored=len(scored),
        n_dates=int(scored["day_idx"].nunique()),
        n_blocks=int(scored["block"].nunique()),
        methods=methods,
        gates=model["gates"] if model else {},
        passed=bool(model and model["pass"]),
        runtime_s=round(time.perf_counter() - started, 1),
        predictions=preds,
    )


# --- reports -----------------------------------------------------------------------------------------


def validation_report(results: dict[int, RegimeEvaluation], pivot: PivotConfig, runtime_s: float) -> dict:
    cfg = load_research_config()
    horizons = sorted(results)
    pvalues = [results[h].methods.get(MODEL_NAME, {}).get("skill_p_one_sided") for h in horizons]
    known = [p for p in pvalues if p is not None]
    qvalues = iter(benjamini_hochberg(known).tolist())
    q = {h: (next(qvalues) if p is not None else None) for h, p in zip(horizons, pvalues, strict=True)}
    periods = {r.period for r in results.values()}
    return {
        "model": MODEL_NAME,
        "period": periods.pop() if len(periods) == 1 else sorted(periods),
        "generated_at": datetime.now(UTC).isoformat(),
        "pivot_sha256": pivot.sha256,
        "config_hashes": config_hashes(),
        "hyperparameters": hyperparameters(),
        "protocol": {
            "walk_forward": {
                "retrain_every_months": pivot.walk_forward.retrain_every_months,
                "expanding": True,
                "gap_bars": {str(h): gap_bars(h, pivot) for h in horizons},
            },
            "base_rate": "per instrument, up-rate of the model's own training rows",
            "resampling": "blocks of h consecutive trading days, all instruments together",
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "calibration_sims": CALIBRATION_SIMS,
            "ece_bins": cfg.go_no_go_1.ece_bins,
            "ece_binning": cfg.go_no_go_1.ece_binning,
            "ci_level": cfg.ci_level,
            "top_decile": "top 10% of |p_up - 0.5|, traded in the called direction, next open to close[t+h]",
            "costs_holding": HOLDING,
            "seed": SEED,
        },
        "caveats": [
            "Universe is today's watchlist large caps (survivorship bias): results are an upper bound.",
            "The pre-registered calibration gate simulates independent outcomes; overlapping labels and "
            "same-day cross-instrument correlation make it reject calibrated forecasts more often than 5%. "
            "calibration_clustered (block-clustered Wald test of intercept 0, slope 1) and "
            "calibration_p_thinned are diagnostics only and do not change the gate.",
        ],
        "pass_rule": pivot.regime_model.go_no_go_2.pass_rule,
        "n_hypotheses": len(horizons),
        "fdr_alpha": cfg.fdr_alpha,
        "horizons": {str(h): {**results[h].to_dict(), "skill_q": q[h]} for h in horizons},
        "pass": any(results[h].passed for h in horizons),
        "runtime_s": round(runtime_s, 1),
    }


def _fmt(x, digits: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _fmt_ci(ci) -> str:
    return "n/a" if not ci else f"[{ci[0]:+.4f}, {ci[1]:+.4f}]"


def markdown_summary(report: dict) -> str:
    lines = [
        "# regime_v1 validation (go/no-go #2)",
        "",
        f"Generated {report['generated_at'][:19]}Z. Period: {report['period']} (walk-forward). "
        f"Pass rule: {report['pass_rule']}. Hypotheses: {report['n_hypotheses']} horizons.",
        "",
        f"**Overall: {'PASS' if report['pass'] else 'FAIL'}**",
        "",
    ]
    for h, entry in report["horizons"].items():
        lines += [
            f"## {h}-day horizon: {'PASS' if entry['pass'] else 'FAIL'}",
            "",
            f"n_scored {entry['n_scored']} ({entry['n_dates']} dates, {entry['n_blocks']} {h}-day blocks), "
            f"{entry['n_folds']} folds, gap {entry['gap_bars']} bars, skill q {_fmt(entry['skill_q'], 3)}.",
            "",
            "| method | Brier | skill vs base | 95% CI (blocks) | calib p | calib p clustered (diag) "
            "| top-decile net | CI |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for name, m in entry["methods"].items():
            top = m["top_decile"]
            lines.append(
                f"| {name} | {_fmt(m['brier'])} | {_fmt(m['skill'])} | {_fmt_ci(m['skill_ci'])} | "
                f"{_fmt(m['calibration_p'], 3)} | {_fmt(m['calibration_clustered']['p_value'], 3)} | "
                f"{_fmt(top.get('expectancy'), 5)} | "
                f"{_fmt_ci(top.get('expectancy_ci'))} |"
            )
        gates = ", ".join(f"{k} {'pass' if g['pass'] else 'FAIL'}" for k, g in entry["gates"].items())
        lines += ["", f"Gates: {gates}.", ""]
        vs = entry["methods"].get(MODEL_NAME, {}).get("skill_vs", {})
        if vs:
            lines.append(
                "regime_v1 Brier skill vs baselines: "
                + "; ".join(f"{k} {_fmt(v['skill'])} {_fmt_ci(v['ci'])}" for k, v in vs.items())
                + "."
            )
            lines.append("")
    lines += ["Caveats:", *[f"- {c}" for c in report["caveats"]], ""]
    return "\n".join(lines)


def write_validation_report(report: dict, json_path: Path | None = None) -> tuple[Path, Path]:
    json_path = json_path or validation_report_path()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path = json_path.with_suffix(".md")
    md_path.write_text(markdown_summary(report), encoding="utf-8")
    return json_path, md_path


def run_validation(
    *, instruments=None, load: CandleLoader | None = None, write: bool = True, save_predictions: bool = True
) -> dict:
    """Validation for every pre-registered horizon, one shared panel (no holdout bars)."""
    started = time.perf_counter()
    pivot = load_pivot_config()
    ids = list(instruments) if instruments is not None else regime_universe()
    panel = build_panel(ids, pivot.regime_model.horizons_days, allow_holdout=False, load=load, pivot=pivot)
    results = {
        h: evaluate_regime(h, "validation", instruments=ids, pivot=pivot, panel=panel)
        for h in pivot.regime_model.horizons_days
    }
    report = validation_report(results, pivot, time.perf_counter() - started)
    if save_predictions:
        out = model_dir()
        out.mkdir(parents=True, exist_ok=True)
        for h, r in results.items():
            r.predictions.to_parquet(out / f"validation_predictions_h{h}.parquet", index=False)
    if write:
        write_validation_report(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="regime_v1 validation (never the holdout), production fit")
    parser.add_argument("--skip-validation", action="store_true")
    parser.add_argument("--skip-production", action="store_true")
    args = parser.parse_args(argv)
    if not args.skip_validation:
        report = run_validation()
        for h, entry in report["horizons"].items():
            m = entry["methods"].get(MODEL_NAME, {})
            print(
                f"h={h}: n={entry['n_scored']} skill={_fmt(m.get('skill'))} ci={_fmt_ci(m.get('skill_ci'))} "
                f"calib_p={_fmt(m.get('calibration_p'), 3)} "
                f"top_decile={_fmt(m.get('top_decile', {}).get('expectancy'), 5)} "
                f"pass={entry['pass']} ({entry['runtime_s']}s)"
            )
        print(f"overall pass={report['pass']} runtime={report['runtime_s']}s -> {validation_report_path()}")
    if not args.skip_production:
        started = time.perf_counter()
        meta = train_production_model()
        n = {h: e["n_train"] for h, e in meta["horizons"].items()}
        print(f"production model saved to {model_dir()} (n_train {n}, {time.perf_counter() - started:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
