"""Fixed train/validation split and the walk-forward windows (PLAN.md §12).

Discovery (headline statistics) uses bars before the fixed `train_end[tf]` minus the purge + embargo gap.
Validation is the walk-forward test windows that tile [train_end, holdout). The dates come from
research.yaml, so a deeper backfill can't move them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from candly.core.calendar import IST
from candly.research.config import ResearchConfig


@dataclass(frozen=True)
class Fold:
    index: int
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    train: np.ndarray
    test: np.ndarray


def fold_boundaries(tf: str, cfg: ResearchConfig) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """[(test_start, test_end)] in UTC: `test_window_months`-long windows from train_end[tf] (midnight
    IST) to the holdout; the last one stops at the holdout."""
    holdout = cfg.holdout_start_utc
    t = cfg.train_end_utc(tf).tz_convert(IST)
    out = []
    while t.tz_convert("UTC") < holdout:
        nxt = t + pd.DateOffset(months=cfg.test_window_months)
        out.append((t.tz_convert("UTC"), min(nxt.tz_convert("UTC"), holdout)))
        t = nxt
    return out


def gap_bars(cfg: ResearchConfig) -> int:
    """Bars dropped from the end of each train window. Purge covers overlapping labels; in an
    expanding walk-forward no train data follows a test window, so the embargo is added to the same gap."""
    return cfg.purge_bars + cfg.embargo_bars


def train_positions(ts: pd.Series, before: pd.Timestamp, cfg: ResearchConfig) -> np.ndarray:
    idx = np.flatnonzero((ts < before).to_numpy())
    return idx[: max(0, len(idx) - gap_bars(cfg))]


def split_labels(ts: pd.Series, tf: str, cfg: ResearchConfig) -> np.ndarray:
    """"train" (before train_end, minus the gap), "validation" ([train_end, holdout)) or "gap"."""
    out = np.full(len(ts), "gap", dtype=object)
    out[train_positions(ts, cfg.train_end_utc(tf), cfg)] = "train"
    out[((ts >= cfg.train_end_utc(tf)) & (ts < cfg.holdout_start_utc)).to_numpy()] = "validation"
    return out


def walk_forward_folds(ts: pd.Series, tf: str, cfg: ResearchConfig) -> list[Fold]:
    """Positional folds over one bar series (`ts` sorted ascending): expanding train window, test
    windows from `fold_boundaries`. Bars on/after the holdout never appear in any fold."""
    folds = []
    for i, (lo, hi) in enumerate(fold_boundaries(tf, cfg)):
        test = np.flatnonzero(((ts >= lo) & (ts < hi)).to_numpy())
        train = train_positions(ts, lo, cfg)
        if len(test) and len(train):
            folds.append(Fold(i, lo, hi, train, test))
    return folds
