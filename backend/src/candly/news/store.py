"""News log in SQLite (WAL) at data/db/news.sqlite. Parameterized SQL only."""

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from candly.core.settings import get_settings
from candly.news.models import NewsItem

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    published_at INTEGER,
    fetched_at INTEGER NOT NULL,
    sentiment REAL,
    sentiment_method TEXT,
    event_type TEXT,
    summary TEXT,
    feed TEXT
);
CREATE INDEX IF NOT EXISTS news_fetched_at ON news (fetched_at);
CREATE TABLE IF NOT EXISTS news_instruments (
    news_id TEXT NOT NULL REFERENCES news (id),
    instrument_id TEXT NOT NULL,
    PRIMARY KEY (news_id, instrument_id)
);
CREATE INDEX IF NOT EXISTS news_instruments_by_instrument ON news_instruments (instrument_id);
CREATE TABLE IF NOT EXISTS feed_state (
    feed TEXT PRIMARY KEY,
    etag TEXT,
    last_modified TEXT,
    polled_at INTEGER NOT NULL,
    status INTEGER,
    error TEXT
);
"""
# An item can't be published after we fetched it, so clamp bad publisher clocks when sorting.
_EFFECTIVE_TIME = "MIN(COALESCE(n.published_at, n.fetched_at), n.fetched_at)"


def _epoch(value: datetime | pd.Timestamp | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return int(value.timestamp())


def _dt(epoch: int | None) -> datetime | None:
    return None if epoch is None else datetime.fromtimestamp(epoch, UTC)


class NewsStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else get_settings().db_dir / "news.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def add(self, items: Iterable[NewsItem]) -> int:
        """Insert new items (dedupe on id). Returns how many were new."""
        added = 0
        with self._connect() as conn:
            for item in items:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO news (id, title, url, source, published_at, fetched_at, sentiment,"
                    " sentiment_method, event_type, summary, feed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.id,
                        item.title,
                        item.url,
                        item.source,
                        _epoch(item.published_at),
                        _epoch(item.fetched_at),
                        item.sentiment,
                        item.sentiment_method,
                        item.event_type,
                        item.summary,
                        item.feed,
                    ),
                )
                if cursor.rowcount:
                    added += 1
                    conn.executemany(
                        "INSERT OR IGNORE INTO news_instruments (news_id, instrument_id) VALUES (?, ?)",
                        [(item.id, instrument_id) for instrument_id in item.instruments],
                    )
        return added

    def query(
        self,
        instrument_id: str | None = None,
        since: datetime | pd.Timestamp | None = None,
        limit: int = 50,
    ) -> list[NewsItem]:
        """Newest first by publish time (never later than fetch time). `since` filters on the same time."""
        sql = "SELECT n.* FROM news n"
        params: list = []
        if instrument_id is not None:
            sql += " JOIN news_instruments ni ON ni.news_id = n.id AND ni.instrument_id = ?"
            params.append(instrument_id)
        if since is not None:
            sql += f" WHERE {_EFFECTIVE_TIME} >= ?"
            params.append(_epoch(since))
        sql += f" ORDER BY {_EFFECTIVE_TIME} DESC, n.fetched_at DESC, n.id LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            ids = [row["id"] for row in rows]
            links: dict[str, list[str]] = {i: [] for i in ids}
            if ids:
                placeholders = ",".join("?" * len(ids))
                for link in conn.execute(
                    f"SELECT news_id, instrument_id FROM news_instruments WHERE news_id IN ({placeholders})"
                    " ORDER BY instrument_id",
                    ids,
                ):
                    links[link["news_id"]].append(link["instrument_id"])
        return [
            NewsItem(
                id=row["id"],
                title=row["title"],
                url=row["url"],
                source=row["source"],
                published_at=_dt(row["published_at"]),
                fetched_at=_dt(row["fetched_at"]),
                instruments=links[row["id"]],
                sentiment=row["sentiment"],
                sentiment_method=row["sentiment_method"],
                event_type=row["event_type"],
                summary=row["summary"],
                feed=row["feed"],
            )
            for row in rows
        ]

    def feed_state(self, feed: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM feed_state WHERE feed = ?", (feed,)).fetchone()
        return dict(row) if row else None

    def save_feed_state(
        self,
        feed: str,
        polled_at: datetime | pd.Timestamp,
        *,
        status: int | None,
        etag: str | None = None,
        last_modified: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO feed_state (feed, etag, last_modified, polled_at, status, error)"
                " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (feed) DO UPDATE SET"
                " etag = COALESCE(excluded.etag, feed_state.etag),"
                " last_modified = COALESCE(excluded.last_modified, feed_state.last_modified),"
                " polled_at = excluded.polled_at, status = excluded.status, error = excluded.error",
                (feed, etag, last_modified, _epoch(polled_at), status, error),
            )
