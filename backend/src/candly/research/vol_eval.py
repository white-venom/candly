"""vol_v1 evaluation (config/edge_search.yaml `volatility`): does realized_vol_model forecast NIFTY 50
realised volatility better than India VIX, and does trading only on disagreement beat always selling vol?

Scored rows: test-window days with every forecaster's forecast and a resolved target (candly.research.
vol_model has the target and the forecasters); every forecaster is scored on the same rows. Loss:
QLIKE(RV, F) = RV/F - log(RV/F) - 1 on annualised variances (Patton 2011: consistent under a noisy but
unbiased realised-variance proxy; 0 for a perfect forecast). Improvement vs VIX = mean QLIKE(VIX) - mean
QLIKE(model), positive when ours is better.

Variance-swap proxy, per non-overlapping h-day period on the grid that starts at the first test day
(phase 0): implied K = VIX_t^2 * h / 252 and realised R = 10^4 * sum_{i=1..h} r_{t+i}^2, both in squared
vol points over the period, for one unit of variance notional. A short earns K - R, a long R - K. Every
traded period pays the options round trip from config/costs.yaml on a straddle of the same exposure: a
delta-hedged ATM straddle earns about (K - R) / 2K of its premium, so its premium is about 2K in these
units and the cost is 2K x the round-trip rate. Delta-hedging costs, margins and real option quotes are
not modelled (the config caveat).
- always_short: short every period.
- conditional: short when VIX_t - forecast_t > margin, long when VIX_t - forecast_t < -margin, else flat;
  VIX and the model's forecast in annualised vol %, the margin fixed per fold before its test window: one
  standard deviation of the model's vol forecast error on that fold's training rows.

Statistics: date-block bootstrap (common.bootstrap). QLIKE resamples blocks of h consecutive test days;
the P&L resamples its h-day periods. Percentile CIs; one draw of blocks is shared by every comparison
(paired). One-sided bootstrap p-values of the gated statistics get Benjamini-Hochberg q-values
(research.yaml fdr_alpha) next to the pre-registered rule, which alone decides pass or fail. Diagnostics
that never change the gate: 60-day blocks (volatility clusters for longer than h days), every start
phase of the P&L grid, and a VIX rescaled by its training-set mean RV / VIX^2 (how much of a QLIKE win is
only the variance risk premium).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from candly.core.settings import REPO_ROOT, get_settings
from candly.research.config import config_hashes, load_costs_config, load_research_config
from candly.research.costs import cost_breakdown
from candly.research.data import CandleLoader
from candly.research.edge_config import EDGE_FILE, EdgeSearchConfig, load_edge_config
from candly.research.stats import benjamini_hochberg
from candly.research.vol_model import (
    ANNUAL,
    DIAGNOSTIC_COLUMNS,
    FORECAST_COLUMNS,
    MODEL_NAME,
    build_vol_frame,
    hyperparameters,
    walk_forward,
    yearly_windows,
)

TEST_NAME = "vol_v1"
VALIDATION_REPORT = "2026-09-24-vol-v1-validation.json"
LONG_BLOCK_DAYS = 60
VARIANCE_POINTS = 1e4
OPTION_KIND = "option"
GATE_KEYS = ("qlike_improvement_vs_vix", "conditional_vs_always_short_sharpe")


def validation_report_path() -> Path:
    return REPO_ROOT / "docs" / "test-reports" / VALIDATION_REPORT


def predictions_dir() -> Path:
    return get_settings().data_dir / "models" / TEST_NAME


# --- loss and statistics ---------------------------------------------------------------------------


def qlike(realized, forecast) -> np.ndarray:
    ratio = np.asarray(realized, float) / np.asarray(forecast, float)
    return ratio - np.log(ratio) - 1.0


def _draws(k: int, resamples: int, seed: int) -> np.ndarray:
    """Block counts per resample: k blocks drawn with replacement, (resamples, k)."""
    return np.random.default_rng(seed).multinomial(k, np.full(k, 1.0 / k), size=resamples)


def bootstrap_means(values, blocks, resamples: int, seed: int) -> np.ndarray:
    """Mean of `values` over `resamples` block-bootstrap draws (whole blocks with replacement)."""
    values = np.asarray(values, float)
    _, inverse = np.unique(np.asarray(blocks), return_inverse=True)
    sums, n = np.bincount(inverse, weights=values), np.bincount(inverse).astype(float)
    counts = _draws(sums.size, resamples, seed)
    return (counts @ sums) / (counts @ n)


def _sharpe_from_moments(n, total, total_sq, periods_per_year: float):
    n, total, total_sq = (np.asarray(x, float) for x in (n, total, total_sq))
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = total / n
        var = np.maximum(total_sq - n * mean**2, 0.0) / (n - 1)
        sd = np.sqrt(var)
        sr = np.where(sd > 0, mean / sd, np.where(mean == 0, 0.0, np.nan))
    return sr * math.sqrt(periods_per_year)


def sharpe(x, periods_per_year: float) -> float | None:
    """Annualised mean / sd (ddof 1). A flat series has Sharpe 0; a constant non-zero one has none."""
    x = np.asarray(x, float)
    if x.size < 2:
        return None
    value = float(_sharpe_from_moments(x.size, x.sum(), (x**2).sum(), periods_per_year))
    return None if math.isnan(value) else value


def bootstrap_sharpe_diff(a, b, blocks, periods_per_year: float, resamples: int, seed: int) -> np.ndarray:
    """Sharpe(a) - Sharpe(b) over paired block-bootstrap draws; draws where either is undefined drop out."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    _, inverse = np.unique(np.asarray(blocks), return_inverse=True)
    n = np.bincount(inverse).astype(float)
    counts = _draws(n.size, resamples, seed)
    rows = counts @ n

    def resampled(x: np.ndarray) -> np.ndarray:
        s1, s2 = np.bincount(inverse, weights=x), np.bincount(inverse, weights=x * x)
        return _sharpe_from_moments(rows, counts @ s1, counts @ s2, periods_per_year)

    diff = resampled(a) - resampled(b)
    return diff[np.isfinite(diff)]


