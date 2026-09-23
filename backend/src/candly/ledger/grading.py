"""Grading of one forecast against the candles that actually closed (CONTRACTS.md, grading definitions)."""

from __future__ import annotations

import math

from candly.forecast.models import Candle, Forecast
from candly.ledger.models import Grade, StepGrade


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


def grade_step(step: int, pred: Candle, act: Candle, atr: float, band=None) -> StepGrade:
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


def grade(forecast: Forecast, actual: list[Candle], atr: float) -> Grade:
    """`actual` holds at least `horizon_bars` closed bars after the reference bar, in order."""
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
