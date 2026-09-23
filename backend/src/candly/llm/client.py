"""The single door to the Claude API: a lazy client, the daily budget, typed error handling, and a row in
data/db/llm.sqlite (prompt, response, tokens, cost) for every call.

Nothing here raises to the caller: a failed or skipped call returns None, so a job that uses Claude keeps
running without it.
"""

import functools
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import anthropic
import pandas as pd

from candly.core.calendar import IST
from candly.core.settings import get_settings
from candly.data import clock
from candly.llm.config import Feature, LLMConfig, Price, load_llm_config
from candly.llm.store import LLMStore

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"  # pairs with fallbacks="default"
ERROR_CHARS = 500

_lock = threading.Lock()
_client: anthropic.Anthropic | None = None
_budget_noted_day: str | None = None

T = TypeVar("T")

# Retryable failures the SDK already retried with backoff (max_retries); they may clear on the next run.
_TRANSIENT = (
    anthropic.RateLimitError,
    anthropic.OverloadedError,
    anthropic.ServiceUnavailableError,
    anthropic.DeadlineExceededError,
    anthropic.InternalServerError,
    anthropic.APIConnectionError,
)
# A key, model or request problem: repeats on every call until someone fixes it.
_PERMANENT = (
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.NotFoundError,
    anthropic.BadRequestError,
    anthropic.RequestTooLargeError,
    anthropic.UnprocessableEntityError,
)


@dataclass(frozen=True)
class Completion:
    call_id: int
    status: str  # "ok", "refusal" or "max_tokens"
    text: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def enabled(feature: Feature | None = None) -> bool:
    """False without ANTHROPIC_API_KEY, or when config/llm.yaml switches Claude (or `feature`) off."""
    if not get_settings().has_anthropic:
        return False
    cfg = load_llm_config()
    return cfg.enabled and (feature is None or cfg.features.get(feature, False))


def budget_day(now: pd.Timestamp | None = None) -> str:
    """The IST date the daily budget and caps count against."""
    return (now if now is not None else clock.utc_now()).tz_convert(IST).date().isoformat()


