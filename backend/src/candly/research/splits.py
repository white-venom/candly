"""Walk-forward folds: expanding train window, fixed-length test windows, all before the holdout."""

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


def fold_boundaries(start: pd.Timestamp, cfg: ResearchConfig) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """[(test_start, test_end)] in UTC. The first test window opens `min_train_years` after the IST
    date of `start`; windows are `test_window_months` long and the last one stops at the holdout."""
    holdout = cfg.holdout_start_utc
    anchor = start.tz_convert(IST).normalize()
    t = anchor + pd.DateOffset(years=cfg.min_train_years)
    out = []
    while t.tz_convert("UTC") < holdout:
        end = min((t + pd.DateOffset(months=cfg.test_window_months)).tz_convert("UTC"), holdout)
        out.append((t.tz_convert("UTC"), end))
        t = t + pd.DateOffset(months=cfg.test_window_months)
    return out


def gap_bars(cfg: ResearchConfig) -> int:
    """Bars dropped from the end of each train window. Purge covers overlapping labels; in an
    expanding walk-forward no train data follows a test window, so the embargo is added to the same gap."""
    return cfg.purge_bars + cfg.embargo_bars


def train_positions(ts: pd.Series, before: pd.Timestamp, cfg: ResearchConfig) -> np.ndarray:
    idx = np.flatnonzero((ts < before).to_numpy())
    return idx[: max(0, len(idx) - gap_bars(cfg))]


def walk_forward_folds(ts: pd.Series, cfg: ResearchConfig, start: pd.Timestamp | None = None) -> list[Fold]:
    """Positional folds over one bar series (`ts` sorted ascending). Bars on/after the holdout never
    appear in any fold. `start` anchors the calendar (defaults to the first bar)."""
    if ts.empty:
        return []
    bounds = fold_boundaries(start if start is not None else ts.iloc[0], cfg)
    folds = []
    for i, (lo, hi) in enumerate(bounds):
        test = np.flatnonzero(((ts >= lo) & (ts < hi)).to_numpy())
        train = train_positions(ts, lo, cfg)
        if len(test) and len(train):
            folds.append(Fold(i, lo, hi, train, test))
    return folds
