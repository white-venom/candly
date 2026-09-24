"""Validation of xs_v1 (config/edge_search.yaml `cross_section`) and pv2_xs (config/edge_search_v2.yaml
`cross_section_patterns`): which Nifty 200 stocks beat the equal-weight universe over 5 and 20 days, and
do candlestick-pattern counts improve that ranking? PLAN.md §20b-c. Validation only: nothing on or after
the holdout start is loaded.

Pipeline: candly.research.xs_panel (bars, causal features, eligibility, forward returns, schedule) ->
candly.research.xs_model (walk-forward LightGBM lambdarank, yearly refits from train_end) ->
candly.research.xs_backtest (long-only top decile vs the equal-weight candidates, after costs).
Strategies per horizon: the xs_v1 model, the pv2_xs model (xs_v1 features + candlestick_patterns, same
hyperparameters and seed), and the three single-factor baselines.

Statistics: one observation per rebalance period (rank IC, period returns); the weekly hit rate has one
per week, grouped under the period its week starts in. Date-block bootstrap (edge_search.yaml
common.bootstrap): each rebalance period is one block of consecutive dates; blocks are drawn with
replacement, and every strategy and comparison of a horizon shares the same draws (paired). Percentile
CIs. One-sided p of "statistic > 0": p = (1 + #{bootstrap statistic <= 0}) / (1 + resamples). A
diagnostic CI resamples IST calendar quarters instead (serial dependence across weeks); it never changes
a gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from candly.core.calendar import IST
from candly.core.settings import REPO_ROOT, get_settings
from candly.research.config import load_costs_config, load_research_config
from candly.research.costs import cost_breakdown
from candly.research.data import CandleLoader
from candly.research.edge_config import EDGE_FILE, CrossSectionSpec, EdgeSearchConfig, load_edge_config
from candly.research.edge_v2_config import EDGE_V2_FILE, edge_v2_section, edge_v2_sha256
from candly.research.stats import benjamini_hochberg
from candly.research.xs_backtest import PeriodBook, Simulation, build_books, restrict_books, simulate
from candly.research.xs_model import hyperparameters, walk_forward, yearly_windows
from candly.research.xs_panel import (
    BASE_FEATURES,
    BASELINES,
    MARKET_ID,
    PATTERN_FEATURES,
    PATTERN_GROUP,
    Panel,
    build_panel,
    label_column,
    panel_frame,
    rank_column,
    rebalance_periods,
    universe_members,
)

XS_NAME = "xs_v1"
PV2_NAME = "pv2_xs"
MODEL_KEY = "model"
XS_REPORT = "2026-09-24-xs-v1-validation.json"
PV2_REPORT = "2026-09-24-pv2-xs-validation.json"
PERIODS_PER_YEAR = {"weekly_first_trading_day": 52.0, "monthly_first_trading_day": 12.0}
EQUITY_KIND, HOLDING = "equity", "multi_day"
LARGE_MOVE = 0.35  # |daily log return| listed as a data note (possible unadjusted corporate action)
PV2_KEYS = {"name", "base", "added_feature_group", "horizons_days", "primary", "pass_if"}
PV2_GATES = {"rank_ic_improvement_ci_lower_above", "excess_after_cost_not_worse_than_xs_v1"}
HASHED = ("costs.yaml", "research.yaml", "patterns.yaml")


def report_dir() -> Path:
    return REPO_ROOT / "docs" / "test-reports"


def predictions_dir() -> Path:
    return get_settings().data_dir / "models" / XS_NAME


# --- pre-registration checks -----------------------------------------------------------------------------


def check_xs_spec(spec: CrossSectionSpec) -> None:
    """The code implements exactly these registered values; anything else is an error, not a variant."""
    if sorted(spec.feature_groups) != sorted(BASE_FEATURES):
        raise ValueError(f"{EDGE_FILE} feature_groups {spec.feature_groups} differ from {BASE_FEATURES}")
    if sorted(spec.baselines) != sorted(BASELINES):
        raise ValueError(f"{EDGE_FILE} baselines {spec.baselines} differ from {tuple(BASELINES)}")
    for h, rule in spec.rebalance.items():
        if rule not in PERIODS_PER_YEAR:
            raise ValueError(f"{EDGE_FILE} rebalance {h}: {rule!r} is not implemented")


def check_pv2_section(section: dict, spec: CrossSectionSpec) -> dict:
    if not isinstance(section, dict) or set(section) != PV2_KEYS:
        raise ValueError(f"{EDGE_V2_FILE} cross_section_patterns keys differ from {sorted(PV2_KEYS)}")
    expected = {"name": PV2_NAME, "base": spec.name, "added_feature_group": PATTERN_GROUP}
    for key, value in expected.items():
        if str(section[key]) != value:
            raise ValueError(
                f"{EDGE_V2_FILE} cross_section_patterns.{key}={section[key]!r}; code has {value!r}"
            )
    if tuple(int(h) for h in section["horizons_days"]) != spec.horizons_days:
        raise ValueError(f"{EDGE_V2_FILE} pv2_xs horizons differ from xs_v1's {spec.horizons_days}")
    gate = section["pass_if"]
    if not isinstance(gate, dict) or set(gate) != PV2_GATES:
        raise ValueError(f"{EDGE_V2_FILE} pv2_xs pass_if keys differ from {sorted(PV2_GATES)}")
    return section


# --- bootstrap ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Draws:
    idx: np.ndarray  # (resamples, k) block indices in draw order
    counts: np.ndarray  # (resamples, k) times each block was drawn


def draw_blocks(k: int, resamples: int, seed: int) -> Draws:
    idx = np.random.default_rng(seed).integers(0, k, size=(resamples, k))
    counts = np.zeros((resamples, k))
    np.add.at(counts, (np.arange(resamples)[:, None], idx), 1.0)
    return Draws(idx=idx, counts=counts)


def _block_moments(values, blocks, k: int):
    values, blocks = np.asarray(values, float), np.asarray(blocks)
    ok = np.isfinite(values)
    n = np.bincount(blocks[ok], minlength=k).astype(float)
    s1 = np.bincount(blocks[ok], weights=values[ok], minlength=k)
    s2 = np.bincount(blocks[ok], weights=values[ok] ** 2, minlength=k)
    return n, s1, s2


def block_means(values, blocks, draws: Draws) -> np.ndarray:
    n, s1, _ = _block_moments(values, blocks, draws.counts.shape[1])
    with np.errstate(divide="ignore", invalid="ignore"):
        return (draws.counts @ s1) / (draws.counts @ n)


def block_tstats(values, blocks, draws: Draws) -> np.ndarray:
    n, s1, s2 = _block_moments(values, blocks, draws.counts.shape[1])
    total, sums, squares = draws.counts @ n, draws.counts @ s1, draws.counts @ s2
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = sums / total
        var = (squares - total * mean**2) / (total - 1.0)
        return mean / np.sqrt(var / total)


def tstat(values) -> float | None:
    x = np.asarray(values, float)
    x = x[np.isfinite(x)]
    if x.size < 2 or x.std(ddof=1) == 0:
        return None
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(x.size)))


def relative_log_returns(net, bench) -> np.ndarray:
    return np.log1p(np.asarray(net, float)) - np.log1p(np.asarray(bench, float))


def max_drawdown(log_rel) -> float:
    """Largest fall of exp(cumulative log relative return) from its running peak (starting at 1)."""
    path = np.concatenate([[0.0], np.cumsum(np.asarray(log_rel, float))])
    return float(1.0 - np.exp(-(np.maximum.accumulate(path) - path).max()))


def block_max_drawdowns(log_rel, draws: Draws) -> np.ndarray:
    """Max drawdown of each resample: the drawn periods chained in draw order (one period per block)."""
    paths = np.cumsum(np.asarray(log_rel, float)[draws.idx], axis=1)
    peak = np.maximum(np.maximum.accumulate(paths, axis=1), 0.0)
    return 1.0 - np.exp(-(peak - paths).max(axis=1))


def p_one_sided(samples) -> float | None:
    """One-sided bootstrap p of "statistic > 0": (1 + #{samples <= 0}) / (1 + resamples)."""
    s = np.asarray(samples, float)
    s = s[np.isfinite(s)]
    return float((1 + (s <= 0).sum()) / (1 + s.size)) if s.size else None


def percentile_ci(samples, level: float) -> list[float] | None:
    s = np.asarray(samples, float)
    s = s[np.isfinite(s)]
    if s.size == 0:
        return None
    tail = (1.0 - level) / 2.0
    return [float(x) for x in np.quantile(s, [tail, 1.0 - tail])]


@dataclass(frozen=True)
class Resampling:
    """Shared draws of one horizon: primary (one block per period) and diagnostic (quarters)."""

    level: float
    periods: Draws
    quarters: Draws
    quarter_of_period: np.ndarray

    def stat(self, values, period_of_obs, scale: float = 1.0) -> dict:
        values = np.asarray(values, float)
        period_of_obs = np.asarray(period_of_obs)
        point = float(np.nanmean(values)) * scale if np.isfinite(values).any() else None
        samples = block_means(values, period_of_obs, self.periods) * scale
        quarter = block_means(values, self.quarter_of_period[period_of_obs], self.quarters) * scale
        return {
            "value": point,
            "ci": percentile_ci(samples, self.level),
            "p_one_sided": p_one_sided(samples),
            "ci_quarter_blocks": percentile_ci(quarter, self.level),
            "p_one_sided_quarter_blocks": p_one_sided(quarter),
        }


def _finite_mean(values, where=None) -> float | None:
    x = np.asarray(values, float)
    ok = np.isfinite(x) if where is None else np.asarray(where, bool)
    return float(x[ok].mean()) if ok.any() else None


def _geometric_annual(returns, ppy: float) -> float | None:
    r = np.asarray(returns, float)
    if r.size == 0:
        return None
    return float(np.exp(np.log1p(r).sum() * ppy / r.size) - 1.0)


def _ci_only(stat: dict) -> dict:
    """A bootstrap summary without the "> 0" p-values, for statistics where that test means nothing."""
    return {k: v for k, v in stat.items() if not k.startswith("p_one_sided")}


def strategy_metrics(sim: Simulation, ppy: float, rs: Resampling) -> dict:
    k = sim.net.size
    period = np.arange(k)
    port_w, bench_w, week_block = sim.weekly()
    weekly_hits = (port_w > bench_w).astype(float)
    log_rel = relative_log_returns(sim.net, sim.bench)
    excess = sim.excess
    sd = excess.std(ddof=1) if k > 1 else float("nan")
    weekly_rel = np.log(sim.week_wealth / sim.week_bench) if sim.week_wealth.size else np.zeros(0)
    ic_t_samples = block_tstats(sim.ic, period, rs.periods)
    return {
        "n_periods": int(k),
        "n_weeks": int(port_w.size),
        "mean_candidates": float(sim.n_candidates.mean()),
        "mean_top": float(sim.n_top.mean()),
        "rank_ic_mean": rs.stat(sim.ic, period),
        "rank_ic_t": {
            "value": tstat(sim.ic),
            "ci": percentile_ci(ic_t_samples, rs.level),
            "note": "mean / (sd / sqrt(n)) over rebalance periods; bootstrap CI of the same statistic",
        },
        "rank_ic_positive_share": _finite_mean(sim.ic > 0, np.isfinite(sim.ic)),
        "top_decile_excess_after_cost_annual": rs.stat(excess, period, ppy),
        "top_decile_excess_gross_annual": rs.stat(sim.excess_gross, period, ppy),
        "top_decile_return_after_cost_annual": rs.stat(sim.net, period, ppy),
        "benchmark_return_annual": _ci_only(rs.stat(sim.bench, period, ppy)),
        "top_decile_cagr_after_cost": _geometric_annual(sim.net, ppy),
        "benchmark_cagr": _geometric_annual(sim.bench, ppy),
        "information_ratio_after_cost": float(excess.mean() / sd * math.sqrt(ppy)) if sd > 0 else None,
        "weekly_hit_rate_vs_benchmark": _ci_only(rs.stat(weekly_hits, week_block)),
        "period_hit_rate_vs_benchmark": _ci_only(rs.stat((excess > 0).astype(float), period)),
        "turnover": {
            **_ci_only(rs.stat(sim.turnover, period)),
            "annual": float(sim.turnover.mean() * ppy),
            "note": "one-way turnover per rebalance, sum|w+ - w-| / 2",
        },
        "cost_annual": _ci_only(rs.stat(sim.cost, period, ppy)),
        "max_drawdown_excess": {
            "value": max_drawdown(log_rel),
            "ci": percentile_ci(block_max_drawdowns(log_rel, rs.periods), rs.level),
            "weekly_marks_value": max_drawdown(np.diff(weekly_rel)) if weekly_rel.size > 1 else None,
            "note": "drawdown of (1 + net) / (1 + benchmark) chained over periods",
        },
    }


def paired(rs: Resampling, a, b, scale: float = 1.0) -> dict:
    return rs.stat(np.asarray(a, float) - np.asarray(b, float), np.arange(len(a)), scale)


def by_year(sim: Simulation, dates: pd.DatetimeIndex) -> dict[str, dict]:
    years = dates[sim.entry].tz_convert(IST).year.to_numpy()
    out = {}
    for y in np.unique(years):
        m = years == y
        out[str(int(y))] = {
            "n_periods": int(m.sum()),
            "excess_after_cost_sum": float(sim.excess[m].sum()),
            "rank_ic_mean": _finite_mean(sim.ic[m]),
        }
    return out


# --- evaluation ---------------------------------------------------------------------------------------------


def equity_round_trip() -> tuple[float, dict[str, float]]:
    parts = cost_breakdown(EQUITY_KIND, HOLDING, costs=load_costs_config())
    return float(sum(parts.values())), parts


@dataclass
class HorizonResult:
    h: int
    rule: str
    periods: pd.DataFrame
    folds: dict[str, list[dict]]
    sims: dict[str, Simulation]
    metrics: dict[str, dict]
    comparisons: dict[str, dict]
    by_year: dict[str, dict]
    diagnostics: dict
    scores: dict[str, np.ndarray] = field(repr=False)


def evaluate_horizon(
    panel: Panel,
    frame: pd.DataFrame,
    spec: CrossSectionSpec,
    edge: EdgeSearchConfig,
    h: int,
    cost_rt: float,
    params: dict | None = None,
    rounds: int | None = None,
) -> HorizonResult:
    holdout = edge.common.holdout_start_utc
    if (panel.dates >= holdout).any():
        raise PermissionError("the panel holds holdout bars")
    rule = spec.rebalance[h]
    periods = rebalance_periods(panel.dates, rule, spec.train_end_utc, holdout)
    books = build_books(panel, frame, periods)
    windows = yearly_windows(spec.train_end_utc, holdout)
    columns = {
        XS_NAME: [rank_column(f) for f in BASE_FEATURES],
        PV2_NAME: [rank_column(f) for f in (*BASE_FEATURES, *PATTERN_FEATURES)],
    }
    scores, folds = {}, {}
    for name, cols in columns.items():
        scores[name], folds[name] = walk_forward(
            frame, cols, h, panel.dates, windows, periods, spec.purge_days, params, rounds
        )
    for name, (col, sign) in BASELINES.items():
        scores[name] = sign * frame[col].to_numpy(dtype=float)
    sims = {name: simulate(books, s, len(panel.ids), cost_rt) for name, s in scores.items()}
    boot = edge.common.bootstrap
    entry_ist = panel.dates[periods["entry"].to_numpy()].tz_convert(IST)
    quarter_key = entry_ist.year.to_numpy() * 4 + (entry_ist.month.to_numpy() - 1) // 3
    _, quarter_of_period = np.unique(quarter_key, return_inverse=True)
    rs = Resampling(
        level=boot.ci_level,
        periods=draw_blocks(len(books), boot.resamples, boot.seed),
        quarters=draw_blocks(int(quarter_of_period.max()) + 1, boot.resamples, boot.seed),
        quarter_of_period=quarter_of_period,
    )
    ppy = PERIODS_PER_YEAR[rule]
    metrics = {name: strategy_metrics(sim, ppy, rs) for name, sim in sims.items()}
    xs, pv2 = sims[XS_NAME], sims[PV2_NAME]
    comparisons = {
        "pv2_minus_xs_rank_ic": paired(rs, pv2.ic, xs.ic),
        "pv2_minus_xs_excess_after_cost_annual": paired(rs, pv2.excess, xs.excess, ppy),
        **{
            f"xs_minus_{name}_excess_after_cost_annual": paired(rs, xs.excess, sims[name].excess, ppy)
            for name in BASELINES
        },
        **{f"xs_minus_{name}_rank_ic": paired(rs, xs.ic, sims[name].ic) for name in BASELINES},
    }
    return HorizonResult(
        h=h,
        rule=rule,
        periods=periods,
        folds=folds,
        sims=sims,
        metrics=metrics,
        comparisons=comparisons,
        by_year={name: by_year(sim, panel.dates) for name, sim in sims.items()},
        diagnostics=survivorship_diagnostics(panel, frame, books, scores, sims, cost_rt, ppy, rs),
        scores=scores,
    )


# --- survivorship diagnostics (never change a gate) --------------------------------------------------------

LIQUID_RANK_MIN = 0.5
COMPACT_METRICS = (
    "rank_ic_mean",
    "top_decile_excess_after_cost_annual",
    "top_decile_excess_gross_annual",
    "turnover",
)


def listing_age_years(panel: Panel, frame: pd.DataFrame) -> np.ndarray:
    """Years from a stock's first stored bar to the row's day (causal; the store starts in 1999, so older
    listings are censored there)."""
    first = panel.dates[panel.traded.to_numpy().argmax(axis=0)]
    t, stock = frame["t"].to_numpy(), frame["stock"].to_numpy()
    return np.asarray((panel.dates[t] - first[stock]).days, dtype=float) / 365.25


def _compact(metrics: dict) -> dict:
    return {k: metrics[k] for k in COMPACT_METRICS}


def survivorship_diagnostics(
    panel: Panel,
    frame: pd.DataFrame,
    books: list[PeriodBook],
    scores: dict[str, np.ndarray],
    sims: dict[str, Simulation],
    cost_rt: float,
    ppy: float,
    rs: Resampling,
) -> dict:
    """How much of a result a current-constituents universe could explain by itself.

    - survivor_proxies: single-factor top deciles on signals that only pay off because today's list is
      made of stocks that grew into it: the smallest traded value, the most recent listing, and the
      equal-weight sum of the momentum and small-traded-value ranks (hand-built, diagnostic only).
    - liquid_half: every registered strategy re-run with only the candidates in the upper half of that
      day's traded-value ranks (already large then, so less exposed to future index entrants).
    - holdings_tilt: mean traded-value and momentum ranks and median listing age of the holdings.
    """
    age = listing_age_years(panel, frame)
    rank_turnover = frame[rank_column("turnover")].to_numpy(dtype=float)
    proxies = {
        "small_turnover": -frame["turnover"].to_numpy(dtype=float),
        "young_listing": -age,
        "momentum_plus_small_turnover": frame[rank_column("momentum_12_1")].to_numpy(dtype=float)
        + 1.0
        - rank_turnover,
    }
    n = len(panel.ids)
    survivor = {
        name: _compact(strategy_metrics(simulate(books, s, n, cost_rt), ppy, rs))
        for name, s in proxies.items()
    }
    liquid_books = restrict_books(books, np.nan_to_num(rank_turnover, nan=-1.0) >= LIQUID_RANK_MIN)
    liquid = {
        name: _compact(strategy_metrics(simulate(liquid_books, s, n, cost_rt), ppy, rs))
        for name, s in scores.items()
    }
    candidate_rows = np.concatenate([b.rows for b in books])
    momentum = frame[rank_column("momentum_12_1")].to_numpy(dtype=float)
    tilt = {}
    for name, sim in sims.items():
        held = np.concatenate(sim.held_rows)
        tilt[name] = {
            "mean_rank_turnover_held": float(np.nanmean(rank_turnover[held])),
            "mean_rank_momentum_12_1_held": float(np.nanmean(momentum[held])),
            "median_listing_age_years_held": float(np.median(age[held])),
        }
    return {
        "note": "diagnostics only; not registered and never part of a gate",
        "survivor_proxies": survivor,
        "liquid_half": {
            "rule": f"candidates with rank_turnover >= {LIQUID_RANK_MIN} on the signal day",
            "mean_candidates": float(np.mean([b.rows.size for b in liquid_books])),
            "strategies": liquid,
        },
        "holdings_tilt": tilt,
        "candidates_median_listing_age_years": float(np.median(age[candidate_rows])),
    }


def _ci_lower_gate(stat: dict, threshold: float) -> dict:
    ci = stat.get("ci")
    return {
        "value": stat.get("value"),
        "ci": ci,
        "threshold": threshold,
        "pass": ci is not None and ci[0] > threshold,
        "diagnostic_quarter_blocks_pass": bool(
            stat.get("ci_quarter_blocks") and stat["ci_quarter_blocks"][0] > threshold
        ),
    }


def xs_gates(r: HorizonResult, spec: CrossSectionSpec) -> tuple[dict, str]:
    gate = spec.pass_if
    model = r.metrics[XS_NAME]
    excess = {name: r.metrics[name]["top_decile_excess_after_cost_annual"]["value"] for name in BASELINES}
    best = max(excess, key=lambda n: -math.inf if excess[n] is None else excess[n])
    mine = model["top_decile_excess_after_cost_annual"]["value"]
    gates = {
        "rank_ic_ci_lower_above": _ci_lower_gate(model["rank_ic_mean"], gate.rank_ic_ci_lower_above),
        "top_decile_excess_after_cost_ci_lower_above": _ci_lower_gate(
            model["top_decile_excess_after_cost_annual"], gate.top_decile_excess_after_cost_ci_lower_above
        ),
        "beats_best_baseline": {
            "required": gate.beats_best_baseline,
            "model_excess_after_cost_annual": mine,
            "best_baseline": best,
            "best_baseline_excess_after_cost_annual": excess[best],
            "pass": (mine is not None and excess[best] is not None and mine > excess[best])
            if gate.beats_best_baseline
            else True,
            "paired_difference": r.comparisons[f"xs_minus_{best}_excess_after_cost_annual"],
        },
    }
    return gates, best


def pv2_gates(r: HorizonResult, section: dict) -> dict:
    gate = section["pass_if"]
    pv2_ex = r.metrics[PV2_NAME]["top_decile_excess_after_cost_annual"]["value"]
    xs_ex = r.metrics[XS_NAME]["top_decile_excess_after_cost_annual"]["value"]
    need_not_worse = bool(gate["excess_after_cost_not_worse_than_xs_v1"])
    return {
        "rank_ic_improvement_ci_lower_above": _ci_lower_gate(
            r.comparisons["pv2_minus_xs_rank_ic"], float(gate["rank_ic_improvement_ci_lower_above"])
        ),
        "excess_after_cost_not_worse_than_xs_v1": {
            "required": need_not_worse,
            "pv2_excess_after_cost_annual": pv2_ex,
            "xs_v1_excess_after_cost_annual": xs_ex,
            "pass": (pv2_ex is not None and xs_ex is not None and pv2_ex >= xs_ex)
            if need_not_worse
            else True,
            "paired_difference": r.comparisons["pv2_minus_xs_excess_after_cost_annual"],
        },
    }


# --- reports ------------------------------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config_sha256(edge: EdgeSearchConfig, spec: CrossSectionSpec, with_v2: bool) -> dict[str, str]:
    config_dir = get_settings().config_dir
    out = {EDGE_FILE: edge.sha256}
    if with_v2:
        out[EDGE_V2_FILE] = edge_v2_sha256()
    for name in (*HASHED, Path(spec.universe).name):
        out[name] = _sha256(config_dir / name)
    return out


def large_moves(panel: Panel, start: pd.Timestamp) -> list[dict]:
    log_cf = np.log(panel.close.ffill())
    r = log_cf.diff().where(panel.traded)
    r = r[r.index >= start]
    t, j = np.nonzero((r.abs() > LARGE_MOVE).to_numpy())
    return [
        {
            "id": panel.ids[b],
            "date": r.index[a].tz_convert(IST).date().isoformat(),
            "log_return": float(r.iat[a, b]),
        }
        for a, b in zip(t, j, strict=True)
    ]


def _split(
    spec: CrossSectionSpec, edge: EdgeSearchConfig, results: dict[int, HorizonResult], name: str
) -> dict:
    return {
        "train_end": spec.train_end.isoformat(),
        "validation": [spec.train_end.isoformat(), edge.common.holdout_start.isoformat()],
        "holdout_start": edge.common.holdout_start.isoformat(),
        "holdout_start_utc": edge.common.holdout_start_utc.isoformat(),
        "retrain": "expanding window, refit at the start of every IST calendar year from train_end",
        "purge_days": spec.purge_days,
        "purge_rule": "a training row's signal day is >= purge_days sessions before the last session before "
        "the cut AND its label ends before the cut",
        "horizons": {
            str(h): {
                "rebalance": r.rule,
                "n_rebalances": int(r.sims[name].net.size),
                "first_entry": r.periods["entry_ts"].iloc[0].isoformat(),
                "last_exit": r.periods["exit_ts"].iloc[-1].isoformat(),
                "folds": r.folds[name],
            }
            for h, r in results.items()
        },
    }


COMMON_DEVIATIONS = [
    "Bootstrap block length is not registered: the gates resample whole rebalance periods (one period = one "
    "block of consecutive dates, the house precedent of h-day blocks); a CI resampling IST calendar quarters "
    "is reported as a diagnostic and flagged when it disagrees.",
    "Execution (not registered): signals use bars up to the close of the session before the rebalance day; "
    "trades fill at the rebalance day's open and positions are held to the next rebalance day's open (the "
    "last traded close before it when a stock has no bar that day). More conservative than same-close fills.",
    "Holding period = the rebalance interval (weekly for h=5, monthly for h=20), so a 'horizon' is 3-6 or "
    "18-23 sessions; rank IC is measured against the same holding-period return the portfolio earns.",
    "The benchmark is the equal-weight portfolio of the same candidates, gross of costs (a frictionless "
    "index); only the strategy pays costs.",
    "Eligibility (not registered beyond the price floor): a bar on the signal day and on the entry day, "
    "close >= min_price_inr, and reversal_1w, momentum_12_1 and volatility_60d defined (about one year of "
    "history). Model, baselines and benchmark all use the same candidates.",
    "min_price_inr is applied to the stored close, which is split/bonus-adjusted (Fyers history): a stock "
    "that later split traded above its adjusted price, so the floor uses information from later corporate "
    "actions. There is no unadjusted price in the store to do better.",
    "Feature definitions are not registered in detail; the definitions fixed before the run are in "
    "'definitions'. beta_1y is against the NIFTY 50 index; sector_relative_return uses today's industry "
    "from the universe file and 21-session returns; turnover is log traded value (no share counts exist).",
    "Model training: every trading day is one lambdarank query (overlapping labels), relevance = within-day "
    "quintile of the forward h-session open-to-open return; hyperparameters fixed before the run.",
    "Annualisation: arithmetic mean per period x 52 (weekly) or x 12 (monthly).",
    "Periods whose exit would fall on or after the holdout start are dropped (the last week / month of "
    "September 2025), never filled from holdout bars.",
    "Costs: one equity-delivery round trip (costs.yaml, Fyers, at reference_notional_inr per position) per "
    "unit of one-way turnover; the first rebalance buys from cash (0.5 turnover) and the final liquidation "
    "pays half a round trip.",
]

COMMON_CAVEATS = [
    "SURVIVORSHIP BIAS: the universe is TODAY's Nifty 200. Stocks that were dropped, delisted or merged are "
    "missing, and stocks that entered the index later (often after strong runs) are present in history. "
    "Every return, excess and IC here is an upper bound on what a point-in-time universe would show.",
    "Read every headline next to survivorship_diagnostics: signals that only pay because today's list is "
    "made of past winners (smallest traded value, most recent listing) and a rerun on the more liquid half "
    "of the candidates. A result that the survivor proxies match, or that vanishes in the liquid half, is "
    "not evidence of a tradable edge.",
    "Price returns only: dividends are not in the data. The low-volatility and high-dividend names are "
    "understated relative to the benchmark; the excess of a high-yield top decile would be a little higher.",
    "Demergers are not adjusted in the store (e.g. ADANIENT 2015-06-03, BAJAJHLDNG 2008); such a jump is a "
    "fake return in features and labels. data_notes lists every |daily log return| > 0.35 in validation.",
    "Several costs.yaml rates are marked verify: true. Slippage is 0.03% per side for equity, which is thin "
    "for the smaller Nifty 200 names in 2016-2018; real impact on weekly turnover could be larger.",
    "The industry map is today's classification and the universe is today's list: both are fixed, "
    "not point-in-time.",
]


def _costs_block(cost_rt: float, parts: dict) -> dict:
    cfg = load_costs_config()
    return {
        "segment": "equity_delivery (costs.yaml mapping equity.multi_day)",
        "broker": "fyers",
        "reference_notional_inr": cfg.get("reference_notional_inr"),
        "round_trip": cost_rt,
        "components": parts,
        "costs_as_of": str(cfg.get("as_of")),
    }


def _definitions() -> dict:
    return {
        "features": {
            "reversal_1w": "log(Cf_t / Cf_{t-5})",
            "return_1m": "log(Cf_t / Cf_{t-21})",
            "momentum_12_1": "log(Cf_{t-21} / Cf_{t-252})",
            "volatility_60d": "annualised sd of daily log returns, last 60 sessions (>= 40)",
            "distance_52w_high": "log(Cf_t / max high of the last 252 sessions (>= 200 bars))",
            "volume_change": "log(mean volume 5 sessions / mean volume 60 sessions)",
            "beta_1y": "OLS beta of daily log returns on NIFTY 50, last 252 sessions (>= 200 pairs)",
            "sector_relative_return": "return_1m - mean return_1m of eligible same-industry stocks that day",
            "turnover": "log mean(close x volume) over 20 sessions (>= 10 bars)",
            "candlestick_patterns": "counts of confirmed bullish / bearish patterns (candly.patterns, "
            "config/patterns.yaml) whose last bar is in the last 5 / 20 sessions (pv2_xs only)",
            "model_inputs": "every feature as its percentile rank among that day's eligible stocks",
            "Cf": "last traded close (carried over days without a bar); sessions = NIFTY 50 trading days",
        },
        "baselines": {name: f"{'+' if s > 0 else '-'}{col}" for name, (col, s) in BASELINES.items()},
        "label": "open of t+1 to open of t+1+h (research only)",
        "portfolio": "top decile = ceil(10% of candidates), equal weight, long only, held to next rebalance",
        "rank_ic": "Spearman(score, holding-period return) over the candidates of each rebalance",
        "excess_after_cost": "(1 - cost)(1 + top-decile return) - 1 - benchmark return, per period",
        "weekly_hit_rate": "share of weeks (first-trading-day open to the next) where net wealth beat the "
        "benchmark's",
        "p_one_sided": "(1 + #{bootstrap statistic <= 0}) / (1 + resamples)",
    }


def _meta(panel: Panel, frame: pd.DataFrame, spec: CrossSectionSpec, edge: EdgeSearchConfig) -> dict:
    return {
        "universe_file": spec.universe,
        "n_listed": len(panel.ids) + len(panel.missing),
        "n_loaded": len(panel.ids),
        "missing": panel.missing,
        "market_calendar": MARKET_ID,
        "first_bar": panel.dates[0].isoformat(),
        "last_bar": panel.dates[-1].isoformat(),
        "n_sessions": len(panel.dates),
        "bars_off_calendar_dropped": panel.n_off_calendar,
        "n_eligible_rows": len(frame),
        "data_notes_large_moves_in_validation": large_moves(panel, spec.train_end_utc),
        "panel_sha256": hashlib.sha256(
            pd.util.hash_pandas_object(panel.close, index=True).to_numpy().tobytes()
        ).hexdigest(),
    }


def _strategy_block(r: HorizonResult, names) -> dict:
    return {n: {**r.metrics[n], "by_year": r.by_year[n]} for n in names}


def xs_report(
    results: dict[int, HorizonResult],
    edge: EdgeSearchConfig,
    meta: dict,
    cost: tuple,
    bh: dict,
    runtime: float,
) -> dict:
    spec = edge.cross_section
    horizons = {}
    for h, r in results.items():
        gates, best = xs_gates(r, spec)
        primary = r.metrics[XS_NAME]["top_decile_excess_after_cost_annual"]
        horizons[str(h)] = {
            "rebalance": r.rule,
            "periods_per_year": PERIODS_PER_YEAR[r.rule],
            "primary": {
                "statistic": "top-decile excess return after cost (annualised), one-sided > 0",
                "value": primary["value"],
                "ci": primary["ci"],
                "p_one_sided": primary["p_one_sided"],
                "bh_q_within_this_run": bh[(XS_NAME, h)],
            },
            "gates": gates,
            "pass": all(g["pass"] for g in gates.values()),
            "best_baseline": best,
            "strategies": _strategy_block(r, [XS_NAME, *BASELINES]),
            "comparisons": {k: v for k, v in r.comparisons.items() if k.startswith("xs_minus_")},
            "survivorship_diagnostics": r.diagnostics,
        }
    return {
        "test": spec.name,
        "period": "validation",
        "generated_at": datetime.now(UTC).isoformat(),
        "config_sha256": config_sha256(edge, spec, with_v2=False),
        "registered": {
            "universe": spec.universe,
            "benchmark": spec.benchmark,
            "horizons_days": list(spec.horizons_days),
            "rebalance": {str(k): v for k, v in spec.rebalance.items()},
            "train_end": spec.train_end.isoformat(),
            "purge_days": spec.purge_days,
            "min_price_inr": spec.min_price_inr,
            "feature_groups": list(spec.feature_groups),
            "model": spec.model,
            "baselines": list(spec.baselines),
            "portfolio": spec.portfolio.__dict__,
            "metrics": list(spec.metrics),
            "pass_if": spec.pass_if.__dict__,
            "bootstrap": edge.common.bootstrap.__dict__,
        },
        "split": _split(spec, edge, results, XS_NAME),
        "universe": meta,
        "costs": _costs_block(*cost),
        "hyperparameters": hyperparameters(),
        "definitions": _definitions(),
        "horizons": horizons,
        "primary_p": {str(h): horizons[str(h)]["primary"]["p_one_sided"] for h in results},
        "pass_rule": spec.pass_if.rule,
        "pass": any(horizons[str(h)]["pass"] for h in results),
        "multiple_testing": _mt_block(bh),
        "deviations": [
            *COMMON_DEVIATIONS,
            "beats_best_baseline is read as written: the model's point excess after cost must exceed the "
            "best baseline's point value, the best baseline picked ex post on validation (conservative); the "
            "paired bootstrap CI of the difference is reported but is not a registered gate.",
        ],
        "caveats": COMMON_CAVEATS,
        "runtime_s": round(runtime, 1),
    }


def pv2_report(
    results: dict[int, HorizonResult],
    edge: EdgeSearchConfig,
    section: dict,
    meta: dict,
    cost: tuple,
    bh: dict,
    runtime: float,
) -> dict:
    spec = edge.cross_section
    horizons = {}
    for h, r in results.items():
        gates = pv2_gates(r, section)
        primary = r.comparisons["pv2_minus_xs_rank_ic"]
        horizons[str(h)] = {
            "rebalance": r.rule,
            "primary": {
                "statistic": "rank-IC improvement over xs_v1 (paired, date-block bootstrap), one-sided > 0",
                "value": primary["value"],
                "ci": primary["ci"],
                "p_one_sided": primary["p_one_sided"],
                "bh_q_within_this_run": bh[(PV2_NAME, h)],
            },
            "gates": gates,
            "pass": all(g["pass"] for g in gates.values()),
            "strategies": _strategy_block(r, [PV2_NAME, XS_NAME]),
            "comparisons": {k: v for k, v in r.comparisons.items() if k.startswith("pv2_minus_")},
            "pattern_feature_gain_share": _pattern_gain_share(r.folds[PV2_NAME]),
            "survivorship_diagnostics": r.diagnostics,
        }
    return {
        "test": PV2_NAME,
        "period": "validation",
        "generated_at": datetime.now(UTC).isoformat(),
        "config_sha256": config_sha256(edge, spec, with_v2=True),
        "registered": {**{k: v for k, v in section.items()}, "base_spec_from": EDGE_FILE},
        "split": _split(spec, edge, results, PV2_NAME),
        "universe": meta,
        "costs": _costs_block(*cost),
        "hyperparameters": {**hyperparameters(), "added_features": list(PATTERN_FEATURES)},
        "definitions": _definitions(),
        "horizons": horizons,
        "primary_p": {str(h): horizons[str(h)]["primary"]["p_one_sided"] for h in results},
        "pass_rule": "per horizon: every pass_if check; the family decision also needs Benjamini-Hochberg at "
        "family.fdr_q across every v2 primary p (done by the lead with the other v2 tests)",
        "pass": any(horizons[str(h)]["pass"] for h in results),
        "multiple_testing": _mt_block(bh),
        "deviations": [
            *COMMON_DEVIATIONS,
            "excess_after_cost_not_worse_than_xs_v1 is read as the point estimate: pv2_xs excess after "
            "cost >= xs_v1's (the stricter reading; 'CI of the difference not entirely below 0' would be "
            "looser).",
            "Pattern counts are ranked among the day's eligible stocks like every other feature; neutral "
            "patterns (doji, inside/outside bar) are not counted.",
        ],
        "caveats": COMMON_CAVEATS,
        "runtime_s": round(runtime, 1),
    }


def _pattern_gain_share(folds: list[dict]) -> float | None:
    total = pattern = 0.0
    for f in folds:
        gains = f.get("feature_importance_gain") or {}
        total += sum(gains.values())
        pattern += sum(v for k, v in gains.items() if k in {rank_column(p) for p in PATTERN_FEATURES})
    return pattern / total if total > 0 else None


def _mt_block(bh: dict) -> dict:
    return {
        "hypotheses_in_this_run": [f"{name} h={h}" for name, h in bh],
        "n": len(bh),
        "bh_q_within_this_run": {f"{name} h={h}": q for (name, h), q in bh.items()},
        "fdr_q": load_research_config().fdr_alpha,
        "note": "edge_search_v2.yaml requires BH across EVERY v2 primary p (tsmom, im, vol_v2, vrp, "
        "pv2_range, pv2_pooled too); these within-run q-values are context only, the lead computes the "
        "family q.",
    }


def primary_bh(results: dict[int, HorizonResult]) -> dict[tuple[str, int], float | None]:
    keys, pvals = [], []
    for h, r in results.items():
        keys.append((XS_NAME, h))
        pvals.append(r.metrics[XS_NAME]["top_decile_excess_after_cost_annual"]["p_one_sided"])
    for h, r in results.items():
        keys.append((PV2_NAME, h))
        pvals.append(r.comparisons["pv2_minus_xs_rank_ic"]["p_one_sided"])
    q = benjamini_hochberg([1.0 if p is None else p for p in pvals]).tolist()
    return dict(zip(keys, q, strict=True))


# --- markdown -----------------------------------------------------------------------------------------------


def _f(x, digits: int = 4, pct: bool = False) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "n/a"
    return f"{100 * x:+.{digits}f}%" if pct else f"{x:+.{digits}f}"


def _ci(ci, digits: int = 4, pct: bool = False) -> str:
    if not ci:
        return "n/a"
    return f"[{_f(ci[0], digits, pct)}, {_f(ci[1], digits, pct)}]"


def _strategy_table(strategies: dict) -> list[str]:
    lines = [
        "| strategy | rank IC [95% CI] | excess after cost / yr [95% CI] | gross excess / yr | weekly hit "
        "| turnover / rebal | max DD excess |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, m in strategies.items():
        ex = m["top_decile_excess_after_cost_annual"]
        lines.append(
            f"| {name} | {_f(m['rank_ic_mean']['value'])} {_ci(m['rank_ic_mean']['ci'])} | "
            f"{_f(ex['value'], 2, True)} {_ci(ex['ci'], 2, True)} | "
            f"{_f(m['top_decile_excess_gross_annual']['value'], 2, True)} | "
            f"{100 * m['weekly_hit_rate_vs_benchmark']['value']:.1f}% | "
            f"{100 * m['turnover']['value']:.0f}% | {100 * m['max_drawdown_excess']['value']:.1f}% |"
        )
    return lines


def _survivorship_lines(d: dict, model: str) -> list[str]:
    proxies, liquid = d["survivor_proxies"], d["liquid_half"]["strategies"]
    tilt = d["holdings_tilt"][model]

    def ex(m: dict) -> str:
        e = m["top_decile_excess_after_cost_annual"]
        return f"{_f(e['value'], 2, True)}/yr {_ci(e['ci'], 2, True)}"

    return [
        "Survivorship check (diagnostic, not a gate):",
        f"- smallest traded value alone: {ex(proxies['small_turnover'])}; most recent listing alone: "
        f"{ex(proxies['young_listing'])}; momentum + small traded value (hand-built): "
        f"{ex(proxies['momentum_plus_small_turnover'])}.",
        f"- {model} holdings: mean traded-value rank {tilt['mean_rank_turnover_held']:.2f}, momentum rank "
        f"{tilt['mean_rank_momentum_12_1_held']:.2f}, median listing age "
        f"{tilt['median_listing_age_years_held']:.1f}y (candidates "
        f"{d['candidates_median_listing_age_years']:.1f}y).",
        f"- liquid half only (~{d['liquid_half']['mean_candidates']:.0f} candidates): {model} "
        f"{ex(liquid[model])}, momentum_12_1 {ex(liquid['momentum_12_1'])}.",
        "",
    ]


def xs_markdown(report: dict) -> str:
    lines = [
        f"# {report['test']} validation: Nifty 200 cross-sectional ranking",
        "",
        f"Generated {report['generated_at'][:19]}Z. Validation {report['split']['validation'][0]} to "
        f"{report['split']['validation'][1]} (holdout untouched), yearly expanding refits, purge "
        f"{report['split']['purge_days']} sessions. Costs: equity delivery round trip "
        f"{100 * report['costs']['round_trip']:.3f}% per unit of one-way turnover. Pass rule: "
        f"{report['pass_rule']}.",
        "",
        f"**Overall: {'PASS' if report['pass'] else 'FAIL'}**",
        "",
        "> Survivorship bias: today's Nifty 200 constituents, so every number is an upper bound.",
        "",
    ]
    for h, e in report["horizons"].items():
        g = e["gates"]
        lines += [
            f"## h = {h} days ({e['rebalance']}): {'PASS' if e['pass'] else 'FAIL'}",
            "",
            f"Primary: excess after cost {_f(e['primary']['value'], 2, True)}/yr, 95% CI "
            f"{_ci(e['primary']['ci'], 2, True)}, one-sided p = {e['primary']['p_one_sided']:.4f} "
            f"({report['split']['horizons'][h]['n_rebalances']} rebalances).",
            "",
            *_strategy_table(e["strategies"]),
            "",
            f"Gates: rank IC CI lower > 0: {'pass' if g['rank_ic_ci_lower_above']['pass'] else 'FAIL'}; "
            f"excess CI lower > 0: "
            f"{'pass' if g['top_decile_excess_after_cost_ci_lower_above']['pass'] else 'FAIL'}; "
            f"beats best baseline ({g['beats_best_baseline']['best_baseline']}, "
            f"{_f(g['beats_best_baseline']['best_baseline_excess_after_cost_annual'], 2, True)}/yr): "
            f"{'pass' if g['beats_best_baseline']['pass'] else 'FAIL'} (paired difference "
            f"{_f(g['beats_best_baseline']['paired_difference']['value'], 2, True)}/yr, 95% CI "
            f"{_ci(g['beats_best_baseline']['paired_difference']['ci'], 2, True)}).",
            "",
            *_survivorship_lines(e["survivorship_diagnostics"], XS_NAME),
        ]
    lines += [
        "Deviations from a literal reading and caveats are listed in the JSON (`deviations`, `caveats`).",
        "",
    ]
    return "\n".join(lines)


def pv2_markdown(report: dict) -> str:
    verdict = "at least one horizon passes" if report["pass"] else "FAIL at every horizon"
    lines = [
        f"# {report['test']} validation: candlestick-pattern counts added to the xs_v1 ranking",
        "",
        f"Generated {report['generated_at'][:19]}Z. Same universe, splits, model, portfolio and costs as "
        "xs_v1; only the candlestick_patterns group (confirmed bullish/bearish counts over 5 and 20 "
        "sessions) is added. Primary: paired rank-IC improvement over xs_v1.",
        "",
        f"**Per-horizon result: {verdict}** (the v2 family BH decision is the lead's).",
        "",
        "> Survivorship bias: today's Nifty 200 constituents, so every number is an upper bound.",
        "",
    ]
    for h, e in report["horizons"].items():
        g = e["gates"]
        lines += [
            f"## h = {h} days ({e['rebalance']}): {'PASS' if e['pass'] else 'FAIL'}",
            "",
            f"Primary: IC improvement {_f(e['primary']['value'])}, 95% CI {_ci(e['primary']['ci'])}, "
            f"one-sided p = {e['primary']['p_one_sided']:.4f}. Pattern features' share of model gain: "
            f"{_f(e['pattern_feature_gain_share'], 3)}.",
            "",
            *_strategy_table(e["strategies"]),
            "",
            f"Gates: IC improvement CI lower > 0: "
            f"{'pass' if g['rank_ic_improvement_ci_lower_above']['pass'] else 'FAIL'}; excess after cost not "
            f"worse than xs_v1: {'pass' if g['excess_after_cost_not_worse_than_xs_v1']['pass'] else 'FAIL'}.",
            "",
            *_survivorship_lines(e["survivorship_diagnostics"], PV2_NAME),
        ]
    lines += ["Deviations and caveats are listed in the JSON.", ""]
    return "\n".join(lines)


def write_report(report: dict, name: str, markdown: str) -> tuple[Path, Path]:
    json_path = report_dir() / name
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path = json_path.with_suffix(".md")
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path


def save_scores(results: dict[int, HorizonResult], frame: pd.DataFrame) -> None:
    out = predictions_dir()
    out.mkdir(parents=True, exist_ok=True)
    for h, r in results.items():
        signals = np.isin(frame["t"].to_numpy(), r.periods["signal"].to_numpy())
        rows = frame.loc[signals, ["ts", "id", label_column(h)]].copy()
        for name, s in r.scores.items():
            rows[f"score_{name}"] = s[signals]
        rows.to_parquet(out / f"validation_scores_h{h}.parquet", index=False)


# --- entry point --------------------------------------------------------------------------------------------


def run_validation(
    *,
    load: CandleLoader | None = None,
    edge: EdgeSearchConfig | None = None,
    write: bool = True,
    params: dict | None = None,
    rounds: int | None = None,
    log=print,
) -> tuple[dict, dict]:
    """xs_v1 and pv2_xs on validation for every registered horizon. Returns (xs_v1 report, pv2_xs report)."""
    started = time.perf_counter()
    edge = edge or load_edge_config()
    spec = edge.cross_section
    check_xs_spec(spec)
    section = check_pv2_section(edge_v2_section("cross_section_patterns"), spec)
    ids, industry = universe_members(spec.universe)
    panel = build_panel(ids, industry, edge.common.holdout_start_utc, load=load, with_patterns=True)
    frame = panel_frame(panel, spec.min_price_inr, spec.horizons_days, with_patterns=True)
    log(
        f"panel: {len(panel.ids)} stocks, {len(panel.dates)} sessions, {len(frame)} eligible rows "
        f"({time.perf_counter() - started:.0f}s)"
    )
    cost = equity_round_trip()
    results = {}
    for h in spec.horizons_days:
        results[h] = evaluate_horizon(panel, frame, spec, edge, h, cost[0], params, rounds)
        log(f"h={h} done ({time.perf_counter() - started:.0f}s)")
    bh = primary_bh(results)
    meta = _meta(panel, frame, spec, edge)
    runtime = time.perf_counter() - started
    xs = xs_report(results, edge, meta, cost, bh, runtime)
    pv2 = pv2_report(results, edge, section, meta, cost, bh, runtime)
    if write:
        write_report(xs, XS_REPORT, xs_markdown(xs))
        write_report(pv2, PV2_REPORT, pv2_markdown(pv2))
        save_scores(results, frame)
    return xs, pv2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="xs_v1 and pv2_xs validation (never the holdout)")
    parser.parse_args(argv)
    xs, pv2 = run_validation()
    for report in (xs, pv2):
        for h, e in report["horizons"].items():
            p = e["primary"]
            print(
                f"{report['test']} h={h}: primary={_f(p['value'])} ci={_ci(p['ci'])} "
                f"p={p['p_one_sided']:.4f} pass={e['pass']}"
            )
    print(f"reports -> {report_dir()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
