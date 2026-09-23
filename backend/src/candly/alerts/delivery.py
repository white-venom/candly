"""Alert bookkeeping in SQLite (data/db/alerts.sqlite) and the one path every message goes out through.

An event is claimed (INSERT OR IGNORE on its key) before it is sent, so two jobs can't both send it,
and the claim is dropped again if Telegram fails, so the next check retries. Parameterized SQL only.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import pandas as pd

from candly.alerts import telegram
from candly.alerts.config import AlertsConfig
from candly.core.calendar import IST
from candly.core.settings import get_settings
from candly.data import clock

Outcome = Literal["sent", "duplicate", "quiet", "capped", "failed"]
BRIEF_KINDS = ("pre_market_brief", "post_market_review")  # scheduled; not counted against max_per_hour

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sent (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    sent_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sent_by_time ON sent (sent_at);
CREATE TABLE IF NOT EXISTS watch (
    forecast_id INTEGER PRIMARY KEY,
    instrument TEXT NOT NULL,
    tf TEXT NOT NULL,
    bullish INTEGER NOT NULL,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    ref_time INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def default_alerts_path() -> Path:
    return get_settings().db_dir / "alerts.sqlite"


class AlertStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_alerts_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def seen(self, key: str) -> bool:
        with self._connect() as con:
            return con.execute("SELECT 1 FROM sent WHERE key = ?", (key,)).fetchone() is not None

    def claim(self, key: str, kind: str, now: pd.Timestamp) -> bool:
        with self._connect() as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO sent (key, kind, sent_at) VALUES (?, ?, ?)",
                (key, kind, clock.epoch_seconds(now)),
            )
            return cur.rowcount == 1

    def release(self, key: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM sent WHERE key = ?", (key,))

    def events_since(self, since: pd.Timestamp) -> int:
        placeholders = ",".join("?" * len(BRIEF_KINDS))
        with self._connect() as con:
            row = con.execute(
                f"SELECT COUNT(*) FROM sent WHERE sent_at > ? AND kind NOT IN ({placeholders})",
                (clock.epoch_seconds(since), *BRIEF_KINDS),
            ).fetchone()
        return int(row[0])

    def watch(
        self,
        forecast_id: int,
        instrument: str,
        tf: str,
        bullish: bool,
        entry: float,
        stop: float,
        ref_time: int,
    ) -> None:
        with self._connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO watch (forecast_id, instrument, tf, bullish, entry, stop, ref_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (forecast_id, instrument, tf, int(bullish), entry, stop, ref_time),
            )

    def watching(self) -> list[sqlite3.Row]:
        with self._connect() as con:
            return con.execute("SELECT * FROM watch ORDER BY ref_time, forecast_id").fetchall()

    def unwatch(self, forecast_id: int) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM watch WHERE forecast_id = ?", (forecast_id,))

    def get_state(self, key: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def set_state(self, key: str, value: dict) -> None:
        with self._connect() as con:
            con.execute(
                """INSERT INTO state (key, value) VALUES (?, ?)
                   ON CONFLICT (key) DO UPDATE SET value = excluded.value""",
                (key, json.dumps(value)),
            )

    def clear_state(self, key: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM state WHERE key = ?", (key,))


def is_quiet(cfg: AlertsConfig, now: pd.Timestamp) -> bool:
    t = now.tz_convert(IST).time()
    start, end = cfg.quiet_hours.start, cfg.quiet_hours.end
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def deliver(
    store: AlertStore,
    cfg: AlertsConfig,
    key: str,
    kind: str,
    text: str,
    now: pd.Timestamp,
    *,
    respect_quiet: bool = True,
    capped: bool = True,
) -> Outcome:
    """Send `text` once per `key`. Quiet hours and the hourly cap defer it (nothing is recorded, so a
    later check sends it if it still applies); a failed send is released for a retry."""
    if store.seen(key):
        return "duplicate"
    if respect_quiet and is_quiet(cfg, now):
        return "quiet"
    if capped and store.events_since(now - pd.Timedelta(hours=1)) >= cfg.max_per_hour:
        return "capped"
    if not store.claim(key, kind, now):
        return "duplicate"
    if telegram.send(text):
        return "sent"
    store.release(key)
    return "failed"
