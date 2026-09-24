"""range_v1 evaluation against its baselines, and go/no-go #2 for range (config/pivot.yaml range_model).

Everything is in ATR(14) units of the reference bar, from the reference close, per (forecast, step).

Methods, all built only from information available at the reference bar:
- range_v1: walk-forward predictions (research.range_model), or the production model on the holdout.
- atr_bands: close +- k_s ATR, with k_s the 80% quantile of |close_s| on the same training rows as each
  range_v1 fit (so it is refitted every window, like the model); its p50 is the reference close, and its
  high/low quantiles are the training rows' unconditional quantiles.
- rolling_quantiles: per instrument, the quantiles of the last ROLLING_WINDOW realised targets that had
  closed by the reference bar (target of bar t-s, for step s).
- analog_v1: the bands analog_v1 draws (same analogs, same pooled fallback, same "no bands below
  abstain.min_analogs" rule), recomputed from causal pattern/context series computed once per series
  instead of one make_forecast call per bar. 1D replays every bar; intraday replays a seeded uniform
  sample of ANALOG_SAMPLE bars per instrument, because each intraday replay scans ~150k bars of history.
  analog_v1 predicts close quantiles only, so its high/low pinball is not scored.

Metrics: coverage of the p10-p90 close band, Winkler interval score (alpha from the band), pinball loss
(close, and all three targets), range IoU of the ghost candle's [low, high] against the actual bar's,
close MAE, and the pivot.yaml candle accuracy categories:
- wrong: the close is outside the p10-p90 band (it takes precedence, so same + close = coverage);
- same: |close - p50| <= candle_accuracy.same ATR, and the bar's high and low stay inside the ghost
  candle's [low, high] (the "predicted range box" drawn on the chart: the p50 high and low);
- close: every other close inside the band.

Gate (range_model.go_no_go_2, NSE slice of research.yaml go_no_go_1: equities and indices, no INDIAVIX):
coverage within the band, and the Winkler improvement 1 - W(range_v1) / W(baseline) against the best
baseline (the one range_v1 improves on least, each compared on the forecasts both made) at least the
minimum, with the lower end of its date-clustered bootstrap CI above the bar. Whole IST dates are
resampled, every instrument's forecasts of a date together.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from candly.core.calendar import IST
from candly.core.instruments import exchange_of, get_instrument
from candly.core.settings import REPO_ROOT
from candly.core.timeframes import is_intraday, validate_tf
from candly.features.context import compute_context
from candly.forecast.analog import _pooled_paths, ghost_path
from candly.patterns import detect_patterns
from candly.research.config import load_research_config
from candly.research.data import CandleLoader
from candly.research.labels import forward_paths
from candly.research.pivot_config import PivotConfig, load_pivot_config, pivot_config_hashes
from candly.research.range_model import (
    MODEL_NAME,
    Panel,
    WalkForwardResult,
    build_panel,
    fold_windows,
    ghost_ohlc,
    load_model,
    load_walk_forward,
    panel_fingerprint,
    research_loader,
    save_walk_forward,
    train_rows,
    training_settings,
    walk_forward,
)
from candly.research.scorecard import clear_scorecard_cache, load_scorecard
from candly.research.stats import clustered_bootstrap_skill_ci

Period = Literal["validation", "holdout"]
ROLLING_WINDOW = 250
ANALOG_SAMPLE = 2000
BOOTSTRAP_RESAMPLES = 2000
SEED = 20260924
REPORT_STEM = "2026-09-24-range-v1-validation"


@dataclass
class MethodOutput:
    """Predictions of one method on panel rows: quantiles (n, steps, 3 targets, 3 q), NaN where the
    method has none, and ghost candles (n, steps, 4: open, high, low, close)."""

    rows: np.ndarray
    pred: np.ndarray
    ghost: np.ndarray
    extra: dict | None = None


# ---------------------------------------------------------------- baselines


def _atr_band_fit(panel: Panel, train: np.ndarray, quantiles: tuple[float, ...]) -> np.ndarray:
    """(steps, 3, 3) band for one training set: close +- k_s with k_s the |close_s| quantile of the band's
    width, unconditional high/low quantiles."""
    Y = panel.Y[train].astype(np.float64)
    lo_q, hi_q = quantiles[0], quantiles[-1]
    out = np.empty((Y.shape[1], 3, len(quantiles)))
    out[:, :2, :] = np.moveaxis(np.quantile(Y[:, :, :2], quantiles, axis=0), 0, -1)
    k = np.quantile(np.abs(Y[:, :, 2]), hi_q - lo_q, axis=0)
    out[:, 2, :] = np.column_stack([-k, np.zeros_like(k), k])
    return out


def atr_bands_output(
    panel: Panel,
    rows: np.ndarray,
    fold: np.ndarray,
    cutoffs: dict[int, pd.Timestamp],
    gap: int,
    quantiles: tuple[float, ...],
) -> MethodOutput:
    pred = np.full((len(rows), panel.Y.shape[1], 3, len(quantiles)), np.nan)
    ks = {}
    for k, cutoff in cutoffs.items():
        sel = fold == k
        if not sel.any():
            continue
        band = _atr_band_fit(panel, train_rows(panel, cutoff, gap), quantiles)
        pred[sel] = band
        ks[str(k)] = [round(float(x), 4) for x in band[:, 2, -1]]
    return MethodOutput(rows, pred, ghost_ohlc(pred), {"k_by_fold": ks})


def rolling_quantiles_output(panel: Panel, rows: np.ndarray, quantiles: tuple[float, ...]) -> MethodOutput:
    steps = panel.Y.shape[1]
    pred = np.full((len(rows), steps, 3, len(quantiles)), np.nan)
    for code in range(len(panel.instruments)):
        block = panel.block(code)
        in_block = (rows >= block.start) & (rows < block.stop)
        if not in_block.any():
            continue
        local = rows[in_block] - block.start
        Y = panel.Y[block].astype(np.float64)
        Y[~panel.valid[block]] = np.nan
        for s in range(1, steps + 1):
            for j in range(3):
                known = pd.Series(Y[:, s - 1, j]).shift(s)  # the target of bar t-s has closed by bar t
                roll = known.rolling(ROLLING_WINDOW, min_periods=ROLLING_WINDOW)
                for qi, q in enumerate(quantiles):
                    pred[in_block, s - 1, j, qi] = roll.quantile(q).to_numpy()[local]
    pred = np.sort(pred, axis=-1)
    return MethodOutput(rows, pred, ghost_ohlc(pred), {"window_bars": ROLLING_WINDOW})


# ---------------------------------------------------------------- analog_v1 replay


def analog_bands_for_series(
    instrument_id: str,
    tf: str,
    df: pd.DataFrame,
    positions: np.ndarray,
    steps: int,
    scorecard=None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """analog_v1's close bands (len, steps, 3) and ghost candles (len, steps, 4), in ATR units, for the
    bars at `positions` of `df`, and whether it drew bands at all (analog_v1 abstains without bands
    below abstain.min_analogs). Equal to make_forecast(df[:i+1], check_stale=False).bands for each i:
    context and patterns are causal, so computing them once on the full series changes no past value."""
    cfg = load_research_config()
    exchange = exchange_of(instrument_id)
    ctx = compute_context(df, tf, exchange, instrument_id=instrument_id)
    found = detect_patterns(df, tf)
    n = len(df)
    atr = ctx["atr14"].to_numpy(dtype=float)
    bucket = ctx[cfg.analog_bucket].to_numpy(dtype=object)
    paths = forward_paths(df, ctx["atr14"], steps)
    pos_of = pd.Index(df["ts"]).get_indexer(found["ts"])
    pattern_masks: dict[str, np.ndarray] = {}
    for name, idx in pd.Series(pos_of).groupby(found["pattern"].to_numpy()):
        mask = np.zeros(n, dtype=bool)
        mask[idx.to_numpy()] = True
        pattern_masks[name] = mask
    active_at = found.groupby(pos_of)["pattern"].agg(list)
    ok_atr = atr > 0
    bucket_masks = {b: (bucket == b) & ok_atr for b in pd.unique(bucket[pd.notna(bucket)])}
    bands = np.full((len(positions), steps, 3), np.nan)
    ghosts = np.full((len(positions), steps, 4), np.nan)
    drew = np.zeros(len(positions), dtype=bool)
    for k, i in enumerate(positions):
        value = bucket[i]
        if not (atr[i] > 0) or not isinstance(value, str) or i < steps:
            continue
        limit = i - steps + 1
        eligible = bucket_masks[value][:limit].copy()
        active = active_at.get(i, [])
        if active:
            eligible &= np.logical_or.reduce([pattern_masks[p][:limit] for p in active])
        sel = paths[:limit][eligible]
        if len(sel) < cfg.min_analogs and active:
            pooled = _pooled_paths(
                scorecard, instrument_id, list(active), cfg.analog_bucket, value, df["ts"].iloc[i], steps
            )
            if len(pooled):
                sel = np.concatenate([sel, pooled])
        if len(sel) < cfg.min_analogs:
            continue
        drew[k] = True
        bands[k] = np.quantile(sel[:, :, 3], cfg.bands, axis=0).T
        ghosts[k] = ghost_path(sel)
    return bands, ghosts, drew


class PooledLibrary:
    """The scorecard's pooled analog library (analog_v1's fallback below abstain.min_analogs), read only
    when a replayed bar needs it: intraday series nearly always have enough analogs of their own, and
    the 5m libraries are hundreds of MB."""

    def __init__(self, tf: str, exchange: str):
        self.tf, self.exchange, self.used, self._card = tf, exchange, False, None

    @property
    def analogs(self) -> pd.DataFrame | None:
        if not self.used:
            self._card, self.used = load_scorecard(self.tf, self.exchange), True
        return None if self._card is None else self._card.analogs


def analog_output(
    panel: Panel,
    rows: np.ndarray,
    get: Callable[[str, str], pd.DataFrame],
    pivot: PivotConfig,
    sample: int | None,
    seed: int = SEED,
) -> MethodOutput:
    steps = panel.Y.shape[1]
    card = PooledLibrary(panel.tf, panel.exchange)
    rng = np.random.default_rng(seed)
    out_rows, out_pred, out_ghost = [], [], []
    n_replayed = 0
    for code, iid in enumerate(panel.instruments):
        block = panel.block(code)
        mine = rows[(rows >= block.start) & (rows < block.stop)]
        if sample is not None and len(mine) > sample:
            mine = np.sort(rng.choice(mine, sample, replace=False))
        if not len(mine):
            continue
        df = get(iid, panel.tf)
        if panel.tf == "1D":
            df = df[df["ts"] >= pivot.daily_start_utc].reset_index(drop=True)
        same_bars = len(df) == block.stop - block.start and bool(
            (pd.DatetimeIndex(df["ts"]) == panel.ts[block]).all()
        )
        if not same_bars:
            raise ValueError(f"{iid} {panel.tf}: candles changed since the panel was built")
        bands, ghosts, drew = analog_bands_for_series(iid, panel.tf, df, mine - block.start, steps, card)
        n_replayed += len(mine)
        pred = np.full((len(mine), steps, 3, 3), np.nan)
        pred[:, :, 2, :] = bands
        out_rows.append(mine[drew])
        out_pred.append(pred[drew])
        out_ghost.append(ghosts[drew])
    if not out_rows:
        empty = np.empty((0, steps, 3, 3))
        return MethodOutput(
            np.empty(0, dtype=np.int64),
            empty,
            np.empty((0, steps, 4)),
            {"replayed": 0, "with_bands": 0, "sampled": sample is not None},
        )
    rows_out = np.concatenate(out_rows)
    return MethodOutput(
        rows_out,
        np.concatenate(out_pred),
        np.concatenate(out_ghost),
        {
            "replayed": n_replayed,
            "with_bands": int(len(rows_out)),
            "sampled": sample is not None,
            "sample_per_instrument": sample,
            "pooled_library_read": card.used,
        },
    )


# ---------------------------------------------------------------- metrics


def interval_iou(lo_a, hi_a, lo_b, hi_b) -> np.ndarray:
    inter = np.clip(np.minimum(hi_a, hi_b) - np.maximum(lo_a, lo_b), 0.0, None)
    union = (hi_a - lo_a) + (hi_b - lo_b) - inter
    same = (lo_a == lo_b) & (hi_a == hi_b)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(union > 0, inter / union, np.where(same, 1.0, 0.0))


def pinball(pred: np.ndarray, y: np.ndarray, quantiles: tuple[float, ...]) -> np.ndarray:
    """Mean pinball loss over the quantiles; pred (..., q), y (...)."""
    diff = y[..., None] - pred
    q = np.asarray(quantiles)
    return np.maximum(q * diff, (q - 1.0) * diff).mean(axis=-1)


def row_scores(
    out: MethodOutput, Y: np.ndarray, quantiles: tuple[float, ...], alpha: float, same_tol: float
) -> dict[str, np.ndarray]:
    """Per (forecast, step) scores; Y is the actual targets (n, steps, 3) on out.rows."""
    y_h, y_l, y_c = Y[..., 0], Y[..., 1], Y[..., 2]
    lo, mid, hi = (out.pred[:, :, 2, i] for i in range(3))
    inside = (y_c >= lo) & (y_c <= hi)
    winkler = (hi - lo) + (2.0 / alpha) * (np.clip(lo - y_c, 0, None) + np.clip(y_c - hi, 0, None))
    g_high, g_low = out.ghost[..., 1], out.ghost[..., 2]
    same = inside & (np.abs(y_c - mid) <= same_tol) & (y_h <= g_high) & (y_l >= g_low)
    return {
        "inside": inside,
        "winkler": winkler,
        "pinball_close": pinball(out.pred[:, :, 2, :], y_c, quantiles),
        "pinball_all": pinball(out.pred, Y, quantiles).mean(axis=-1),
        "iou": interval_iou(g_low, g_high, y_l, y_h),
        "close_err": np.abs(mid - y_c),
        "same": same,
        "wrong": ~inside,
    }


def _mean(x: np.ndarray) -> float | None:
    x = x[np.isfinite(x)] if x.dtype.kind == "f" else x
    return float(np.mean(x)) if x.size else None


def summarise(scores: dict[str, np.ndarray], has_high_low: bool) -> dict:
    same, wrong = scores["same"], scores["wrong"]
    close = ~same & ~wrong
    n = int(scores["winkler"].shape[0])
    return {
        "n": n,
        "coverage_80": _mean(scores["inside"].astype(float)),
        "coverage_by_step": [
            _mean(scores["inside"][:, s].astype(float)) for s in range(scores["inside"].shape[1])
        ],
        "winkler": _mean(scores["winkler"]),
        "winkler_by_step": [_mean(scores["winkler"][:, s]) for s in range(scores["winkler"].shape[1])],
        "pinball_close": _mean(scores["pinball_close"]),
        "pinball_all": _mean(scores["pinball_all"]) if has_high_low else None,
        "range_iou": _mean(scores["iou"]),
        "close_mae_atr": _mean(scores["close_err"]),
        "categories": {
            "same": _mean(same.astype(float)),
            "close": _mean(close.astype(float)),
            "wrong": _mean(wrong.astype(float)),
        },
        "categories_step1": {
            "same": _mean(same[:, 0].astype(float)),
            "close": _mean(close[:, 0].astype(float)),
            "wrong": _mean(wrong[:, 0].astype(float)),
        },
    }


def compare(model_w: np.ndarray, base_w: np.ndarray, dates: np.ndarray, level: float) -> dict:
    """Winkler improvement 1 - sum(model) / sum(baseline) over the same forecasts (each a row summed over
    steps), with the pre-registered date-clustered bootstrap percentile CI. `ci_month_blocks` resamples
    whole calendar months instead: information only, for the serial dependence that date clusters ignore
    (overlapping 3-step targets, volatility clustering)."""
    total_m, total_b = float(model_w.sum()), float(base_w.sum())
    ci = clustered_bootstrap_skill_ci(model_w, base_w, dates, level, resamples=BOOTSTRAP_RESAMPLES, seed=SEED)
    months = np.array([d[:7] for d in dates]) if len(dates) else dates
    ci_months = clustered_bootstrap_skill_ci(
        model_w, base_w, months, level, resamples=BOOTSTRAP_RESAMPLES, seed=SEED
    )
    return {
        "n": int(len(model_w)),
        "n_dates": int(len(np.unique(dates))),
        "n_months": int(len(np.unique(months))),
        "winkler_model": total_m / len(model_w) if len(model_w) else None,
        "winkler_baseline": total_b / len(base_w) if len(base_w) else None,
        "improvement": 1.0 - total_m / total_b if total_b > 0 else None,
        "ci": list(ci) if ci is not None else None,
        "ci_month_blocks": list(ci_months) if ci_months is not None else None,
    }


# ---------------------------------------------------------------- evaluation


def _ist_dates(ts: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(ts.tz_convert(IST).strftime("%Y-%m-%d"))


def _in_slice(instrument_id: str) -> bool:
    gng = load_research_config().go_no_go_1.slice
    inst = get_instrument(instrument_id)
    return inst.exchange == gng.exchange and inst.kind in gng.kinds and instrument_id not in gng.exclude


def _by_instrument(
    panel: Panel,
    rows: np.ndarray,
    scores: dict[str, np.ndarray],
    model_w: np.ndarray,
    best: tuple[np.ndarray, np.ndarray] | None,
) -> list[dict]:
    """range_v1 per instrument, with its Winkler improvement over the best baseline on that
    instrument's forecasts that both made (no CI: a breakdown, not a test)."""
    codes = panel.inst[rows]
    same, wrong = scores["same"], scores["wrong"]
    out = []
    for code, iid in enumerate(panel.instruments):
        sel = codes == code
        if not sel.any():
            continue
        entry = {
            "instrument": iid,
            "n": int(sel.sum()),
            "coverage_80": _mean(scores["inside"][sel].astype(float)),
            "winkler": _mean(scores["winkler"][sel]),
            "categories": {
                "same": _mean(same[sel].astype(float)),
                "close": _mean((~same & ~wrong)[sel].astype(float)),
                "wrong": _mean(wrong[sel].astype(float)),
            },
            "improvement_vs_best": None,
        }
        if best is not None:
            at, base_w = best
            mine = codes[at] == code
            total_b = float(base_w[mine].sum())
            if total_b > 0:
                entry["improvement_vs_best"] = 1.0 - float(model_w[at][mine].sum()) / total_b
        out.append(entry)
    return out