def percentile_ci(samples: np.ndarray, level: float) -> list[float] | None:
    if samples.size == 0:
        return None
    tail = (1.0 - level) / 2.0
    return [float(x) for x in np.quantile(samples, [tail, 1.0 - tail])]


def p_not_above(samples: np.ndarray) -> float | None:
    """One-sided bootstrap p-value of H0 "statistic <= 0": the share of draws at or below 0."""
    return float((samples <= 0).mean()) if samples.size else None


def max_drawdown(pnl) -> float:
    """Largest fall of the cumulative (additive) P&L from its running peak, starting from 0."""
    cum = np.concatenate([[0.0], np.cumsum(np.asarray(pnl, float))])
    return float((np.maximum.accumulate(cum) - cum).max())


# --- costs and the variance-swap proxy ----------------------------------------------------------------


def option_round_trip() -> tuple[float, dict[str, float]]:
    """Round-trip cost of an index option position as a fraction of premium: costs.yaml `options` segment,
    Fyers brokerage at the reference notional, GST, and the default slippage on both sides."""
    costs = copy.deepcopy(load_costs_config())
    costs["mapping"] = {**costs["mapping"], OPTION_KIND: {"multi_day": "options"}}
    parts = cost_breakdown(OPTION_KIND, "multi_day", costs=costs)
    return float(sum(parts.values())), parts


def vol_pct(variance) -> np.ndarray:
    return 100.0 * np.sqrt(np.asarray(variance, float))


