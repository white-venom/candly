"""One polling cycle over config/news_feeds.yaml: conditional GET, parse, map, score, store."""

import email.utils
import html
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote_plus

import feedparser
import httpx
import yaml

from candly.core.calendar import IST
from candly.core.instruments import load_watchlist
from candly.core.settings import get_settings
from candly.data import clock
from candly.news import sentiment
from candly.news.map import InstrumentMapper
from candly.news.models import NewsItem, news_id
from candly.news.store import NewsStore

log = logging.getLogger(__name__)

TIMEOUT = 20.0
GOOGLE_GAP_SECONDS = 1.0  # be polite: Google News queries go out one per second
POLL_GRACE_SECONDS = 30  # scheduler jitter must not skip a cycle
SUMMARY_CHARS = 500
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}
_NSE_TIME = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{1,2}):(\d{2})(?::(\d{2}))?$")
_TAGS = re.compile(r"<[^>]+>")

_sleep = time.sleep


@dataclass(frozen=True)
class FeedSpec:
    key: str
    name: str
    url: str
    poll_seconds: int
    instrument_id: str | None = None


def load_feed_config() -> dict:
    path = get_settings().config_dir / "news_feeds.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def feed_specs(config: dict, instruments) -> list[FeedSpec]:
    base = int(config.get("poll_minutes", 5)) * 60
    specs = [FeedSpec(f["id"], f["name"], f["url"], base) for f in config.get("feeds") or []]
    google = (config.get("per_instrument") or {}).get("google_news")
    if google:
        for inst in instruments:
            if inst.kind in google.get("kinds", []):
                query = google["query_template"].format(name=inst.name)
                specs.append(
                    FeedSpec(
                        key=f"google_news:{inst.id}",
                        name="Google News",
                        url=google["url_template"].format(query=quote_plus(query)),
                        poll_seconds=int(google.get("poll_minutes", 30)) * 60,
                        instrument_id=inst.id,
                    )
                )
    return specs


def _parse_rfc822(text: str) -> datetime | None:
    try:
        return email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None


def _parse_iso(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_published(raw: str | None, parsed: time.struct_time | None = None) -> datetime | None:
    """Publisher time in UTC. Times without a zone are IST (Indian feeds), e.g. NSE's
    "23-Sep-2026 10:45:40"."""
    text = (raw or "").strip()
    match = _NSE_TIME.match(text)
    if match and match.group(2).lower() in _MONTHS:
        day, month, year, hour, minute, second = match.groups()
        local = datetime(
            int(year), _MONTHS[month.lower()], int(day), int(hour), int(minute), int(second or 0)
        )
        return local.replace(tzinfo=IST).astimezone(UTC)
    if text:
        value = _parse_rfc822(text) or _parse_iso(text)
        if value is not None:
            return (value if value.tzinfo else value.replace(tzinfo=IST)).astimezone(UTC)
    if parsed:
        return datetime(*parsed[:6], tzinfo=UTC)
    return None


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", value or ""))).strip()


def to_item(entry, spec: FeedSpec, fetched_at: datetime, mapper: InstrumentMapper) -> NewsItem | None:
    title = _clean_text(entry.get("title"))
    link = (entry.get("link") or "").strip()
    if not title or not link:
        return None
    summary = _clean_text(entry.get("summary"))[:SUMMARY_CHARS] or None
    source = spec.name
    if spec.instrument_id:
        publisher = (entry.get("source") or {}).get("title")
        if publisher:
            source = f"Google News / {publisher}"
            title = title.removesuffix(f" - {publisher}").strip()
        summary = None  # Google's summary only repeats the headline and publisher
    text = f"{title}. {summary}" if summary else title
    instruments = mapper.map(text)
    if spec.instrument_id and not instruments:
        # Search results are often about a namesake ("Reliance Power" for Reliance Industries); an item
        # the mapper can't tie to any instrument is noise, not general market news.
        return None
    return NewsItem(
        id=news_id(title, link),
        title=title,
        url=link,
        source=source,
        # dict.get skips feedparser's deprecated updated->published key remapping
        published_at=parse_published(
            dict.get(entry, "published") or dict.get(entry, "updated"),
            dict.get(entry, "published_parsed") or dict.get(entry, "updated_parsed"),
        ),
        fetched_at=fetched_at,
        instruments=instruments,
        sentiment=sentiment.lexicon_sentiment(text),
        sentiment_method=sentiment.METHOD,
        event_type=sentiment.event_type(text),
        summary=summary,
        feed=spec.key,
    )


def _poll_feed(client: httpx.Client, spec: FeedSpec, state: dict | None, mapper: InstrumentMapper):
    headers = {}
    if state and state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state and state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]
    response = client.get(spec.url, headers=headers)
    fetched_at = clock.utc_now().to_pydatetime()
    if response.status_code == 304:
        return [], response
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    items = [item for entry in parsed.entries if (item := to_item(entry, spec, fetched_at, mapper))]
    return items, response


def poll_news() -> int:
    """Poll every feed that is due. Returns the number of new items stored."""
    config = load_feed_config()
    instruments = load_watchlist()
    mapper = InstrumentMapper(instruments)
    store = NewsStore()
    total, polled_google = 0, False
    headers = {"User-Agent": config.get("user_agent", "candly")}
    with httpx.Client(headers=headers, timeout=TIMEOUT, follow_redirects=True) as client:
        for spec in feed_specs(config, instruments):
            now = clock.utc_now()
            state = store.feed_state(spec.key)
            if state and now.timestamp() - state["polled_at"] < spec.poll_seconds - POLL_GRACE_SECONDS:
                continue
            if spec.instrument_id:
                if polled_google:
                    _sleep(GOOGLE_GAP_SECONDS)
                polled_google = True
            try:
                items, response = _poll_feed(client, spec, state, mapper)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("news feed %s failed: %s", spec.key, exc)
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                store.save_feed_state(spec.key, now, status=status, error=str(exc)[:300])
                continue
            added = store.add(items)
            total += added
            store.save_feed_state(
                spec.key,
                now,
                status=response.status_code,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
            )
            log.debug("news feed %s: %d entries, %d new", spec.key, len(items), added)
    log.info("news poll: %d new items", total)
    return total