def _holdout_predictions(panel: Panel, rows: np.ndarray) -> np.ndarray:
    model = load_model(panel.exchange, panel.tf)
    if model is None:
        raise FileNotFoundError(f"no saved {MODEL_NAME} model for {panel.exchange} {panel.tf}")
    if model.feature_names != panel.feature_names:
        raise ValueError("the saved model's features differ from the current feature set")
    X = panel.X[rows].copy()
    codes = {
        i: model.instruments.index(iid) for i, iid in enumerate(panel.instruments) if iid in model.instruments
    }
    X[:, -1] = [codes.get(int(c), np.nan) for c in X[:, -1]]
    return model.predict(X)


def evaluate_range(
    tf: str,
    exchange: str,
    period: Period = "validation",
    allow_holdout: bool = False,
    *,
    load: CandleLoader | None = None,
    instruments: list[str] | None = None,
    analog_sample: int | None | Literal["default"] = "default",
    compute_if_missing: bool = True,
    log: Callable[[str], None] | None = None,
) -> dict:
    """Score range_v1 and its baselines on `period` for one (exchange, tf). The validation span uses
    walk-forward predictions (cached by research.range_model, else computed here); the holdout uses the
    saved production model and needs allow_holdout=True, which only the official go/no-go run passes."""
    tf = validate_tf(tf)
    if period not in ("validation", "holdout"):
        raise ValueError("period must be 'validation' or 'holdout'")
    if period == "holdout" and not allow_holdout:
        raise PermissionError("the holdout is locked; the official go/no-go run passes allow_holdout=True")
    pivot = load_pivot_config()
    spec = pivot.range_model
    cfg = load_research_config()
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    panel = build_panel(
        exchange, tf, load=load, allow_holdout=period == "holdout", instruments=instruments, pivot=pivot
    )
    timings["panel"] = time.perf_counter() - t0
    gap = pivot.walk_forward.gap_bars

    t0 = time.perf_counter()
    if period == "validation":
        result = load_walk_forward(panel) if instruments is None else None
        cached = result is not None
        if result is None:
            if not compute_if_missing:
                raise FileNotFoundError(f"no walk-forward predictions for {exchange} {tf}; run range_model")
            result = walk_forward(panel, pivot, log=log)
            if instruments is None:
                meta = {
                    "fingerprint": panel_fingerprint(panel),
                    "settings": training_settings(),
                    "rows": len(panel),
                }
                save_walk_forward(result, panel, meta)
        cutoffs = {k: lo for k, (lo, _) in enumerate(fold_windows(tf, pivot))}
        start, end = pivot.train_end_utc(tf), pivot.holdout_start_utc
    else:
        rows = np.flatnonzero(np.asarray(panel.ts >= pivot.holdout_start_utc))
        result = WalkForwardResult(
            rows, _holdout_predictions(panel, rows), np.zeros(len(rows), dtype=int), []
        )
        cached = False
        cutoffs = {0: pivot.holdout_start_utc}
        start, end = pivot.holdout_start_utc, None
    timings["range_v1"] = time.perf_counter() - t0
    panel.X = np.empty((0, panel.X.shape[1]), dtype=np.float32)

    keep = panel.valid[result.rows]
    rows, fold = result.rows[keep], result.fold[keep]
    Y = panel.Y[rows].astype(np.float64)
    dates = _ist_dates(panel.ts[rows])
    quantiles, alpha = spec.quantiles, spec.interval_alpha
    tol = pivot.candle_accuracy.same_close_atr

    if not np.isfinite(result.pred[keep]).all():
        raise ValueError(f"{MODEL_NAME} produced non-finite quantiles for {exchange} {tf}")
    outputs: dict[str, MethodOutput] = {
        MODEL_NAME: MethodOutput(rows, result.pred[keep], ghost_ohlc(result.pred[keep]))
    }
    t0 = time.perf_counter()
    if "atr_bands" in spec.baselines:
        outputs["atr_bands"] = atr_bands_output(panel, rows, fold, cutoffs, gap, quantiles)
    timings["atr_bands"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    if "rolling_quantiles" in spec.baselines:
        outputs["rolling_quantiles"] = rolling_quantiles_output(panel, rows, quantiles)
    timings["rolling_quantiles"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    if "analog_v1" in spec.baselines:
        sample = (ANALOG_SAMPLE if is_intraday(tf) else None) if analog_sample == "default" else analog_sample
        outputs["analog_v1"] = analog_output(
            panel, rows, research_loader(period == "holdout", load), pivot, sample
        )
    timings["analog_v1"] = time.perf_counter() - t0

    position = pd.Series(np.arange(len(rows)), index=rows)
    model_scores = row_scores(outputs[MODEL_NAME], Y, quantiles, alpha, tol)
    model_w = model_scores["winkler"].sum(axis=1)
    methods, comparisons, aligned = {}, {}, {}
    for name, out in outputs.items():
        at = position.loc[out.rows].to_numpy()
        ok = np.isfinite(out.pred[:, :, 2, :]).all(axis=(1, 2))
        sub = MethodOutput(out.rows[ok], out.pred[ok], out.ghost[ok], out.extra)
        at = at[ok]
        scores = model_scores if name == MODEL_NAME else row_scores(sub, Y[at], quantiles, alpha, tol)
        has_high_low = bool(np.isfinite(sub.pred).all()) if len(sub.rows) else False
        methods[name] = summarise(scores, has_high_low) | ({"details": out.extra} if out.extra else {})
        if name != MODEL_NAME:
            aligned[name] = (at, scores["winkler"].sum(axis=1))
            comparisons[name] = compare(model_w[at], aligned[name][1], dates[at], cfg.ci_level)
    ranked = [(c["improvement"], name) for name, c in comparisons.items() if c["improvement"] is not None]
    best = min(ranked)[1] if ranked else None
    by_instrument = _by_instrument(panel, rows, model_scores, model_w, aligned.get(best))

    by_fold = []
    for k in np.unique(fold):
        sel = fold == k
        entry = {
            "fold": int(k),
            "n": int(sel.sum()),
            "coverage_80": _mean(model_scores["inside"][sel].astype(float)),
            "winkler": _mean(model_scores["winkler"][sel]),
        }
        if result.folds:
            window = next((f for f in result.folds if f["fold"] == k), {})
            entry.update({"test_start": window.get("test_start"), "test_end": window.get("test_end")})
        by_fold.append(entry)

    gate = spec.gate
    cov = methods[MODEL_NAME]["coverage_80"]
    comp = comparisons.get(best, {}) if best else {}
    improvement, ci = comp.get("improvement"), comp.get("ci")
    checks = {
        "coverage": cov is not None and gate.coverage_within[0] <= cov <= gate.coverage_within[1],
        "improvement": improvement is not None and improvement >= gate.min_improvement,
        "ci_lower": ci is not None and ci[0] > gate.ci_lower_above,
    }
    return {
        "model": MODEL_NAME,
        "exchange": exchange,
        "tf": tf,
        "period": period,
        "start": start.isoformat(),
        "end": end.isoformat() if end is not None else None,
        "instruments": panel.instruments,
        "in_go_no_go_slice": all(_in_slice(i) for i in panel.instruments),
        "units": "ATR(14) at the reference bar",
        "steps": spec.steps,
        "n_forecasts": int(len(rows)),
        "n_dates": int(len(np.unique(dates))),
        "interval_alpha": alpha,
        "same_close_atr": tol,
        "methods": methods,
        "comparisons": comparisons,
        "best_baseline": best,
        "gate": {
            "coverage_80": cov,
            "coverage_within": list(gate.coverage_within),
            "best_baseline": best,
            "improvement": improvement,
            "min_improvement": gate.min_improvement,
            "ci": ci,
            "ci_level": cfg.ci_level,
            "ci_lower_above": gate.ci_lower_above,
            "checks": checks,
            "pass": all(checks.values()),
        },
        "by_fold": by_fold,
        "by_instrument": by_instrument,
        "walk_forward_cached": cached,
        "training_folds": result.folds,
        "training_seconds": round(sum(f.get("fit_seconds", 0.0) for f in result.folds), 1),
        "runtime_seconds": {k: round(v, 1) for k, v in timings.items()},
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "seed": SEED,
    }


def multiple_testing(results: list[dict]) -> dict:
    gated = [r for r in results if r["exchange"] == "NSE" and r["period"] == "validation"]
    return {
        "n_comparisons_gated": sum(len(r["comparisons"]) for r in gated),
        "n_comparisons_all": sum(len(r["comparisons"]) for r in results),
        "n_timeframes_gated": len(gated),
        "note": (
            "A timeframe passes only if range_v1 clears the bar against every baseline (the gate uses the "
            "baseline it improves on least), an intersection-union test, so more baselines make a pass "
            "harder, not easier; no FDR adjustment applies. The pass rule then asks for 2 of 4 timeframes, "
            "1D or 1h among them."
        ),
    }


def go_no_go_2(results: list[dict], pivot: PivotConfig | None = None) -> dict:
    """The range pass rule over the NSE validation results, one per timeframe."""
    pivot = pivot or load_pivot_config()
    gate = pivot.range_model.gate
    nse = {r["tf"]: r for r in results if r["exchange"] == "NSE" and r["period"] == "validation"}
    passed = {
        tf: bool(nse[tf]["gate"]["pass"]) if tf in nse else False for tf in pivot.range_model.timeframes
    }
    n_pass = sum(passed.values())
    required = any(passed.get(tf, False) for tf in gate.required_any)
    return {
        "timeframes": passed,
        "missing": [tf for tf in pivot.range_model.timeframes if tf not in nse],
        "n_pass": n_pass,
        "min_passing": gate.min_passing,
        "required_any": list(gate.required_any),
        "required_any_passes": required,
        "pass": n_pass >= gate.min_passing and required,
    }


# ---------------------------------------------------------------- report


def report_paths() -> tuple[Path, Path]:
    folder = REPO_ROOT / "docs" / "test-reports"
    return folder / f"{REPORT_STEM}.json", folder / f"{REPORT_STEM}.md"


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _num(x, digits=3) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _ci(ci) -> str:
    return "[n/a]" if not ci else f"[{_pct(ci[0])}, {_pct(ci[1])}]"


def render_markdown(report: dict) -> str:
    lines = [
        "# range_v1 validation (go/no-go #2, range)",
        "",
        f"Generated {report['generated_at']} by `python -m candly.research.range_eval`. "
        "Validation period only (walk-forward, train_end to 2025-10-01); the holdout was not read. "
        "Units: ATR(14) at the reference bar.",
        "",
        f"**Go/no-go #2 (range, NSE): {'PASS' if report['go_no_go_2']['pass'] else 'FAIL'}** "
        f"({report['go_no_go_2']['n_pass']} of {len(report['go_no_go_2']['timeframes'])} timeframes pass; "
        f"rule: {report['pass_rule']}).",
        "",
        "| exchange | tf | n | coverage p10-p90 | best baseline | Winkler range_v1 / baseline "
        "| improvement [95% CI] | month-block CI (info) | same / close / wrong | gate |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        m = r["methods"][MODEL_NAME]
        best = r["best_baseline"]
        comp = r["comparisons"].get(best, {}) if best else {}
        cats = m["categories"]
        gate = ("PASS" if r["gate"]["pass"] else "fail") if r["exchange"] == "NSE" else "(not gated)"
        per_step = {
            k: comp[k] / r["steps"] if comp.get(k) is not None else None
            for k in ("winkler_model", "winkler_baseline")
        }
        lines.append(
            f"| {r['exchange']} | {r['tf']} | {r['n_forecasts']:,} | {_pct(m['coverage_80'])} "
            f"| {best} | {_num(per_step['winkler_model'])} / {_num(per_step['winkler_baseline'])} "
            f"| {_pct(comp.get('improvement'))} "
            f"{_ci(comp.get('ci'))} | {_ci(comp.get('ci_month_blocks'))} "
            f"| {_pct(cats['same'])} / {_pct(cats['close'])} / {_pct(cats['wrong'])} | {gate} |"
        )
    mt = report.get("multiple_testing", {})
    lines += [
        "",
        "Winkler scores are per forecast step, in ATR, on the forecasts both methods made (analog_v1 draws "
        "bands on a subset). Improvement = 1 - Winkler(range_v1) / Winkler(baseline) on those forecasts, "
        "pooled over the 3 steps; the best baseline is the one range_v1 improves on least. CIs resample "
        "whole IST dates "
        f"({report['bootstrap_resamples']} resamples), as pre-registered; the month-block CI resamples whole "
        "months and is shown for information only. Categories pool the 3 steps; `wrong` = close outside "
        "p10-p90, `same` = close within "
        f"{report['same_close_atr']} ATR of p50 and the bar inside the ghost candle's p50 high/low.",
        "",
        f"Tests: {mt.get('n_comparisons_gated', 'n/a')} range_v1-vs-baseline comparisons on the gated NSE "
        f"slice ({mt.get('n_comparisons_all', 'n/a')} in all). {mt.get('note', '')}",
        "",
        "## Coverage and categories by step (range_v1)",
        "",
        "| exchange | tf | coverage step 1 / 2 / 3 | same / close / wrong, step 1 |",
        "|---|---|---|---|",
    ]
    for r in report["results"]:
        m = r["methods"][MODEL_NAME]
        by_step = " / ".join(_pct(x) for x in m["coverage_by_step"])
        c1 = m["categories_step1"]
        lines.append(
            f"| {r['exchange']} | {r['tf']} | {by_step} | "
            f"{_pct(c1['same'])} / {_pct(c1['close'])} / {_pct(c1['wrong'])} |"
        )
    lines += [
        "",
        "## By instrument (range_v1; improvement vs that timeframe's best baseline, no CI)",
        "",
        "| exchange | tf | instrument | n | coverage | improvement | same / close / wrong |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        for e in r.get("by_instrument", []):
            cats = e["categories"]
            lines.append(
                f"| {r['exchange']} | {r['tf']} | {e['instrument']} | {e['n']:,} | {_pct(e['coverage_80'])} "
                f"| {_pct(e['improvement_vs_best'])} "
                f"| {_pct(cats['same'])} / {_pct(cats['close'])} / {_pct(cats['wrong'])} |"
            )
    lines += [
        "",
        "## All baselines",
        "",
        "| exchange | tf | method | n | coverage | Winkler | pinball close | range IoU | close MAE | "
        "improvement vs it [95% CI] |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        for name, m in r["methods"].items():
            comp = r["comparisons"].get(name)
            imp = "" if comp is None else f"{_pct(comp['improvement'])} {_ci(comp['ci'])} (n={comp['n']:,})"
            lines.append(
                f"| {r['exchange']} | {r['tf']} | {name} | {m['n']:,} | {_pct(m['coverage_80'])} | "
                f"{_num(m['winkler'])} | {_num(m['pinball_close'])} | {_num(m['range_iou'])} | "
                f"{_num(m['close_mae_atr'])} | {imp} |"
            )
    lines += ["", "## Notes", ""] + [f"- {n}" for n in report["notes"]]
    for f in report.get("failures", []):
        lines.append(f"- **{f['exchange']} {f['tf']} could not be evaluated** (a fail): {f['error']}")
    return "\n".join(lines) + "\n"


NOTES = [
    "analog_v1 is replayed on every validation bar for 1D and on a seeded sample of "
    f"{ANALOG_SAMPLE} bars per instrument intraday; its comparison uses only the bars where it drew bands "
    "(it draws none below abstain.min_analogs analogs).",
    "atr_bands is refitted on each walk-forward window's training rows, like range_v1.",
    f"rolling_quantiles uses the last {ROLLING_WINDOW} realised targets per instrument that had closed "
    "by the reference bar.",
    "Intraday fits use a seeded uniform sample of at most range_model.MAX_TRAIN_ROWS training bars.",
    "Only the NSE slice (equities and indices, INDIAVIX excluded) is gated; BSE (SENSEX only) and MCX "
    "are reported for information. MCX continuous futures are not roll-adjusted, so roll gaps stay in "
    "the targets.",
    "Watchlist instruments are today's large caps (survivorship bias): treat results as an upper bound.",
]


def run_validation(
    exchanges=None, tfs=None, *, write: bool = True, log: Callable[[str], None] | None = None
) -> dict:
    pivot = load_pivot_config()
    spec = pivot.range_model
    results, failures = [], []
    for exchange in exchanges or spec.exchanges:
        for tf in tfs or spec.timeframes:
            started = time.perf_counter()
            try:
                result = evaluate_range(tf, exchange, "validation", log=log)
            except Exception as exc:  # one broken series must not lose the other results; it fails the gate
                failures.append({"exchange": exchange, "tf": tf, "error": repr(exc)})
                if log is not None:
                    log(f"{exchange} {tf}: FAILED {exc!r}")
                continue
            finally:
                clear_scorecard_cache()
            result["runtime_seconds"]["total"] = round(time.perf_counter() - started, 1)
            results.append(result)
            if log is not None:
                g = result["gate"]
                log(
                    f"{exchange} {tf}: coverage {_pct(g['coverage_80'])}, "
                    f"improvement vs {g['best_baseline']} "
                    f"{_pct(g['improvement'])} CI {g['ci']}, pass={g['pass']}"
                )
    report = {
        "model": MODEL_NAME,
        "period": "validation",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "config_sha256": pivot_config_hashes(),
        "training_settings": training_settings(),
        "pass_rule": spec.gate.rule,
        "go_no_go_2": go_no_go_2(results, pivot),
        "multiple_testing": multiple_testing(results),
        "same_close_atr": pivot.candle_accuracy.same_close_atr,
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "notes": NOTES,
        "failures": failures,
        "results": results,
    }
    if write:
        json_path, md_path = report_paths()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_bytes((json.dumps(report, indent=2) + "\n").encode("utf-8"))
        md_path.write_bytes(render_markdown(report).encode("utf-8"))
    return report


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate range_v1 against its baselines (never the holdout)"
    )
    parser.add_argument("--exchange", nargs="*", default=None)
    parser.add_argument("--tf", nargs="*", default=None)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    def log(line: str) -> None:
        print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {line}", flush=True)

    run_validation(args.exchange, args.tf, write=not args.no_write, log=log)


if __name__ == "__main__":
    main()
