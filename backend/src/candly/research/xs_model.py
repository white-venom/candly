"""LightGBM lambdarank for xs_v1 / pv2_xs, trained walk-forward (config/edge_search.yaml `cross_section`).

Training rows: every eligible (day, stock) of every trading day, one ranking query per day. The relevance
of a row is the quintile (0-4) of its forward h-session return among that day's labelled rows. Features are
the daily percentile ranks from candly.research.xs_panel.

Walk-forward: expanding window, refit at the start of every IST calendar year from train_end. For a cut c,
a training row's signal day is at least `purge_days` sessions before the last session before c, and its
label ends strictly before c, so no training return overlaps the test window.

Hyperparameters are fixed here before any fit and are never tuned on validation results: shallow trees,
large leaves (labels of neighbouring days overlap), L2, column subsampling, a fixed seed, 2 threads.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from candly.core.calendar import IST
from candly.research.lgbm import lgb
from candly.research.xs_panel import label_column

SEED = 20260924
N_GRADES = 5
LGBM_PARAMS: dict = {
    "objective": "lambdarank",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "max_depth": 4,
    "min_data_in_leaf": 1000,
    "feature_fraction": 0.8,
    "lambda_l2": 10.0,
    "seed": SEED,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 2,
    "verbosity": -1,
}
NUM_BOOST_ROUND = 200
MIN_TRAIN_DAYS = 250

Window = tuple[pd.Timestamp, pd.Timestamp]


def hyperparameters(params: dict | None = None, num_boost_round: int | None = None) -> dict:
    return json.loads(
        json.dumps(
            {
                "lightgbm": params or LGBM_PARAMS,
                "num_boost_round": num_boost_round or NUM_BOOST_ROUND,
                "relevance": f"within-day quintile of the forward h-session return, grades 0..{N_GRADES - 1}",
                "label_gain": "LightGBM default (2^grade - 1)",
                "query": "one per trading day (all eligible stocks with a label)",
                "min_train_days": MIN_TRAIN_DAYS,
                "features": "daily percentile ranks of the feature columns",
            }
        )
    )


def yearly_windows(start: pd.Timestamp, end: pd.Timestamp) -> list[Window]:
    """[(lo, hi)] in UTC: 12-month windows stepped in IST calendar time from `start`; the last stops at
    `end`."""
    out = []
    t = start.tz_convert(IST)
    while t.tz_convert("UTC") < end:
        nxt = t + pd.DateOffset(years=1)
        out.append((t.tz_convert("UTC"), min(nxt.tz_convert("UTC"), end)))
        t = nxt
    return out


def relevance_grades(y, day) -> np.ndarray:
    """Quintile grade 0..N_GRADES-1 of `y` within each day: floor((rank - 1) * N_GRADES / n), ranks
    ascending with ties averaged; NaN stays NaN."""
    groups = pd.Series(np.asarray(y, float)).groupby(np.asarray(day))
    rank, n = groups.rank(method="average").to_numpy(), groups.transform("count").to_numpy()
    return np.clip(np.floor((rank - 1.0) * N_GRADES / n), 0.0, N_GRADES - 1.0)


def training_mask(t, labelled, cut_pos: int, h: int, purge: int) -> np.ndarray:
    """Rows whose signal day is at least `purge` sessions before the last session before the cut and
    whose label (open of t+1 to open of t+1+h) ends before the cut. `cut_pos` is the calendar position of
    the first session on or after the cut."""
    t = np.asarray(t)
    return np.asarray(labelled, bool) & (t <= cut_pos - 1 - purge) & (t + 1 + h <= cut_pos - 1)


def fit_ranker(
    X: np.ndarray, grade: np.ndarray, day: np.ndarray, params: dict | None, rounds: int | None
) -> lgb.Booster:
    """Rows must be sorted by day so each query is contiguous."""
    if np.any(np.diff(day) < 0):
        raise ValueError("training rows must be sorted by day")
    _, sizes = np.unique(day, return_counts=True)
    data = lgb.Dataset(X, label=grade, group=sizes, free_raw_data=True)
    return lgb.train(params or LGBM_PARAMS, data, num_boost_round=rounds or NUM_BOOST_ROUND)


def walk_forward(
    frame: pd.DataFrame,
    feature_cols: list[str],
    h: int,
    dates: pd.DatetimeIndex,
    windows: list[Window],
    signal_days: pd.DataFrame,
    purge: int,
    params: dict | None = None,
    rounds: int | None = None,
) -> tuple[np.ndarray, list[dict]]:
    """Out-of-sample scores for the rows of `frame` on the signal days of `signal_days` (columns signal,
    entry_ts); NaN elsewhere. The fold of a signal day is the window holding its entry."""
    y = frame[label_column(h)].to_numpy(dtype=float)
    t = frame["t"].to_numpy()
    labelled = np.isfinite(y)
    grade = relevance_grades(y, t)
    X = frame[feature_cols].to_numpy(dtype=np.float64)
    scores = np.full(len(frame), np.nan)
    folds = []
    for k, (lo, hi) in enumerate(windows):
        entry_ts = signal_days["entry_ts"]
        signals = signal_days.loc[(entry_ts >= lo) & (entry_ts < hi), "signal"].to_numpy()
        if signals.size == 0:
            continue
        cut_pos = int(dates.searchsorted(lo))
        train = training_mask(t, labelled, cut_pos, h, purge)
        test = np.isin(t, signals)
        n_days = int(np.unique(t[train]).size)
        record = {
            "fold": k,
            "test_start": lo.isoformat(),
            "test_end": hi.isoformat(),
            "n_signal_days": int(signals.size),
            "n_test_rows": int(test.sum()),
            "n_train_rows": int(train.sum()),
            "n_train_days": n_days,
            "fitted": False,
        }
        if n_days >= MIN_TRAIN_DAYS and test.any():
            booster = fit_ranker(X[train], grade[train], t[train], params, rounds)
            scores[test] = booster.predict(X[test], num_threads=(params or LGBM_PARAMS)["num_threads"])
            last = int(t[train].max())
            record.update(
                fitted=True,
                first_train_day=dates[int(t[train].min())].isoformat(),
                last_train_day=dates[last].isoformat(),
                last_label_end=dates[last + 1 + h].isoformat(),
                feature_importance_gain=dict(
                    zip(
                        feature_cols,
                        (float(v) for v in booster.feature_importance("gain")),
                        strict=True,
                    )
                ),
            )
            if dates[last + 1 + h] >= lo:
                raise AssertionError("a training label ends at or after the cut")
        folds.append(record)
    return scores, folds
