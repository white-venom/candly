"""config/llm.yaml, validated."""

from typing import Literal

import yaml
from pydantic import BaseModel

from candly.core.settings import get_settings

Feature = Literal["news_tagging", "explanations", "briefs"]


class Price(BaseModel):
    input: float
    output: float


class LLMConfig(BaseModel):
    enabled: bool = True
    features: dict[Feature, bool]
    models: dict[Feature, str]
    prices_usd_per_mtok: dict[str, Price]
    cache_write_multiplier: float = 1.25
    cache_read_multiplier: float = 0.10
    daily_budget_usd: float = 3.0
    max_explanations_per_day: int = 40
    batch_size: int = 20
    tag_max_age_hours: float = 24
    tag_max_attempts: int = 3
    max_tokens: dict[Feature, int]
    effort: dict[Feature, Literal["low", "medium", "high", "xhigh", "max"]] = {}
    refusal_fallbacks: dict[Feature, Literal["default"]] = {}
    max_retries: int = 3
    timeout_seconds: float = 120


def load_llm_config() -> LLMConfig:
    path = get_settings().config_dir / "llm.yaml"
    return LLMConfig(**yaml.safe_load(path.read_text(encoding="utf-8")))
