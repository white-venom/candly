"""tsmom_v1 validation (config/edge_search_v2.yaml `tsmom`, PLAN.md §20c): rule-based 12-1 time-series
momentum (Moskowitz, Ooi & Pedersen 2012) on NIFTY 50, BANKNIFTY, SENSEX and MCX crude oil, natural gas,
gold and silver. Nothing is fitted.

Returns: candly.research.tsmom_data (index futures proxy = spot minus 5% a year; MCX front month with the
registered roll rule, the roll days detected from the series).

Per instrument, at the close of the first trading day of each month (its own calendar):
- signal = sign of the compounded daily return from the close 252 bars ago to the close 21 bars ago
  (the instrument's futures-proxy return: carry-adjusted for indices, roll-adjusted for MCX);
- sigma = sqrt(252 x EWMA of squared daily returns), centre of mass 60 days (MOP 2012), >= 60 returns;
- weight = signal x 10% / sigma, held from the next bar until the next rebalance close;
- buy-and-hold equal risk: weight = +10% / sigma on the same dates; always flat: weight 0.
An instrument without 252 bars of history (or 60 returns) is flat. Daily P&L = weight x return.

Costs (config/costs.yaml; futures segment for indices, mcx_futures for MCX, each with its slippage), in
return units at every rebalance: round trip x |new weight - old weight| (one round trip per position
change, charged on the traded notional) + round trip x |new weight| (one roll per month per open
position). Brokerage is costed at the reference notional (Rs 1 lakh), which overstates it for real lots.

Portfolio (equal risk): each day the mean of the sleeve P&Ls of the instruments whose evaluation has
started (indices from indices_from, MCX from mcx_from; a sleeve with no bar that day contributes 0).
Sharpe = annualised from calendar-month P&L (sum of daily P&L), mean / sd x sqrt(12).

Statistics: date-block bootstrap (edge_search.yaml common.bootstrap), one block = one calendar month (the
holding period); percentile CIs; one-sided p = (1 + #{bootstrap Sharpe <= 0}) / (1 + B). Diagnostics that
never change the gate: 12-month blocks, before-cost numbers, sub-periods, roll-rule and signal variants.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from candly.core.calendar import IST
from candly.core.settings import REPO_ROOT
from candly.research.config import config_hashes, ist_midnight_utc, load_costs_config
from candly.research.costs import cost_breakdown
from candly.research.data import CandleLoader, load_research_candles
from candly.research.edge_config import EDGE_FILE, EdgeSearchConfig, load_edge_config
from candly.research.edge_v2_config import EDGE_V2_FILE, edge_v2_section, edge_v2_sha256
from candly.research.stats import benjamini_hochberg
from candly.research.tsmom_data import (
    HOLE_DAYS,
    INDEX_CARRY_PER_YEAR,
    ROLL_WINDOWS,
    ReturnAdjustments,
    index_futures_returns,
    mcx_adjustments,
    mcx_futures_returns,
)
from candly.research.vol_eval import bootstrap_sharpe_diff, max_drawdown, percentile_ci

TEST_NAME = "tsmom_v1"
REPORT_NAME = "2026-09-24-tsmom-v1-validation.json"
UNIVERSE = (
    "NSE:NIFTY50",
    "NSE:BANKNIFTY",
    "BSE:SENSEX",
    "MCX:CRUDEOIL",
    "MCX:NATURALGAS",
    "MCX:GOLD",
    "MCX:SILVER",
)
LOOKBACK = 252
SKIP = 21
VOL_TARGET = 0.10
EWMA_COM = 60
MIN_VOL_RETURNS = 60
TRADING_DAYS = 252
MONTHS_PER_YEAR = 12
LONG_BLOCK_MONTHS = 12
BASELINES = ("buy_and_hold_equal_risk", "always_flat")
REBALANCE = "monthly_first_trading_day"
KEYS = {
    "name",
    "universe",
    "signal",
    "sizing",
    "rebalance",
    "holding",
    "index_futures_return",
    "mcx_roll_rule",
    "costs",
    "evaluation",
    "baselines",
    "primary",
    "pass_if",
}
# Fragments of the registered text that the code implements; a changed text fails loudly.
TEXT_CHECKS = {
    "signal": ("sign of the close-to-close return", "t-252", "t-21"),
    "sizing": ("10% annualised vol", "60-day EWMA", "equal risk"),
    "holding": ("until the next rebalance",),
    "index_futures_return": ("minus 5% a year carry", "both strategy and baseline"),
    "mcx_roll_rule": ("first trading day of a new front contract", "open-to-close"),
    "costs": (
        "futures segment",
        "one round trip per position change",
        "one roll per month per open position",
    ),
    "primary": ("annualised Sharpe after cost of the combined portfolio", "one-sided > 0"),
}


def report_path() -> Path:
    return REPO_ROOT / "docs" / "test-reports" / REPORT_NAME


# --- the registration ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TsmomSpec:
    name: str
    universe: tuple[str, ...]
    indices_from: date
    mcx_from: date
    until: date
    sharpe_ci_lower_above: float
    beats_buy_and_hold: bool
    registered: dict

    def start_for(self, instrument_id: str) -> date:
        return self.mcx_from if instrument_id.startswith("MCX:") else self.indices_from


def _date(value, key: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{EDGE_V2_FILE} tsmom.{key}={value!r} is not a date") from None


def parse_spec(raw: dict, holdout_start: date) -> TsmomSpec:
    if set(raw) != KEYS:
        raise ValueError(
            f"{EDGE_V2_FILE} tsmom: unknown {sorted(set(raw) - KEYS)}, missing {sorted(KEYS - set(raw))}"
        )
    for key, fragments in TEXT_CHECKS.items():
        text = " ".join(str(raw[key]).split())
        missing = [f for f in fragments if f not in text]
        if missing:
            raise ValueError(f"{EDGE_V2_FILE} tsmom.{key} no longer says {missing}; the code implements them")
    if raw["name"] != TEST_NAME or tuple(raw["universe"]) != UNIVERSE:
        raise ValueError(f"{EDGE_V2_FILE} tsmom name/universe differ from what the code implements")
    if raw["rebalance"] != REBALANCE or tuple(raw["baselines"]) != BASELINES:
        raise ValueError(f"{EDGE_V2_FILE} tsmom rebalance/baselines differ from what the code implements")
    ev = raw["evaluation"]
    if set(ev) != {"indices_from", "mcx_from", "until"} or ev["until"] != "holdout_start":
        raise ValueError(
            f"{EDGE_V2_FILE} tsmom.evaluation must be indices_from, mcx_from, until: holdout_start"
        )
    gate = raw["pass_if"]
    if set(gate) != {"sharpe_after_cost_ci_lower_above", "sharpe_beats_buy_and_hold_point"}:
        raise ValueError(f"{EDGE_V2_FILE} tsmom.pass_if keys changed: {sorted(gate)}")
    spec = TsmomSpec(
        name=TEST_NAME,
        universe=UNIVERSE,
        indices_from=_date(ev["indices_from"], "evaluation.indices_from"),
        mcx_from=_date(ev["mcx_from"], "evaluation.mcx_from"),
        until=holdout_start,
        sharpe_ci_lower_above=float(gate["sharpe_after_cost_ci_lower_above"]),
        beats_buy_and_hold=bool(gate["sharpe_beats_buy_and_hold_point"]),
        registered=raw,
    )
    if not spec.indices_from < spec.until or not spec.mcx_from < spec.until:
        raise ValueError(f"{EDGE_V2_FILE} tsmom evaluation starts must be before the holdout")
    return spec


def load_spec(edge: EdgeSearchConfig | None = None) -> TsmomSpec:
    edge = edge or load_edge_config()
    return parse_spec(edge_v2_section("tsmom"), edge.common.holdout_start)


# --- costs -----------------------------------------------------------------------------------------------


def cost_kind(instrument_id: str) -> str:
    return "future" if instrument_id.startswith("MCX:") else "index"


def round_trip(instrument_id: str) -> tuple[float, dict[str, float]]:
    parts = cost_breakdown(cost_kind(instrument_id), "multi_day")
    return float(sum(parts.values())), parts


# --- signal, sizing and P&L (causal) ---------------------------------------------------------------------


def ist_dates(ts: pd.Series) -> pd.Series:
    return ts.dt.tz_convert(IST).dt.date


def rebalance_mask(ts: pd.Series) -> np.ndarray:
    """First bar of each IST calendar month."""
    month = ts.dt.tz_convert(IST).dt.strftime("%Y-%m")
    return (month != month.shift(1)).to_numpy()


def strategy_weights(
    ts: pd.Series, ret: pd.Series, skip: int = SKIP, lookback: int = LOOKBACK
) -> pd.DataFrame:
    """Per bar, decided at its close from bars up to it: signal, annualised sigma and the target weights
    (NaN off rebalance days; 0 on a rebalance day without enough history)."""
    ret = pd.Series(np.asarray(ret, float))
    if (ret <= -1.0).any():
        raise ValueError("a daily return of -100% or worse cannot be compounded")
    wealth = np.log1p(ret.fillna(0.0)).cumsum()
    past = wealth.shift(skip) - wealth.shift(lookback)
    signal = np.sign(past)
    var = (ret**2).ewm(com=EWMA_COM, adjust=True, min_periods=MIN_VOL_RETURNS).mean()
    sigma = np.sqrt(TRADING_DAYS * var)
    rebal = rebalance_mask(ts)
    ok = signal.notna() & sigma.gt(0)
    unit = (VOL_TARGET / sigma).where(ok, 0.0)
    return pd.DataFrame(
        {
            "signal": signal,
            "sigma": sigma,
            "w_tsmom": (signal.fillna(0.0) * unit).where(rebal),
            "w_buy_and_hold": unit.where(rebal),
        }
    )


def sleeve_pnl(ret: pd.Series, target: pd.Series, round_trip_cost: float) -> pd.DataFrame:
    """Daily P&L of trading `target` weights (set at a rebalance close, held from the next bar)."""
    ret = pd.Series(np.asarray(ret, float)).fillna(0.0)
    target = pd.Series(np.asarray(target, float))
    rebal = target.notna()
    held = target.ffill().fillna(0.0)
    position = held.shift(1).fillna(0.0)
    turnover = (held - position).abs()
    cost = round_trip_cost * (turnover + rebal.astype(float) * held.abs())
    gross = position * ret
    return pd.DataFrame(
        {"position": position, "gross": gross, "cost": cost, "net": gross - cost, "rebalance": rebal}
    )


# --- data ------------------------------------------------------------------------------------------------


@dataclass
class InstrumentData:
    instrument_id: str
    candles: pd.DataFrame
    adjustments: ReturnAdjustments | None
    round_trip: float
    cost_parts: dict[str, float]


def load_instrument(instrument_id: str, load: CandleLoader | None = None) -> InstrumentData:
    candles = load_research_candles(instrument_id, "1D", allow_holdout=False, load=load)
    adj = mcx_adjustments(candles, instrument_id) if instrument_id in ROLL_WINDOWS else None
    rt, parts = round_trip(instrument_id)
    return InstrumentData(instrument_id, candles, adj, rt, parts)


def returns_for(
    data: InstrumentData,
    rolls: str = "registered",
    zero_invalid: bool = True,
    carry: float = INDEX_CARRY_PER_YEAR,
) -> pd.Series:
    if data.adjustments is None:
        return index_futures_returns(data.candles, carry)
    return mcx_futures_returns(data.candles, data.adjustments, rolls=rolls, zero_invalid=zero_invalid)


@dataclass
class Variant:
    """How the sleeves are built. The registered analysis uses the defaults."""

    rolls: str = "registered"
    zero_invalid: bool = True
    signal_carry: float = INDEX_CARRY_PER_YEAR
    with_costs: bool = True


def build_sleeves(
    datas: dict[str, InstrumentData], variant: Variant | None = None
) -> dict[str, pd.DataFrame]:
    """Per instrument daily frame: date, ret, weights and the P&L of tsmom and buy-and-hold."""
    variant = variant or Variant()
    out = {}
    for iid, data in datas.items():
        ret = returns_for(data, variant.rolls, variant.zero_invalid)
        signal_ret = (
            ret
            if variant.signal_carry == INDEX_CARRY_PER_YEAR
            else returns_for(data, variant.rolls, variant.zero_invalid, carry=variant.signal_carry)
        )
        w = strategy_weights(data.candles["ts"], signal_ret)
        if variant.signal_carry != INDEX_CARRY_PER_YEAR:
            sizing = strategy_weights(data.candles["ts"], ret)
            w["w_buy_and_hold"] = sizing["w_buy_and_hold"]
            w["w_tsmom"] = (w["signal"].fillna(0.0) * sizing["w_buy_and_hold"]).where(w["w_tsmom"].notna())
        rt = data.round_trip if variant.with_costs else 0.0
        frame = pd.DataFrame({"date": ist_dates(data.candles["ts"]).to_numpy(), "ret": ret.to_numpy()})
        frame = pd.concat([frame, w.reset_index(drop=True)], axis=1)
        for name, col in (("tsmom", "w_tsmom"), ("buy_and_hold", "w_buy_and_hold")):
            pnl = sleeve_pnl(ret, w[col], rt)
            frame[f"{name}_position"] = pnl["position"].to_numpy()
            frame[f"{name}_gross"] = pnl["gross"].to_numpy()
            frame[f"{name}_cost"] = pnl["cost"].to_numpy()
            frame[f"{name}_net"] = pnl["net"].to_numpy()
        out[iid] = frame
    return out


def portfolio_daily(
    sleeves: dict[str, pd.DataFrame], spec: TsmomSpec, column: str, members: tuple[str, ...] | None = None
) -> pd.Series:
    """Equal-risk portfolio: mean over the instruments whose evaluation has started, by IST date."""
    members = members or tuple(sleeves)
    start = min(spec.start_for(i) for i in members)
    dates = sorted({d for i in members for d in sleeves[i]["date"] if start <= d < spec.until})
    index = pd.Index(dates, name="date")
    total = pd.Series(0.0, index=index)
    active = pd.Series(0, index=index)
    for iid in members:
        s = sleeves[iid].set_index("date")[column]
        s = s[~s.index.duplicated(keep="last")].reindex(index).fillna(0.0)
        live = index >= spec.start_for(iid)
        total += s.where(live, 0.0)
        active += live.astype(int)
    return total / active


def sleeve_daily(sleeve: pd.DataFrame, start: date, until: date, column: str) -> pd.Series:
    rows = sleeve[(sleeve["date"] >= start) & (sleeve["date"] < until)]
    return pd.Series(rows[column].to_numpy(), index=pd.Index(rows["date"], name="date"))


# --- statistics ------------------------------------------------------------------------------------------


def monthly(daily: pd.Series) -> pd.Series:
    keys = pd.Index([f"{d.year:04d}-{d.month:02d}" for d in daily.index], name="month")
    return daily.groupby(keys).sum()


def annualised_sharpe(x, periods_per_year: float) -> float | None:
    x = np.asarray(x, float)
    if x.size < 2:
        return None
    sd = x.std(ddof=1)
    if sd == 0:
        return 0.0 if x.mean() == 0 else None
    return float(x.mean() / sd * math.sqrt(periods_per_year))


def bootstrap_sharpe(values, blocks, periods_per_year: float, resamples: int, seed: int) -> np.ndarray:
    """Sharpe over date-block bootstrap draws (whole blocks with replacement); undefined draws drop out.
    Uses the same block draws as candly.research.vol_eval for the same seed, so comparisons are paired."""
    values = np.asarray(values, float)
    _, inverse = np.unique(np.asarray(blocks), return_inverse=True)
    n = np.bincount(inverse).astype(float)
    s1, s2 = np.bincount(inverse, weights=values), np.bincount(inverse, weights=values * values)
    counts = np.random.default_rng(seed).multinomial(n.size, np.full(n.size, 1.0 / n.size), size=resamples)
    rows, tot, tot2 = counts @ n, counts @ s1, counts @ s2
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = tot / rows
        sd = np.sqrt(np.maximum(tot2 - rows * mean**2, 0.0) / (rows - 1))
        sr = np.where(sd > 0, mean / sd, np.where(mean == 0, 0.0, np.nan)) * math.sqrt(periods_per_year)
    return sr[np.isfinite(sr)]


def p_one_sided(samples: np.ndarray) -> float | None:
    """p of H0 "statistic <= 0": (1 + #{draws <= 0}) / (1 + B)."""
    if samples.size == 0:
        return None
    return float((1 + np.count_nonzero(samples <= 0)) / (1 + samples.size))


def performance(daily: pd.Series, boot, position: pd.Series | None = None) -> dict:
    """Metrics of a daily P&L series (fractions of capital); Sharpe from calendar-month sums."""
    m = monthly(daily)
    blocks = np.arange(len(m))
    samples = bootstrap_sharpe(m.to_numpy(), blocks, MONTHS_PER_YEAR, boot.resamples, boot.seed)
    long_samples = bootstrap_sharpe(
        m.to_numpy(), blocks // LONG_BLOCK_MONTHS, MONTHS_PER_YEAR, boot.resamples, boot.seed
    )
    out = {
        "n_days": int(len(daily)),
        "n_months": int(len(m)),
        "first_month": m.index[0] if len(m) else None,
        "last_month": m.index[-1] if len(m) else None,
        "annual_return": float(m.mean() * MONTHS_PER_YEAR) if len(m) else None,
        "annual_vol": float(m.std(ddof=1) * math.sqrt(MONTHS_PER_YEAR)) if len(m) > 1 else None,
        "sharpe": annualised_sharpe(m, MONTHS_PER_YEAR),
        "sharpe_ci": percentile_ci(samples, boot.ci_level),
        "sharpe_p_one_sided": p_one_sided(samples),
        "sharpe_ci_12m_blocks": percentile_ci(long_samples, boot.ci_level),
        "sharpe_from_daily": annualised_sharpe(daily, TRADING_DAYS),
        "max_drawdown": max_drawdown(daily.to_numpy()),
        "hit_rate_months": float((m > 0).mean()) if len(m) else None,
        "worst_month": float(m.min()) if len(m) else None,
        "best_month": float(m.max()) if len(m) else None,
        "total": float(daily.sum()),
    }
    if position is not None:
        out["mean_abs_position"] = float(position.abs().mean())
        out["max_abs_position"] = float(position.abs().max())
        out["share_days_long"] = float((position > 0).mean())
        out["share_days_short"] = float((position < 0).mean())
    return out


def _trades(sleeve: pd.DataFrame, start: date, until: date, name: str) -> dict:
    rows = sleeve[(sleeve["date"] >= start) & (sleeve["date"] < until) & sleeve[f"w_{name}"].notna()]
    w = rows[f"w_{name}"].to_numpy()
    before = sleeve.loc[(sleeve["date"] < start) & sleeve[f"w_{name}"].notna(), f"w_{name}"]
    prev = np.concatenate([[before.iloc[-1] if len(before) else 0.0], w[:-1]])
    return {
        "n_rebalances": int(len(w)),
        "n_position_changes": int(np.count_nonzero(np.abs(w - prev) > 0)),
        "n_sign_flips": int(np.count_nonzero(np.sign(w) != np.sign(prev))),
        "mean_turnover_per_rebalance": float(np.abs(w - prev).mean()) if len(w) else None,
        "months_open": int(np.count_nonzero(w != 0)),
    }


def _gate(value, above: float, ci) -> dict:
    lower = ci[0] if ci else None
    return {"value": value, "ci": ci, "ci_lower_above": above, "pass": lower is not None and lower > above}


# --- the evaluation --------------------------------------------------------------------------------------


@dataclass
class TsmomResult:
    report: dict
    sleeves: dict[str, pd.DataFrame] = field(repr=False)


def _portfolio_block(sleeves, spec, boot, members=None) -> dict:
    out = {}
    for name in ("tsmom", "buy_and_hold"):
        net = portfolio_daily(sleeves, spec, f"{name}_net", members)
        gross = portfolio_daily(sleeves, spec, f"{name}_gross", members)
        out[name] = {"after_cost": performance(net, boot), "before_cost": performance(gross, boot)}
        out[name]["annual_cost"] = float((gross - net).sum() / max(len(monthly(net)), 1) * MONTHS_PER_YEAR)
    return out


def _sharpe_diff(sleeves, spec, boot, members=None) -> dict:
    a = monthly(portfolio_daily(sleeves, spec, "tsmom_net", members))
    b = monthly(portfolio_daily(sleeves, spec, "buy_and_hold_net", members))
    samples = bootstrap_sharpe_diff(
        a.to_numpy(), b.to_numpy(), np.arange(len(a)), MONTHS_PER_YEAR, boot.resamples, boot.seed
    )
    sa, sb = annualised_sharpe(a, MONTHS_PER_YEAR), annualised_sharpe(b, MONTHS_PER_YEAR)
    return {
        "value": None if sa is None or sb is None else sa - sb,
        "ci": percentile_ci(samples, boot.ci_level),
        "p_one_sided": p_one_sided(samples),
    }


def _sub_period(sleeves, spec: TsmomSpec, boot, start: date, until: date, members) -> dict:
    sub = replace(spec, until=until)
    out = {}
    for name in ("tsmom", "buy_and_hold"):
        daily = portfolio_daily(sleeves, sub, f"{name}_net", members)
        daily = daily[daily.index >= start]
        out[name] = performance(daily, boot)
    return out


def evaluate(
    *, load: CandleLoader | None = None, edge: EdgeSearchConfig | None = None, datas: dict | None = None
) -> TsmomResult:
    started = time.perf_counter()
    edge = edge or load_edge_config()
    spec = load_spec(edge)
    boot = edge.common.bootstrap
    holdout_utc = edge.common.holdout_start_utc
    datas = datas or {iid: load_instrument(iid, load) for iid in spec.universe}
    for iid, data in datas.items():
        if (data.candles["ts"] >= holdout_utc).any():
            raise PermissionError(f"{iid} holds holdout bars; the holdout is locked")
    sleeves = build_sleeves(datas)

    port = _portfolio_block(sleeves, spec, boot)
    tsmom_net = port["tsmom"]["after_cost"]
    bh_net = port["buy_and_hold"]["after_cost"]
    beats = (
        tsmom_net["sharpe"] is not None
        and bh_net["sharpe"] is not None
        and tsmom_net["sharpe"] > bh_net["sharpe"]
    )
    checks = {
        "sharpe_after_cost_ci_lower_above": _gate(
            tsmom_net["sharpe"], spec.sharpe_ci_lower_above, tsmom_net["sharpe_ci"]
        ),
        "sharpe_beats_buy_and_hold_point": {
            "tsmom_sharpe": tsmom_net["sharpe"],
            "buy_and_hold_sharpe": bh_net["sharpe"],
            "required": spec.beats_buy_and_hold,
            "pass": bool(beats) if spec.beats_buy_and_hold else True,
        },
    }
    per_instrument = {}
    for iid in spec.universe:
        s, start = sleeves[iid], spec.start_for(iid)
        entry = {"evaluation_from": start.isoformat(), "round_trip_cost": datas[iid].round_trip}
        for name in ("tsmom", "buy_and_hold"):
            net = sleeve_daily(s, start, spec.until, f"{name}_net")
            gross = sleeve_daily(s, start, spec.until, f"{name}_gross")
            pos = sleeve_daily(s, start, spec.until, f"{name}_position")
            entry[name] = {
                "after_cost": performance(net, boot, pos),
                "before_cost_sharpe": annualised_sharpe(monthly(gross), MONTHS_PER_YEAR),
                "annual_cost": float((gross - net).sum() / max(len(monthly(net)), 1) * MONTHS_PER_YEAR),
                "trades": _trades(s, start, spec.until, name),
            }
        per_instrument[iid] = entry
    descriptive = [per_instrument[i]["tsmom"]["after_cost"]["sharpe_p_one_sided"] for i in spec.universe]
    if all(p is not None for p in descriptive):
        for iid, q in zip(spec.universe, benjamini_hochberg(descriptive), strict=True):
            per_instrument[iid]["tsmom"]["after_cost"]["bh_q_across_instruments"] = float(q)

    indices = tuple(i for i in spec.universe if not i.startswith("MCX:"))
    mcx = tuple(i for i in spec.universe if i.startswith("MCX:"))
    diagnostics = {
        "sharpe_diff_vs_buy_and_hold": _sharpe_diff(sleeves, spec, boot),
        "sub_periods": {
            f"{spec.indices_from}..{spec.mcx_from} (indices only)": _sub_period(
                sleeves, spec, boot, spec.indices_from, spec.mcx_from, spec.universe
            ),
            f"{spec.mcx_from}..{spec.until} (all seven)": _sub_period(
                sleeves, spec, boot, spec.mcx_from, spec.until, spec.universe
            ),
        },
        "indices_only_portfolio": _portfolio_block(sleeves, spec, boot, indices)["tsmom"]["after_cost"],
        "mcx_only_portfolio": _portfolio_block(sleeves, spec, boot, mcx)["tsmom"]["after_cost"],
        "variants": _variants(datas, spec, boot),
    }
    report = _report(spec, edge, datas, sleeves, port, checks, per_instrument, diagnostics)
    report["runtime_s"] = round(time.perf_counter() - started, 1)
    return TsmomResult(report=report, sleeves=sleeves)


def _variants(datas, spec, boot) -> dict:
    """Registered pipeline with one choice changed; never part of the gate."""
    variants = {
        "mcx_rolls_evidenced_only": Variant(rolls="evidenced_only"),
        "mcx_no_roll_adjustment": Variant(rolls="none"),
        "crude_2020_04_20_floored_price_kept": Variant(zero_invalid=False),
        "index_signal_on_spot_without_carry": Variant(signal_carry=0.0),
    }
    out = {}
    for name, v in variants.items():
        sleeves = build_sleeves(datas, v)
        net = portfolio_daily(sleeves, spec, "tsmom_net")
        bh = portfolio_daily(sleeves, spec, "buy_and_hold_net")
        perf = performance(net, boot)
        out[name] = {
            "tsmom_sharpe": perf["sharpe"],
            "tsmom_sharpe_ci": perf["sharpe_ci"],
            "tsmom_p_one_sided": perf["sharpe_p_one_sided"],
            "buy_and_hold_sharpe": annualised_sharpe(monthly(bh), MONTHS_PER_YEAR),
        }
    return out


def _data_summary(datas: dict[str, InstrumentData]) -> dict:
    out = {}
    for iid, d in datas.items():
        ts = d.candles["ts"]
        out[iid] = {
            "bars": int(len(ts)),
            "first_bar": ts.min().isoformat() if len(ts) else None,
            "last_bar": ts.max().isoformat() if len(ts) else None,
        }
    return out


def _roll_summary(datas: dict[str, InstrumentData]) -> dict:
    out = {}
    for iid, d in datas.items():
        adj = d.adjustments
        if adj is None:
            continue
        r = adj.rolls
        dates = r["ts"].dt.tz_convert(IST).dt.date.astype(str)
        w = ROLL_WINDOWS[iid]
        out[iid] = {
            "window": {"months": list(w.months), "days": [w.first_day, w.last_day]},
            "n_rolls": int(len(r)),
            "by_evidence": {k: int(v) for k, v in r["evidence"].value_counts().items()},
            "weak_by_year": {
                str(k): int(v)
                for k, v in r.loc[r["evidence"] == "weak_volume_jump", "ts"]
                .dt.year.value_counts()
                .sort_index()
                .items()
            },
            "mean_roll_gap": float(np.nanmean(np.clip(r["gap"], -1, 1))),
            "roll_days": {day: ev for day, ev in zip(dates, r["evidence"], strict=True)},
            "repairs": adj.repairs,
        }
    return out


def _costs(datas: dict[str, InstrumentData]) -> dict:
    notional = float(load_costs_config().get("reference_notional_inr") or 100_000.0)
    out = {"reference_notional_inr": notional}
    for kind in ("index", "future"):
        iid = next((i for i, d in datas.items() if cost_kind(i) == kind), None)
        if iid is None:
            continue
        d = datas[iid]
        out["index_futures" if kind == "index" else "mcx_futures"] = {
            "segment": "futures" if kind == "index" else "mcx_futures",
            "round_trip_pct": 100.0 * d.round_trip,
            "round_trip_inr_at_reference_notional": d.round_trip * notional,
            "components_pct": {k: 100.0 * v for k, v in d.cost_parts.items()},
        }
    return out


def _report(spec, edge, datas, sleeves, port, checks, per_instrument, diagnostics) -> dict:
    boot = edge.common.bootstrap
    primary = port["tsmom"]["after_cost"]
    passed = all(c["pass"] for c in checks.values())
    return {
        "test": spec.name,
        "period": "validation",
        "generated_at": datetime.now(UTC).isoformat(),
        "edge_search_v2_sha256": edge_v2_sha256(),
        "config_hashes": {**config_hashes(), EDGE_FILE: edge.sha256, EDGE_V2_FILE: edge_v2_sha256()},
        "registered": spec.registered,
        "split": {
            "indices_from": spec.indices_from.isoformat(),
            "mcx_from": spec.mcx_from.isoformat(),
            "until_holdout_start": spec.until.isoformat(),
            "holdout_start_utc": ist_midnight_utc(spec.until).isoformat(),
            "reading": "evaluation starts at indices_from / mcx_from; the data before each start (at least "
            "one year) is the signal warm-up",
        },
        "data": _data_summary(datas),
        "implementation": {
            "signal": f"sign of the compounded futures-proxy return from the close {LOOKBACK} bars ago "
            "to the "
            f"close {SKIP} bars ago, at the close of the first trading day of each IST month",
            "sizing": f"weight = signal x {VOL_TARGET:.0%} / sigma, "
            f"sigma = sqrt({TRADING_DAYS} x EWMA of squared "
            f"daily returns), centre of mass {EWMA_COM} days, >= {MIN_VOL_RETURNS} returns, no leverage cap",
            "execution": "decided and traded at the rebalance close, held from the next bar to the next "
            "rebalance",
            "index_futures_proxy": f"spot close-to-close minus {INDEX_CARRY_PER_YEAR:.0%} a year, "
            "accrued per calendar day",
            "costs": "round trip x |weight change| + round trip x |new weight| (monthly roll) per rebalance",
            "portfolio": "daily mean of the active sleeves (3 indices before mcx_from, all 7 after); sleeves "
            "without 252 bars of history are flat but counted",
            "sharpe": "calendar-month P&L (sum of daily), mean / sd x sqrt(12)",
            "bootstrap": {
                "kind": boot.kind,
                "resamples": boot.resamples,
                "ci_level": boot.ci_level,
                "seed": boot.seed,
                "block": "one calendar month",
                "p_value": "(1 + #{bootstrap Sharpe <= 0}) / (1 + B)",
                "diagnostic_block": f"{LONG_BLOCK_MONTHS} months",
            },
        },
        "roll_rule": _roll_summary(datas),
        "costs": _costs(datas),
        "portfolio": port,
        "primary": {
            "hypothesis": "tsmom_v1 combined portfolio",
            "statistic": "annualised Sharpe after cost",
            "value": primary["sharpe"],
            "ci": primary["sharpe_ci"],
            "p_one_sided": primary["sharpe_p_one_sided"],
            "n_months": primary["n_months"],
            "n_days": primary["n_days"],
        },
        "pass_if": checks,
        "pass_own_rule": passed,
        "n_hypotheses": 1,
        "n_descriptive_tests": {
            "per_instrument_sharpe_p": len(per_instrument),
            "note": "per-instrument p-values are not gated; bh_q_across_instruments is BH over those seven",
        },
        "family": {
            "fdr_q": edge_v2_section("family")["fdr_q"],
            "note": "a pass also needs this p to survive Benjamini-Hochberg across every v2 primary p-value; "
            "the lead computes that across the family",
        },
        "per_instrument": per_instrument,
        "diagnostics": diagnostics,
        "interpretations": INTERPRETATIONS,
        "deviations": DEVIATIONS,
        "caveats": CAVEATS,
    }


INTERPRETATIONS = [
    "Evaluation windows start at indices_from (2008-01-01) and mcx_from (2019-01-01); the data before each "
    "start is the signal warm-up (MCX data begins 2018-01-01, so its first year is exactly the warm-up).",
    "'60-day EWMA' is read as MOP 2012's estimator: exponential weights with a centre of mass of 60 days on "
    "squared daily returns (not demeaned), annualised by 252, at least 60 returns.",
    "The signal uses the instrument's futures-proxy return (spot minus carry for indices, roll-adjusted for "
    "MCX), as MOP's futures excess return; a spot-price signal for indices is a diagnostic.",
    "Rebalance: decided and traded at the close of the first trading day of each IST month on the "
    "instrument's own calendar; the new weight earns from the next bar.",
]
DEVIATIONS = [
    "MCX roll days are detected from the series itself (no historical MCX expiry calendar in the repo): "
    "gold and silver rolls all have contract-change evidence, but many crude oil (2018-2020, 2024-25) and "
    "natural gas (2018-2020) windows have none and use the largest volume jump in the usual days of the "
    "month (a weak guess). Diagnostics show the result with only evidenced rolls and with no roll rule.",
    "Data repairs not in the registration, applied identically to strategy and baselines: open-to-close on "
    f"the first bar after a data hole (> {HOLE_DAYS} calendar days; GOLD 2019-04-05..2019-05-09 and "
    "2019-05-29..2019-06-05), open-to-close on both bars of a one-day off-market print (tiny volume, a big "
    "gap reversed next day), and zero return on the CRUDEOIL 2020-04-20 bar whose close is the vendor floor "
    "1.0 (MCX settled the April 2020 contract at a negative price). Keeping the floored price is a "
    "diagnostic.",
    "Costs: 'one round trip per position change' is read conservatively as a full round trip on the traded "
    "notional |weight change| at every monthly rebalance (a flip pays for twice the position), plus a full "
    "round trip on every open position each month for the roll, also for gold and silver, whose contracts "
    "roll every two months.",
    "Cost rates are today's (futures STT 0.05% since 2026-04-01) applied to the whole history, and brokerage "
    "is costed at the Rs 1 lakh reference notional; both overstate historical costs.",
]
CAVEATS = [
    "Index futures are proxied by spot minus 5% a year; the real basis moved with rates and dividends, and "
    "SENSEX futures were illiquid for most of the sample.",
    "NIFTY 50, SENSEX and BANKNIFTY are highly correlated, so the equal-risk portfolio is mostly one equity "
    "bet before 2019; the sample has about 213 monthly observations and one crisis (2008) at its start.",
    "The MCX continuous series is Fyers's unadjusted front month; its switch day is not always the "
    "exchange's expiry day, which is why the roll days come from the data.",
    "Gold and silver are costed with a roll every month although their contracts roll every two months "
    "(registered; conservative).",
    "Validation only: the holdout (>= 2025-10-01) was not read.",
]


# --- reports ---------------------------------------------------------------------------------------------


def _f(x, digits: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _ci(ci, digits: int = 2) -> str:
    return "n/a" if not ci else f"[{ci[0]:+.{digits}f}, {ci[1]:+.{digits}f}]"


def _pct(x, digits: int = 2) -> str:
    return "n/a" if x is None else f"{100 * x:.{digits}f}%"


def markdown_summary(report: dict) -> str:
    p = report["primary"]
    port = report["portfolio"]
    t, b = port["tsmom"], port["buy_and_hold"]
    verdict = "PASS" if report["pass_own_rule"] else "FAIL"
    lines = [
        f"# {report['test']} validation: 12-1 time-series momentum on indices and MCX",
        "",
        f"Generated {report['generated_at'][:19]}Z. Validation only: indices from "
        f"{report['split']['indices_from']}, "
        f"MCX from {report['split']['mcx_from']}, until the holdout "
        f"({report['split']['until_holdout_start']}, "
        "never read). Rule-based; nothing fitted.",
        "",
        f"**Own pass rule: {verdict}.** Primary: combined-portfolio annualised Sharpe after cost "
        f"{_f(p['value'])}, 95% CI {_ci(p['ci'])}, one-sided p = {_f(p['p_one_sided'], 4)} "
        f"({p['n_months']} months, month blocks, B = {report['implementation']['bootstrap']['resamples']}). "
        "A pass also needs Benjamini-Hochberg across the whole v2 family (computed by the lead).",
        "",
        f"Bottom line: the combined tsmom portfolio's Sharpe is {_f(t['before_cost']['sharpe'])} "
        "before costs "
        f"and {_f(t['after_cost']['sharpe'])} after ({_pct(t['annual_cost'])} a year of costs); buy-and-hold "
        f"equal risk scored {_f(b['after_cost']['sharpe'])} after costs.",
        "",
        "| portfolio | Sharpe after cost | 95% CI | Sharpe before cost | return/yr | vol/yr | cost/yr "
        "| max DD |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, e in (("tsmom", t), ("buy and hold, equal risk", b)):
        a = e["after_cost"]
        lines.append(
            f"| {name} | {_f(a['sharpe'])} | {_ci(a['sharpe_ci'])} | {_f(e['before_cost']['sharpe'])} | "
            f"{_pct(a['annual_return'])} | {_pct(a['annual_vol'])} | {_pct(e['annual_cost'])} | "
            f"{_pct(a['max_drawdown'])} |"
        )
    lines += ["| always flat | 0 (no risk) | - | - | 0 | 0 | 0 | 0 |", ""]
    lines += ["Pass checks:", ""]
    for key, c in report["pass_if"].items():
        if "ci" in c:
            lines.append(
                f"- {key}: Sharpe {_f(c['value'])}, CI lower {_f(c['ci'][0] if c['ci'] else None)} "
                f"> {c['ci_lower_above']}: {'pass' if c['pass'] else 'FAIL'}"
            )
        else:
            lines.append(
                f"- {key}: tsmom {_f(c['tsmom_sharpe'])} vs buy-and-hold {_f(c['buy_and_hold_sharpe'])}: "
                f"{'pass' if c['pass'] else 'FAIL'}"
            )
    lines += [
        "",
        "Per instrument (not gated; q = BH across the seven):",
        "",
        "| instrument | from | tsmom Sharpe | 95% CI | p | q | buy-and-hold Sharpe | long/short days "
        "| flips |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for iid, e in report["per_instrument"].items():
        a = e["tsmom"]["after_cost"]
        lines.append(
            f"| {iid} | {e['evaluation_from']} | {_f(a['sharpe'])} | {_ci(a['sharpe_ci'])} | "
            f"{_f(a['sharpe_p_one_sided'], 3)} | {_f(a.get('bh_q_across_instruments'), 3)} | "
            f"{_f(e['buy_and_hold']['after_cost']['sharpe'])} | "
            f"{_pct(a['share_days_long'], 0)}/{_pct(a['share_days_short'], 0)} | "
            f"{e['tsmom']['trades']['n_sign_flips']} |"
        )
    c = report["costs"]
    lines += [
        "",
        f"Costs per round trip: index futures {_f(c['index_futures']['round_trip_pct'], 3)}% "
        f"(Rs {_f(c['index_futures']['round_trip_inr_at_reference_notional'])} per Rs 1 lakh), MCX futures "
        f"{_f(c['mcx_futures']['round_trip_pct'], 3)}% "
        f"(Rs {_f(c['mcx_futures']['round_trip_inr_at_reference_notional'])}).",
        "",
        "Diagnostics (never part of the gate):",
        "",
    ]
    d = report["diagnostics"]
    lines.append(
        f"- Sharpe difference vs buy-and-hold {_f(d['sharpe_diff_vs_buy_and_hold']['value'])}, CI "
        f"{_ci(d['sharpe_diff_vs_buy_and_hold']['ci'])}."
    )
    for name, sub in d["sub_periods"].items():
        lines.append(
            f"- {name}: tsmom Sharpe {_f(sub['tsmom']['sharpe'])} CI {_ci(sub['tsmom']['sharpe_ci'])}, "
            f"buy-and-hold {_f(sub['buy_and_hold']['sharpe'])}."
        )
    lines.append(
        f"- Indices-only portfolio Sharpe {_f(d['indices_only_portfolio']['sharpe'])}; MCX-only "
        f"{_f(d['mcx_only_portfolio']['sharpe'])} (from {report['split']['mcx_from']})."
    )
    for name, v in d["variants"].items():
        lines.append(
            f"- {name}: tsmom Sharpe {_f(v['tsmom_sharpe'])} CI {_ci(v['tsmom_sharpe_ci'])}, "
            f"buy-and-hold {_f(v['buy_and_hold_sharpe'])}."
        )
    lines += ["", "MCX roll rule (fixed from the series before any strategy number):", ""]
    for iid, r in report["roll_rule"].items():
        lines.append(f"- {iid}: {r['n_rolls']} rolls, evidence {r['by_evidence']}.")
    lines += ["", "Readings of the registration:", *[f"- {x}" for x in report["interpretations"]]]
    lines += ["", "Deviations:", *[f"- {x}" for x in report["deviations"]], ""]
    lines += ["Caveats:", *[f"- {x}" for x in report["caveats"]], ""]
    return "\n".join(lines)


def write_report(report: dict, json_path: Path | None = None) -> tuple[Path, Path]:
    json_path = json_path or report_path()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md = json_path.with_suffix(".md")
    md.write_text(markdown_summary(report), encoding="utf-8")
    return json_path, md


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tsmom_v1 validation (never the holdout)")
    parser.parse_args(argv)
    result = evaluate()
    json_path, md_path = write_report(result.report)
    p = result.report["primary"]
    print(
        f"tsmom_v1 Sharpe after cost {_f(p['value'])} CI {_ci(p['ci'])} p={_f(p['p_one_sided'], 4)} "
        f"own rule pass={result.report['pass_own_rule']} ({result.report['runtime_s']}s) "
        f"-> {json_path}, {md_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
