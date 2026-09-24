"""im_v1 validation (config/edge_search_v2.yaml `intraday_momentum`, PLAN.md §20c): does the first
half-hour (with the overnight gap) predict the last half-hour on NIFTY 50 and BANKNIFTY? Gao, Han, Li &
Zhou (2018, JFE) "Market intraday momentum". Nothing is fitted; the two trading rules are fixed.

Prices are 5m spot bar closes (`ts` is the bar's open time, UTC), IST session 09:15-15:30:
- r1 = previous session's last close (its 15:25 bar) -> close of today's 09:40 bar (the price at 09:45);
- r12 = close of the 14:25 bar (14:30) -> close of the 14:55 bar (15:00);
- target = close of the 14:55 bar (15:00) -> close of the 15:25 bar (15:30), simple return.
Both signals are known at 15:00 (truncation-tested); the target is the label.

Rules, entered at 15:00 and exited at 15:30: sign_r1 = long if r1 > 0 else short; agree = trade sign(r1)
only when sign(r1) == sign(r12), else flat. Net = direction x target - one index-futures intraday round
trip (config/costs.yaml futures segment + index slippage) per trade.

Days used: every bar of each window present (09:15..09:40, 14:25..14:55, 15:00..15:25, and the previous
session's 15:25 bar); not a special session (weekend, markets.yaml special session, or hours other than
09:15-15:30 in the data) and the previous session not special either.

Primary per instrument x variant: mean net return per usable validation day (flat days 0), one-sided
date-block bootstrap (edge_search.yaml common.bootstrap; one block = one trading day, the holding
horizon), p = (1 + #{bootstrap mean <= 0}) / (1 + B). pass_if: the CI lower bound above 0 and the train
mean of the same sign. Also reported: the predictive regressions (target on r1, on r12, on both; OLS
with HC1 t-stats), hit rates against the base rate, 20-day-block CIs, and per-year means.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from candly.core.calendar import IST, get_calendar
from candly.core.settings import REPO_ROOT
from candly.research.config import config_hashes, load_costs_config, load_research_config
from candly.research.costs import cost_breakdown
from candly.research.data import CandleLoader, load_research_candles
from candly.research.edge_config import EDGE_FILE, EdgeSearchConfig, load_edge_config
from candly.research.edge_v2_config import EDGE_V2_FILE, edge_v2_section, edge_v2_sha256
from candly.research.stats import benjamini_hochberg
from candly.research.vol_eval import bootstrap_means, percentile_ci

TEST_NAME = "im_v1"
REPORT_NAME = "2026-09-24-im-v1-validation.json"
INSTRUMENTS = ("NSE:NIFTY50", "NSE:BANKNIFTY")
VARIANTS = ("sign_r1", "agree")
TF = "5m"
BAR = timedelta(minutes=5)
SESSION = ("09:15", "15:30")
EXCHANGE = "NSE"
LONG_BLOCK_DAYS = 20
TRADING_DAYS = 252
KEYS = {
    "name",
    "instruments",
    "times_ist",
    "r1",
    "r12",
    "target",
    "split",
    "variants",
    "costs",
    "skip_days",
    "primary",
    "pass_if",
}
TEXT_CHECKS = {
    "r1": ("previous session close", "close of the 09:40 bar"),
    "r12": ("14:30 to 15:00",),
    "target": ("15:00 to 15:30", "last 5m bar's close"),
    "costs": ("index futures intraday", "one round trip per trade", "slippage"),
    "skip_days": ("special sessions", "missing bar"),
    "primary": ("mean after-cost return per trading day", "flat days count as 0", "one-sided > 0"),
}
VARIANT_TEXT = {
    "sign_r1": ("long if r1 > 0 else short", "exit at 15:30"),
    "agree": ("sign(r1) == sign(r12)", "flat otherwise"),
}


def report_path() -> Path:
    return REPO_ROOT / "docs" / "test-reports" / REPORT_NAME


# --- the registration ------------------------------------------------------------------------------------


def _hm(value: str) -> datetime:
    return datetime.strptime(str(value), "%H:%M")


def _bar_before(value: str) -> str:
    """The 5m bar that closes at `value` (its open time)."""
    return (_hm(value) - BAR).strftime("%H:%M")


def _bars(start: str, end: str) -> tuple[str, ...]:
    """Open times of the 5m bars covering [start, end)."""
    t, stop, out = _hm(start), _hm(end), []
    while t < stop:
        out.append(t.strftime("%H:%M"))
        t += BAR
    return tuple(out)


@dataclass(frozen=True)
class ImSpec:
    name: str
    instruments: tuple[str, ...]
    r1_end_bar: str  # its close ends r1
    r12_start_bar: str
    r12_end_bar: str
    target_end_bar: str
    last_bar: str  # the session's last bar: the previous session's close
    window_bars: tuple[str, ...]  # every bar that must be present
    train: tuple[date, date]
    validation: tuple[date, date]
    ci_lower_above: float
    train_same_sign: bool
    registered: dict


def _date(value, key: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{EDGE_V2_FILE} intraday_momentum.{key}={value!r} is not a date") from None


def _period(raw, key: str, holdout_start: date) -> tuple[date, date]:
    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError(f"{EDGE_V2_FILE} intraday_momentum.split.{key} must be [start, end]")
    end = holdout_start if raw[1] == "holdout_start" else _date(raw[1], f"split.{key}")
    return _date(raw[0], f"split.{key}"), end


def parse_spec(raw: dict, holdout_start: date) -> ImSpec:
    if set(raw) != KEYS:
        raise ValueError(
            f"{EDGE_V2_FILE} intraday_momentum: unknown {sorted(set(raw) - KEYS)}, "
            f"missing {sorted(KEYS - set(raw))}"
        )
    checks = {**TEXT_CHECKS, **{f"variants.{k}": v for k, v in VARIANT_TEXT.items()}}
    for key, fragments in checks.items():
        value = raw["variants"].get(key.split(".")[1]) if key.startswith("variants.") else raw[key]
        text = " ".join(str(value).split())
        missing = [f for f in fragments if f not in text]
        if missing:
            raise ValueError(f"{EDGE_V2_FILE} intraday_momentum.{key} no longer says {missing}")
    if raw["name"] != TEST_NAME or tuple(raw["instruments"]) != INSTRUMENTS:
        raise ValueError(f"{EDGE_V2_FILE} intraday_momentum name/instruments differ from the code")
    if tuple(raw["variants"]) != VARIANTS:
        raise ValueError(
            f"{EDGE_V2_FILE} intraday_momentum variants differ from the code: {list(raw['variants'])}"
        )
    times = raw["times_ist"]
    if set(times) != {"first_end", "second_last", "last"}:
        raise ValueError(f"{EDGE_V2_FILE} intraday_momentum.times_ist keys changed: {sorted(times)}")
    first_end = str(times["first_end"])
    (r12_start, r12_end), (t_start, t_end) = times["second_last"], times["last"]
    if r12_end != t_start or t_end != SESSION[1]:
        raise ValueError(
            f"{EDGE_V2_FILE} intraday_momentum: the target must start where r12 ends and end at the close"
        )
    gate = raw["pass_if"]
    if set(gate) != {"mean_after_cost_ci_lower_above", "train_period_same_sign"}:
        raise ValueError(f"{EDGE_V2_FILE} intraday_momentum.pass_if keys changed: {sorted(gate)}")
    split = raw["split"]
    if set(split) != {"train", "validation"}:
        raise ValueError(f"{EDGE_V2_FILE} intraday_momentum.split keys changed: {sorted(split)}")
    train = _period(split["train"], "train", holdout_start)
    validation = _period(split["validation"], "validation", holdout_start)
    if not train[0] < train[1] <= validation[0] < validation[1] <= holdout_start:
        raise ValueError(f"{EDGE_V2_FILE} intraday_momentum.split must be ordered and end by the holdout")
    windows = _bars(SESSION[0], first_end) + _bars(_bar_before(r12_start), r12_end) + _bars(t_start, t_end)
    return ImSpec(
        name=TEST_NAME,
        instruments=INSTRUMENTS,
        r1_end_bar=_bar_before(first_end),
        r12_start_bar=_bar_before(r12_start),
        r12_end_bar=_bar_before(r12_end),
        target_end_bar=_bar_before(t_end),
        last_bar=_bar_before(SESSION[1]),
        window_bars=tuple(sorted(set(windows))),
        train=train,
        validation=validation,
        ci_lower_above=float(gate["mean_after_cost_ci_lower_above"]),
        train_same_sign=bool(gate["train_period_same_sign"]),
        registered=raw,
    )


def load_spec(edge: EdgeSearchConfig | None = None) -> ImSpec:
    edge = edge or load_edge_config()
    return parse_spec(edge_v2_section("intraday_momentum"), edge.common.holdout_start)


# --- sessions, features (causal) and labels -------------------------------------------------------------


def _grid(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(close, ts) tables: one row per IST date, one column per HH:MM bar open."""
    local = df["ts"].dt.tz_convert(IST)
    frame = pd.DataFrame(
        {"day": local.dt.date, "hm": local.dt.strftime("%H:%M"), "close": df["close"], "ts": df["ts"]}
    )
    close = frame.pivot(index="day", columns="hm", values="close")
    ts = frame.pivot(index="day", columns="hm", values="ts")
    return close, ts