def conditional_positions(vix_var, model_var, margin_vol) -> np.ndarray:
    """+1 short vol, -1 long vol, 0 flat, from the VIX-minus-forecast spread in annualised vol %."""
    spread = vol_pct(vix_var) - vol_pct(model_var)
    margin = np.asarray(margin_vol, float)
    return np.where(spread > margin, 1, np.where(spread < -margin, -1, 0))


def variance_swap_legs(vix_var, realized_var, h: int, cost_rate: float):
    """(K, R, cost) per period in squared vol points over h days."""
    K = VARIANCE_POINTS * np.asarray(vix_var, float) * h / ANNUAL
    R = VARIANCE_POINTS * np.asarray(realized_var, float) * h / ANNUAL
    return K, R, 2.0 * cost_rate * K


def strategy_pnl(position, K, R, cost) -> tuple[np.ndarray, np.ndarray]:
    """(net, gross) P&L per period: position x (K - R), minus the cost when a position is held."""
    position = np.asarray(position, float)
    gross = position * (K - R)
    return gross - np.abs(position) * cost, gross


def period_starts(day_idx, first_day: int, h: int, phase: int = 0) -> np.ndarray:
    offset = np.asarray(day_idx) - first_day - phase
    return (offset >= 0) & (offset % h == 0)


def _summary(net: np.ndarray, gross: np.ndarray, position: np.ndarray, periods_per_year: float) -> dict:
    traded = position != 0
    return {
        "n_periods": int(net.size),
        "n_short": int((position > 0).sum()),
        "n_long": int((position < 0).sum()),
        "n_flat": int((~traded).sum()),
        "mean_net": float(net.mean()) if net.size else None,
        "mean_gross": float(gross.mean()) if net.size else None,
        "mean_net_when_short": float(net[position > 0].mean()) if (position > 0).any() else None,
        "mean_net_when_long": float(net[position < 0].mean()) if (position < 0).any() else None,
        "total_net": float(net.sum()),
        "std_net": float(net.std(ddof=1)) if net.size > 1 else None,
        "sharpe": sharpe(net, periods_per_year),
        "max_drawdown": max_drawdown(net),
        "worst_period": float(net.min()) if net.size else None,
        "hit_rate_traded": float((net[traded] > 0).mean()) if traded.any() else None,
    }


def _pnl_series(rows: pd.DataFrame, h: int, cost_rate: float):
    K, R, cost = variance_swap_legs(rows["vix_var"], rows["y"], h, cost_rate)
    always = np.ones(len(rows), dtype=int)
    cond = conditional_positions(rows["vix_var"], rows["model_var"], rows["margin_vol"])
    net_a, gross_a = strategy_pnl(always, K, R, cost)
    net_c, gross_c = strategy_pnl(cond, K, R, cost)
    return (net_a, gross_a, always), (net_c, gross_c, cond), (K, R, cost)


