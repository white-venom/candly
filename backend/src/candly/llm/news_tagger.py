"""Claude tagging for fresh news (PLAN.md §7-8): instruments, event type, sentiment, magnitude and a short
summary per headline, written back to data/db/news.sqlite with sentiment_method="claude".

Live use only. Claude knows how old stories turned out, so only news fetched in the last
`tag_max_age_hours` is tagged. Never backfill these tags onto old news for a backtest, and evaluate them
only on news published after the model's training cutoff (PLAN.md §8). What the lexicon wrote first
(sentiment, event type, the feed's summary) is kept in llm.sqlite `news_tags` as prev_*, together with
the magnitude, which news.sqlite has no column for.

SQL this module runs against news.sqlite, because NewsStore has no method for it:
- select: items Claude hasn't tagged, fetched since the cutoff, not yet given up on (llm news_tags.attempts)
- update: news.sentiment, sentiment_method, event_type and summary of a tagged item
- insert: news_instruments links for the instruments Claude named. They are added to the mapper's links;
  none are removed.
"""

import json
import logging
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

import anthropic
import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from candly.core.instruments import Instrument, load_watchlist
from candly.data import clock
from candly.llm.client import complete, enabled, never_raises
from candly.llm.config import load_llm_config
from candly.llm.store import LLMStore
from candly.news.store import NewsStore

log = logging.getLogger(__name__)

SUMMARY_WORDS = 25
EventType = Literal[
    "results", "rating_change", "order_win", "regulatory", "deal", "management", "macro", "other"
]
_EVENT_TYPES = """\
- results: quarterly or annual financial results, earnings previews and reviews
- rating_change: a broker or analyst rating or target-price change, or a credit rating action
- order_win: new orders, contracts, letters of award
- regulatory: action by a regulator, court, tribunal or tax authority (SEBI, CCI, NCLT, ED, tax or GST \
demands, bans, penalties, licences)
- deal: acquisitions, mergers, stake sales or purchases, block and bulk deals, joint ventures, fund raising, \
IPOs, QIPs, buybacks
- management: CEO, MD, CFO or board changes, resignations, appointments
- macro: economy-wide news: RBI policy and rates, inflation, GDP, fiscal policy, the rupee, commodity \
prices, global markets, FII and DII flows
- other: anything else"""


class NewsTag(BaseModel):
    id: str
    instruments: list[str] = Field(description="Watchlist ids the item is materially about; [] if none")
    event_type: EventType
    sentiment: float = Field(ge=-1, le=1, description="Likely price impact, -1 negative to 1 positive")
    magnitude: int = Field(ge=0, le=3, description="0 no price relevance, 1 minor, 2 notable, 3 major")
    summary: str = Field(description=f"What happened, in at most {SUMMARY_WORDS} words")


class NewsTags(BaseModel):
    items: list[NewsTag]


# The API can't enforce ranges or lengths; transform_schema moves them into descriptions, and _clean()
# clamps whatever comes back.
SCHEMA = anthropic.transform_schema(NewsTags.model_json_schema())


def system_prompt(instruments: list[Instrument]) -> str:
    """Stable across calls (watchlist order, no timestamps) so it can be cached."""
    lines = [
        f"- {i.id}: {i.name} ({i.kind})" + (f"; also called {', '.join(i.aliases)}" if i.aliases else "")
        for i in instruments
    ]
    watchlist = "\n".join(lines)
    return f"""\
You tag Indian market news for candly, a candlestick forecasting tool for NSE, BSE and MCX.

The user message is a JSON list of news items, each with an id, a title and sometimes a summary and a \
source. Return exactly one entry per item, with the item's id copied exactly.

Watchlist:
{watchlist}

Fields:
- instruments: the watchlist ids the item is materially about, using only the ids above; [] when it is \
about none of them. Tag an index only for news about the whole market or the sector that index tracks, \
not for one company's news. Tag a commodity future for news that moves that commodity's price. A \
namesake is not the instrument: Reliance Power is not Reliance Industries, and Tata Motors is not TCS.
- event_type, one of:
{_EVENT_TYPES}
- sentiment: the likely price impact on the tagged instruments (on Indian markets as a whole when \
instruments is empty), from -1 (clearly negative) through 0 (neutral or mixed) to 1 (clearly positive).
- magnitude: 0 no price relevance, 1 minor, 2 notable, 3 major (likely to move the price on its own).
- summary: one plain sentence of at most {SUMMARY_WORDS} words saying what happened. Use only facts in \
the item and add no numbers that are not in it.

Judge each item only from its own text, as of when it was published: do not use anything you know \
about what happened afterwards."""


def _prompt(batch: list[sqlite3.Row]) -> str:
    items = [
        {k: row[k] for k in ("id", "title", "summary", "source") if row[k]}
        for row in batch
    ]
    return f"Tag these {len(items)} news items.\n\n{json.dumps(items, ensure_ascii=False)}"