def _present(close: pd.DataFrame, bars) -> pd.Series:
    cols = [b for b in bars if b in close.columns]
    if len(cols) < len(tuple(bars)):
        return pd.Series(False, index=close.index)
    return close[cols].notna().all(axis=1)


def _col(table: pd.DataFrame, bar: str) -> pd.Series:
    return table[bar] if bar in table.columns else pd.Series(np.nan, index=table.index)


def im_features(df: pd.DataFrame, spec: ImSpec) -> pd.DataFrame:
    """Per day with a decision bar (the one closing at 15:00): r1, r12 and whether their windows are
    complete, using only bars up to that bar's close. `ts` is the decision bar's open time."""
    close, ts = _grid(df)
    prev_close = _col(close, spec.last_bar).shift(1)
    prev_day = pd.Series(close.index, index=close.index).shift(1)
    r1_bars = _bars(SESSION[0], (_hm(spec.r1_end_bar) + BAR).strftime("%H:%M"))
    r12_bars = _bars(spec.r12_start_bar, (_hm(spec.r12_end_bar) + BAR).strftime("%H:%M"))
    decision = _col(ts, spec.r12_end_bar)
    out = pd.DataFrame(
        {
            "ts": decision,
            "day": close.index,
            "prev_day": prev_day,
            "prev_close": prev_close,
            "r1": _col(close, spec.r1_end_bar) / prev_close - 1.0,
            "r12": _col(close, spec.r12_end_bar) / _col(close, spec.r12_start_bar) - 1.0,
            "r1_complete": _present(close, r1_bars) & prev_close.notna(),
            "r12_complete": _present(close, r12_bars),
        },
        index=close.index,
    )
    out = out[out["ts"].notna()].reset_index(drop=True)
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    return out


