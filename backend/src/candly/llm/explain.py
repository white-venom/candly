"""Plain-language explanations of directional calls (PLAN.md §8, Phase 4).

Claude only restates what candly computed: the forecast, its drivers and scorecard record, the trade, the
expiry and the latest headlines. It never adds a number or a probability of its own; an explanation with
a number the facts don't contain is discarded. Explanations are cached in data/db/llm.sqlite by
(instrument, tf, ref_time, method), the ledger's key, and abstaining forecasts are never explained.
"""

import json
import logging

import pandas as pd

from candly.core.calendar import IST
from candly.core.instruments import UnknownInstrument, get_instrument
from candly.data import clock
from candly.forecast.analog import METHOD
from candly.forecast.models import Forecast
from candly.ledger import Ledger, default_ledger_path
from candly.llm.client import budget_day, complete, enabled, never_raises
from candly.llm.config import load_llm_config
from candly.llm.grounding import ungrounded_numbers
from candly.llm.store import LLMStore, default_llm_path
from candly.news.store import NewsStore

log = logging.getLogger(__name__)

RECENT_DAYS = 3  # explain_recent_calls looks at forecasts made this recently
HEADLINES = 3

SYSTEM = """\
You write the "why" note for a directional candlestick forecast in candly, a tool that forecasts Indian \
markets (NSE, BSE, MCX) for an experienced trader.

The user message holds FACTS: a JSON object of values candly's statistical model has already computed. \
The model's probability is final. Your job is to explain the call, not to re-estimate it.

Write one paragraph of 3 to 5 plain sentences, with no headings, bullet points or markdown:
1. The call and why it was made: the direction and horizon, p_up against the base rate, and the drivers \
that matter (patterns with their scorecard record, the analog count, trend, volatility, nearby levels).
2. What invalidates it: the invalidation (stop) level in the instrument's price.
3. The main risk: for example a wide probability interval, low confidence, a reward/risk below 1, drivers \
that point the other way, a derivatives expiry inside the horizon, or a headline that cuts against the call.

Rules:
- Use only numbers that appear in FACTS. Copy them as given; you may round them, write a 0-1 probability \
as a percentage (0.563 as 56%) and add thousands separators. Do not calculate any new figure: no \
differences, distances, percentages of price, ratios, sums or position sizes.
- Never give a probability, likelihood, price target or level of your own. The only probabilities you may \
quote are p_up, its interval and the base rate.
- Mention a headline only if it bears on the call, and say only what the headline says.
- If the facts are thin or contradict each other, say so plainly rather than filling the gap.
- Times in FACTS are IST. Prices are in INR.
- Write for a trader who knows the jargon: direct, no hype, no disclaimers, no advice to buy or sell.
"""


def _r(value, digits: int):
    return None if value is None else round(float(value), digits)


def _ist(unix: int) -> str:
    return pd.Timestamp(int(unix), unit="s", tz="UTC").tz_convert(IST).strftime("%Y-%m-%d %H:%M")


def _name(instrument_id: str) -> str:
    try:
        return get_instrument(instrument_id).name
    except UnknownInstrument:
        return instrument_id


def facts_for(forecast: dict, context: dict | None = None) -> dict:
    """The only values Claude sees: candly's own numbers, rounded for reading."""
    context = context or {}
    p_up, base = forecast.get("p_up"), forecast.get("base_rate")
    trade = forecast.get("trade") or {}
    bands = forecast.get("bands") or []
    ghosts = forecast.get("ghost_candles") or []
    ci = forecast.get("p_up_ci")
    return {
        "instrument": forecast["instrument"],
        "name": _name(forecast["instrument"]),
        "timeframe": forecast["tf"],
        "method": forecast["method"],
        "reference_bar_open_ist": _ist(forecast["ref_time"]),
        "reference_close": _r(forecast.get("ref_close"), 2),
        "call": None if p_up is None or base is None else ("bullish" if p_up > base else "bearish"),
        "horizon_bars": forecast.get("horizon_bars"),
        "horizon_last_bar_open_ist": _ist(ghosts[-1]["time"]) if ghosts else None,
        "p_up": _r(p_up, 3),
        "p_up_interval": [_r(ci[0], 3), _r(ci[1], 3)] if ci else None,
        "base_rate": _r(base, 3),
        "confidence": forecast.get("confidence"),
        "expected_move_pct": _r(forecast.get("expected_move_pct"), 2),
        "n_analogs": forecast.get("n_analogs"),
        "invalidation": _r(forecast.get("invalidation"), 2),
        "trade": (
            {k: _r(trade.get(k), 2) for k in ("entry", "stop", "target", "reward_risk")} if trade else None
        ),
        "last_step_band": {k: _r(bands[-1][k], 2) for k in ("p10", "p50", "p90")} if bands else None,
        "drivers": [{k: d[k] for k in ("name", "effect", "detail")} for d in forecast.get("drivers") or []],
        "scorecard": context.get("scorecard"),
        "expiry": context.get("expiry"),
        "headlines": (context.get("headlines") or [])[:HEADLINES],
    }


