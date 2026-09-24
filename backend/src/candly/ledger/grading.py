"""Grading of one forecast against the candles that actually closed (CONTRACTS.md, grading definitions).

Actual bars are matched to the forecast's predicted bar times, never by position: a predicted time with
no bar (a holiday the calendar didn't know, a missing bar) leaves that step missing instead of grading a
later bar in its place.
"""

from __future__ import annotations

import math

import pandas as pd

from candly.core.instruments import exchange_of
from candly.forecast.models import Candle, Forecast
from candly.forecast.timing import future_bar_times
from candly.ledger.models import Category, Grade, StepGrade
from candly.research.pivot_config import load_pivot_config


def target_times(forecast: Forecast) -> list[pd.Timestamp]:
    """Open times of the bars the forecast is graded on: its ghost candles' times, or (for a forecast
    without enough ghosts) the calendar's next bars after the reference bar."""
    n = max(forecast.horizon_bars, len(forecast.ghost_candles))
    if len(forecast.ghost_candles) >= n:
        return [pd.Timestamp(c.time, unit="s", tz="UTC") for c in forecast.ghost_candles[:n]]
    ref_ts = pd.Timestamp(forecast.ref_time, unit="s", tz="UTC")
    return future_bar_times(exchange_of(forecast.instrument), forecast.tf, ref_ts, n)


def match_bars(times: list[pd.Timestamp], candles: pd.DataFrame | None) -> list[Candle | None]:
    """The candle that opened at each target time, or None where no bar has that time."""
    if not times or candles is None or candles.empty:
        return [None] * len(times)
    pos = pd.Index(candles["ts"]).get_indexer(pd.DatetimeIndex(times).tz_convert("UTC"))
    out: list[Candle | None] = []
    for p in pos:
        if p < 0:
            out.append(None)
            continue
        r = candles.iloc[p]
        out.append(
            Candle(
                time=int(r["ts"].timestamp()),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=float(r["volume"]),
            )
        )
    return out


def interval_iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Overlap of two closed intervals divided by their union; 1.0 for two identical points."""
    lo_a, hi_a = min(a), max(a)
    lo_b, hi_b = min(b), max(b)
    inter = max(0.0, min(hi_a, hi_b) - max(lo_a, lo_b))
    union = (hi_a - lo_a) + (hi_b - lo_b) - inter
    if union <= 0:
        return 1.0 if (lo_a, hi_a) == (lo_b, hi_b) else 0.0
    return inter / union


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def candle_category(pred: Candle, act: Candle, band, atr: float, same_atr: float) -> Category | None:
    """pivot.yaml candle_accuracy: "wrong" when the close is outside the p10-p90 band (checked first, so
    same + close = band coverage); "same" when the close is within `same_atr` ATR of the p50 and the bar's
    high and low stay inside the ghost candle's; otherwise "close". None without a band."""
    if band is None:
        return None
    if not band.p10 <= act.close <= band.p90:
        return "wrong"
    near = abs(act.close - band.p50) <= same_atr * atr
    return "same" if near and act.high <= pred.high and act.low >= pred.low else "close"


def grade_step(step: int, pred: Candle, act: Candle, atr: float, band=None) -> StepGrade:
    same_atr = load_pivot_config().candle_accuracy.same_close_atr
    return StepGrade(
        step=step,
        close_err_pct=100.0 * abs(pred.close - act.close) / act.close,
        close_err_atr=abs(pred.close - act.close) / atr,
        high_err_atr=abs(pred.high - act.high) / atr,
        low_err_atr=abs(pred.low - act.low) / atr,
        range_iou=interval_iou((pred.low, pred.high), (act.low, act.high)),
        body_iou=interval_iou((pred.open, pred.close), (act.open, act.close)),
        color_match=_sign(pred.close - pred.open) == _sign(act.close - act.open),
        in_band_80=band is not None and band.p10 <= act.close <= band.p90,
        category=candle_category(pred, act, band, atr, same_atr),
    )


def match_score(steps: list[StepGrade]) -> float:
    if not steps:
        return 0.0
    parts = [
        0.4 * s.range_iou + 0.3 * max(0.0, 1.0 - s.close_err_atr) + 0.3 * float(s.color_match) for s in steps
    ]
    return 100.0 * sum(parts) / len(parts)


def outcome_up(forecast: Forecast, actual: list[Candle]) -> int:
    return int(actual[forecast.horizon_bars - 1].close > forecast.ref_close)


def brier_baseline(forecast: Forecast, actual: list[Candle]) -> float | None:
    """(base_rate - y)^2: the score of always predicting the base rate."""
    if forecast.base_rate is None:
        return None
    return (forecast.base_rate - outcome_up(forecast, actual)) ** 2


def grade(forecast: Forecast, actual: list[Candle], atr: float) -> Grade:
    """`actual` holds the bars at the forecast's target times (at least `horizon_bars`), in order."""
    if not (atr > 0 and math.isfinite(atr)):
        raise ValueError("grading needs a positive ATR at the reference bar")
    steps = [
        grade_step(i + 1, pred, act, atr, forecast.bands[i] if i < len(forecast.bands) else None)
        for i, (pred, act) in enumerate(zip(forecast.ghost_candles, actual, strict=False))
    ]
    close_h = actual[forecast.horizon_bars - 1].close
    y = outcome_up(forecast, actual)
    p, base = forecast.p_up, forecast.base_rate
    direction_hit = None
    if not forecast.abstain and p is not None and base is not None and p != base:
        direction_hit = _sign(close_h - forecast.ref_close) == _sign(p - base)
    brier = (p - y) ** 2 if p is not None else None
    return Grade(direction_hit=direction_hit, brier=brier, match_score=match_score(steps), steps=steps)