def im_labels(df: pd.DataFrame, spec: ImSpec) -> pd.DataFrame:
    """Per day: the target (15:00 -> 15:30) and whether its window is complete. Uses future bars."""
    close, _ = _grid(df)
    target_bars = _bars((_hm(spec.r12_end_bar) + BAR).strftime("%H:%M"), SESSION[1])
    return pd.DataFrame(
        {
            "target": _col(close, spec.target_end_bar) / _col(close, spec.r12_end_bar) - 1.0,
            "target_complete": _present(close, target_bars),
            "n_bars": close.notna().sum(axis=1),
        },
        index=close.index,
    )


def special_days(days, spec: ImSpec) -> set[date]:
    listed = set(get_calendar().spec(EXCHANGE).special_sessions)
    return {d for d in days if d.weekday() >= 5 or d in listed}


def standard_sessions(df: pd.DataFrame, spec: ImSpec) -> pd.Series:
    """Per IST date: True for a regular session (bars from 09:15 to 15:25, not listed as special)."""
    close, _ = _grid(df)
    first = close.notna().idxmax(axis=1)
    last = close.iloc[:, ::-1].notna().idxmax(axis=1)
    listed = special_days(close.index, spec)
    return (first == SESSION[0]) & (last == spec.last_bar) & ~close.index.isin(list(listed))


