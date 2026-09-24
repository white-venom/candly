"""vol_v2 validation (config/edge_search_v2.yaml `vol_intraday`): does a log-HAR on 5-minute realised
variance plus VIX forecast NIFTY 50 realised variance better than a recalibrated VIX, and does shorting
the variance-swap proxy only when VIX^2 exceeds that forecast beat always shorting it?

Forecasters are fitted once on the train window (candly.research.vol2_model) and scored on validation
rows with every input and a target that ends before the holdout. Loss: QLIKE, as vol_v1. Primary per
horizon: mean QLIKE(recalibrated VIX) - mean QLIKE(model), one-sided date-block bootstrap
p = (1 + #{draws <= 0}) / (1 + B). The block length is not registered: blocks of h days (vol_v1's gate)
and of 60 days are both run on the same draws, and the gate and the primary p take the more conservative
(the larger p, and both CI lower bounds must clear the bar).

Variance-swap proxy: vol_v1's (candly.research.vol_eval), on non-overlapping h-day periods from the first
validation day. always_short shorts every period; conditional shorts when VIX_t^2 > the model's forecast
and is flat otherwise. Check: Sharpe(conditional) - Sharpe(always_short), CI lower bound above 0 with
one-period blocks and with 60-day blocks.

Context forecasters, never gating: raw VIX, the vol_v1 LightGBM model (its own walk-forward from vol_v1's
train_end, as registered in edge_search.yaml), vol_v1's HAR on daily squared returns and a log-HAR on 5m
realised variance without VIX, both fitted on the vol_v2 train window.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from candly.core.settings import REPO_ROOT, get_settings
from candly.research.config import config_hashes
from candly.research.data import CandleLoader
from candly.research.edge_config import EDGE_FILE, load_edge_config
from candly.research.edge_v2_config import EDGE_V2_FILE, edge_v2_section, edge_v2_sha256
from candly.research.vol2_model import (
    BASELINE,
    HAR_5M_WINDOWS,
    MIN_SESSION_BARS,
    MODEL,
    OVERNIGHT_FLOOR,
    REGRESSORS,
    Vol2Spec,
    build_frame,
    fit_forecasters,
    load_inputs,
    load_spec,
    train_mask,
    validation_mask,
)
from candly.research.vol_eval import (
    max_drawdown,
    option_round_trip,
    percentile_ci,
    period_starts,
    qlike,
    sharpe,
    strategy_pnl,
    variance_swap_legs,
)
from candly.research.vol_model import (
    ANNUAL,
    HAR_COLUMNS,
    HAR_FLOOR_SHARE,
    LGBM_PARAMS,
    fit_har,
    predict_har,
    walk_forward,
    yearly_windows,
)
from candly.research.vol_model import build_frame as build_v1_frame

REPORT_STEM = "2026-09-24-vol-v2-validation"
LONG_BLOCK_DAYS = 60
FORECASTERS = (MODEL, BASELINE, "india_vix_raw", "vol_v1_model", "har_rv_daily", "har_5m_log")
CONTEXT = {"india_vix_raw", "vol_v1_model", "har_rv_daily", "har_5m_log"}
V1_PARAMS = {**LGBM_PARAMS, "num_threads": 1}


def predictions_dir() -> Path:
    return get_settings().data_dir / "models" / "vol_v2"


def save_predictions(results: dict[int, dict]) -> Path:
    out = predictions_dir()
    out.mkdir(parents=True, exist_ok=True)
    for h, r in results.items():
        r["predictions"].to_parquet(out / f"validation_predictions_h{h}.parquet", index=False)
    return out


def report_path() -> Path:
    return REPO_ROOT / "docs" / "test-reports" / f"{REPORT_STEM}.json"


# --- statistics -------------------------------------------------------------------------------------------


def _draws(k: int, resamples: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).multinomial(k, np.full(k, 1.0 / k), size=resamples)


def block_bootstrap_means(values, blocks, resamples: int, seed: int) -> np.ndarray:
    values = np.asarray(values, float)
    _, inverse = np.unique(np.asarray(blocks), return_inverse=True)
    sums, n = np.bincount(inverse, weights=values), np.bincount(inverse).astype(float)
    counts = _draws(sums.size, resamples, seed)
    return (counts @ sums) / (counts @ n)


def block_bootstrap_sharpe_diff(a, b, blocks, periods_per_year: float, resamples: int, seed: int):
    """Sharpe(a) - Sharpe(b) over paired draws of whole blocks; undefined draws drop out."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    _, inverse = np.unique(np.asarray(blocks), return_inverse=True)
    n = np.bincount(inverse).astype(float)
    counts = _draws(n.size, resamples, seed)
    rows = counts @ n

    def resampled(x: np.ndarray) -> np.ndarray:
        s1, s2 = counts @ np.bincount(inverse, weights=x), counts @ np.bincount(inverse, weights=x * x)
        with np.errstate(divide="ignore", invalid="ignore"):
            mean = s1 / rows
            sd = np.sqrt(np.maximum(s2 - rows * mean**2, 0.0) / (rows - 1))
            sr = np.where(sd > 0, mean / sd, np.where(mean == 0, 0.0, np.nan))
        return sr * math.sqrt(periods_per_year)

    diff = resampled(a) - resampled(b)
    return diff[np.isfinite(diff)]


