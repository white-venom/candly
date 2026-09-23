"""Readers for the pre-registered research protocol (research.yaml) and the cost table (costs.yaml)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from candly.core.calendar import IST
from candly.core.settings import get_settings
from candly.core.timeframes import is_intraday, validate_tf

HASHED_CONFIGS = (
    "research.yaml",
    "patterns.yaml",
    "costs.yaml",
    "expiry.yaml",
    "markets.yaml",
    "watchlist.yaml",
)
HOLIDAYS_FILE = "holidays_observed.json"  # in the derived dir, written by candly.data.holidays
CONTEXT_BUCKETS = ("trend", "vol_regime", "expiry")


@dataclass(frozen=True)
class ConfidenceRule:
    ci_excludes_base: bool
    min_edge_multiple: float


@dataclass(frozen=True)
class GoNoGoSlice:
    tf: str
    exchange: str
    kinds: tuple[str, ...]
    exclude: tuple[str, ...]


@dataclass(frozen=True)
class GoNoGo:
    slice: GoNoGoSlice
    population: str
    require_certified_buckets: int
    require_brier_skill_above: float
    brier_skill_ci: str
    calibration_test: str
    calibration_min_p: float
    min_scored: int
    ece_bins: int
    ece_binning: str


@dataclass(frozen=True)
class ResearchConfig:
    holdout_start: date
    train_end: dict[str, date]
    test_window_months: int
    purge_bars: int
    embargo_bars: int
    horizons: tuple[int, ...]
    min_samples: int
    fdr_alpha: float
    bh_family: str
    ci_level: float
    prior_strength: float
    test: str
    min_clusters: int
    bucket_null: str
    analog_bucket: str
    analog_patterns: str
    analog_no_pattern: str
    analog_effective_n: str
    min_analogs: int
    min_edge: float
    require_validated_bucket: bool
    fallback_stop_atr: float
    intraday_within_session: bool
    require_edge_over_costs: bool
    validation_min_clusters: int
    validation_max_p: float
    same_exchange_validation: bool
    min_reward_risk: float
    context_buckets: tuple[str, ...]
    confidence: dict[str, ConfidenceRule]
    forecast_steps: int
    bands: tuple[float, ...]
    go_no_go_1: GoNoGo

    @property
    def holdout_start_utc(self) -> pd.Timestamp:
        """Midnight IST on the holdout date: every bar of that trading day and later is locked."""
        return ist_midnight_utc(self.holdout_start)

    @property
    def max_path_bars(self) -> int:
        return max(*self.horizons, self.forecast_steps)

    def train_end_for(self, tf: str) -> date:
        return self.train_end["intraday" if is_intraday(validate_tf(tf)) else "1D"]

    def train_end_utc(self, tf: str) -> pd.Timestamp:
        """Midnight IST on the train_end date for this timeframe: discovery uses bars before it."""
        return ist_midnight_utc(self.train_end_for(tf))


def ist_midnight_utc(d: date) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(d, time(0), tzinfo=IST)).tz_convert("UTC")


def _date(value) -> date:
    return date.fromisoformat(value) if isinstance(value, str) else value


def _choice(value: str, allowed: tuple[str, ...], key: str) -> str:
    if value not in allowed:
        raise ValueError(f"research.yaml {key}={value!r} is not implemented; expected one of {allowed}")
    return value


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# Every key research.yaml may hold, per section. A key the code doesn't read is an error, so a
# pre-registered setting can never be silently ignored.
KNOWN_KEYS: dict[str, set[str]] = {
    "": {
        "holdout", "train_end", "walk_forward", "horizons_bars", "stats", "analog", "abstain",
        "scorecard", "confidence", "forecast", "go_no_go_1",
    },
    "holdout": {"start"},
    "walk_forward": {"test_window_months", "purge_bars", "embargo_bars"},
    "stats": {
        "min_samples", "fdr_alpha", "bh_family", "ci_level", "prior_strength", "test", "min_clusters",
        "bucket_null",
    },
    "analog": {"bucket", "patterns", "no_pattern", "effective_n"},
    "abstain": {
        "min_analogs", "min_edge", "require_validated_bucket", "fallback_stop_atr", "intraday_within_session",
        "require_edge_over_costs", "validation_min_clusters", "validation_max_p", "same_exchange_validation",
        "min_reward_risk",
    },
    "scorecard": {"context_buckets"},
    "forecast": {"steps", "bands"},
    "go_no_go_1": {
        "slice", "population", "require_certified_buckets", "require_brier_skill_above", "brier_skill_ci",
        "calibration_test", "calibration_min_p", "min_scored", "ece_bins", "ece_binning",
    },
    "go_no_go_1.slice": {"tf", "exchange", "kinds", "exclude"},
}


def _check_keys(section: dict, name: str) -> dict:
    unknown = sorted(set(section) - KNOWN_KEYS[name])
    if unknown:
        raise ValueError(f"research.yaml {name or 'top level'} has unknown keys {unknown}")
    return section


def _go_no_go(raw: dict) -> GoNoGo:
    _check_keys(raw, "go_no_go_1")
    s = _check_keys(raw["slice"], "go_no_go_1.slice")
    return GoNoGo(
        slice=GoNoGoSlice(
            tf=validate_tf(str(s["tf"])),
            exchange=str(s["exchange"]),
            kinds=tuple(str(k) for k in s["kinds"]),
            exclude=tuple(str(x) for x in s.get("exclude") or ()),
        ),
        population=_choice(str(raw["population"]), ("pattern_bars", "all_bars"), "go_no_go_1.population"),
        require_certified_buckets=int(raw["require_certified_buckets"]),
        require_brier_skill_above=float(raw["require_brier_skill_above"]),
        brier_skill_ci=_choice(
            str(raw["brier_skill_ci"]), ("date_clustered_bootstrap",), "go_no_go_1.brier_skill_ci"
        ),
        calibration_test=_choice(
            str(raw["calibration_test"]), ("self_consistency",), "go_no_go_1.calibration_test"
        ),
        calibration_min_p=float(raw["calibration_min_p"]),
        min_scored=int(raw["min_scored"]),
        ece_bins=int(raw["ece_bins"]),
        ece_binning=_choice(str(raw["ece_binning"]), ("quantile", "uniform"), "go_no_go_1.ece_binning"),
    )


@lru_cache(maxsize=8)
def _research_cached(path: str, mtime: float) -> ResearchConfig:
    raw = _check_keys(_read_yaml(Path(path)), "")
    for name in ("holdout", "walk_forward", "stats", "analog", "abstain", "scorecard", "forecast"):
        _check_keys(raw[name], name)
    wf, stats, abstain, fc = raw["walk_forward"], raw["stats"], raw["abstain"], raw["forecast"]
    analog, confidence = raw["analog"], raw["confidence"]
    train_end = {str(k): _date(v) for k, v in raw["train_end"].items()}
    if set(train_end) != {"1D", "intraday"}:
        raise ValueError("research.yaml train_end needs exactly the keys 1D and intraday")
    rules = {
        label: ConfidenceRule(
            ci_excludes_base=bool(rule.get("ci_excludes_base", False)),
            min_edge_multiple=float(rule.get("min_edge_multiple", 0.0)),
        )
        for label, rule in confidence.items()
    }
    if not set(rules) <= {"high", "medium"}:
        raise ValueError(f"research.yaml confidence labels must be high/medium, got {sorted(rules)}")
    buckets = tuple(str(b) for b in raw["scorecard"]["context_buckets"])
    unknown = sorted(set(buckets) - set(CONTEXT_BUCKETS))
    if unknown:
        raise ValueError(
            f"research.yaml scorecard.context_buckets {unknown} are not implemented; use {CONTEXT_BUCKETS}"
        )
    return ResearchConfig(
        holdout_start=_date(raw["holdout"]["start"]),
        train_end=train_end,
        test_window_months=int(wf["test_window_months"]),
        purge_bars=int(wf["purge_bars"]),
        embargo_bars=int(wf["embargo_bars"]),
        horizons=tuple(int(h) for h in raw["horizons_bars"]),
        min_samples=int(stats["min_samples"]),
        fdr_alpha=float(stats["fdr_alpha"]),
        bh_family=_choice(str(stats["bh_family"]), ("min_samples", "all"), "stats.bh_family"),
        ci_level=float(stats["ci_level"]),
        prior_strength=float(stats["prior_strength"]),
        test=_choice(str(stats["test"]), ("cluster_robust",), "stats.test"),
        min_clusters=int(stats["min_clusters"]),
        bucket_null=_choice(
            str(stats["bucket_null"]), ("bucket_base_rate", "unconditional"), "stats.bucket_null"
        ),
        analog_bucket=_choice(str(analog["bucket"]), ("trend",), "analog.bucket"),
        analog_patterns=_choice(str(analog["patterns"]), ("union_of_active",), "analog.patterns"),
        analog_no_pattern=_choice(str(analog["no_pattern"]), ("bucket_only",), "analog.no_pattern"),
        analog_effective_n=_choice(str(analog["effective_n"]), ("n_over_steps", "n"), "analog.effective_n"),
        min_analogs=int(abstain["min_analogs"]),
        min_edge=float(abstain["min_edge"]),
        require_validated_bucket=bool(abstain["require_validated_bucket"]),
        fallback_stop_atr=float(abstain["fallback_stop_atr"]),
        intraday_within_session=bool(abstain["intraday_within_session"]),
        require_edge_over_costs=bool(abstain["require_edge_over_costs"]),
        validation_min_clusters=int(abstain["validation_min_clusters"]),
        validation_max_p=float(abstain["validation_max_p"]),
        same_exchange_validation=bool(abstain["same_exchange_validation"]),
        min_reward_risk=float(abstain["min_reward_risk"]),
        context_buckets=buckets,
        confidence={k: rules[k] for k in ("high", "medium") if k in rules},
        forecast_steps=int(fc["steps"]),
        bands=tuple(float(b) for b in fc["bands"]),
        go_no_go_1=_go_no_go(raw["go_no_go_1"]),
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config_hashes(config_dir: Path | None = None) -> dict[str, str]:
    """sha256 of each file a scorecard depends on, so a build can be traced to its protocol: the
    HASHED_CONFIGS, plus the derived holidays file (it moves the calendar) when there is one."""
    settings = get_settings()
    config_dir = config_dir or settings.config_dir
    out = {name: _sha256(config_dir / name) for name in HASHED_CONFIGS}
    holidays = settings.derived_dir / HOLIDAYS_FILE
    if holidays.exists():
        out[HOLIDAYS_FILE] = _sha256(holidays)
    return out
