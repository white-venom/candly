"""Claude API integration (PLAN.md §8): news tagging, forecast explanations and briefs.

Claude explains and tags; it never produces probabilities or prices. Everything is off without
ANTHROPIC_API_KEY, and no function here raises to its caller.
"""

from candly.llm.briefs import write_brief
from candly.llm.client import enabled
from candly.llm.explain import explain_forecast, explain_recent_calls, get_explanation
from candly.llm.news_tagger import tag_pending_news

__all__ = [
    "enabled",
    "explain_forecast",
    "explain_recent_calls",
    "get_explanation",
    "tag_pending_news",
    "write_brief",
]