def never_raises(default: T) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """For the public entry points that jobs and routes call: log any failure and return `default`."""

    def wrap(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def inner(*args, **kwargs) -> T:
            try:
                return fn(*args, **kwargs)
            except Exception:
                log.exception("%s failed; carrying on without Claude", fn.__qualname__)
                return default

        return inner

    return wrap


def _anthropic(cfg: LLMConfig) -> anthropic.Anthropic:
    global _client
    with _lock:
        if _client is None:
            _client = anthropic.Anthropic(
                api_key=get_settings().anthropic_api_key.get_secret_value(),
                max_retries=cfg.max_retries,
                timeout=cfg.timeout_seconds,
            )
        return _client


def _price(model: str, cfg: LLMConfig) -> Price:
    """Exact id, else the longest configured id it starts with (dated snapshots), else the dearest."""
    table = cfg.prices_usd_per_mtok
    if model in table:
        return table[model]
    prefixes = [m for m in table if model.startswith(m)]
    if prefixes:
        return table[max(prefixes, key=len)]
    log.warning("no price for model %s in config/llm.yaml; costing it at the dearest listed", model)
    return max(table.values(), key=lambda p: p.output)


def usage_cost(usage: Any, requested: str, served_by: str, cfg: LLMConfig) -> tuple[dict[str, int], float]:
    """Token totals and USD. With server-side fallbacks, usage.iterations lists every attempt at its own
    model's price; summing them all can count a declined attempt that was not billed, which errs on the
    safe side of the budget."""
    iterations = getattr(usage, "iterations", None)
    if iterations:
        parts = [(p, getattr(p, "model", None) or requested) for p in iterations]
    else:
        parts = [(usage, served_by)]
    tokens = dict.fromkeys(("input", "output", "cache_write", "cache_read"), 0)
    cost = 0.0
    for part, model in parts:
        price = _price(model, cfg)
        counts = {
            "input": part.input_tokens or 0,
            "output": part.output_tokens or 0,
            "cache_write": part.cache_creation_input_tokens or 0,
            "cache_read": part.cache_read_input_tokens or 0,
        }
        for key, value in counts.items():
            tokens[key] += value
        cost += (
            counts["input"] * price.input
            + counts["output"] * price.output
            + counts["cache_write"] * price.input * cfg.cache_write_multiplier
            + counts["cache_read"] * price.input * cfg.cache_read_multiplier
        ) / 1e6
    return tokens, cost


def _note_budget(day: str, spent: float, budget: float) -> None:
    global _budget_noted_day
    if _budget_noted_day != day:
        _budget_noted_day = day
        log.warning("Claude budget reached ($%.4f of $%.2f on %s IST); calls skipped", spent, budget, day)
    else:
        log.debug("Claude budget reached; call skipped")


def _request(cfg: LLMConfig, feature: Feature, system: str, prompt: str, json_schema: dict | None) -> dict:
    params: dict[str, Any] = {
        "model": cfg.models[feature],
        "max_tokens": cfg.max_tokens[feature],
        # The system prompt is the stable prefix; only the user turn changes between calls.
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": prompt}],
    }
    output_config: dict[str, Any] = {}
    if feature in cfg.effort:
        output_config["effort"] = cfg.effort[feature]
    if json_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": json_schema}
    if output_config:
        params["output_config"] = output_config
    return params


def complete(
    feature: Feature, system: str, prompt: str, json_schema: dict | None = None
) -> Completion | None:
    """One Messages API call for `feature`, logged with its cost. None when Claude is off, the day's budget
    is spent, or the API failed after the SDK's retries. A refusal or a max_tokens cut-off still costs
    money, so it comes back as a Completion whose status says so."""
    if not enabled(feature):
        return None
    cfg = load_llm_config()
    store = LLMStore()
    now = clock.utc_now()
    base = {
        "ts": int(now.timestamp()),
        "day": budget_day(now),
        "purpose": feature,
        "model": cfg.models[feature],
        "system": system,
        "prompt": prompt,
    }
    spent, budget = store.spent_on(base["day"]), cfg.daily_budget_usd
    if spent >= budget:
        store.log_call(**base, status="budget_reached", error=f"spent ${spent:.4f} of ${budget:.2f}")
        _note_budget(base["day"], spent, budget)
        return None

    params = _request(cfg, feature, system, prompt, json_schema)
    fallbacks = cfg.refusal_fallbacks.get(feature)
    try:
        client = _anthropic(cfg)
        if fallbacks:
            response = client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks=fallbacks)
        else:
            response = client.messages.create(**params)
    except _PERMANENT as exc:
        return _failed(store, base, exc, logging.ERROR)
    except _TRANSIENT as exc:
        return _failed(store, base, exc, logging.WARNING)
    except anthropic.APIError as exc:
        return _failed(store, base, exc, logging.ERROR)

    text = "".join(block.text for block in response.content if block.type == "text")
    tokens, cost = usage_cost(response.usage, params["model"], response.model, cfg)
    status = {"refusal": "refusal", "max_tokens": "max_tokens"}.get(response.stop_reason or "", "ok")
    call_id = store.log_call(
        **base,
        status=status,
        served_by=response.model,
        response=text,
        stop_reason=response.stop_reason,
        request_id=getattr(response, "_request_id", None),
        input_tokens=tokens["input"],
        output_tokens=tokens["output"],
        cache_write_tokens=tokens["cache_write"],
        cache_read_tokens=tokens["cache_read"],
        cost_usd=cost,
    )
    if status != "ok":
        log.warning("Claude %s call %d stopped with %s", feature, call_id, status)
    return Completion(call_id=call_id, status=status, text=text)


def _failed(store: LLMStore, base: dict, exc: anthropic.APIError, level: int) -> None:
    error = f"{type(exc).__name__}: {exc}"[:ERROR_CHARS]
    store.log_call(**base, status="error", error=error)
    log.log(level, "Claude %s call failed: %s", base["purpose"], error)
    return None
