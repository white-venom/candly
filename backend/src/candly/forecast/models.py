"""Forecast shapes (docs/CONTRACTS.md, "Forecast"). Times are UNIX seconds (UTC)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["bullish", "bearish", "neutral"]


class Candle(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class Band(BaseModel):
    time: int
    p10: float
    p50: float
    p90: float


class Driver(BaseModel):
    name: str
    effect: Direction
    detail: str


class Trade(BaseModel):
    """A directional call as a trade: enter at the reference close (filled at the next open), exit at
    the stop (the invalidation) or the target (p50 of the last step). reward_risk is the target's
    distance in the call's direction over the stop's distance, so it is negative when the median path
    ends against the call."""

    entry: float
    stop: float
    target: float
    reward_risk: float


class ForecastContext(BaseModel):
    """Conditions at the reference bar. Stored by the ledger for grouping; not part of the API shape."""

    patterns: list[str] = []
    trend: str | None = None
    vol_regime: str | None = None
    session_phase: str | None = None
    rel_volume: float | None = None
    atr: float | None = None


class Forecast(BaseModel):
    instrument: str
    tf: str
    method: str
    made_at: int
    ref_time: int
    ref_close: float
    horizon_bars: int
    p_up: float | None
    p_up_ci: tuple[float, float] | None
    base_rate: float | None
    abstain: bool
    abstain_reason: str | None
    confidence: Literal["low", "medium", "high"] | None
    expected_move_pct: float | None
    ghost_candles: list[Candle]
    bands: list[Band]
    invalidation: float | None
    trade: Trade | None = None  # non-null only for directional calls (abstain=false)
    drivers: list[Driver]
    n_analogs: int
    explanation: str | None = None
    context: ForecastContext | None = Field(default=None, exclude=True)