def _key(forecast: dict) -> tuple[str, str, int, str]:
    return forecast["instrument"], forecast["tf"], int(forecast["ref_time"]), forecast["method"]


@never_raises(None)
def explain_forecast(forecast: dict, context: dict | None = None) -> str | None:
    """A 3-5 sentence explanation of a directional call, from the cache or from Claude.

    `forecast` is a Forecast as a dict (Forecast.model_dump()). `context` may carry "headlines" (the latest
    few, newest first), "expiry" and "scorecard". None for an abstaining forecast, when Claude is off, over
    the daily explanation cap or budget, or when Claude's text fails the no-invented-numbers check."""
    if forecast.get("abstain", True):
        return None
    key = _key(forecast)
    store = LLMStore()
    cached = store.explanation(*key)
    if cached is not None:
        return cached
    if not enabled("explanations"):
        return None
    cfg = load_llm_config()
    if store.calls_answered(budget_day(), "explanations") >= cfg.max_explanations_per_day:
        log.info("explanation cap of %d per day reached; %s not explained", cfg.max_explanations_per_day, key)
        return None
    facts = facts_for(forecast, context)
    prompt = "FACTS:\n" + json.dumps(facts, ensure_ascii=False, indent=1)
    completion = complete("explanations", SYSTEM, prompt)
    if completion is None or not completion.ok:
        return None
    text = " ".join(completion.text.split())
    invented = ungrounded_numbers(text, facts)
    if not text or invented:
        store.set_status(completion.call_id, "rejected", f"numbers not in the facts: {invented}")
        log.warning("explanation for %s discarded: numbers not in the facts %s", key, invented)
        return None
    now = int(clock.utc_now().timestamp())
    store.save_explanation(*key, text=text, call_id=completion.call_id, created_at=now)
    return text


@never_raises(None)
def get_explanation(instrument: str, tf: str, ref_time: int, method: str) -> str | None:
    """The cached explanation for a forecast, for Forecast.explanation. Never calls Claude."""
    path = default_llm_path()
    if not path.exists():
        return None
    return LLMStore(path).explanation(instrument, tf, ref_time, method)


def _headlines(instrument_id: str) -> list[dict]:
    items = NewsStore().query(instrument_id=instrument_id, limit=HEADLINES)
    return [
        {
            "title": n.title,
            "source": n.source,
            "time_ist": _ist(int((n.published_at or n.fetched_at).timestamp())),
        }
        for n in items
    ]


def _expiry(instrument_id: str, ref_time: int) -> dict | None:
    from candly.data.expiries import expiry_info

    day = pd.Timestamp(int(ref_time), unit="s", tz="UTC").tz_convert(IST).date()
    info = expiry_info(instrument_id, day)
    if info is None:
        return None
    return {
        "next_expiry": info.next_expiry.isoformat(),
        "kind": info.kind,
        "trading_days_to_expiry": info.days_to_expiry,
        "is_expiry_day": info.is_expiry_day,
    }


def context_for(forecast: dict) -> dict:
    """Headlines and expiry for a forecast; a part that fails is left out rather than blocking the rest."""
    context = {}
    for name, build in (
        ("headlines", lambda: _headlines(forecast["instrument"])),
        ("expiry", lambda: _expiry(forecast["instrument"], forecast["ref_time"])),
    ):
        try:
            context[name] = build()
        except Exception:
            log.exception("could not build %s for the %s explanation", name, forecast["instrument"])
    return context


@never_raises(0)
def explain_recent_calls(limit: int = 10, tf: str | None = None, method: str = METHOD) -> int:
    """Explain the newest directional calls in the ledger that are still pending (never abstentions).
    Returns how many new explanations were written. The scheduler runs it after each forecast cycle."""
    if not enabled("explanations") or not default_ledger_path().exists():
        return 0
    since = int((clock.utc_now() - pd.Timedelta(days=RECENT_DAYS)).timestamp())
    frame = Ledger().frame(tf=tf, method=method, since=since)
    if frame.empty:
        return 0
    calls = frame[(frame["abstain"] == 0) & (frame["status"] == "pending")]
    calls = calls.sort_values(["made_at", "id"], ascending=False).head(limit)
    store = LLMStore()
    written = 0
    for payload in calls["payload"]:
        forecast = Forecast.model_validate_json(payload).model_dump()
        if store.explanation(*_key(forecast)) is not None:
            continue
        if explain_forecast(forecast, context_for(forecast)) is not None:
            written += 1
    log.info("explained %d new calls", written)
    return written
