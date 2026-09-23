"""Accuracy summary, calibration, rolling series and group breakdowns from ledger rows.

Abstained forecasts are counted but excluded from hit rate, Brier and calibration (CONTRACTS.md).
Range metrics (match score, close error, band coverage) use every graded forecast that had ghost
candles; band coverage only counts steps that had a band.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from candly.ledger.models import (
    AccuracyResponse,
    AccuracySummary,
    CalibrationBin,
    GroupStat,
    RollingPoint,
)

N_BINS = 10
ROLLING_WINDOW = 30
GROUPS = ("instrument", "tf", "pattern", "session_phase", "vol_regime")


def _mean(values) -> float | None:
    arr = np.asarray([v for v in values if v is not None and not pd.isna(v)], dtype=float)
    return float(arr.mean()) if arr.size else None


def _calibration(p: np.ndarray, y: np.ndarray) -> tuple[list[CalibrationBin], float | None]:
    edges = np.linspace(0.0, 1.0, N_BINS + 1)
    idx = np.clip((p * N_BINS).astype(int), 0, N_BINS - 1) if p.size else np.array([], dtype=int)
    bins, ece = [], 0.0
    for b in range(N_BINS):
        mask = idx == b
        n = int(mask.sum())
        mean_pred = float(p[mask].mean()) if n else None
        observed = float(y[mask].mean()) if n else None
        if n:
            ece += n / p.size * abs(mean_pred - observed)
        bins.append(
            CalibrationBin(
                bin_low=float(edges[b]),
                bin_high=float(edges[b + 1]),
                mean_pred=mean_pred,
                observed=observed,
                n=n,
            )
        )
    return bins, (ece if p.size else None)


def accuracy_from_frame(df: pd.DataFrame) -> AccuracyResponse:
    if df.empty:
        bins, _ = _calibration(np.array([]), np.array([]))
        empty = AccuracySummary(
            n_forecasts=0,
            n_graded=0,
            n_abstained=0,
            direction_hit_rate=None,
            brier=None,
            brier_baseline=None,
            skill=None,
            ece=None,
            band_coverage_80=None,
            mean_match_score=None,
            mean_close_err_atr=None,
        )
        return AccuracyResponse(summary=empty, calibration=bins, rolling=[], by_group=[])

    graded = df[df["status"] == "graded"].sort_values(["ref_time", "id"]).copy()
    payload = graded["payload"].map(json.loads)
    grades = graded["grade"].map(lambda g: json.loads(g) if g else {"steps": []})
    actual = graded["actual"].map(json.loads)
    graded["y"] = [
        float(a[p["horizon_bars"] - 1]["close"] > p["ref_close"])
        for a, p in zip(actual, payload, strict=True)
    ]
    graded["has_steps"] = grades.map(lambda g: bool(g["steps"]))
    scored_mask = (graded["abstain"] == 0) & graded["p_up"].notna() & graded["base_rate"].notna()
    scored = graded[scored_mask]

    steps = [s for g in grades for s in g["steps"]]
    band_steps = [
        s["in_band_80"]
        for g, p in zip(grades, payload, strict=True)
        for s in g["steps"]
        if s["step"] <= len(p["bands"])
    ]
    brier, baseline = _mean(scored["brier"]), _mean(scored["brier_baseline"])
    bins, ece = _calibration(scored["p_up"].to_numpy(dtype=float), scored["y"].to_numpy(dtype=float))
    summary = AccuracySummary(
        n_forecasts=len(df),
        n_graded=len(graded),
        n_abstained=int((df["abstain"] == 1).sum()),
        direction_hit_rate=_mean(scored["direction_hit"]),
        brier=brier,
        brier_baseline=baseline,
        skill=(1.0 - brier / baseline) if brier is not None and baseline else None,
        ece=ece,
        band_coverage_80=_mean([float(x) for x in band_steps]),
        mean_match_score=_mean(graded.loc[graded["has_steps"], "match_score"]),
        mean_close_err_atr=_mean([s["close_err_atr"] for s in steps]),
    )

    rolling = []
    for i in range(len(graded)):
        window = graded.iloc[max(0, i - ROLLING_WINDOW + 1) : i + 1]
        w_scored = window[scored_mask.loc[window.index]]
        rolling.append(
            RollingPoint(
                time=int(graded["ref_time"].iloc[i]),
                hit_rate=_mean(w_scored["direction_hit"]),
                brier=_mean(w_scored["brier"]),
                match_score=_mean(window.loc[window["has_steps"], "match_score"]),
            )
        )

    context = scored["context"].map(lambda c: json.loads(c) if c else {})
    keyed = pd.DataFrame(
        {
            "instrument": scored["instrument"],
            "tf": scored["tf"],
            "pattern": context.map(lambda c: c.get("patterns") or ["none"]),
            "session_phase": context.map(lambda c: c.get("session_phase") or "none"),
            "vol_regime": context.map(lambda c: c.get("vol_regime") or "none"),
            "direction_hit": scored["direction_hit"],
            "brier": scored["brier"],
        }
    )
    by_group = []
    for group in GROUPS:
        frame = keyed.explode("pattern") if group == "pattern" else keyed
        for key, sub in frame.groupby(group, sort=True):
            by_group.append(
                GroupStat(
                    group_by=group,
                    key=str(key),
                    n=len(sub),
                    hit_rate=_mean(sub["direction_hit"]),
                    brier=_mean(sub["brier"]),
                )
            )
    return AccuracyResponse(summary=summary, calibration=bins, rolling=rolling, by_group=by_group)
