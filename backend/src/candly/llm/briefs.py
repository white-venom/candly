"""Pre-market (08:45 IST) and post-market (16:15 IST) briefs: Claude turns a facts dict, built by the
alerts job from candly's own data, into a short readable note. Same rule as explanations: no number that
isn't in the facts, and no probability of Claude's own."""

import json
import logging
from typing import Literal

from candly.llm.client import complete, never_raises
from candly.llm.grounding import ungrounded_numbers
from candly.llm.store import LLMStore

log = logging.getLogger(__name__)

BriefKind = Literal["pre_market", "post_market"]

_TASKS: dict[str, str] = {
    "pre_market": (
        "Pre-market brief, read at 08:45 IST before NSE opens: the directional calls to watch today with "
        "their invalidation levels, relevant overnight news, expiries, and any data that is stale or missing."
    ),
    "post_market": (
        "Post-market review, read at 16:15 IST: how the day's calls did (predicted against actual), the "
        "accuracy figures given, notable moves and the news behind them."
    ),
}

SYSTEM = """\
You write short market briefs for candly, a tool that forecasts Indian markets (NSE, BSE, MCX), for an \
experienced trader who reads them on Telegram.

The user message names the brief and holds FACTS: a JSON object candly built from its own forecasts, \
graded results, levels and news. Turn it into a brief of at most 150 words: plain text, short lines or \
"- " bullets, no headings, tables, markdown or emojis. Lead with what matters most to a trader today.

Rules:
- Use only numbers that appear in FACTS. Copy them as given; you may round them, write a 0-1 probability \
as a percentage (0.563 as 56%) and add thousands separators. Do not calculate any new figure: no \
differences, distances, ratios, sums, averages or position sizes.
- Never give a probability, likelihood, price target or level of your own, and never add a call that is \
not in FACTS.
- Say only what a headline says. If FACTS are thin, empty or contradict each other, say so briefly.
- Times in FACTS are IST unless marked otherwise. Prices are in INR.
- Direct and factual: no hype, no disclaimers, no advice to buy or sell.
"""


@never_raises(None)
def write_brief(kind: BriefKind, facts: dict) -> str | None:
    """The brief text, or None when Claude is off, over budget, fails, or writes a number that isn't in
    `facts`. The caller should then fall back to a plain rendering of the facts."""
    if kind not in _TASKS:
        raise ValueError(f"unknown brief kind {kind!r}")
    prompt = f"{_TASKS[kind]}\n\nFACTS:\n{json.dumps(facts, ensure_ascii=False, indent=1, default=str)}"
    completion = complete("briefs", SYSTEM, prompt)
    if completion is None or not completion.ok:
        return None
    text = completion.text.strip()
    invented = ungrounded_numbers(text, {"task": _TASKS[kind], "facts": facts})
    if not text or invented:
        LLMStore().set_status(completion.call_id, "rejected", f"numbers not in the facts: {invented}")
        log.warning("%s brief discarded: numbers not in the facts %s", kind, invented)
        return None
    return text