def daily_table(df: pd.DataFrame, spec: ImSpec) -> pd.DataFrame:
    """Features + labels + the skip rules, one row per day with a decision bar."""
    t = im_features(df, spec).join(im_labels(df, spec), on="day")
    standard = standard_sessions(df, spec)
    t["special"] = ~t["day"].map(standard).astype(bool)
    t["prev_special"] = ~t["prev_day"].map(standard).fillna(False).astype(bool)
    t["usable"] = (
        t["r1_complete"] & t["r12_complete"] & t["target_complete"] & ~t["special"] & ~t["prev_special"]
    )
    return t


def directions(r1, r12) -> dict[str, np.ndarray]:
    r1, r12 = np.asarray(r1, float), np.asarray(r12, float)
    s1, s12 = np.sign(r1), np.sign(r12)
    return {
        "sign_r1": np.where(r1 > 0, 1.0, -1.0),
        "agree": np.where((s1 == s12) & (s1 != 0), s1, 0.0),
    }


# --- statistics ------------------------------------------------------------------------------------------


def p_one_sided(samples: np.ndarray) -> float | None:
    """p of H0 "statistic <= 0": (1 + #{draws <= 0}) / (1 + B)."""
    if samples.size == 0:
        return None
    return float((1 + np.count_nonzero(samples <= 0)) / (1 + samples.size))


def ols_hc1(y, X, names: tuple[str, ...]) -> dict:
    """OLS with an intercept; HC1 (heteroskedasticity-robust) standard errors."""
    y = np.asarray(y, float)
    X = np.column_stack([np.ones(len(y)), np.asarray(X, float).reshape(len(y), -1)])
    n, k = X.shape
    if n <= k:
        return {"n": int(n)}
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    resid = y - X @ beta
    meat = X.T @ (X * resid[:, None] ** 2)
    cov = xtx_inv @ meat @ xtx_inv * n / (n - k)
    se = np.sqrt(np.diag(cov))
    with np.errstate(divide="ignore", invalid="ignore"):
        r2 = 1.0 - (resid @ resid) / ((y - y.mean()) @ (y - y.mean()))
        t = beta / se

    def num(x) -> float | None:
        return float(x) if np.isfinite(x) else None

    out = {"n": int(n), "r2": num(r2), "intercept": float(beta[0]), "intercept_t": num(t[0])}
    for i, name in enumerate(names, start=1):
        out[f"slope_{name}"] = float(beta[i])
        out[f"t_{name}"] = num(t[i])
    return out


def regressions(rows: pd.DataFrame) -> dict:
    y = rows["target"].to_numpy()
    return {
        "target_on_r1": ols_hc1(y, rows[["r1"]].to_numpy(), ("r1",)),
        "target_on_r12": ols_hc1(y, rows[["r12"]].to_numpy(), ("r12",)),
        "target_on_r1_and_r12": ols_hc1(y, rows[["r1", "r12"]].to_numpy(), ("r1", "r12")),
    }