def p_one_sided(samples: np.ndarray) -> float | None:
    """(1 + #{draws <= 0}) / (1 + B): H0 "statistic <= 0"."""
    if samples.size == 0:
        return None
    return float((1 + int((samples <= 0).sum())) / (1 + samples.size))


def _bootstrap_summary(samples: np.ndarray, level: float) -> dict:
    return {
        "ci": percentile_ci(samples, level),
        "p_one_sided": p_one_sided(samples),
        "n_draws": int(samples.size),
    }


def _conservative(schemes: dict[str, dict], above: float) -> dict:
    """The gate over every block scheme: the largest p, and a pass only if every CI clears `above`."""
    ps = [s["p_one_sided"] for s in schemes.values()]
    lowers = [s["ci"][0] if s["ci"] else None for s in schemes.values()]
    return {
        "p_one_sided": None if any(p is None for p in ps) else max(ps),
        "ci_lower_above": above,
        "pass": all(lo is not None and lo > above for lo in lowers),
    }


# --- forecasts --------------------------------------------------------------------------------------------


def vol_v1_forecasts(daily: pd.DataFrame, vix: pd.DataFrame, h: int, until: pd.Timestamp) -> pd.Series:
    """vol_v1's out-of-sample LightGBM forecasts (its own yearly walk-forward from its train_end), by ts."""
    edge = load_edge_config()
    spec = edge.volatility
    frame = build_v1_frame(daily, vix, [h], spec.underlying)
    preds, _ = walk_forward(frame, h, yearly_windows(spec.train_end_utc, until), V1_PARAMS)
    return preds.set_index("ts")["model_var"]


def horizon_forecasts(
    frame: pd.DataFrame, spec: Vol2Spec, h: int, v1: pd.Series | None
) -> tuple[pd.DataFrame, dict]:
    """Validation rows with every forecaster's forecast (NaN where a context one has none), and the fits."""
    fits = fit_forecasters(frame, spec, h)
    train = frame[train_mask(frame, spec, h)]
    har_train = train[np.isfinite(train[list(HAR_COLUMNS)]).all(axis=1)]
    y_har = har_train[f"y_{h}"].to_numpy(dtype=float)
    har_coef = fit_har(har_train[list(HAR_COLUMNS)].to_numpy(dtype=float), y_har)
    har_floor = HAR_FLOOR_SHARE * float(y_har.mean())
    rows = frame[validation_mask(frame, spec, h)]
    out = pd.DataFrame(
        {"ts": rows["ts"], "day_idx": rows["day_idx"], "y": rows[f"y_{h}"], "vix_var": rows["vix_var"]}
    )
    for name, fit in fits.items():
        out[name] = fit.predict(rows)
    out["india_vix_raw"] = rows["vix_var"]
    har_x = rows[list(HAR_COLUMNS)].to_numpy(dtype=float)
    har_ok = np.isfinite(har_x).all(axis=1)
    out["har_rv_daily"] = np.nan
    n_clipped = 0
    if har_ok.any():
        pred, n_clipped = predict_har(har_coef, har_x[har_ok], har_floor)
        out.loc[har_ok, "har_rv_daily"] = pred
    out["vol_v1_model"] = np.nan if v1 is None else rows["ts"].map(v1).to_numpy(dtype=float)
    fitted = {
        "n_train": len(train),
        "first_train_ts": train["ts"].iloc[0].isoformat(),
        "last_train_ts": train["ts"].iloc[-1].isoformat(),
        "last_label_end_ts": train[f"end_ts_{h}"].max().isoformat(),
        **{name: fit.to_dict() for name, fit in fits.items()},
        "har_rv_daily": {
            "coef": dict(zip(("intercept", *HAR_COLUMNS), map(float, har_coef), strict=True)),
            "floor": har_floor,
            "n_clipped_validation": n_clipped,
        },
        "n_train_on2_at_floor": int((train["on2"] / ANNUAL < OVERNIGHT_FLOOR**2).sum()),
    }
    return out.reset_index(drop=True), fitted