def variance_swap_evaluation(
    scored: pd.DataFrame, h: int, first_day: int, cost_rate: float, level: float, resamples: int, seed: int
) -> dict:
    periods_per_year = ANNUAL / h
    rows = scored[period_starts(scored["day_idx"], first_day, h)]
    (net_a, gross_a, pos_a), (net_c, gross_c, pos_c), (K, R, cost) = _pnl_series(rows, h, cost_rate)
    n = len(rows)
    point = None
    s_a, s_c = sharpe(net_a, periods_per_year), sharpe(net_c, periods_per_year)
    if s_a is not None and s_c is not None:
        point = s_c - s_a
    samples = bootstrap_sharpe_diff(net_c, net_a, np.arange(n), periods_per_year, resamples, seed)
    long_blocks = np.arange(n) // max(1, math.ceil(LONG_BLOCK_DAYS / h))
    long_samples = bootstrap_sharpe_diff(net_c, net_a, long_blocks, periods_per_year, resamples, seed)
    phases = []
    for phase in range(h):
        sub = scored[period_starts(scored["day_idx"], first_day, h, phase)]
        (pa, _, _), (pc, _, _), _ = _pnl_series(sub, h, cost_rate)
        sa, sc = sharpe(pa, periods_per_year), sharpe(pc, periods_per_year)
        phases.append(None if sa is None or sc is None else sc - sa)
    known = np.array([p for p in phases if p is not None])
    return {
        "n_periods": n,
        "first_period_ts": rows["ts"].iloc[0].isoformat() if n else None,
        "cost_rate_of_premium": cost_rate,
        "mean_implied": float(K.mean()) if n else None,
        "mean_realized": float(R.mean()) if n else None,
        "mean_cost": float(cost.mean()) if n else None,
        "always_short": _summary(net_a, gross_a, pos_a, periods_per_year),
        "conditional": _summary(net_c, gross_c, pos_c, periods_per_year),
        "sharpe_diff": point,
        "sharpe_diff_ci": percentile_ci(samples, level),
        "sharpe_diff_p_one_sided": p_not_above(samples),
        "sharpe_diff_ci_60d_blocks": percentile_ci(long_samples, level),
        "phase_sharpe_diff": {
            "values": phases,
            "min": float(known.min()) if known.size else None,
            "median": float(np.median(known)) if known.size else None,
            "max": float(known.max()) if known.size else None,
            "share_above_0": float((known > 0).mean()) if known.size else None,
        },
    }


def qlike_evaluation(
    scored: pd.DataFrame, h: int, first_day: int, level: float, resamples: int, seed: int
) -> tuple[dict, dict]:
    """(per-forecaster QLIKE and bias, realized_vol_model vs every other forecaster)."""
    y = scored["y"].to_numpy(dtype=float)
    blocks = (scored["day_idx"].to_numpy() - first_day) // h
    long_blocks = (scored["day_idx"].to_numpy() - first_day) // LONG_BLOCK_DAYS
    columns = {**FORECAST_COLUMNS, **DIAGNOSTIC_COLUMNS}
    losses = {name: qlike(y, scored[col]) for name, col in columns.items()}
    per = {
        name: {
            "qlike": float(losses[name].mean()),
            "mean_forecast_vol": float(vol_pct(scored[col]).mean()),
            "mean_realized_vol": float(vol_pct(y).mean()),
            "realized_over_forecast_variance": float(y.mean() / scored[col].mean()),
            "diagnostic_only": name in DIAGNOSTIC_COLUMNS,
        }
        for name, col in columns.items()
    }
    model = losses[MODEL_NAME]
    vs = {}
    for name in columns:
        if name == MODEL_NAME:
            continue
        diff = losses[name] - model
        samples = bootstrap_means(diff, blocks, resamples, seed)
        vs[name] = {
            "improvement": float(diff.mean()),
            "relative_improvement": float(1.0 - model.mean() / losses[name].mean()),
            "ci": percentile_ci(samples, level),
            "p_one_sided": p_not_above(samples),
            "ci_60d_blocks": percentile_ci(bootstrap_means(diff, long_blocks, resamples, seed), level),
            "diagnostic_only": name in DIAGNOSTIC_COLUMNS,
        }
    return per, vs


def _gate(value, ci, above: float) -> dict:
    lower = ci[0] if ci else None
    return {"value": value, "ci": ci, "ci_lower_above": above, "pass": lower is not None and lower > above}


# --- evaluation ------------------------------------------------------------------------------------------


@dataclass
class VolEvaluation:
    horizon: int
    period: str
    start: str
    end: str
    n_folds: int
    folds: list[dict]
    n_rows: int  # test-window rows
    n_no_features: int  # warm-up rows and dates without a VIX print
    n_unresolved: int  # target bars after the last loaded bar
    n_unfitted: int  # rows whose fold had too little training data
    n_scored: int
    n_blocks: int
    qlike: dict
    model_vs: dict
    variance_swap: dict
    gates: dict
    passed: bool
    runtime_s: float
    predictions: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)

    def to_dict(self) -> dict:
        out = {k: v for k, v in self.__dict__.items() if k != "predictions"}
        out["pass"] = out.pop("passed")
        return out


