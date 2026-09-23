"""Go/no-go #1 harness (PLAN.md §19): replay a forecast method bar by bar and score it like the ledger.

For every reference bar in the period, the method sees only candles up to that bar, with `now` one
second after that bar's close. The stale-data abstention is off: the calendar has no pre-2026 holidays,
so it can't say which historical bars were due. Each forecast is graded as the ledger grades it (actual
bars matched to its target times; void when one never appeared), against the base-rate baseline.

The period is the validation span [train_end, holdout) by default. The holdout can only be replayed with
`allow_holdout=True`, which only an official go/no-go run may pass.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from candly.core.calendar import get_calendar
from candly.core.instruments import exchange_of
from candly.core.timeframes import validate_tf
from candly.forecast import METHOD, Forecast, baseline_forecasts, make_forecast
from candly.forecast.jobs import NOT_RECORDED
from candly.indicators.functions import atr as atr_fn
from candly.ledger.grading import brier_baseline, grade, match_bars, outcome_up, target_times
from candly.research.config import ResearchConfig, load_research_config
from candly.research.data import CandleLoader, load_research_candles
from candly.research.scorecard import Scorecard, build_scorecard, default_instruments
from candly.research.stats import expected_calibration_error

METHODS = (METHOD, "baseline_base_rate", "baseline_persistence", "baseline_random_walk")
Period = Literal["validation", "holdout"]


@dataclass
class Evaluation:
    tf: str
    method: str
    period: str
    start: str
    end: str | None
    instruments: list[str]
    n_forecasts: int  # recorded the way the forecast job records them
    n_not_recorded: int  # no forecast possible (not enough history); the forecast job records none
    n_abstained: int
    n_graded: int
    n_void: int  # a target bar never appeared in the data
    n_unresolved: int  # target bars fall after the last available bar
    n_scored: int  # graded and not abstained: the forecasts behind brier, skill, ece and hit_rate
    brier: float | None
    brier_baseline: float | None
    skill: float | None
    ece: float | None
    ece_bins: int
    ece_binning: str
    hit_rate: float | None
    band_coverage_80: float | None
    n_certified: int | None
    gates: dict[str, bool] = field(default_factory=dict)
    records: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)


def _forecast(method: str, instrument_id: str, tf: str, hist: pd.DataFrame, card, now) -> Forecast | None:
    if method == METHOD:
        return make_forecast(instrument_id, tf, hist, card, now=now, check_stale=False)
    baselines = baseline_forecasts(instrument_id, tf, hist, now=now)
    return next((fc for fc in baselines if fc.method == method), None)


def _grade_record(fc: Forecast, df: pd.DataFrame, atr_at_ref: float) -> dict:
    rec = {
        "instrument": fc.instrument,
        "ref_time": pd.Timestamp(fc.ref_time, unit="s", tz="UTC"),
        "p_up": fc.p_up,
        "base_rate": fc.base_rate,
        "abstain": fc.abstain,
        "abstain_reason": fc.abstain_reason,
        "confidence": fc.confidence,
        "n_analogs": fc.n_analogs,
        "status": "unresolved",
        "y": np.nan,
        "brier": np.nan,
        "brier_baseline": np.nan,
        "direction_hit": None,
        "band_hits": 0,
        "band_steps": 0,
    }
    times = target_times(fc)
    if not times or times[-1] > df["ts"].iloc[-1]:
        return rec
    matched = match_bars(times, df)
    atr = fc.context.atr if fc.context is not None and fc.context.atr else atr_at_ref
    if any(c is None for c in matched) or not (atr > 0):
        return {**rec, "status": "void"}
    actual = [c for c in matched if c is not None]
    g = grade(fc, actual, atr)
    band_steps = [s.in_band_80 for s in g.steps if s.step <= len(fc.bands)]
    return {
        **rec,
        "status": "graded",
        "y": float(outcome_up(fc, actual)),
        "brier": np.nan if g.brier is None else g.brier,
        "brier_baseline": np.nan if (b := brier_baseline(fc, actual)) is None else b,
        "direction_hit": g.direction_hit,
        "band_hits": int(sum(band_steps)),
        "band_steps": len(band_steps),
    }


def _replay(
    instrument_id: str,
    tf: str,
    df: pd.DataFrame,
    card: Scorecard | None,
    method: str,
    start: pd.Timestamp,
    end: pd.Timestamp | None,
) -> tuple[list[dict], int]:
    """Graded records for every reference bar in [start, end), and how many reference bars gave no
    forecast the job would record."""
    cal = get_calendar()
    exchange = exchange_of(instrument_id)
    ts = df["ts"]
    in_period = (ts >= start) & (ts < end) if end is not None else ts >= start
    atr = atr_fn(df, 14).to_numpy()
    records, skipped = [], 0
    for i in np.flatnonzero(in_period.to_numpy()):
        now = cal.bar_close_time(exchange, ts.iloc[i], tf) + pd.Timedelta(seconds=1)
        fc = _forecast(method, instrument_id, tf, df.iloc[: i + 1], card, now)
        if fc is None or (fc.abstain and (fc.abstain_reason or "").startswith(NOT_RECORDED)):
            skipped += 1
            continue
        records.append(_grade_record(fc, df, float(atr[i])))
    return records, skipped


def _mean(values: pd.Series) -> float | None:
    values = values.dropna()
    return float(values.astype(float).mean()) if len(values) else None


def _summarise(records: pd.DataFrame, cfg: ResearchConfig) -> dict:
    if records.empty:
        return dict.fromkeys(
            ("brier", "brier_baseline", "skill", "ece", "hit_rate", "band_coverage_80"), None
        ) | {"n_scored": 0}
    graded = records[records["status"] == "graded"]
    scored = graded[~graded["abstain"] & graded["p_up"].notna() & graded["base_rate"].notna()]
    brier, baseline = _mean(scored["brier"]), _mean(scored["brier_baseline"])
    gng = cfg.go_no_go_1
    band_steps = int(graded["band_steps"].sum())
    return {
        "n_scored": len(scored),
        "brier": brier,
        "brier_baseline": baseline,
        "skill": (1.0 - brier / baseline) if brier is not None and baseline else None,
        "ece": expected_calibration_error(
            scored["p_up"].to_numpy(float), scored["y"].to_numpy(float), gng.ece_bins, gng.ece_binning
        ),
        "hit_rate": _mean(scored["direction_hit"]),
        "band_coverage_80": float(graded["band_hits"].sum()) / band_steps if band_steps else None,
    }


def evaluate_forecasts(
    tf: str,
    method: str = METHOD,
    allow_holdout: bool = False,
    *,
    period: Period = "validation",
    instruments: Iterable[str] | None = None,
    load: CandleLoader | None = None,
    scorecard: Scorecard | None = None,
) -> Evaluation:
    """Replay `method` over `period` for the go/no-go slice (or `instruments`) and score it.

    analog_v1 draws pooled analogs from `scorecard`, by default an in-memory build over the same
    instruments from pre-holdout data (as the live system uses); its analogs are filtered to those whose
    outcomes had closed by each reference bar.
    """
    tf = validate_tf(tf)
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}; expected one of {METHODS}")
    if period not in ("validation", "holdout"):
        raise ValueError("period must be 'validation' or 'holdout'")
    if period == "holdout" and not allow_holdout:
        raise PermissionError("the holdout is locked; an official go/no-go run passes allow_holdout=True")
    cfg = load_research_config()
    ids = list(instruments) if instruments is not None else default_instruments(tf)
    start = cfg.train_end_utc(tf) if period == "validation" else cfg.holdout_start_utc
    end = cfg.holdout_start_utc if period == "validation" else None
    card = scorecard
    if card is None and method == METHOD:
        card = build_scorecard(tf, ids, load=load, persist=False)

    rows: list[dict] = []
    not_recorded = 0
    for instrument_id in ids:
        df = load_research_candles(instrument_id, tf, allow_holdout=period == "holdout", load=load)
        if df.empty:
            continue
        records, skipped = _replay(instrument_id, tf, df, card, method, start, end)
        rows.extend(records)
        not_recorded += skipped
    records = pd.DataFrame(rows)
    summary = _summarise(records, cfg)
    status = records["status"] if not records.empty else pd.Series(dtype=object)
    n_certified = int(card.rows["certified"].sum()) if card is not None and len(card.rows) else None
    gng = cfg.go_no_go_1
    gates = {
        "certified_buckets": n_certified is not None and n_certified >= gng.require_certified_buckets,
        "brier_skill": summary["skill"] is not None and summary["skill"] > gng.require_brier_skill_above,
        "ece": summary["ece"] is not None and summary["ece"] < gng.require_ece_below,
    }
    return Evaluation(
        tf=tf,
        method=method,
        period=period,
        start=start.isoformat(),
        end=end.isoformat() if end is not None else None,
        instruments=ids,
        n_forecasts=len(records),
        n_not_recorded=not_recorded,
        n_abstained=int(records["abstain"].sum()) if not records.empty else 0,
        n_graded=int((status == "graded").sum()),
        n_void=int((status == "void").sum()),
        n_unresolved=int((status == "unresolved").sum()),
        ece_bins=gng.ece_bins,
        ece_binning=gng.ece_binning,
        n_certified=n_certified,
        gates=gates,
        records=records,
        **summary,
    )