# --- evaluation -------------------------------------------------------------------------------------------


def qlike_section(scored: pd.DataFrame, h: int, first_day: int, boot) -> tuple[dict, dict]:
    """Per-forecaster QLIKE, and the model's improvement over each (on the rows both have)."""
    per, vs = {}, {}
    y = scored["y"].to_numpy(dtype=float)
    offsets = scored["day_idx"].to_numpy() - first_day
    schemes = {f"{h}d_blocks": offsets // h, f"{LONG_BLOCK_DAYS}d_blocks": offsets // LONG_BLOCK_DAYS}
    model_loss = qlike(y, scored[MODEL])
    for name in FORECASTERS:
        f = scored[name].to_numpy(dtype=float)
        ok = np.isfinite(f) & (f > 0)
        if not ok.any():
            per[name] = {"n": 0, "context_only": name in CONTEXT}
            continue
        loss = qlike(y[ok], f[ok])
        per[name] = {
            "n": int(ok.sum()),
            "qlike": float(loss.mean()),
            "qlike_ci": percentile_ci(
                block_bootstrap_means(loss, offsets[ok] // h, boot.resamples, boot.seed), boot.ci_level
            ),
            "mean_forecast_vol": float((100 * np.sqrt(f[ok])).mean()),
            "mean_realized_vol": float((100 * np.sqrt(y[ok])).mean()),
            "realized_over_forecast_variance": float(y[ok].mean() / f[ok].mean()),
            "context_only": name in CONTEXT,
        }
        if name == MODEL:
            continue
        diff = loss - model_loss[ok]
        vs[name] = {
            "n": int(ok.sum()),
            "improvement": float(diff.mean()),
            "relative_improvement": float(1.0 - model_loss[ok].mean() / loss.mean()),
            **{
                label: _bootstrap_summary(
                    block_bootstrap_means(diff, blocks[ok], boot.resamples, boot.seed), boot.ci_level
                )
                for label, blocks in schemes.items()
            },
            "context_only": name in CONTEXT,
        }
    return per, vs


def _pnl_summary(net: np.ndarray, gross: np.ndarray, position: np.ndarray, periods_per_year: float) -> dict:
    traded = position != 0
    return {
        "n_periods": int(net.size),
        "n_short": int(traded.sum()),
        "n_flat": int((~traded).sum()),
        "mean_net": float(net.mean()) if net.size else None,
        "mean_gross": float(gross.mean()) if net.size else None,
        "total_net": float(net.sum()),
        "std_net": float(net.std(ddof=1)) if net.size > 1 else None,
        "sharpe": sharpe(net, periods_per_year),
        "max_drawdown": max_drawdown(net),
        "worst_period": float(net.min()) if net.size else None,
        "hit_rate_traded": float((net[traded] > 0).mean()) if traded.any() else None,
    }


def conditional_short(vix_var, forecast) -> np.ndarray:
    """1 (short the variance swap) when VIX^2 exceeds the forecast, else 0 (flat)."""
    return (np.asarray(vix_var, float) > np.asarray(forecast, float)).astype(int)


def _pnl(rows: pd.DataFrame, h: int, cost_rate: float):
    K, R, cost = variance_swap_legs(rows["vix_var"], rows["y"], h, cost_rate)
    always = np.ones(len(rows), dtype=int)
    cond = conditional_short(rows["vix_var"], rows[MODEL])
    return strategy_pnl(always, K, R, cost), strategy_pnl(cond, K, R, cost), always, cond, (K, R, cost)


def variance_swap_section(scored: pd.DataFrame, h: int, first_day: int, cost_rate: float, boot) -> dict:
    periods_per_year = ANNUAL / h
    rows = scored[period_starts(scored["day_idx"], first_day, h)]
    (net_a, gross_a), (net_c, gross_c), pos_a, pos_c, (K, R, cost) = _pnl(rows, h, cost_rate)
    n = len(rows)
    s_a, s_c = sharpe(net_a, periods_per_year), sharpe(net_c, periods_per_year)
    point = None if s_a is None or s_c is None else s_c - s_a
    schemes = {
        "1_period_blocks": np.arange(n),
        f"{LONG_BLOCK_DAYS}d_blocks": np.arange(n) // max(1, math.ceil(LONG_BLOCK_DAYS / h)),
    }
    boots = {
        label: _bootstrap_summary(
            block_bootstrap_sharpe_diff(net_c, net_a, blocks, periods_per_year, boot.resamples, boot.seed),
            boot.ci_level,
        )
        for label, blocks in schemes.items()
    }
    phases = []
    for phase in range(h):
        sub = scored[period_starts(scored["day_idx"], first_day, h, phase)]
        (pa, _), (pc, _), *_ = _pnl(sub, h, cost_rate)
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
        "always_short": _pnl_summary(net_a, gross_a, pos_a, periods_per_year),
        "conditional": _pnl_summary(net_c, gross_c, pos_c, periods_per_year),
        "sharpe_diff": point,
        **boots,
        "phase_sharpe_diff": {
            "values": phases,
            "min": float(known.min()) if known.size else None,
            "median": float(np.median(known)) if known.size else None,
            "max": float(known.max()) if known.size else None,
            "share_above_0": float((known > 0).mean()) if known.size else None,
        },
    }


def evaluate_horizon(
    frame: pd.DataFrame, spec: Vol2Spec, h: int, v1: pd.Series | None, boot, cost_rate: float
) -> dict:
    started = time.perf_counter()
    if (frame["ts"] >= spec.holdout_start).any():
        raise PermissionError("the frame holds holdout bars")
    preds, fitted = horizon_forecasts(frame, spec, h, v1)
    in_window = (frame["ts"] >= spec.validation_start) & (frame["ts"] < spec.holdout_start)
    first_day = int(frame.loc[in_window, "day_idx"].min())
    scored = preds[np.isfinite(preds[[MODEL, BASELINE]]).all(axis=1)].reset_index(drop=True)
    per, vs = qlike_section(scored, h, first_day, boot)
    pnl = variance_swap_section(scored, h, first_day, cost_rate, boot)
    primary_schemes = {k: v for k, v in vs[BASELINE].items() if k.endswith("_blocks")}
    pnl_schemes = {k: pnl[k] for k in ("1_period_blocks", f"{LONG_BLOCK_DAYS}d_blocks")}
    gates = {
        "qlike_improvement_vs_recalibrated_vix": {
            "value": vs[BASELINE]["improvement"],
            "by_block_scheme": primary_schemes,
            **_conservative(primary_schemes, spec.qlike_ci_lower_above),
        },
        "conditional_vs_always_short_sharpe": {
            "value": pnl["sharpe_diff"],
            "by_block_scheme": pnl_schemes,
            **_conservative(pnl_schemes, spec.sharpe_ci_lower_above),
        },
    }
    return {
        "horizon": h,
        "n_validation_rows": int(in_window.sum()),
        "n_scored": len(scored),
        "n_blocks": int(np.unique((scored["day_idx"] - first_day) // h).size),
        "first_scored_ts": scored["ts"].iloc[0].isoformat(),
        "last_scored_ts": scored["ts"].iloc[-1].isoformat(),
        "fits": fitted,
        "qlike": per,
        "model_vs": vs,
        "variance_swap": pnl,
        "primary_p": gates["qlike_improvement_vs_recalibrated_vix"]["p_one_sided"],
        "gates": gates,
        "pass_if_met": all(g["pass"] for g in gates.values()),
        "runtime_s": round(time.perf_counter() - started, 1),
        "predictions": scored,
    }


def run(load: CandleLoader | None = None, with_vol_v1: bool = True) -> dict[int, dict]:
    spec = load_spec()
    boot = load_edge_config().common.bootstrap
    daily, bars5m, vix = load_inputs(spec, load)
    frame = build_frame(daily, bars5m, vix, spec.horizons_days)
    cost_rate, _ = option_round_trip()
    out = {}
    for h in spec.horizons_days:
        v1 = vol_v1_forecasts(daily, vix, h, spec.holdout_start) if with_vol_v1 else None
        out[h] = evaluate_horizon(frame, spec, h, v1, boot, cost_rate)
    return out


# --- report -----------------------------------------------------------------------------------------------


def validation_report(results: dict[int, dict], spec: Vol2Spec, runtime_s: float) -> dict:
    edge = load_edge_config()
    boot = edge.common.bootstrap
    cost_rate, cost_parts = option_round_trip()
    family = edge_v2_section("family")
    horizons = {
        str(h): {k: v for k, v in r.items() if k != "predictions"} for h, r in sorted(results.items())
    }
    return {
        "test": spec.name,
        "section": "vol_intraday",
        "period": "validation",
        "generated_at": datetime.now(UTC).isoformat(),
        "edge_search_v2_sha256": edge_v2_sha256(),
        "config_hashes": {**config_hashes(), EDGE_FILE: edge.sha256, EDGE_V2_FILE: edge_v2_sha256()},
        "registration": spec.raw,
        "split": {
            "registered_ist_dates": spec.raw.get("split"),
            "holdout_start_ist": spec.holdout_start.tz_convert("Asia/Kolkata").date().isoformat(),
            "train": [spec.train_start.isoformat(), spec.validation_start.isoformat()],
            "validation": [spec.validation_start.isoformat(), spec.holdout_start.isoformat()],
            "holdout_start": spec.holdout_start.isoformat(),
            "holdout_used": False,
        },
        "definitions": {
            "target": "vol_v1's: RV_h(t) = 252/h * sum_{i=1..h} r_{t+i}^2, r = daily close-to-close "
            "log return",
            "rv5m": "252 * sum of squared 5m log returns inside the IST session (first bar open->close, then "
            f"close->close); sessions with fewer than {MIN_SESSION_BARS} bars have none",
            "har_components": {
                k: f"mean rv5m over the last {n} regular sessions" for k, n in HAR_5M_WINDOWS.items()
            },
            "overnight": "on2 = 252 * g^2, g = log(open_t / close_{t-1}) on daily bars; in the regression "
            f"log(252 * max(g^2, {OVERNIGHT_FLOOR}^2))",
            "vix_var": "(VIX_t / 100)^2, same IST date, no carry-forward",
            "regressors": {k: list(v) for k, v in REGRESSORS.items()},
            "retransformation": "Duan smearing: forecast = exp(x'b) * mean(exp(train residuals)), the same "
            "procedure for every log model",
            "loss": "QLIKE(RV, F) = RV/F - log(RV/F) - 1",
            "variance_swap": "vol_v1's proxy: per non-overlapping h-day period from the first validation "
            "day, "
            "K = 1e4 * VIX^2 * h/252, R = 1e4 * RV_h * h/252, short earns K - R, cost 2K x option round trip",
            "conditional": "short when VIX_t^2 > model forecast, flat otherwise; always_short shorts "
            "every period",
            "context_forecasters": {
                "india_vix_raw": "(VIX/100)^2",
                "vol_v1_model": "vol_v1 LightGBM, its own yearly walk-forward from 2016-01-01 "
                "(num_threads=1)",
                "har_rv_daily": "vol_v1's HAR on daily squared returns (levels), fitted on the vol_v2 "
                "train rows",
                "har_5m_log": "the vol_v2 model without log VIX^2",
            },
        },
        "protocol": {
            "fit": "once on the train window; no refit in validation; train targets end before validation",
            "bootstrap": {
                "kind": boot.kind,
                "resamples": boot.resamples,
                "ci_level": boot.ci_level,
                "seed": boot.seed,
                "p_value": "(1 + #{draws <= 0}) / (1 + B)",
                "qlike_blocks": f"h consecutive trading days and {LONG_BLOCK_DAYS} days; the gate takes the "
                "larger p and needs both CI lower bounds above 0",
                "pnl_blocks": f"one h-day period and ceil({LONG_BLOCK_DAYS}/h) periods; same rule",
            },
        },
        "costs": {"option_round_trip_of_premium": cost_rate, "components": cost_parts},
        "n_hypotheses": len(results),
        "primary_p": {str(h): r["primary_p"] for h, r in sorted(results.items())},
        "family_fdr": {
            "q": family["fdr_q"],
            "status": "pending: Benjamini-Hochberg runs across every primary p in edge_search_v2.yaml",
            "survival_possible": {
                str(h): r["primary_p"] is not None and r["primary_p"] <= family["fdr_q"]
                for h, r in sorted(results.items())
            },
            "note": "a BH q-value is never below its p-value, so p > q means no survival whatever the "
            "other tests show",
        },
        "horizons": horizons,
        "pass_if_met": {str(h): r["pass_if_met"] for h, r in sorted(results.items())},
        "deviations": DEVIATIONS,
        "caveats": [spec.caveat, *CAVEATS],
        "runtime_s": round(runtime_s, 1),
    }


DEVIATIONS = [
    "Block length is not registered: the QLIKE gate uses both h-day blocks (vol_v1's gate) and 60-day blocks "
    "and keeps the more conservative (larger p; both CI lower bounds must be above 0). Same for the P&L "
    "check "
    "(one-period and ceil(60/h)-period blocks).",
    f"log of the overnight squared return needs a floor: |g| is floored at {OVERNIGHT_FLOOR} (1 bp) before "
    "the log; chosen before any fit.",
    "The model and baseline are fitted once on the train window (the split is a fixed train/validation "
    "split); there is no refit inside validation, unlike vol_v1's yearly walk-forward.",
    "The train window starts on the first 5m session (2017-07-17), so the first 21 sessions have no 22-day "
    "HAR component and are not trained on.",
    f"A session with fewer than {MIN_SESSION_BARS} of 75 5m bars (Muhurat, weekend DR drills, the 2021-02-24 "
    "outage) or with no 5m bars (7 Muhurat days) has no rv5m: the HAR windows skip it (means over the last "
    "1/5/22 regular sessions) and that day gets no forecast. The target still includes those days' returns.",
    "The conditional rule is exactly the registered one (short when VIX^2 > forecast, else flat); unlike "
    "vol_v1 there is no margin and no long-volatility leg.",
]

CAVEATS = [
    "Smearing factors are estimated on 2017-07..2020-12 residuals, which include March 2020; the same factor "
    "type scales the model and the recalibrated VIX, so the comparison is like for like, but both can be "
    "biased in validation (see realised / forecast variance per horizon).",
    "The recalibrated VIX is fitted on 2017-07..2020-12, which contains the March 2020 crash; both it and "
    "the model can be miscalibrated in 2021-2025 if the level of the variance risk premium moved.",
    "The variance-swap proxy uses VIX (a 30-calendar-day implied vol) as the strike at both horizons; at "
    "h = 5 that is a horizon mismatch (as in vol_v1).",
    "Option costs: costs.yaml has no option slippage, so the default 0.05% per side applies; delta-hedging, "
    "margin and real quotes are not modelled.",
    "Short variance has no loss cap; the sample's worst period is not the worst possible.",
    "Validation (2021-01..2025-09) had no crash of March-2020 size; a calm sample flatters short-vol P&L.",
]


def _fmt(x, digits: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _fmt_ci(ci, digits: int = 4) -> str:
    return "n/a" if not ci else f"[{ci[0]:+.{digits}f}, {ci[1]:+.{digits}f}]"


def markdown_summary(report: dict) -> str:
    split = report["split"]["registered_ist_dates"] or {}
    train, validation = split.get("train", ["?", "?"]), split.get("validation", ["?", "?"])
    lines = [
        f"# {report['test']} validation: log-HAR on 5m realised variance + VIX vs a recalibrated VIX",
        "",
        f"Generated {report['generated_at'][:19]}Z from edge_search_v2.yaml sha256 "
        f"`{report['edge_search_v2_sha256'][:12]}`. Train {train[0]} to {train[1]}, "
        f"validation {validation[0]} "
        f"to {report['split']['holdout_start_ist']} (IST dates; holdout untouched). Fitted once on train.",
        "",
        "A horizon passes only if both registered checks pass AND its primary p survives Benjamini-Hochberg "
        f"at q = {report['family_fdr']['q']} across the whole v2 family (pending the other v2 tests).",
        "",
    ]
    for h, e in report["horizons"].items():
        g = e["gates"]
        q, s = g["qlike_improvement_vs_recalibrated_vix"], g["conditional_vs_always_short_sharpe"]
        lines += [
            f"## {h}-day horizon: pass_if {'MET' if e['pass_if_met'] else 'NOT MET'}",
            "",
            f"n_scored {e['n_scored']} days ({e['n_blocks']} {h}-day blocks), "
            f"n_train {e['fits']['n_train']}.",
            "",
            "| forecaster | n | QLIKE | mean forecast vol | model improvement | 95% CI (h-day blocks) | "
            "95% CI (60d blocks) |",
            "|---|---|---|---|---|---|---|",
        ]
        for name, m in e["qlike"].items():
            if not m.get("n"):
                continue
            vs = e["model_vs"].get(name)
            label = f"{name} (context)" if m["context_only"] else name
            lines.append(
                f"| {label} | {m['n']} | {_fmt(m['qlike'])} | {_fmt(m['mean_forecast_vol'], 2)} | "
                f"{_fmt(vs['improvement']) if vs else '-'} | "
                f"{_fmt_ci(vs[f'{h}d_blocks']['ci']) if vs else '-'} | "
                f"{_fmt_ci(vs['60d_blocks']['ci']) if vs else '-'} |"
            )
        pnl = e["variance_swap"]
        a, c = pnl["always_short"], pnl["conditional"]
        ratio = ", ".join(
            f"{k} {m['realized_over_forecast_variance']:.2f}"
            for k, m in e["qlike"].items()
            if m.get("n") and k in (MODEL, BASELINE, "india_vix_raw")
        )
        lines += [
            "",
            f"Mean realised / mean forecast variance (1 = unbiased): {ratio}.",
            "",
            f"Primary: QLIKE improvement over recalibrated VIX {_fmt(q['value'])}, one-sided p "
            f"{_fmt(q['p_one_sided'], 4)} -> {'pass' if q['pass'] else 'FAIL'}.",
            "",
            f"Variance-swap proxy, {pnl['n_periods']} periods: always short Sharpe {_fmt(a['sharpe'], 3)} "
            f"(max drawdown {_fmt(a['max_drawdown'], 1)}); conditional short {c['n_short']}/{c['n_periods']} "
            f"periods, Sharpe {_fmt(c['sharpe'], 3)} (max drawdown {_fmt(c['max_drawdown'], 1)}). Difference "
            f"{_fmt(s['value'], 3)}, CI {_fmt_ci(pnl['1_period_blocks']['ci'], 3)} (1-period blocks), "
            f"{_fmt_ci(pnl['60d_blocks']['ci'], 3)} (60d blocks) -> {'pass' if s['pass'] else 'FAIL'}.",
            "",
        ]
    lines += ["Deviations (chosen before any result):", *[f"- {d}" for d in report["deviations"]], ""]
    lines += ["Caveats:", *[f"- {c}" for c in report["caveats"]], ""]
    return "\n".join(lines)


def write_report(report: dict, json_path: Path | None = None) -> tuple[Path, Path]:
    json_path = json_path or report_path()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path = json_path.with_suffix(".md")
    md_path.write_text(markdown_summary(report), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="vol_v2 validation (never the holdout)")
    parser.parse_args(argv)
    started = time.perf_counter()
    results = run()
    report = validation_report(results, load_spec(), time.perf_counter() - started)
    write_report(report)
    save_predictions(results)
    for h, e in report["horizons"].items():
        g = e["gates"]
        print(
            f"h={h}: n={e['n_scored']} "
            f"qlike_vs_recal={_fmt(g['qlike_improvement_vs_recalibrated_vix']['value'])} "
            f"p={_fmt(e['primary_p'])} "
            f"sharpe_diff={_fmt(g['conditional_vs_always_short_sharpe']['value'], 3)} "
            f"pass_if={e['pass_if_met']}"
        )
    print(f"-> {report_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