def _clean(raw: object, batch_ids: set[str], watchlist_ids: set[str]) -> NewsTag | None:
    """Repair what can be repaired (clamp ranges, drop unknown instruments, shorten the summary); reject
    the rest (an unknown id, a wrong type, a non-finite number)."""
    if not isinstance(raw, dict) or raw.get("id") not in batch_ids:
        return None
    item = dict(raw)
    try:
        sentiment, magnitude = float(item["sentiment"]), float(item["magnitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(sentiment) and math.isfinite(magnitude)):
        return None
    item["sentiment"] = min(1.0, max(-1.0, sentiment))
    item["magnitude"] = min(3, max(0, round(magnitude)))
    instruments = item.get("instruments")
    if not isinstance(instruments, list):
        return None
    named = dict.fromkeys(i for i in instruments if isinstance(i, str))
    item["instruments"] = [i for i in named if i in watchlist_ids]
    summary = item.get("summary")
    if isinstance(summary, str):
        words = summary.split()
        item["summary"] = " ".join(words[:SUMMARY_WORDS]) + ("…" if len(words) > SUMMARY_WORDS else "")
    try:
        return NewsTag.model_validate(item)
    except ValidationError:
        return None


def parse_tags(text: str, batch_ids: set[str], watchlist_ids: set[str]) -> dict[str, NewsTag]:
    """Valid tags by news id; the first entry wins when Claude repeats an id."""
    try:
        items = json.loads(text).get("items")
    except (ValueError, AttributeError):
        return {}
    tags: dict[str, NewsTag] = {}
    for raw in items if isinstance(items, list) else []:
        tag = _clean(raw, batch_ids, watchlist_ids)
        if tag is not None and tag.id not in tags:
            tags[tag.id] = tag
    return tags


def _attached(news_path: Path, llm_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(news_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS llm", (str(llm_path),))
    return conn


def pending_news(
    news_path: Path, llm_path: Path, since: int, max_attempts: int, limit: int
) -> list[sqlite3.Row]:
    """Newest first: fetched at or after `since`, not tagged by Claude, fewer than `max_attempts` tries."""
    with closing(_attached(news_path, llm_path)) as conn:
        return conn.execute(
            "SELECT n.id, n.title, n.summary, n.source, n.sentiment, n.sentiment_method, n.event_type"
            " FROM news n LEFT JOIN llm.news_tags t ON t.news_id = n.id"
            " WHERE (n.sentiment_method IS NULL OR n.sentiment_method != 'claude')"
            " AND n.fetched_at >= ? AND COALESCE(t.attempts, 0) < ?"
            " ORDER BY n.fetched_at DESC, n.id LIMIT ?",
            (since, max_attempts, int(limit)),
        ).fetchall()


def _write(
    news_path: Path,
    llm_path: Path,
    batch: list[sqlite3.Row],
    tags: dict[str, NewsTag],
    call_id: int,
    now: int,
) -> None:
    with closing(_attached(news_path, llm_path)) as conn, conn:
        for row in batch:
            conn.execute(
                "INSERT INTO llm.news_tags (news_id, attempts) VALUES (?, 1)"
                " ON CONFLICT (news_id) DO UPDATE SET attempts = attempts + 1",
                (row["id"],),
            )
            tag = tags.get(row["id"])
            if tag is None:
                continue
            conn.execute(
                "UPDATE llm.news_tags SET tagged_at = ?, call_id = ?, instruments = ?, event_type = ?,"
                " sentiment = ?, magnitude = ?, summary = ?, prev_sentiment = ?, prev_method = ?,"
                " prev_event_type = ?, prev_summary = ? WHERE news_id = ?",
                (
                    now, call_id, json.dumps(tag.instruments), tag.event_type, tag.sentiment, tag.magnitude,
                    tag.summary, row["sentiment"], row["sentiment_method"], row["event_type"], row["summary"],
                    tag.id,
                ),
            )
            conn.execute(
                "UPDATE news SET sentiment = ?, sentiment_method = 'claude', event_type = ?, summary = ?"
                " WHERE id = ?",
                (tag.sentiment, tag.event_type, tag.summary, tag.id),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO news_instruments (news_id, instrument_id) VALUES (?, ?)",
                [(tag.id, instrument_id) for instrument_id in tag.instruments],
            )


@never_raises(0)
def tag_pending_news(limit: int = 200) -> int:
    """Tag up to `limit` fresh untagged headlines in batches of `batch_size`. Returns how many were tagged.
    Stops early when Claude is off, the budget is spent or the API is failing; the rest wait for the next
    run. A headline that gets no valid tag in `tag_max_attempts` calls is left to the lexicon."""
    if not enabled("news_tagging"):
        return 0
    cfg = load_llm_config()
    watchlist = load_watchlist()
    system = system_prompt(watchlist)
    watchlist_ids = {i.id for i in watchlist}
    news_path, llm_path = NewsStore().path, LLMStore().path
    now = clock.utc_now()
    since = int((now - pd.Timedelta(hours=cfg.tag_max_age_hours)).timestamp())
    rows = pending_news(news_path, llm_path, since, cfg.tag_max_attempts, limit)
    tagged = 0
    for start in range(0, len(rows), cfg.batch_size):
        batch = rows[start : start + cfg.batch_size]
        completion = complete("news_tagging", system, _prompt(batch), json_schema=SCHEMA)
        if completion is None:
            break
        tags = parse_tags(completion.text, {r["id"] for r in batch}, watchlist_ids) if completion.ok else {}
        if len(tags) < len(batch):
            log.warning("tagging call %d: %d of %d items valid", completion.call_id, len(tags), len(batch))
        _write(news_path, llm_path, batch, tags, completion.call_id, int(now.timestamp()))
        tagged += len(tags)
    if rows:
        log.info("Claude tagged %d of %d pending news items", tagged, len(rows))
    return tagged
