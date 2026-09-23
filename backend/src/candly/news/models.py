import hashlib
import re
from datetime import datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field, field_validator


class NewsItem(BaseModel):
    """One headline. `published_at` is the publisher's claim; `fetched_at` is when we first had it,
    and it is the only time a backtest may use."""

    id: str
    title: str
    url: str
    source: str
    published_at: datetime | None
    fetched_at: datetime
    instruments: list[str] = Field(default_factory=list)
    sentiment: float | None = None
    sentiment_method: Literal["lexicon", "claude"] | None = None
    event_type: str | None = None
    summary: str | None = None
    feed: str | None = None

    @field_validator("published_at", "fetched_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("news timestamps must be timezone-aware")
        return value

    def to_api(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": int(self.published_at.timestamp()) if self.published_at else None,
            "fetched_at": int(self.fetched_at.timestamp()),
            "instruments": self.instruments,
            "sentiment": self.sentiment,
            "sentiment_method": self.sentiment_method,
            "event_type": self.event_type,
            "summary": self.summary,
        }


def normalize_title(title: str) -> str:
    return re.sub(r"[^\w]+", " ", title.lower()).strip()


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), "")
    )


def news_id(title: str, url: str) -> str:
    key = f"{normalize_title(title)}|{normalize_url(url)}"
    return hashlib.sha1(key.encode()).hexdigest()[:20]
