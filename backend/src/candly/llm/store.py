"""Claude call log, explanation cache and news-tag provenance in SQLite (WAL) at data/db/llm.sqlite.

Parameterized SQL only. `day` columns are IST dates: the daily budget and the explanation cap reset at
IST midnight.
"""

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from candly.core.settings import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    day TEXT NOT NULL,
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    served_by TEXT,
    status TEXT NOT NULL,
    system_sha TEXT,
    prompt TEXT NOT NULL,
    response TEXT,
    error TEXT,
    stop_reason TEXT,
    request_id TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS calls_by_day ON calls (day, purpose);
CREATE TABLE IF NOT EXISTS system_prompts (
    sha TEXT PRIMARY KEY,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS explanations (
    instrument TEXT NOT NULL,
    tf TEXT NOT NULL,
    ref_time INTEGER NOT NULL,
    method TEXT NOT NULL,
    text TEXT NOT NULL,
    call_id INTEGER REFERENCES calls (id),
    created_at INTEGER NOT NULL,
    PRIMARY KEY (instrument, tf, ref_time, method)
);
CREATE TABLE IF NOT EXISTS news_tags (
    news_id TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL DEFAULT 0,
    tagged_at INTEGER,
    call_id INTEGER REFERENCES calls (id),
    instruments TEXT,
    event_type TEXT,
    sentiment REAL,
    magnitude INTEGER,
    summary TEXT,
    prev_sentiment REAL,
    prev_method TEXT,
    prev_event_type TEXT,
    prev_summary TEXT
);
"""


def default_llm_path() -> Path:
    return get_settings().db_dir / "llm.sqlite"


class LLMStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_llm_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def log_call(
        self,
        *,
        ts: int,
        day: str,
        purpose: str,
        model: str,
        status: str,
        system: str,
        prompt: str,
        served_by: str | None = None,
        response: str | None = None,
        error: str | None = None,
        stop_reason: str | None = None,
        request_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> int:
        sha = hashlib.sha256(system.encode()).hexdigest()
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO system_prompts (sha, text) VALUES (?, ?)", (sha, system))
            cur = conn.execute(
                "INSERT INTO calls (ts, day, purpose, model, served_by, status, system_sha, prompt, response,"
                " error, stop_reason, request_id, input_tokens, output_tokens, cache_write_tokens,"
                " cache_read_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts, day, purpose, model, served_by, status, sha, prompt, response, error, stop_reason,
                    request_id, input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, cost_usd,
                ),
            )
            return int(cur.lastrowid)

    def set_status(self, call_id: int, status: str, error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE calls SET status = ?, error = ? WHERE id = ?", (status, error, call_id))

    def spent_on(self, day: str) -> float:
        with self.connect() as conn:
            row = conn.execute("SELECT SUM(cost_usd) FROM calls WHERE day = ?", (day,)).fetchone()
        return float(row[0] or 0.0)

    def calls_answered(self, day: str, purpose: str) -> int:
        """Calls Claude answered (and billed), whatever became of the answer."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM calls WHERE day = ? AND purpose = ?"
                " AND status NOT IN ('budget_reached', 'error')",
                (day, purpose),
            ).fetchone()
        return int(row[0])

    def explanation(self, instrument: str, tf: str, ref_time: int, method: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT text FROM explanations"
                " WHERE instrument = ? AND tf = ? AND ref_time = ? AND method = ?",
                (instrument, tf, int(ref_time), method),
            ).fetchone()
        return row["text"] if row else None

    def save_explanation(
        self,
        instrument: str,
        tf: str,
        ref_time: int,
        method: str,
        *,
        text: str,
        call_id: int,
        created_at: int,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO explanations"
                " (instrument, tf, ref_time, method, text, call_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (instrument, tf, int(ref_time), method, text, call_id, created_at),
            )
