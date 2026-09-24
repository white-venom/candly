"""Reader for the pre-registered pivot protocol (config/pivot.yaml, PLAN.md §20a).

Strict like research.yaml: an unknown or missing key is an error, and so is a value the code doesn't
implement. The prose rules (the go/no-go #2 pass rule and the candle-accuracy definitions) are parsed
against the exact wording they were registered with, so an edit to them can't be silently ignored.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from candly.core.instruments import EXCHANGES
from candly.core.settings import get_settings
from candly.core.timeframes import is_intraday, validate_tf
from candly.research.config import config_hashes, ist_midnight_utc, load_research_config
from candly.research.regime_features import FEATURE_GROUPS as REGIME_FEATURE_GROUPS

PIVOT_FILE = "pivot.yaml"
REGIME_NAME = "regime_v1"
REGIME_ALGO = "lightgbm_classifier_isotonic"
REGIME_TIMEFRAME = "1D"
REGIME_TARGET = "close[t+h] > close[t] (flat closes dropped)"
REGIME_BASELINES = ("base_rate", "trend_rule_ema200", "momentum_sign_20d")
REGIME_PASS_RULE = "at least one horizon passes every check"
RANGE_TARGETS = ("high", "low", "close")
RANGE_FEATURE_GROUPS = (
    "volatility",
    "recent_ranges",
    "last_candle_shape",
    "volume",
    "time_of_day",
    "calendar",
    "expiry",
    "trend",
    "market",
)
RANGE_BASELINES = ("atr_bands", "rolling_quantiles", "analog_v1")

KNOWN_KEYS: dict[str, set[str]] = {
    "": {"data", "range_model", "regime_model", "candle_accuracy", "holdout_rule"},
    "data": {"daily_start", "holdout_start", "train_end", "walk_forward"},
    "data.walk_forward": {"retrain_every_months", "purge_bars", "embargo_bars", "expanding"},
    "range_model": {
        "name",
        "algo",
        "quantiles",
        "targets",
        "steps",
        "timeframes",
        "exchanges",
        "feature_groups",
        "baselines",
        "go_no_go_2",
    },
    "range_model.go_no_go_2": {
        "coverage_80_within",
        "interval_score_improvement_vs_best_baseline",
        "improvement_ci_lower_above",
        "pass_rule",
    },
    "regime_model": {
        "name",
        "algo",
        "timeframe",
        "horizons_days",
        "target",
        "feature_groups",
        "baselines",
        "go_no_go_2",
    },
    "regime_model.go_no_go_2": {
        "brier_skill_ci_lower_above",
        "calibration_min_p",
        "min_scored",
        "top_decile_expectancy_after_cost_positive",
        "pass_rule",
    },
    "candle_accuracy": {"same", "close", "wrong", "direction"},
}

_PASS_RULE = re.compile(r"^at least (\d+) of (\d+) timeframes pass, and (\S+) or (\S+) is one of them$")
_SAME = re.compile(
    r"^actual close within (\d+(?:\.\d+)?) ATR of the predicted median "
    r"AND actual high/low inside the predicted range box$"
)
_CLOSE = "actual close inside the p10-p90 band (and not 'same')"
_WRONG = "actual close outside the p10-p90 band"


@dataclass(frozen=True)
class WalkForward:
    retrain_every_months: int
    purge_bars: int
    embargo_bars: int
    expanding: bool

    @property
    def gap_bars(self) -> int:
        """Bars dropped from the end of every training window. In an expanding walk-forward no training
        data follows a test window, so the embargo is added to the same gap as the purge."""
        return self.purge_bars + self.embargo_bars


@dataclass(frozen=True)
class RangeGate:
    coverage_within: tuple[float, float]
    min_improvement: float
    ci_lower_above: float
    min_passing: int
    required_any: tuple[str, ...]
    rule: str


@dataclass(frozen=True)
class RangeSpec:
    name: str
    algo: str
    quantiles: tuple[float, float, float]
    targets: tuple[str, ...]
    steps: int
    timeframes: tuple[str, ...]
    exchanges: tuple[str, ...]
    feature_groups: tuple[str, ...]
    baselines: tuple[str, ...]
    gate: RangeGate

    @property
    def band(self) -> tuple[float, float]:
        return self.quantiles[0], self.quantiles[-1]

    @property
    def interval_alpha(self) -> float:
        """Winkler alpha of the p_lo-p_hi band: 0.2 for p10-p90."""
        lo, hi = self.band
        return round(1.0 - (hi - lo), 10)


@dataclass(frozen=True)
class RegimeGate:
    brier_skill_ci_lower_above: float
    calibration_min_p: float
    min_scored: int
    top_decile_expectancy_after_cost_positive: bool
    pass_rule: str


@dataclass(frozen=True)
class RegimeSpec:
    name: str
    algo: str
    timeframe: str
    horizons_days: tuple[int, ...]
    target: str
    feature_groups: tuple[str, ...]
    baselines: tuple[str, ...]
    go_no_go_2: RegimeGate


@dataclass(frozen=True)
class CandleAccuracy:
    same_close_atr: float  # "same": |actual close - predicted median| within this many ATR
    text: dict[str, str]


@dataclass(frozen=True)
class PivotConfig:
    daily_start: date
    holdout_start: date
    train_end: dict[str, date]
    walk_forward: WalkForward
    range_model: RangeSpec
    regime_model: RegimeSpec
    candle_accuracy: CandleAccuracy
    holdout_rule: str
    sha256: str

    @property
    def holdout_start_utc(self) -> pd.Timestamp:
        return ist_midnight_utc(self.holdout_start)

    @property
    def daily_start_utc(self) -> pd.Timestamp:
        return ist_midnight_utc(self.daily_start)

    def train_end_for(self, tf: str) -> date:
        return self.train_end["intraday" if is_intraday(validate_tf(tf)) else "1D"]

    def train_end_utc(self, tf: str = REGIME_TIMEFRAME) -> pd.Timestamp:
        return ist_midnight_utc(self.train_end_for(tf))


def _date(value) -> date:
    return date.fromisoformat(value) if isinstance(value, str) else value


def _section(raw, name: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"pivot.yaml {name or 'top level'} must be a mapping")
    unknown, missing = sorted(set(raw) - KNOWN_KEYS[name]), sorted(KNOWN_KEYS[name] - set(raw))
    if unknown or missing:
        raise ValueError(f"pivot.yaml {name or 'top level'}: unknown keys {unknown}, missing keys {missing}")
    return raw


def _expect(value, allowed, key: str):
    if value not in allowed:
        raise ValueError(f"pivot.yaml {key}={value!r} is not implemented; expected one of {allowed!r}")
    return value


def _gate(raw: dict, timeframes: tuple[str, ...]) -> RangeGate:
    _section(raw, "range_model.go_no_go_2")
    lo, hi = (float(x) for x in raw["coverage_80_within"])
    match = _PASS_RULE.match(str(raw["pass_rule"]).strip())
    if match is None:
        raise ValueError(
            f"pivot.yaml range_model.go_no_go_2.pass_rule {raw['pass_rule']!r} is not implemented"
        )
    k, of, a, b = int(match[1]), int(match[2]), match[3], match[4]
    if of != len(timeframes) or not {a, b} <= set(timeframes):
        raise ValueError("pivot.yaml range_model pass_rule doesn't match range_model.timeframes")
    return RangeGate(
        coverage_within=(lo, hi),
        min_improvement=float(raw["interval_score_improvement_vs_best_baseline"]),
        ci_lower_above=float(raw["improvement_ci_lower_above"]),
        min_passing=k,
        required_any=(validate_tf(a), validate_tf(b)),
        rule=str(raw["pass_rule"]).strip(),
    )


def _range_spec(raw: dict) -> RangeSpec:
    _section(raw, "range_model")
    quantiles = tuple(float(q) for q in raw["quantiles"])
    if len(quantiles) != 3 or quantiles[1] != 0.5 or abs(quantiles[0] + quantiles[2] - 1.0) > 1e-12:
        raise ValueError(
            "pivot.yaml range_model.quantiles must be a symmetric band around 0.5, e.g. [0.1, 0.5, 0.9]"
        )
    targets = tuple(str(t) for t in raw["targets"])
    if targets != RANGE_TARGETS:
        raise ValueError(f"pivot.yaml range_model.targets must be {list(RANGE_TARGETS)}")
    timeframes = tuple(validate_tf(str(t)) for t in raw["timeframes"])
    exchanges = tuple(_expect(str(e), EXCHANGES, "range_model.exchanges") for e in raw["exchanges"])
    groups = tuple(
        _expect(str(g), RANGE_FEATURE_GROUPS, "range_model.feature_groups") for g in raw["feature_groups"]
    )
    baselines = tuple(_expect(str(b), RANGE_BASELINES, "range_model.baselines") for b in raw["baselines"])
    steps = int(raw["steps"])
    if steps < 1:
        raise ValueError("pivot.yaml range_model.steps must be at least 1")
    return RangeSpec(
        name=str(raw["name"]),
        algo=_expect(str(raw["algo"]), ("lightgbm_quantile",), "range_model.algo"),
        quantiles=quantiles,  # type: ignore[arg-type]
        targets=targets,
        steps=steps,
        timeframes=timeframes,
        exchanges=exchanges,
        feature_groups=groups,
        baselines=baselines,
        gate=_gate(raw["go_no_go_2"], timeframes),
    )


def _exactly(value, implemented, key: str):
    if value != implemented:
        raise ValueError(f"pivot.yaml {key}={value!r} is not implemented; the code has {implemented!r}")
    return value


def _regime_spec(raw: dict) -> RegimeSpec:
    _section(raw, "regime_model")
    gate = _section(raw["go_no_go_2"], "regime_model.go_no_go_2")
    horizons = tuple(int(h) for h in raw["horizons_days"])
    if not horizons or any(h < 1 for h in horizons):
        raise ValueError("pivot.yaml regime_model.horizons_days must be positive integers")
    groups = tuple(str(g) for g in raw["feature_groups"])
    _exactly(sorted(groups), sorted(REGIME_FEATURE_GROUPS), "regime_model.feature_groups")
    baselines = tuple(str(b) for b in raw["baselines"])
    _exactly(sorted(baselines), sorted(REGIME_BASELINES), "regime_model.baselines")
    return RegimeSpec(
        name=_exactly(str(raw["name"]), REGIME_NAME, "regime_model.name"),
        algo=_exactly(str(raw["algo"]), REGIME_ALGO, "regime_model.algo"),
        timeframe=_exactly(str(raw["timeframe"]), REGIME_TIMEFRAME, "regime_model.timeframe"),
        horizons_days=horizons,
        target=_exactly(str(raw["target"]), REGIME_TARGET, "regime_model.target"),
        feature_groups=groups,
        baselines=baselines,
        go_no_go_2=RegimeGate(
            brier_skill_ci_lower_above=float(gate["brier_skill_ci_lower_above"]),
            calibration_min_p=float(gate["calibration_min_p"]),
            min_scored=int(gate["min_scored"]),
            top_decile_expectancy_after_cost_positive=bool(gate["top_decile_expectancy_after_cost_positive"]),
            pass_rule=_exactly(str(gate["pass_rule"]), REGIME_PASS_RULE, "regime_model.go_no_go_2.pass_rule"),
        ),
    )


def _candle_accuracy(raw: dict) -> CandleAccuracy:
    _section(raw, "candle_accuracy")
    text = {k: str(v).strip() for k, v in raw.items()}
    match = _SAME.match(text["same"])
    if match is None or text["close"] != _CLOSE or text["wrong"] != _WRONG:
        raise ValueError(
            "pivot.yaml candle_accuracy wording changed; the grading code implements the registered text"
        )
    return CandleAccuracy(same_close_atr=float(match[1]), text=text)


@lru_cache(maxsize=8)
def _pivot_cached(path: str, mtime: float) -> PivotConfig:
    raw_bytes = Path(path).read_bytes()
    raw = _section(yaml.safe_load(raw_bytes), "")
    data = _section(raw["data"], "data")
    wf = _section(data["walk_forward"], "data.walk_forward")
    if not bool(wf["expanding"]):
        raise ValueError("pivot.yaml data.walk_forward.expanding=false is not implemented")
    train_end = {str(k): _date(v) for k, v in data["train_end"].items()}
    if set(train_end) != {"1D", "intraday"}:
        raise ValueError("pivot.yaml data.train_end needs exactly the keys 1D and intraday")
    holdout = _date(data["holdout_start"])
    research_holdout = load_research_config().holdout_start
    if holdout != research_holdout:
        raise ValueError(f"pivot.yaml holdout_start {holdout} differs from research.yaml {research_holdout}")
    return PivotConfig(
        daily_start=_date(data["daily_start"]),
        holdout_start=holdout,
        train_end=train_end,
        walk_forward=WalkForward(
            retrain_every_months=int(wf["retrain_every_months"]),
            purge_bars=int(wf["purge_bars"]),
            embargo_bars=int(wf["embargo_bars"]),
            expanding=True,
        ),
        range_model=_range_spec(raw["range_model"]),
        regime_model=_regime_spec(raw["regime_model"]),
        candle_accuracy=_candle_accuracy(raw["candle_accuracy"]),
        holdout_rule=str(raw["holdout_rule"]),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def load_pivot_config(path: Path | None = None) -> PivotConfig:
    path = path or get_settings().config_dir / PIVOT_FILE
    return _pivot_cached(str(path), path.stat().st_mtime)


def pivot_config_hashes() -> dict[str, str]:
    """The research config hashes (research.yaml, costs.yaml, calendar, ...) plus pivot.yaml's."""
    return {**config_hashes(), PIVOT_FILE: load_pivot_config().sha256}
