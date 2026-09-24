"""Reader for the pre-registered edge search (config/edge_search.yaml, PLAN.md §20b).

Strict like pivot.yaml: an unknown or missing key is an error in every section, and so is a value other
than the one registered where the code implements exactly that value (models, baselines, losses, pass
rules). The file is binding and must never be edited to fit a result; its sha256 goes into every report.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from candly.core.settings import get_settings
from candly.research.config import ist_midnight_utc, load_research_config

EDGE_FILE = "edge_search.yaml"

KNOWN_KEYS: dict[str, set[str]] = {
    "": {"common", "cross_section", "volatility", "options_flow"},
    "common": {"holdout_start", "costs", "bootstrap"},
    "common.bootstrap": {"kind", "resamples", "ci_level", "seed"},
    "cross_section": {
        "name",
        "universe",
        "benchmark",
        "horizons_days",
        "rebalance",
        "train_end",
        "purge_days",
        "min_price_inr",
        "feature_groups",
        "model",
        "baselines",
        "portfolio",
        "metrics",
        "pass_if",
    },
    "cross_section.portfolio": {"long", "weighting", "short"},
    "cross_section.pass_if": {
        "rank_ic_ci_lower_above",
        "top_decile_excess_after_cost_ci_lower_above",
        "beats_best_baseline",
        "rule",
    },
    "volatility": {
        "name",
        "underlying",
        "implied",
        "horizons_days",
        "train_end",
        "forecast",
        "baselines",
        "loss",
        "pnl_proxy",
        "pass_if",
        "caveat",
    },
    "volatility.pass_if": {
        "qlike_improvement_vs_vix_ci_lower_above",
        "conditional_vs_always_short_sharpe_ci_lower_above",
        "rule",
    },
    "options_flow": {"name", "status", "pre_register_before"},
}

BOOTSTRAP_KINDS = ("date_block",)
COSTS_PATH = "config/costs.yaml"

XS_NAME = "xs_v1"
XS_BENCHMARK = "equal_weight_universe"
XS_MODEL = "lightgbm_lambdarank"
XS_REBALANCE = ("weekly_first_trading_day", "monthly_first_trading_day")
XS_PORTFOLIO = {"long": "top_decile", "weighting": "equal", "short": "none"}
XS_PASS_RULE = "at least one horizon passes every check"

VOL_NAME = "vol_v1"
VOL_FORECAST = "realized_vol_model"
VOL_BASELINES = ("india_vix", "har_rv", "ewma_rv")
VOL_LOSS = "qlike"
VOL_PNL_PROXY = "variance_swap"
VOL_PASS_RULE = "at least one horizon passes both checks"

OPTIONS_FLOW_NAME = "oi_v1"


@dataclass(frozen=True)
class Bootstrap:
    kind: str
    resamples: int
    ci_level: float
    seed: int


@dataclass(frozen=True)
class Common:
    holdout_start: date
    costs: str
    bootstrap: Bootstrap

    @property
    def holdout_start_utc(self) -> pd.Timestamp:
        return ist_midnight_utc(self.holdout_start)


@dataclass(frozen=True)
class Portfolio:
    long: str
    weighting: str
    short: str


@dataclass(frozen=True)
class CrossSectionGate:
    rank_ic_ci_lower_above: float
    top_decile_excess_after_cost_ci_lower_above: float
    beats_best_baseline: bool
    rule: str


@dataclass(frozen=True)
class CrossSectionSpec:
    name: str
    universe: str
    benchmark: str
    horizons_days: tuple[int, ...]
    rebalance: dict[int, str]
    train_end: date
    purge_days: int
    min_price_inr: float
    feature_groups: tuple[str, ...]
    model: str
    baselines: tuple[str, ...]
    portfolio: Portfolio
    metrics: tuple[str, ...]
    pass_if: CrossSectionGate

    @property
    def train_end_utc(self) -> pd.Timestamp:
        return ist_midnight_utc(self.train_end)


@dataclass(frozen=True)
class VolatilityGate:
    qlike_improvement_vs_vix_ci_lower_above: float
    conditional_vs_always_short_sharpe_ci_lower_above: float
    rule: str


@dataclass(frozen=True)
class VolatilitySpec:
    name: str
    underlying: str
    implied: str
    horizons_days: tuple[int, ...]
    train_end: date
    forecast: str
    baselines: tuple[str, ...]
    loss: str
    pnl_proxy: str
    pass_if: VolatilityGate
    caveat: str

    @property
    def train_end_utc(self) -> pd.Timestamp:
        return ist_midnight_utc(self.train_end)


@dataclass(frozen=True)
class OptionsFlowSpec:
    name: str
    status: str
    pre_register_before: str


@dataclass(frozen=True)
class EdgeSearchConfig:
    common: Common
    cross_section: CrossSectionSpec
    volatility: VolatilitySpec
    options_flow: OptionsFlowSpec
    sha256: str


def _section(raw, name: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{EDGE_FILE} {name or 'top level'} must be a mapping")
    unknown, missing = sorted(set(raw) - KNOWN_KEYS[name]), sorted(KNOWN_KEYS[name] - set(raw))
    if unknown or missing:
        raise ValueError(f"{EDGE_FILE} {name or 'top level'}: unknown keys {unknown}, missing keys {missing}")
    return raw


def _exactly(value, registered, key: str):
    if value != registered:
        raise ValueError(f"{EDGE_FILE} {key}={value!r} is not implemented; the code has {registered!r}")
    return value


def _date(value, key: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{EDGE_FILE} {key}={value!r} is not a date") from None


def _names(value, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{EDGE_FILE} {key} must be a non-empty list")
    names = tuple(str(v) for v in value)
    if len(set(names)) != len(names):
        raise ValueError(f"{EDGE_FILE} {key} has duplicates")
    return names


def _horizons(value, key: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{EDGE_FILE} {key} must be a non-empty list")
    horizons = tuple(int(h) for h in value)
    if any(h < 1 for h in horizons) or len(set(horizons)) != len(horizons):
        raise ValueError(f"{EDGE_FILE} {key} must be distinct positive integers")
    return horizons


def _instrument(value, key: str) -> str:
    text = str(value)
    exchange, _, symbol = text.partition(":")
    if not exchange or not symbol:
        raise ValueError(f"{EDGE_FILE} {key}={value!r} must look like EXCHANGE:SYMBOL")
    return text


def _common(raw) -> Common:
    raw = _section(raw, "common")
    boot = _section(raw["bootstrap"], "common.bootstrap")
    kind = str(boot["kind"])
    if kind not in BOOTSTRAP_KINDS:
        raise ValueError(
            f"{EDGE_FILE} common.bootstrap.kind={kind!r} is not implemented; use {BOOTSTRAP_KINDS}"
        )
    resamples, level = int(boot["resamples"]), float(boot["ci_level"])
    if resamples < 1 or not 0.0 < level < 1.0:
        raise ValueError(f"{EDGE_FILE} common.bootstrap needs resamples >= 1 and 0 < ci_level < 1")
    return Common(
        holdout_start=_date(raw["holdout_start"], "common.holdout_start"),
        costs=_exactly(str(raw["costs"]), COSTS_PATH, "common.costs"),
        bootstrap=Bootstrap(kind=kind, resamples=resamples, ci_level=level, seed=int(boot["seed"])),
    )


def _cross_section(raw) -> CrossSectionSpec:
    raw = _section(raw, "cross_section")
    portfolio = _section(raw["portfolio"], "cross_section.portfolio")
    gate = _section(raw["pass_if"], "cross_section.pass_if")
    horizons = _horizons(raw["horizons_days"], "cross_section.horizons_days")
    rebalance = raw["rebalance"]
    if not isinstance(rebalance, dict) or sorted(int(k) for k in rebalance) != sorted(horizons):
        raise ValueError(f"{EDGE_FILE} cross_section.rebalance needs exactly one entry per horizon")
    rebalance = {int(k): str(v) for k, v in rebalance.items()}
    for h, rule in rebalance.items():
        if rule not in XS_REBALANCE:
            raise ValueError(
                f"{EDGE_FILE} cross_section.rebalance.{h}={rule!r}; expected one of {XS_REBALANCE}"
            )
    portfolio = {k: str(v) for k, v in portfolio.items()}
    _exactly(portfolio, XS_PORTFOLIO, "cross_section.portfolio")
    purge = int(raw["purge_days"])
    if purge < max(horizons):
        raise ValueError(f"{EDGE_FILE} cross_section.purge_days must cover the longest horizon")
    return CrossSectionSpec(
        name=_exactly(str(raw["name"]), XS_NAME, "cross_section.name"),
        universe=str(raw["universe"]),
        benchmark=_exactly(str(raw["benchmark"]), XS_BENCHMARK, "cross_section.benchmark"),
        horizons_days=horizons,
        rebalance=rebalance,
        train_end=_date(raw["train_end"], "cross_section.train_end"),
        purge_days=purge,
        min_price_inr=float(raw["min_price_inr"]),
        feature_groups=_names(raw["feature_groups"], "cross_section.feature_groups"),
        model=_exactly(str(raw["model"]), XS_MODEL, "cross_section.model"),
        baselines=_names(raw["baselines"], "cross_section.baselines"),
        portfolio=Portfolio(**portfolio),
        metrics=_names(raw["metrics"], "cross_section.metrics"),
        pass_if=CrossSectionGate(
            rank_ic_ci_lower_above=float(gate["rank_ic_ci_lower_above"]),
            top_decile_excess_after_cost_ci_lower_above=float(gate["top_decile_excess_after_cost_ci_lower_above"]),
            beats_best_baseline=bool(gate["beats_best_baseline"]),
            rule=_exactly(str(gate["rule"]).strip(), XS_PASS_RULE, "cross_section.pass_if.rule"),
        ),
    )


def _volatility(raw) -> VolatilitySpec:
    raw = _section(raw, "volatility")
    gate = _section(raw["pass_if"], "volatility.pass_if")
    baselines = _names(raw["baselines"], "volatility.baselines")
    _exactly(sorted(baselines), sorted(VOL_BASELINES), "volatility.baselines")
    return VolatilitySpec(
        name=_exactly(str(raw["name"]), VOL_NAME, "volatility.name"),
        underlying=_instrument(raw["underlying"], "volatility.underlying"),
        implied=_instrument(raw["implied"], "volatility.implied"),
        horizons_days=_horizons(raw["horizons_days"], "volatility.horizons_days"),
        train_end=_date(raw["train_end"], "volatility.train_end"),
        forecast=_exactly(str(raw["forecast"]), VOL_FORECAST, "volatility.forecast"),
        baselines=baselines,
        loss=_exactly(str(raw["loss"]), VOL_LOSS, "volatility.loss"),
        pnl_proxy=_exactly(str(raw["pnl_proxy"]), VOL_PNL_PROXY, "volatility.pnl_proxy"),
        pass_if=VolatilityGate(
            qlike_improvement_vs_vix_ci_lower_above=float(gate["qlike_improvement_vs_vix_ci_lower_above"]),
            conditional_vs_always_short_sharpe_ci_lower_above=float(
                gate["conditional_vs_always_short_sharpe_ci_lower_above"]
            ),
            rule=_exactly(str(gate["rule"]).strip(), VOL_PASS_RULE, "volatility.pass_if.rule"),
        ),
        caveat=str(raw["caveat"]).strip(),
    )


def _options_flow(raw) -> OptionsFlowSpec:
    raw = _section(raw, "options_flow")
    return OptionsFlowSpec(
        name=_exactly(str(raw["name"]), OPTIONS_FLOW_NAME, "options_flow.name"),
        status=str(raw["status"]),
        pre_register_before=str(raw["pre_register_before"]),
    )


def parse_edge_config(raw_bytes: bytes, research_holdout: date | None = None) -> EdgeSearchConfig:
    raw = _section(yaml.safe_load(raw_bytes), "")
    common = _common(raw["common"])
    research_holdout = research_holdout or load_research_config().holdout_start
    if common.holdout_start != research_holdout:
        raise ValueError(
            f"{EDGE_FILE} common.holdout_start {common.holdout_start} differs from research.yaml "
            f"{research_holdout}"
        )
    cross_section, volatility = _cross_section(raw["cross_section"]), _volatility(raw["volatility"])
    for name, spec in (("cross_section", cross_section), ("volatility", volatility)):
        if spec.train_end >= common.holdout_start:
            raise ValueError(f"{EDGE_FILE} {name}.train_end must be before common.holdout_start")
    return EdgeSearchConfig(
        common=common,
        cross_section=cross_section,
        volatility=volatility,
        options_flow=_options_flow(raw["options_flow"]),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


@lru_cache(maxsize=8)
def _edge_cached(path: str, mtime_ns: int) -> EdgeSearchConfig:
    return parse_edge_config(Path(path).read_bytes())


def load_edge_config(path: Path | None = None) -> EdgeSearchConfig:
    path = path or get_settings().config_dir / EDGE_FILE
    return _edge_cached(str(path), path.stat().st_mtime_ns)