def _window(period: str, frame: pd.DataFrame, edge: EdgeSearchConfig) -> tuple[pd.Timestamp, pd.Timestamp]:
    if period == "validation":
        return edge.volatility.train_end_utc, edge.common.holdout_start_utc
    return edge.common.holdout_start_utc, frame["ts"].max() + pd.Timedelta(days=1)


def evaluate_vol(
    h: int,
    period: str = "validation",
    allow_holdout: bool = False,
    *,
    load: CandleLoader | None = None,
    edge: EdgeSearchConfig | None = None,
    frame: pd.DataFrame | None = None,
    params: dict | None = None,
) -> VolEvaluation:
    """Walk-forward replay of vol_v1 at horizon `h` over `period`, judged by the pre-registered pass_if.
    "validation" is [train_end, holdout_start); "holdout" continues the same walk-forward from the
    holdout start and needs `allow_holdout=True`, which only the official go/no-go run passes."""
    started = time.perf_counter()
    edge = edge or load_edge_config()
    spec = edge.volatility
    if h not in spec.horizons_days:
        raise ValueError(f"horizon {h} is not pre-registered: {spec.horizons_days}")
    if period not in ("validation", "holdout"):
        raise ValueError("period must be 'validation' or 'holdout'")
    if period == "holdout" and not allow_holdout:
        raise PermissionError("the holdout is locked; an official go/no-go run passes allow_holdout=True")
    if frame is None:
        frame = build_vol_frame(edge, allow_holdout=period == "holdout", load=load)
    elif period == "validation" and (frame["ts"] >= edge.common.holdout_start_utc).any():
        raise PermissionError("the frame holds holdout bars; build it with allow_holdout=False")
    start, end = _window(period, frame, edge)
    preds, folds = walk_forward(frame, h, yearly_windows(start, end), params)
    boot = edge.common.bootstrap
    ok = preds["features_ok"].astype(bool)
    resolved = preds["y"].notna() & (preds["y"] > 0)
    columns = [*FORECAST_COLUMNS.values(), *DIAGNOSTIC_COLUMNS.values(), "margin_vol"]
    positive = (preds[list(FORECAST_COLUMNS.values())] > 0).all(axis=1)
    fitted = np.isfinite(preds[columns]).all(axis=1) & positive
    scored = preds[ok & resolved & fitted].reset_index(drop=True)
    first_day = int(preds["day_idx"].min()) if len(preds) else 0
    per, vs, pnl, gates = {}, {}, {}, {}
    if len(scored) > 1:
        per, vs = qlike_evaluation(scored, h, first_day, boot.ci_level, boot.resamples, boot.seed)
        cost_rate, _ = option_round_trip()
        pnl = variance_swap_evaluation(
            scored, h, first_day, cost_rate, boot.ci_level, boot.resamples, boot.seed
        )
        gate = spec.pass_if
        vix = vs["india_vix"]
        gates = {
            "qlike_improvement_vs_vix": {
                **_gate(vix["improvement"], vix["ci"], gate.qlike_improvement_vs_vix_ci_lower_above),
                "p_one_sided": vix["p_one_sided"],
            },
            "conditional_vs_always_short_sharpe": {
                **_gate(
                    pnl["sharpe_diff"],
                    pnl["sharpe_diff_ci"],
                    gate.conditional_vs_always_short_sharpe_ci_lower_above,
                ),
                "p_one_sided": pnl["sharpe_diff_p_one_sided"],
            },
        }
    return VolEvaluation(
        horizon=h,
        period=period,
        start=start.isoformat(),
        end=end.isoformat(),
        n_folds=len(folds),
        folds=folds,
        n_rows=len(preds),
        n_no_features=int((~ok).sum()),
        n_unresolved=int((ok & ~resolved).sum()),
        n_unfitted=int((ok & resolved & ~fitted).sum()),
        n_scored=len(scored),
        n_blocks=int(np.unique((scored["day_idx"] - first_day) // h).size) if len(scored) else 0,
        qlike=per,
        model_vs=vs,
        variance_swap=pnl,
        gates=gates,
        passed=bool(gates) and all(g["pass"] for g in gates.values()),
        runtime_s=round(time.perf_counter() - started, 1),
        predictions=preds,
    )


# --- reports ---------------------------------------------------------------------------------------------


def report_hashes(edge: EdgeSearchConfig) -> dict[str, str]:
    return {**config_hashes(), EDGE_FILE: edge.sha256}


def validation_report(results: dict[int, VolEvaluation], edge: EdgeSearchConfig, runtime_s: float) -> dict:
    research = load_research_config()
    spec = edge.volatility
    horizons = sorted(results)
    tests = [(h, key) for h in horizons for key in GATE_KEYS if results[h].gates]
    pvalues = [results[h].gates[key]["p_one_sided"] for h, key in tests]
    known = [(t, p) for t, p in zip(tests, pvalues, strict=True) if p is not None]
    q_known = benjamini_hochberg([p for _, p in known]).tolist()
    qvalues = dict(zip((t for t, _ in known), q_known, strict=True))
    horizons_out = {}
    for h in horizons:
        entry = results[h].to_dict()
        for key in GATE_KEYS:
            if key in entry["gates"]:
                q = qvalues.get((h, key))
                entry["gates"][key]["q_value"] = q
                entry["gates"][key]["survives_fdr"] = q is not None and q <= research.fdr_alpha
        horizons_out[str(h)] = entry
    cost_rate, cost_parts = option_round_trip()
    periods = {r.period for r in results.values()}
    return {
        "test": spec.name,
        "period": periods.pop() if len(periods) == 1 else sorted(periods),
        "generated_at": datetime.now(UTC).isoformat(),
        "edge_search_sha256": edge.sha256,
        "config_hashes": report_hashes(edge),
        "underlying": spec.underlying,
        "implied": spec.implied,
        "hyperparameters": hyperparameters(),
        "definitions": {
            "realized_variance": "RV_h(t) = 252/h * sum_{i=1..h} r_{t+i}^2, r = close-to-close log return "
            "(overnight gaps included, not demeaned); realised vol = 100*sqrt(RV_h), annualised %",
            "realized_variance_source": "NIFTY 50 daily closes (Fyers); 5m bars not used (start 2017-07)",
            "loss": "QLIKE(RV, F) = RV/F - log(RV/F) - 1 on annualised variances",
            "india_vix": "(VIX_t/100)^2 for both horizons (30-calendar-day implied vol: horizon mismatch)",
            "variance_swap": "per h-day period: K = VIX_t^2*h/252, R = 1e4*sum r^2 (squared vol points), "
            "short earns K - R; cost = 2K x option round-trip rate",
            "conditional": "short if VIX - forecast > margin, long if < -margin, else flat "
            "(annualised vol %); margin = std of the model's vol forecast error on the fold's training "
            "rows (in-sample)",
            "periods": "non-overlapping h-day periods from the first test day (phase 0)",
        },
        "protocol": {
            "walk_forward": "expanding window, refit every IST calendar year from train_end",
            "purge_rows": {str(h): h for h in horizons},
            "train_end": spec.train_end.isoformat(),
            "holdout_start": edge.common.holdout_start.isoformat(),
            "bootstrap": {
                "kind": edge.common.bootstrap.kind,
                "resamples": edge.common.bootstrap.resamples,
                "ci_level": edge.common.bootstrap.ci_level,
                "seed": edge.common.bootstrap.seed,
                "qlike_blocks": "h consecutive test days",
                "pnl_blocks": "one h-day period",
                "diagnostic_blocks_days": LONG_BLOCK_DAYS,
            },
            "option_round_trip_of_premium": cost_rate,
            "option_cost_components": cost_parts,
        },
        "pass_rule": spec.pass_if.rule,
        "n_hypotheses": len(tests),
        "fdr_alpha": research.fdr_alpha,
        "horizons": horizons_out,
        "pass": any(results[h].passed for h in horizons),
        "caveats": [
            spec.caveat,
            "India VIX is a 30-calendar-day risk-neutral volatility. It carries the variance risk premium, "
            "so it overstates realised variance on average; a QLIKE win over raw VIX can come from that bias "
            "alone. india_vix_rescaled (diagnostic) shows how much is left once the bias is removed.",
            "At h = 5 VIX measures a 30-day horizon: its use as a 5-day forecast and a 5-day swap strike is "
            "a mismatch the pre-registration accepted.",
            "Costs: costs.yaml has no option slippage, so the default 0.05% per side applies; real "
            "bid-ask on Nifty options, delta-hedging costs and margin are not included.",
            "The P&L is per unit of variance notional. March 2020 is in the sample, but a short variance "
            "position has no loss cap, so a worse tail than the sample's is possible.",
            "Blocks of h days are short for volatility, which clusters for months; the 60-day-block CIs "
            "are a diagnostic of that.",
        ],
        "runtime_s": round(runtime_s, 1),
    }


def _fmt(x, digits: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _fmt_ci(ci, digits: int = 4) -> str:
    return "n/a" if not ci else f"[{ci[0]:+.{digits}f}, {ci[1]:+.{digits}f}]"


def markdown_summary(report: dict) -> str:
    lines = [
        f"# {report['test']} validation: NIFTY 50 realised volatility vs India VIX",
        "",
        f"Generated {report['generated_at'][:19]}Z. Period: {report['period']} (walk-forward, "
        f"{report['protocol']['train_end']} to {report['protocol']['holdout_start']}, holdout untouched). "
        f"Pass rule: {report['pass_rule']}. Gated tests: {report['n_hypotheses']} "
        f"(BH q-values at FDR {report['fdr_alpha']} shown for context).",
        "",
        f"**Overall: {'PASS' if report['pass'] else 'FAIL'}**",
        "",
        f"> {report['caveats'][0]}",
        "",
        f"Realised variance: {report['definitions']['realized_variance']}.",
        "",
    ]
    for h, e in report["horizons"].items():
        lines += [
            f"## {h}-day horizon: {'PASS' if e['pass'] else 'FAIL'}",
            "",
            f"n_scored {e['n_scored']} days ({e['n_blocks']} {h}-day blocks), {e['n_folds']} yearly folds, "
            f"purge {h} rows, runtime {e['runtime_s']}s.",
            "",
            "| forecaster | QLIKE | mean forecast vol | model improvement | 95% CI | CI (60d blocks) |",
            "|---|---|---|---|---|---|",
        ]
        for name, m in e["qlike"].items():
            vs = e["model_vs"].get(name)
            label = f"{name} (diagnostic)" if m["diagnostic_only"] else name
            lines.append(
                f"| {label} | {_fmt(m['qlike'])} | {_fmt(m['mean_forecast_vol'], 2)} | "
                f"{_fmt(vs['improvement']) if vs else '-'} | {_fmt_ci(vs['ci']) if vs else '-'} | "
                f"{_fmt_ci(vs['ci_60d_blocks']) if vs else '-'} |"
            )
        first = next(iter(e["qlike"].values()), {})
        lines += ["", f"Mean realised vol {_fmt(first.get('mean_realized_vol'), 2)}.", ""]
        pnl = e["variance_swap"]
        if pnl:
            lines += [
                f"Variance-swap proxy: {pnl['n_periods']} non-overlapping periods, mean implied "
                f"{_fmt(pnl['mean_implied'], 2)}, mean realised {_fmt(pnl['mean_realized'], 2)}, mean cost "
                f"{_fmt(pnl['mean_cost'], 3)} (squared vol points per period).",
                "",
                "| strategy | short/long/flat | mean net | Sharpe | max drawdown | worst period |",
                "|---|---|---|---|---|---|",
            ]
            for name in ("always_short", "conditional"):
                s = pnl[name]
                lines.append(
                    f"| {name} | {s['n_short']}/{s['n_long']}/{s['n_flat']} | {_fmt(s['mean_net'], 3)} | "
                    f"{_fmt(s['sharpe'], 3)} | {_fmt(s['max_drawdown'], 2)} | {_fmt(s['worst_period'], 2)} |"
                )
            phase = pnl["phase_sharpe_diff"]
            lines += [
                "",
                f"Sharpe difference (conditional - always short) {_fmt(pnl['sharpe_diff'], 3)}, 95% CI "
                f"{_fmt_ci(pnl['sharpe_diff_ci'], 3)}; 60-day-block CI "
                f"{_fmt_ci(pnl['sharpe_diff_ci_60d_blocks'], 3)}; over all {h} start phases min "
                f"{_fmt(phase['min'], 3)}, median {_fmt(phase['median'], 3)}, max {_fmt(phase['max'], 3)}.",
                "",
            ]
        gates = "; ".join(
            f"{k} {'pass' if g['pass'] else 'FAIL'} (CI {_fmt_ci(g['ci'])}, q {_fmt(g.get('q_value'), 3)})"
            for k, g in e["gates"].items()
        )
        lines += [f"Gates: {gates}.", ""]
    lines += ["Caveats:", *[f"- {c}" for c in report["caveats"][1:]], ""]
    return "\n".join(lines)


def write_validation_report(report: dict, json_path: Path | None = None) -> tuple[Path, Path]:
    json_path = json_path or validation_report_path()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path = json_path.with_suffix(".md")
    md_path.write_text(markdown_summary(report), encoding="utf-8")
    return json_path, md_path


def run_validation(
    *, load: CandleLoader | None = None, write: bool = True, save_predictions: bool = True
) -> dict:
    """Validation for every pre-registered horizon on one shared frame (no holdout bars)."""
    started = time.perf_counter()
    edge = load_edge_config()
    frame = build_vol_frame(edge, allow_holdout=False, load=load)
    results = {
        h: evaluate_vol(h, "validation", edge=edge, frame=frame) for h in edge.volatility.horizons_days
    }
    report = validation_report(results, edge, time.perf_counter() - started)
    report["frame_sha256"] = hashlib.sha256(
        pd.util.hash_pandas_object(frame[["ts", "vix_var", "ewma_var"]], index=False).to_numpy().tobytes()
    ).hexdigest()
    if save_predictions:
        out = predictions_dir()
        out.mkdir(parents=True, exist_ok=True)
        for h, r in results.items():
            r.predictions.to_parquet(out / f"validation_predictions_h{h}.parquet", index=False)
    if write:
        write_validation_report(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="vol_v1 validation (never the holdout)")
    parser.parse_args(argv)
    report = run_validation()
    for h, e in report["horizons"].items():
        g = e["gates"]
        print(
            f"h={h}: n={e['n_scored']} qlike_vs_vix={_fmt(g['qlike_improvement_vs_vix']['value'])} "
            f"ci={_fmt_ci(g['qlike_improvement_vs_vix']['ci'])} "
            f"sharpe_diff={_fmt(g['conditional_vs_always_short_sharpe']['value'], 3)} "
            f"ci={_fmt_ci(g['conditional_vs_always_short_sharpe']['ci'], 3)} "
            f"pass={e['pass']} ({e['runtime_s']}s)"
        )
    print(f"overall pass={report['pass']} runtime={report['runtime_s']}s -> {validation_report_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