def strategy_stats(rows: pd.DataFrame, direction: np.ndarray, round_trip: float, boot) -> dict:
    """Stats of one rule on usable days (rows): flat days count as 0."""
    target = rows["target"].to_numpy()
    traded = direction != 0
    gross = direction * target
    net = gross - np.abs(direction) * round_trip
    days = np.arange(len(rows))
    samples = bootstrap_means(net, days, boot.resamples, boot.seed) if len(rows) else np.array([])
    long_samples = (
        bootstrap_means(net, days // LONG_BLOCK_DAYS, boot.resamples, boot.seed)
        if len(rows)
        else np.array([])
    )
    gross_samples = bootstrap_means(gross, days, boot.resamples, boot.seed) if len(rows) else np.array([])
    up = float((target > 0).mean()) if len(rows) else None
    share_long = float((direction > 0).sum() / max(traded.sum(), 1))
    null_hit = None if up is None else share_long * up + (1 - share_long) * (1 - up)
    years = pd.Series(net, index=[d.year for d in rows["day"]]).groupby(level=0)
    return {
        "n_days": int(len(rows)),
        "n_trades": int(traded.sum()),
        "n_long": int((direction > 0).sum()),
        "n_short": int((direction < 0).sum()),
        "mean_net_per_day": float(net.mean()) if len(rows) else None,
        "mean_net_per_day_ci": percentile_ci(samples, boot.ci_level),
        "p_one_sided": p_one_sided(samples),
        "mean_net_per_day_ci_20d_blocks": percentile_ci(long_samples, boot.ci_level),
        "mean_gross_per_day": float(gross.mean()) if len(rows) else None,
        "mean_gross_per_day_ci": percentile_ci(gross_samples, boot.ci_level),
        "mean_gross_per_trade": float(gross[traded].mean()) if traded.any() else None,
        "mean_net_per_trade": float(net[traded].mean()) if traded.any() else None,
        "round_trip_cost": round_trip,
        "hit_rate": float((gross[traded] > 0).mean()) if traded.any() else None,
        "hit_rate_if_independent": null_hit,
        "base_rate_up": up,
        "net_win_rate": float((net[traded] > 0).mean()) if traded.any() else None,
        "sharpe_annualised": (
            float(net.mean() / net.std(ddof=1) * math.sqrt(TRADING_DAYS))
            if len(rows) > 1 and net.std() > 0
            else None
        ),
        "total_net": float(net.sum()),
        "mean_net_per_day_by_year": {str(k): float(v) for k, v in years.mean().items()},
    }


# --- evaluation ------------------------------------------------------------------------------------------


def round_trip() -> tuple[float, dict[str, float]]:
    parts = cost_breakdown("index", "intraday")
    return float(sum(parts.values())), parts


@dataclass
class ImResult:
    report: dict
    tables: dict[str, pd.DataFrame] = field(repr=False)


def _in(days: pd.Series, period: tuple[date, date]) -> pd.Series:
    return (days >= period[0]) & (days < period[1])


def evaluate(
    *, load: CandleLoader | None = None, edge: EdgeSearchConfig | None = None, frames: dict | None = None
) -> ImResult:
    started = time.perf_counter()
    edge = edge or load_edge_config()
    spec = load_spec(edge)
    boot = edge.common.bootstrap
    rt, parts = round_trip()
    frames = frames or {
        i: load_research_candles(i, TF, allow_holdout=False, load=load) for i in spec.instruments
    }
    holdout_utc = edge.common.holdout_start_utc
    tables, per, regs, data = {}, {}, {}, {}
    for iid in spec.instruments:
        df = frames[iid]
        if (df["ts"] >= holdout_utc).any():
            raise PermissionError(f"{iid} holds holdout bars; the holdout is locked")
        t = daily_table(df, spec)
        tables[iid] = t
        data[iid] = {
            "bars": int(len(df)),
            "first_bar": df["ts"].min().isoformat() if len(df) else None,
            "last_bar": df["ts"].max().isoformat() if len(df) else None,
            "days_with_decision_bar": int(len(t)),
            "skipped": {
                "special_session": int(t["special"].sum()),
                "previous_session_special": int((~t["special"] & t["prev_special"]).sum()),
                "missing_window_bar": int(
                    (
                        ~t["special"]
                        & ~t["prev_special"]
                        & ~(t["r1_complete"] & t["r12_complete"] & t["target_complete"])
                    ).sum()
                ),
            },
        }
        per[iid], regs[iid] = {}, {}
        for period_name, period in (("train", spec.train), ("validation", spec.validation)):
            rows = t[t["usable"] & _in(t["day"], period)].reset_index(drop=True)
            dirs = directions(rows["r1"], rows["r12"])
            regs[iid][period_name] = regressions(rows) if len(rows) > 3 else {}
            for variant in VARIANTS:
                per[iid].setdefault(variant, {})[period_name] = strategy_stats(rows, dirs[variant], rt, boot)
    hypotheses = []
    for iid in spec.instruments:
        for variant in VARIANTS:
            val, train = per[iid][variant]["validation"], per[iid][variant]["train"]
            ci = val["mean_net_per_day_ci"]
            same = (
                val["mean_net_per_day"] is not None
                and train["mean_net_per_day"] is not None
                and np.sign(val["mean_net_per_day"]) == np.sign(train["mean_net_per_day"])
            )
            ci_ok = ci is not None and ci[0] > spec.ci_lower_above
            hypotheses.append(
                {
                    "instrument": iid,
                    "variant": variant,
                    "primary_mean_net_per_day": val["mean_net_per_day"],
                    "ci": ci,
                    "p_one_sided": val["p_one_sided"],
                    "n_days": val["n_days"],
                    "n_trades": val["n_trades"],
                    "checks": {
                        "mean_after_cost_ci_lower_above": {
                            "ci_lower": ci[0] if ci else None,
                            "above": spec.ci_lower_above,
                            "pass": bool(ci_ok),
                        },
                        "train_period_same_sign": {
                            "train_mean_net_per_day": train["mean_net_per_day"],
                            "validation_mean_net_per_day": val["mean_net_per_day"],
                            "pass": bool(same) if spec.train_same_sign else True,
                        },
                    },
                    "pass_own_rule": bool(ci_ok and (same or not spec.train_same_sign)),
                }
            )
    pvals = [h["p_one_sided"] for h in hypotheses]
    q = benjamini_hochberg(pvals) if all(p is not None for p in pvals) else [None] * len(pvals)
    for h, qv in zip(hypotheses, q, strict=True):
        h["bh_q_within_im_v1"] = None if qv is None else float(qv)
    report = _report(spec, edge, rt, parts, data, per, regs, hypotheses)
    report["runtime_s"] = round(time.perf_counter() - started, 1)
    return ImResult(report=report, tables=tables)


def _report(spec: ImSpec, edge, rt, parts, data, per, regs, hypotheses) -> dict:
    boot = edge.common.bootstrap
    notional = float(load_costs_config().get("reference_notional_inr") or 100_000.0)
    return {
        "test": spec.name,
        "period": "validation",
        "generated_at": datetime.now(UTC).isoformat(),
        "edge_search_v2_sha256": edge_v2_sha256(),
        "config_hashes": {**config_hashes(), EDGE_FILE: edge.sha256, EDGE_V2_FILE: edge_v2_sha256()},
        "registered": spec.registered,
        "split": {
            "train": [spec.train[0].isoformat(), spec.train[1].isoformat()],
            "validation": [spec.validation[0].isoformat(), spec.validation[1].isoformat()],
            "holdout_start": edge.common.holdout_start.isoformat(),
            "holdout_start_utc": edge.common.holdout_start_utc.isoformat(),
        },
        "implementation": {
            "prices": "5m spot bar closes; ts = bar open (UTC), times below are IST bar opens",
            "r1": f"previous session's {spec.last_bar} bar close -> {spec.r1_end_bar} bar close",
            "r12": f"{spec.r12_start_bar} bar close -> {spec.r12_end_bar} bar close",
            "target": f"{spec.r12_end_bar} bar close -> {spec.target_end_bar} bar close (simple return)",
            "required_bars": list(spec.window_bars) + [f"previous session {spec.last_bar}"],
            "special_session": "weekend, a markets.yaml NSE special session, or a session whose bars do not "
            "run 09:15..15:25; the day after a special session is skipped too",
            "bootstrap": {
                "kind": boot.kind,
                "resamples": boot.resamples,
                "ci_level": boot.ci_level,
                "seed": boot.seed,
                "block": "one trading day",
                "diagnostic_block_days": LONG_BLOCK_DAYS,
                "p_value": "(1 + #{bootstrap mean <= 0}) / (1 + B)",
            },
            "regression_t_stats": "OLS with intercept, HC1 standard errors",
        },
        "costs": {
            "segment": "futures (index intraday mapping) + index slippage",
            "round_trip_pct": 100.0 * rt,
            "round_trip_inr_at_reference_notional": rt * notional,
            "reference_notional_inr": notional,
            "components_pct": {k: 100.0 * v for k, v in parts.items()},
        },
        "data": data,
        "hypotheses": hypotheses,
        "n_hypotheses": len(hypotheses),
        "pass_own_rule_any": any(h["pass_own_rule"] for h in hypotheses),
        "family": {
            "fdr_q": edge_v2_section("family")["fdr_q"],
            "research_fdr_alpha": load_research_config().fdr_alpha,
            "note": "a pass also needs its p to survive Benjamini-Hochberg across every v2 primary p-value "
            "(computed by the lead); bh_q_within_im_v1 is context only",
        },
        "per_instrument": per,
        "regressions": regs,
        "deviations": DEVIATIONS,
        "caveats": CAVEATS,
    }


DEVIATIONS = [
    "r12 starts at the close of the 14:25 bar (the price at 14:30), so that bar is required too; the "
    "registration names only the 14:30..15:00 window.",
    "A day whose previous session was special is skipped as well (its r1 would start from a special "
    "session's close); the registration names only special sessions themselves.",
    "Costs use today's rates (futures STT 0.05% since 2026-04-01) for the whole history and brokerage at the "
    "Rs 1 lakh reference notional (Rs 20 an order = 0.02%); one NIFTY lot is ~Rs 15-20 lakh, so brokerage "
    "is overstated. Both make the after-cost numbers conservative.",
]
CAVEATS = [
    "Spot index bars proxy index futures: the futures basis, futures bid-ask and the 15:30 closing auction "
    "of the futures are not modelled; entry and exit are at the 5m bar closes at 15:00 and 15:30.",
    "The spot index's last print is not the official closing price (a 30-minute VWAP of constituents); the "
    "target uses the last 5m bar's close as registered.",
    "sign_r1 and agree on the same instrument share most trades, and NIFTY and BANKNIFTY are correlated, so "
    "the four p-values are dependent; BH is valid under positive dependence.",
    "Validation only: the holdout (>= 2025-10-01) was not read.",
]


# --- reports ---------------------------------------------------------------------------------------------


def _bp(x) -> str:
    return "n/a" if x is None else f"{1e4 * x:+.2f}bp"


def _ci_bp(ci) -> str:
    return "n/a" if not ci else f"[{1e4 * ci[0]:+.2f}, {1e4 * ci[1]:+.2f}]bp"


def _f(x, digits: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _pct(x, digits: int = 1) -> str:
    return "n/a" if x is None else f"{100 * x:.{digits}f}%"


def markdown_summary(report: dict) -> str:
    c, split = report["costs"], report["split"]
    verdict = "at least one PASS" if report["pass_own_rule_any"] else "FAIL on all four"
    gross = [
        p["validation"]["mean_gross_per_trade"]
        for v in report["per_instrument"].values()
        for p in v.values()
        if p["validation"]["mean_gross_per_trade"] is not None
    ]
    lines = [
        f"# {report['test']} validation: intraday momentum on NIFTY 50 and BANKNIFTY",
        "",
        f"Generated {report['generated_at'][:19]}Z. Train {split['train'][0]}..{split['train'][1]}, "
        f"validation {split['validation'][0]}..{split['validation'][1]} (holdout never read). "
        f"Round trip {c['round_trip_pct']:.3f}% "
        f"(Rs {c['round_trip_inr_at_reference_notional']:.2f} per Rs 1 lakh) per trade. "
        "bp = basis points of notional.",
        "",
        f"Bottom line: before costs the rules made {_bp(min(gross, default=None))} to "
        f"{_bp(max(gross, default=None))} per trade in "
        f"validation, against a round trip of {_bp(c['round_trip_pct'] / 100)}.",
        "",
        f"**Own pass rule: {verdict}.** "
        "A pass also needs Benjamini-Hochberg across the whole v2 family (computed by the lead).",
        "",
        "| instrument | variant | validation mean net/day | 95% CI | p (one-sided) | train mean net/day "
        "| trades/days | pass |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for h in report["hypotheses"]:
        train = h["checks"]["train_period_same_sign"]["train_mean_net_per_day"]
        lines.append(
            f"| {h['instrument']} | {h['variant']} | {_bp(h['primary_mean_net_per_day'])} | "
            f"{_ci_bp(h['ci'])} | {_f(h['p_one_sided'], 4)} | {_bp(train)} "
            f"| {h['n_trades']}/{h['n_days']} | {'PASS' if h['pass_own_rule'] else 'FAIL'} |"
        )
    lines += [
        "",
        "Per trade, validation (gross = before costs):",
        "",
        "| instrument | variant | gross/trade | net/trade | hit rate | hit rate if independent "
        "| gross/day CI |",
        "|---|---|---|---|---|---|---|",
    ]
    for iid, variants in report["per_instrument"].items():
        for variant, periods in variants.items():
            v = periods["validation"]
            lines.append(
                f"| {iid} | {variant} | {_bp(v['mean_gross_per_trade'])} | {_bp(v['mean_net_per_trade'])} | "
                f"{_pct(v['hit_rate'])} | {_pct(v['hit_rate_if_independent'])} | "
                f"{_ci_bp(v['mean_gross_per_day_ci'])} |"
            )
    lines += [
        "",
        "Predictive regressions (slope, HC1 t-stat):",
        "",
        "| instrument | period | n | target on r1 | target on r12 | R^2 (both) |",
        "|---|---|---|---|---|---|",
    ]
    for iid, periods in report["regressions"].items():
        for period, r in periods.items():
            a, b, j = r["target_on_r1"], r["target_on_r12"], r["target_on_r1_and_r12"]
            lines.append(
                f"| {iid} | {period} | {a['n']} | {_f(a['slope_r1'], 4)} (t {_f(a['t_r1'], 2)}) | "
                f"{_f(b['slope_r12'], 4)} (t {_f(b['t_r12'], 2)}) | {_pct(j['r2'], 2)} |"
            )
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
    parser = argparse.ArgumentParser(description="im_v1 validation (never the holdout)")
    parser.parse_args(argv)
    result = evaluate()
    json_path, md_path = write_report(result.report)
    for h in result.report["hypotheses"]:
        print(
            f"{h['instrument']} {h['variant']}: mean net/day {_bp(h['primary_mean_net_per_day'])} "
            f"CI {_ci_bp(h['ci'])} p={_f(h['p_one_sided'], 4)} pass={h['pass_own_rule']}"
        )
    print(f"({result.report['runtime_s']}s) -> {json_path}, {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
