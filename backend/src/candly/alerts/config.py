"""config/alerts.yaml. Read on every use, so edits apply without a restart; a missing file means defaults."""

from __future__ import annotations

from datetime import time
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from candly.core.settings import get_settings
from candly.core.timeframes import validate_tf

Confidence = Literal["low", "medium", "high"]
CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuietHours(_Strict):
    start: time = time(23, 45)
    end: time = time(8, 30)


class AlertTypes(_Strict):
    new_call: bool = True
    stop_hit: bool = True
    expiry_today: bool = True
    data_paused: bool = True
    pre_market_brief: bool = True
    post_market_review: bool = True


class BriefTimes(_Strict):
    pre_market: time = time(8, 45)
    post_market: time = time(16, 15)
    calendar: Literal["NSE", "BSE", "MCX"] = "NSE"


class AlertsConfig(_Strict):
    enabled: bool = True
    timeframes: tuple[str, ...] = ("1D", "1h")
    min_confidence: Confidence = "medium"
    quiet_hours: QuietHours = QuietHours()
    max_per_hour: int = 12
    stop_watch_bars: int = 3
    types: AlertTypes = AlertTypes()
    briefs: BriefTimes = BriefTimes()

    @field_validator("timeframes")
    @classmethod
    def _tfs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_tf(tf) for tf in value)

    @field_validator("max_per_hour", "stop_watch_bars")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be at least 1")
        return value


def load_alerts_config() -> AlertsConfig:
    path = get_settings().config_dir / "alerts.yaml"
    if not path.exists():
        return AlertsConfig()
    return AlertsConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
