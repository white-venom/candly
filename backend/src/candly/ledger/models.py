"""Ledger and accuracy shapes (docs/CONTRACTS.md, "LedgerEntry" and "AccuracyResponse")."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from candly.forecast.models import Band, Candle

Status = Literal["pending", "graded", "void"]
GroupBy = Literal["instrument", "tf", "pattern", "session_phase", "vol_regime"]


class StepGrade(BaseModel):
    step: int
    close_err_pct: float
    close_err_atr: float
    high_err_atr: float
    low_err_atr: float
    range_iou: float
    body_iou: float
    color_match: bool
    in_band_80: bool


class Grade(BaseModel):
    direction_hit: bool | None
    brier: float | None
    match_score: float
    steps: list[StepGrade]


class LedgerEntry(BaseModel):
    id: int
    instrument: str
    tf: str
    method: str
    made_at: int
    ref_time: int
    ref_close: float
    horizon_bars: int
    p_up: float | None
    abstain: bool
    predicted: list[Candle]
    bands: list[Band]
    actual: list[Candle]
    status: Status
    grade: Grade | None


class AccuracySummary(BaseModel):
    n_forecasts: int
    n_graded: int
    n_abstained: int
    direction_hit_rate: float | None
    brier: float | None
    brier_baseline: float | None
    skill: float | None
    ece: float | None
    band_coverage_80: float | None
    mean_match_score: float | None
    mean_close_err_atr: float | None


class CalibrationBin(BaseModel):
    bin_low: float
    bin_high: float
    mean_pred: float | None
    observed: float | None
    n: int


class RollingPoint(BaseModel):
    time: int
    hit_rate: float | None
    brier: float | None
    match_score: float | None


class GroupStat(BaseModel):
    group_by: GroupBy
    key: str
    n: int
    hit_rate: float | None
    brier: float | None


class AccuracyResponse(BaseModel):
    summary: AccuracySummary
    calibration: list[CalibrationBin]
    rolling: list[RollingPoint]
    by_group: list[GroupStat]
