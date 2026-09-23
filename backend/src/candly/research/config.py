"""Readers for the pre-registered research protocol (research.yaml) and the cost table (costs.yaml)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from candly.core.calendar import IST
from candly.core.settings import get_settings


@dataclass(frozen=True)
class ResearchConfig:
    holdout_start: date
    min_train_years: int
    test_window_months: int
    purge_bars: int
    embargo_bars: int
    horizons: tuple[int, ...]
    min_samples: int
    fdr_alpha: float
    ci_level: float
    prior_strength: float
    min_analogs: int
    min_edge: float
    forecast_steps: int
    bands: tuple[float, ...]

    @property
    def holdout_start_utc(self) -> pd.Timestamp:
        """Midnight IST on the holdout date: every bar of that trading day and later is locked."""
        return pd.Timestamp(datetime.combine(self.holdout_start, time(0), tzinfo=IST)).tz_convert("UTC")

    @property
    def max_path_bars(self) -> int:
        return max(*self.horizons, self.forecast_steps)


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def _research_cached(path: str, mtime: float) -> ResearchConfig:
    raw = _read_yaml(Path(path))
    wf, stats, abstain, fc = raw["walk_forward"], raw["stats"], raw["abstain"], raw["forecast"]
    start = raw["holdout"]["start"]
    if isinstance(start, str):
        start = date.fromisoformat(start)
    return ResearchConfig(
        holdout_start=start,
        min_train_years=int(wf["min_train_years"]),
        test_window_months=int(wf["test_window_months"]),
        purge_bars=int(wf["purge_bars"]),
        embargo_bars=int(wf["embargo_bars"]),
        horizons=tuple(int(h) for h in raw["horizons_bars"]),
        min_samples=int(stats["min_samples"]),
        fdr_alpha=float(stats["fdr_alpha"]),
        ci_level=float(stats["ci_level"]),
        prior_strength=float(stats["prior_strength"]),
        min_analogs=int(abstain["min_analogs"]),
        min_edge=float(abstain["min_edge"]),
        forecast_steps=int(fc["steps"]),
        bands=tuple(float(b) for b in fc["bands"]),
    )


def load_research_config(path: Path | None = None) -> ResearchConfig:
    path = path or get_settings().config_dir / "research.yaml"
    return _research_cached(str(path), path.stat().st_mtime)


@lru_cache(maxsize=8)
def _costs_cached(path: str, mtime: float) -> dict:
    return _read_yaml(Path(path))


def load_costs_config(path: Path | None = None) -> dict:
    path = path or get_settings().config_dir / "costs.yaml"
    return _costs_cached(str(path), path.stat().st_mtime)
