"""vrp_v1 validation (config/edge_search_v2.yaml `vrp_direction`): does the variance risk premium predict
NIFTY 50 log returns 21 and 63 sessions ahead better than the expanding historical mean?

Forecasts come from candly.research.vrp_model (yearly expanding refits from the validation start).
Scored rows: validation rows with a vrp and a target that ends before the holdout.
- Primary per horizon: Clark & West (2007) test of equal MSPE for nested models,
  f_t = (y - b)^2 - [(y - m)^2 - (b - m)^2] with b the benchmark and m the model forecast; t = mean(f) /
  its Newey-West (Bartlett, h lags) standard error; one-sided p = 1 - Phi(t).
- OOS R^2 = 1 - sum (y - m)^2 / sum (y - b)^2, with a date-block (h-day blocks) bootstrap CI.

Timing rule (registered: long the NIFTY futures proxy when the forecast exceeds the round-trip cost, else
flat), daily:
- decision d_t = 1 if the model's forecast at t's close > the index-futures round trip (costs.yaml), else
  0; a day without a forecast (no VIX print) keeps the previous decision;
- executed at the next close (one-day lag, since t's close is only known once the market has shut), so
  the position over day s is d_{s-2};
- futures proxy return on day s: close-to-close spot return minus 5% a year carry, accrued per calendar
  day (edge_search_v2.yaml tsmom.index_futures_return);
- costs: half a round trip per position change, and one roll (a full round trip) per 21 sessions held,
  charged pro rata per day held;
- buy_and_hold: long every evaluated day, same carry and roll costs, half a round trip to enter.
Check: annualised Sharpe of daily net returns, timing > buy-and-hold (point, as registered); a paired
h-day-block bootstrap CI of the difference is reported, not gated.
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
from scipy import stats as sps

from candly.core.settings import REPO_ROOT, get_settings
from candly.research.config import config_hashes
from candly.research.costs import cost_breakdown
from candly.research.data import CandleLoader
from candly.research.edge_config import EDGE_FILE, load_edge_config
from candly.research.edge_v2_config import EDGE_V2_FILE, edge_v2_section, edge_v2_sha256
from candly.research.vol2_eval import (
    block_bootstrap_sharpe_diff,
    p_one_sided,
)
from candly.research.vol2_model import newey_west_cov
from candly.research.vol_eval import max_drawdown, percentile_ci, sharpe
from candly.research.vrp_model import (
    MONTHS_PER_YEAR,
    RV_SESSIONS,
    VrpSpec,
    build_frame,
    load_inputs,
    load_spec,
    walk_forward,
)

REPORT_STEM = "2026-09-24-vrp-v1-validation"
ANNUAL = 252
FUTURES_CARRY = 0.05  # a year, edge_search_v2.yaml tsmom.index_futures_return
CARRY_DAY_COUNT = 365
ROLL_SESSIONS = 21
EXECUTION_LAG = 1
COST_KIND, COST_HOLDING = "index", "multi_day"


def predictions_dir() -> Path:
    return get_settings().data_dir / "models" / "vrp_v1"


def save_predictions(results: dict[int, dict]) -> Path:
    out = predictions_dir()
    out.mkdir(parents=True, exist_ok=True)
    for h, r in results.items():
        r["predictions"].to_parquet(out / f"validation_predictions_h{h}.parquet", index=False)
    return out


def report_path() -> Path:
    return REPO_ROOT / "docs" / "test-reports" / f"{REPORT_STEM}.json"


def futures_round_trip() -> tuple[float, dict[str, float]]:
    parts = cost_breakdown(COST_KIND, COST_HOLDING)
    return float(sum(parts.values())), parts


# --- statistics -------------------------------------------------------------------------------------------


def clark_west(y, benchmark, model, lags: int) -> dict:
    y, b, m = (np.asarray(x, float) for x in (y, benchmark, model))
    f = (y - b) ** 2 - ((y - m) ** 2 - (b - m) ** 2)
    n = f.size
    mean = float(f.mean())
    var = float(newey_west_cov(np.ones((n, 1)), f - mean, lags)[0, 0])
    se = math.sqrt(var) if var > 0 else float("nan")
    t = mean / se if se > 0 else float("nan")
    return {
        "n": n,
        "lags": lags,
        "mean_adjusted_diff": mean,
        "hac_se": se,
        "t": t,
        "p_one_sided": float(sps.norm.sf(t)) if math.isfinite(t) else None,
    }


def oos_r2(y, benchmark, model) -> float:
    y, b, m = (np.asarray(x, float) for x in (y, benchmark, model))
    return float(1.0 - ((y - m) ** 2).sum() / ((y - b) ** 2).sum())


def bootstrap_oos_r2(y, benchmark, model, blocks, resamples: int, seed: int) -> np.ndarray:
    y, b, m = (np.asarray(x, float) for x in (y, benchmark, model))
    _, inverse = np.unique(np.asarray(blocks), return_inverse=True)
    sm, sb = np.bincount(inverse, weights=(y - m) ** 2), np.bincount(inverse, weights=(y - b) ** 2)
    k = sm.size
    counts = np.random.default_rng(seed).multinomial(k, np.full(k, 1.0 / k), size=resamples)
    with np.errstate(divide="ignore", invalid="ignore"):
        r2 = 1.0 - (counts @ sm) / (counts @ sb)
    return r2[np.isfinite(r2)]


# --- timing rule ------------------------------------------------------------------------------------------


def futures_returns(frame: pd.DataFrame, carry: float = FUTURES_CARRY) -> pd.Series:
    """Daily futures-proxy return: spot close-to-close minus the carry accrued over the calendar days."""
    close = frame["close"].astype("float64")
    days = frame["ts"].diff().dt.total_seconds() / 86_400.0
    return close.pct_change() - carry * days.round() / CARRY_DAY_COUNT


def decisions(frame_len: int, rows_idx, forecast, threshold: float) -> np.ndarray:
    """Per daily row: 1 long, 0 flat, from the forecast on that row; rows without one keep the last."""
    d = pd.Series(np.nan, index=range(frame_len))
    d.iloc[np.asarray(rows_idx)] = (np.asarray(forecast, float) > threshold).astype(float)
    return d.ffill().fillna(0.0).to_numpy()


def strategy_returns(position: np.ndarray, ret: np.ndarray, rt: float, entry_state: float = 0.0) -> dict:
    """Net and gross daily returns for a 0/1 position series; costs per the module docstring."""
    position, ret = np.asarray(position, float), np.asarray(ret, float)
    prev = np.r_[entry_state, position[:-1]]
    switch = 0.5 * rt * np.abs(position - prev)
    roll = rt / ROLL_SESSIONS * position
    gross = position * ret
    return {"net": gross - switch - roll, "gross": gross, "switch_cost": switch, "roll_cost": roll}


def _strategy_summary(series: dict, position: np.ndarray) -> dict:
    net = series["net"]
    return {
        "n_days": int(net.size),
        "share_long": float(position.mean()),
        "n_switches": int((np.abs(np.diff(np.r_[0.0, position])) > 0).sum()),
        "annual_return_net": float(net.mean() * ANNUAL),
        "annual_vol": float(net.std(ddof=1) * math.sqrt(ANNUAL)),
        "sharpe_net": sharpe(net, ANNUAL),
        "sharpe_gross": sharpe(series["gross"], ANNUAL),
        "total_cost": float((series["switch_cost"] + series["roll_cost"]).sum()),
        "max_drawdown": max_drawdown(net),
    }


def timing_section(frame: pd.DataFrame, preds: pd.DataFrame, spec: VrpSpec, h: int, rt: float, boot) -> dict:
    ret = futures_returns(frame).to_numpy()
    pos_of_row = pd.Series(np.arange(len(frame)), index=frame["day_idx"].to_numpy())
    rows_idx = pos_of_row.loc[preds["day_idx"].to_numpy()].to_numpy()
    first = int(rows_idx.min())
    evaluated = np.arange(first + 1 + EXECUTION_LAG, len(frame))
    thresholds = {"registered": rt, "carry_adjusted_diagnostic": rt + FUTURES_CARRY * h / ANNUAL}
    out = {
        "first_day": frame["ts"].iloc[evaluated[0]].isoformat(),
        "last_day": frame["ts"].iloc[evaluated[-1]].isoformat(),
        "execution_lag_days": EXECUTION_LAG,
    }
    hold = np.ones(evaluated.size)
    bh = strategy_returns(hold, ret[evaluated], rt)
    out["buy_and_hold"] = _strategy_summary(bh, hold)
    blocks = np.arange(evaluated.size) // h
    for label, threshold in thresholds.items():
        d = decisions(len(frame), rows_idx, preds["forecast"], threshold)
        position = d[evaluated - 1 - EXECUTION_LAG]
        timing = strategy_returns(position, ret[evaluated], rt)
        summary = _strategy_summary(timing, position)
        s_t, s_b = summary["sharpe_net"], out["buy_and_hold"]["sharpe_net"]
        diff = None if s_t is None or s_b is None else s_t - s_b
        samples = block_bootstrap_sharpe_diff(
            timing["net"], bh["net"], blocks, ANNUAL, boot.resamples, boot.seed
        )
        out[label] = {
            "threshold": threshold,
            **summary,
            "sharpe_diff_vs_buy_and_hold": diff,
            "sharpe_diff_ci_h_day_blocks": percentile_ci(samples, boot.ci_level),
            "sharpe_diff_p_one_sided": p_one_sided(samples),
            "beats_buy_and_hold_point": diff is not None and diff > 0,
        }
    return out


# --- evaluation -------------------------------------------------------------------------------------------


def evaluate_horizon(frame: pd.DataFrame, spec: VrpSpec, h: int, boot, rt: float) -> dict:
    started = time.perf_counter()
    if (frame["ts"] >= spec.holdout_start).any():
        raise PermissionError("the frame holds holdout bars")
    preds, folds = walk_forward(frame, spec, h)
    resolved = preds["y"].notna() & (preds["end_ts"] < spec.holdout_start)
    scored = preds[resolved].reset_index(drop=True)
    y, b, m = scored["y"], scored["benchmark"], scored["forecast"]
    cw = clark_west(y, b, m, h)
    blocks = (scored["day_idx"] - scored["day_idx"].min()) // h
    r2_samples = bootstrap_oos_r2(y, b, m, blocks, boot.resamples, boot.seed)
    timing = timing_section(frame, preds, spec, h, rt, boot)
    cw_pass = cw["p_one_sided"] is not None and cw["p_one_sided"] < spec.clark_west_p_below
    timing_pass = timing["registered"]["beats_buy_and_hold_point"]
    return {
        "horizon": h,
        "n_forecasts": len(preds),
        "n_scored": len(scored),
        "n_blocks": int(np.unique(blocks).size),
        "first_scored_ts": scored["ts"].iloc[0].isoformat(),
        "last_scored_ts": scored["ts"].iloc[-1].isoformat(),
        "folds": folds,
        "clark_west": cw,
        "oos_r2": oos_r2(y, b, m),
        "oos_r2_ci_h_day_blocks": percentile_ci(r2_samples, boot.ci_level),
        "oos_r2_by_fold": {
            str(k): oos_r2(g["y"], g["benchmark"], g["forecast"]) for k, g in scored.groupby("fold")
        },
        "mse_model": float(((y - m) ** 2).mean()),
        "mse_benchmark": float(((y - b) ** 2).mean()),
        "mean_target": float(y.mean()),
        "mean_forecast": float(m.mean()),
        "mean_benchmark": float(b.mean()),
        "forecast_target_correlation": float(np.corrcoef(m, y)[0, 1]),
        "timing": timing,
        "primary_p": cw["p_one_sided"],
        "checks": {
            "clark_west_p_below": {
                "value": cw["p_one_sided"],
                "below": spec.clark_west_p_below,
                "pass": cw_pass,
            },
            "timing_sharpe_after_cost_beats_buy_and_hold_point": {
                "value": timing["registered"]["sharpe_diff_vs_buy_and_hold"],
                "pass": timing_pass,
            },
        },
        "pass_if_met": bool(cw_pass and timing_pass),
        "runtime_s": round(time.perf_counter() - started, 1),
        "predictions": preds,
    }


def input_summary(frame: pd.DataFrame, spec: VrpSpec) -> dict:
    out = {}
    for label, lo, hi in (
        ("train", spec.train_start, spec.validation_start),
        ("validation", spec.validation_start, spec.holdout_start),
    ):
        v = frame.loc[(frame["ts"] >= lo) & (frame["ts"] < hi), "vrp"].dropna()
        out[label] = {
            "n": int(v.size),
            "mean_vrp": float(v.mean()),
            "std_vrp": float(v.std(ddof=1)),
            "share_negative": float((v < 0).mean()),
        }
    return out


def run(load: CandleLoader | None = None) -> tuple[dict[int, dict], dict]:
    spec = load_spec()
    boot = load_edge_config().common.bootstrap
    daily, vix = load_inputs(spec, load)
    frame = build_frame(daily, vix, spec.horizons_days)
    rt, _ = futures_round_trip()
    results = {h: evaluate_horizon(frame, spec, h, boot, rt) for h in spec.horizons_days}
    return results, input_summary(frame, spec)


# --- report -----------------------------------------------------------------------------------------------


DEVIATIONS = [
    "VIX^2 in monthly units is (VIX/100)^2 / 12 (BTZ's convention), not x 22/252 or x 30/365; chosen before "
    "any fit.",
    "Clark-West p uses the standard normal for the HAC t statistic (as Clark and West tabulate).",
    "Execution: a decision made at t's close is traded at t+1's close (one-day lag), the conservative "
    "reading of a rule that uses t's closing VIX and NIFTY.",
    "The rule compares the forecast of the SPOT log return with the round trip, as registered; it ignores "
    "the "
    "~5%/yr carry a futures holder pays. A carry-adjusted threshold is reported as a diagnostic only.",
    "Futures rolls: one full round trip per 21 sessions held, charged pro rata per day (tsmom's 'one roll "
    "per "
    "month'); the same for buy-and-hold.",
    "A day without a VIX print keeps the previous decision (no forecast can be made that day).",
    "The OOS R^2 CI (h-day blocks) is reported for context; the registered primary is the Clark-West p.",
]

CAVEATS = [
    "NIFTY 50 here is the price index; dividends (~1-1.5%/yr) are not in the target or in buy-and-hold. The "
    "futures proxy's 5% carry stands in for (interest - dividend yield), a constant that ignores rate "
    "changes.",
    "One index, ~9.7 years of validation: 63-day targets give only ~38 non-overlapping observations, so the "
    "test has little power at h = 63.",
    "Buy-and-hold beat-or-not is a point comparison as registered; its bootstrap CI is wide.",
    "VIX methodology and the NIFTY option market changed over 2009-2025 (weekly options, retail flows); a "
    "constant VRP slope is a strong assumption.",
]


def validation_report(results: dict[int, dict], inputs: dict, spec: VrpSpec, runtime_s: float) -> dict:
    edge = load_edge_config()
    boot = edge.common.bootstrap
    rt, parts = futures_round_trip()
    family = edge_v2_section("family")
    horizons = {
        str(h): {k: v for k, v in r.items() if k != "predictions"} for h, r in sorted(results.items())
    }
    return {
        "test": spec.name,
        "section": "vrp_direction",
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
            "vrp": f"(VIX_t/100)^2 / {MONTHS_PER_YEAR} - sum of daily log-return^2 over the last "
            f"{RV_SESSIONS} "
            "sessions (monthly variance units)",
            "target": "log(close_{t+h} / close_t), NIFTY 50 spot",
            "model": "OLS y_h = a + b vrp, expanding window from the train start, refitted every IST "
            "calendar "
            "year from the validation start; training targets end before the fold",
            "benchmark": "mean of y_h over the same training rows",
            "clark_west": "f = (y-b)^2 - [(y-m)^2 - (b-m)^2]; t = mean(f)/NW se (Bartlett, h lags); "
            "p = 1-Phi(t)",
            "timing": "long futures proxy over day s if the forecast at s-2 exceeded the round trip, "
            "else flat",
            "futures_proxy": f"spot return - {FUTURES_CARRY} x calendar days / {CARRY_DAY_COUNT}",
            "max_drawdown": "largest fall of the running sum of daily net returns (additive, not compounded)",
        },
        "protocol": {
            "bootstrap": {
                "kind": boot.kind,
                "resamples": boot.resamples,
                "ci_level": boot.ci_level,
                "seed": boot.seed,
                "blocks": "h consecutive trading days",
                "p_value": "(1 + #{draws <= 0}) / (1 + B)",
            }
        },
        "costs": {
            "futures_round_trip": rt,
            "components": parts,
            "roll": f"one round trip per {ROLL_SESSIONS} sessions held",
        },
        "inputs": inputs,
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
        "caveats": CAVEATS,
        "runtime_s": round(runtime_s, 1),
    }


def _fmt(x, digits: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _fmt_ci(ci, digits: int = 4) -> str:
    return "n/a" if not ci else f"[{ci[0]:+.{digits}f}, {ci[1]:+.{digits}f}]"


def markdown_summary(report: dict) -> str:
    split = report["split"]["registered_ist_dates"] or {}
    train, validation = split.get("train", ["?", "?"]), split.get("validation", ["?", "?"])
    lines = [
        f"# {report['test']} validation: does the variance risk premium predict NIFTY 50 returns?",
        "",
        f"Generated {report['generated_at'][:19]}Z from edge_search_v2.yaml sha256 "
        f"`{report['edge_search_v2_sha256'][:12]}`. Train {train[0]} to {train[1]}, "
        f"validation {validation[0]} "
        f"to {report['split']['holdout_start_ist']} (IST dates) with yearly expanding refits "
        "(holdout untouched).",
        "",
        "A horizon passes only if Clark-West p < 0.05, the timing rule's after-cost Sharpe beats "
        "buy-and-hold, AND its primary p survives Benjamini-Hochberg at "
        f"q = {report['family_fdr']['q']} across the v2 family "
        "(pending the other v2 tests).",
        "",
        f"Round trip (index futures, costs.yaml): {report['costs']['futures_round_trip']:.5f}.",
        "",
    ]
    for h, e in report["horizons"].items():
        cw, t = e["clark_west"], e["timing"]
        reg, bh = t["registered"], t["buy_and_hold"]
        lines += [
            f"## {h}-day horizon: pass_if {'MET' if e['pass_if_met'] else 'NOT MET'}",
            "",
            f"n_scored {e['n_scored']} days ({e['n_blocks']} {h}-day blocks), "
            f"{len(e['folds'])} yearly folds.",
            "",
            f"- Clark-West t {_fmt(cw['t'], 3)}, one-sided p {_fmt(cw['p_one_sided'], 4)} "
            f"({'pass' if e['checks']['clark_west_p_below']['pass'] else 'FAIL'} at < 0.05).",
            f"- OOS R^2 {_fmt(e['oos_r2'], 4)}, 95% CI {_fmt_ci(e['oos_r2_ci_h_day_blocks'], 4)}; by yearly "
            f"fold: {', '.join(_fmt(v, 3) for v in e['oos_r2_by_fold'].values())}.",
            f"- Slope on vrp by fold: {', '.join(_fmt(f['slope'], 2) for f in e['folds'])} "
            f"(HAC t in the first fold {_fmt(e['folds'][0]['slope_hac_t'], 2)}).",
            f"- Timing: long {reg['share_long']:.0%} of days, {reg['n_switches']} switches, "
            "Sharpe after cost "
            f"{_fmt(reg['sharpe_net'], 3)} vs buy-and-hold {_fmt(bh['sharpe_net'], 3)}; difference "
            f"{_fmt(reg['sharpe_diff_vs_buy_and_hold'], 3)}, "
            f"CI {_fmt_ci(reg['sharpe_diff_ci_h_day_blocks'], 3)} "
            f"({'pass' if reg['beats_buy_and_hold_point'] else 'FAIL'}).",
            "- Diagnostic only, carry-adjusted threshold: Sharpe "
            f"{_fmt(t['carry_adjusted_diagnostic']['sharpe_net'], 3)}"
            f", long {t['carry_adjusted_diagnostic']['share_long']:.0%} of days.",
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
    parser = argparse.ArgumentParser(description="vrp_v1 validation (never the holdout)")
    parser.parse_args(argv)
    started = time.perf_counter()
    results, inputs = run()
    report = validation_report(results, inputs, load_spec(), time.perf_counter() - started)
    write_report(report)
    save_predictions(results)
    for h, e in report["horizons"].items():
        print(
            f"h={h}: n={e['n_scored']} cw_t={_fmt(e['clark_west']['t'], 3)} p={_fmt(e['primary_p'])} "
            f"oos_r2={_fmt(e['oos_r2'])} timing_sharpe_diff="
            f"{_fmt(e['timing']['registered']['sharpe_diff_vs_buy_and_hold'], 3)} pass_if={e['pass_if_met']}"
        )
    print(f"-> {report_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
