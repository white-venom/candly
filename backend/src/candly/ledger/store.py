"""The prediction ledger: write-once forecasts in SQLite (WAL), graded as their target bars close."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pandas as pd

from candly.core.calendar import get_calendar
from candly.core.instruments import exchange_of
from candly.core.settings import get_settings
from candly.forecast.models import Candle, Forecast, ForecastContext
from candly.forecast.timing import future_bar_times, to_unix
from candly.indicators.functions import atr as atr_fn
from candly.ledger.accuracy import accuracy_from_frame
from candly.ledger.grading import grade
from candly.ledger.models import AccuracyResponse, Grade, LedgerEntry

VOID_AFTER = timedelta(days=7)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument TEXT NOT NULL,
    tf TEXT NOT NULL,
    method TEXT NOT NULL,
    made_at INTEGER NOT NULL,
    ref_time INTEGER NOT NULL,
    ref_close REAL NOT NULL,
    horizon_bars INTEGER NOT NULL,
    p_up REAL,
    base_rate REAL,
    abstain INTEGER NOT NULL,
    payload TEXT NOT NULL,
    context TEXT,
    UNIQUE (instrument, tf, ref_time, method)
);
CREATE INDEX IF NOT EXISTS forecasts_by_series ON forecasts (instrument, tf, method, ref_time);
CREATE INDEX IF NOT EXISTS forecasts_by_made_at ON forecasts (made_at);
CREATE TRIGGER IF NOT EXISTS forecasts_no_update BEFORE UPDATE ON forecasts
BEGIN SELECT RAISE(ABORT, 'ledger forecasts are write-once'); END;
CREATE TRIGGER IF NOT EXISTS forecasts_no_delete BEFORE DELETE ON forecasts
BEGIN SELECT RAISE(ABORT, 'ledger forecasts are write-once'); END;

CREATE TABLE IF NOT EXISTS outcomes (
    forecast_id INTEGER PRIMARY KEY REFERENCES forecasts (id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'graded', 'void')),
    actual TEXT NOT NULL DEFAULT '[]',
    grade TEXT,
    direction_hit INTEGER,
    brier REAL,
    brier_baseline REAL,
    match_score REAL,
    atr REAL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS outcomes_by_status ON outcomes (status);
CREATE TRIGGER IF NOT EXISTS outcomes_final BEFORE UPDATE ON outcomes
WHEN OLD.status != 'pending'
BEGIN SELECT RAISE(ABORT, 'graded or void outcomes are final'); END;
"""


class DuplicateForecast(ValueError):
    pass


class LateForecast(ValueError):
    pass


def default_ledger_path() -> Path:
    return get_settings().db_dir / "ledger.sqlite"


class Ledger:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_ledger_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    def record(self, forecast: Forecast, now: pd.Timestamp | None = None) -> int:
        """Store a forecast before its outcome exists. Rejects duplicates, and forecasts recorded
        (at `now`, default the wall clock) after their first target bar has already closed."""
        now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        exchange = exchange_of(forecast.instrument)
        ref_ts = pd.Timestamp(forecast.ref_time, unit="s", tz="UTC")
        first = future_bar_times(exchange, forecast.tf, ref_ts, 1)
        if first:
            first_close = get_calendar().bar_close_time(exchange, first[0], forecast.tf)
            if max(now, pd.Timestamp(forecast.made_at, unit="s", tz="UTC")) >= first_close:
                raise LateForecast(
                    f"{forecast.instrument} {forecast.tf}: first target bar closed at {first_close}; "
                    "too late to record"
                )
        context = forecast.context.model_dump_json() if forecast.context is not None else None
        made = to_unix(now)
        try:
            with self._connect() as con:
                cur = con.execute(
                    """INSERT INTO forecasts (instrument, tf, method, made_at, ref_time, ref_close,
                       horizon_bars, p_up, base_rate, abstain, payload, context)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        forecast.instrument,
                        forecast.tf,
                        forecast.method,
                        forecast.made_at,
                        forecast.ref_time,
                        forecast.ref_close,
                        forecast.horizon_bars,
                        forecast.p_up,
                        forecast.base_rate,
                        int(forecast.abstain),
                        forecast.model_dump_json(),
                        context,
                    ),
                )
                forecast_id = int(cur.lastrowid)
                con.execute(
                    "INSERT INTO outcomes (forecast_id, status, updated_at) VALUES (?, 'pending', ?)",
                    (forecast_id, made),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateForecast(
                f"{forecast.instrument} {forecast.tf} {forecast.method} "
                f"ref_time={forecast.ref_time} already recorded"
            ) from exc
        return forecast_id

    def _rows(self, where: str = "", params: tuple = ()) -> list[sqlite3.Row]:
        with self._connect() as con:
            return con.execute(
                f"""SELECT f.*, o.status, o.actual, o.grade, o.direction_hit, o.brier, o.brier_baseline,
                    o.match_score, o.atr AS graded_atr
                    FROM forecasts f JOIN outcomes o ON o.forecast_id = f.id {where}""",
                params,
            ).fetchall()

    def grade_pending(
        self, load_candles: Callable[..., pd.DataFrame], now: pd.Timestamp | None = None
    ) -> int:
        """Fill in actual candles for pending forecasts; grade those whose target bars have all
        closed, void those whose data is still missing a week after it was due. Returns how many
        forecasts were graded or voided."""
        now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        pending = self._rows("WHERE o.status = 'pending'")
        cache: dict[tuple[str, str], pd.DataFrame | None] = {}
        finished = 0
        for row in pending:
            key = (row["instrument"], row["tf"])
            if key not in cache:
                try:
                    cache[key] = load_candles(row["instrument"], row["tf"])
                except Exception:
                    cache[key] = None
            finished += self._grade_one(row, cache[key], now)
        return finished

    def _grade_one(self, row: sqlite3.Row, candles: pd.DataFrame | None, now: pd.Timestamp) -> int:
        forecast = Forecast.model_validate_json(row["payload"])
        ref_ts = pd.Timestamp(forecast.ref_time, unit="s", tz="UTC")
        n_steps = max(forecast.horizon_bars, len(forecast.ghost_candles))
        after = pd.DataFrame()
        if candles is not None and not candles.empty:
            after = candles[candles["ts"] > ref_ts].iloc[:n_steps]
        actual = [
            Candle(time=to_unix(r.ts), open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume)
            for r in after.itertuples()
        ]
        actual_json = json.dumps([c.model_dump() for c in actual])
        stamp = to_unix(now)
        if len(actual) >= n_steps:
            atr = self._atr(row, candles, ref_ts)
            if atr is None:
                return self._set(row["id"], "void", actual_json, stamp)
            g: Grade = grade(forecast, actual, atr)
            y = int(actual[forecast.horizon_bars - 1].close > forecast.ref_close)
            baseline = (forecast.base_rate - y) ** 2 if forecast.base_rate is not None else None
            with self._connect() as con:
                con.execute(
                    """UPDATE outcomes SET status = 'graded', actual = ?, grade = ?, direction_hit = ?,
                       brier = ?, brier_baseline = ?, match_score = ?, atr = ?, updated_at = ?
                       WHERE forecast_id = ?""",
                    (
                        actual_json,
                        g.model_dump_json(),
                        None if g.direction_hit is None else int(g.direction_hit),
                        g.brier,
                        baseline,
                        g.match_score,
                        atr,
                        stamp,
                        row["id"],
                    ),
                )
            return 1
        if now > self._deadline(forecast, ref_ts, n_steps):
            return self._set(row["id"], "void", actual_json, stamp)
        if actual_json != row["actual"]:
            self._set(row["id"], "pending", actual_json, stamp)
        return 0

    def _set(self, forecast_id: int, status: str, actual_json: str, stamp: int) -> int:
        with self._connect() as con:
            con.execute(
                "UPDATE outcomes SET status = ?, actual = ?, updated_at = ? WHERE forecast_id = ?",
                (status, actual_json, stamp, forecast_id),
            )
        return int(status != "pending")

    @staticmethod
    def _atr(row: sqlite3.Row, candles: pd.DataFrame, ref_ts: pd.Timestamp) -> float | None:
        if row["context"]:
            atr = ForecastContext.model_validate_json(row["context"]).atr
            if atr is not None and atr > 0:
                return float(atr)
        history = candles[candles["ts"] <= ref_ts]
        if len(history) < 15:
            return None
        value = float(atr_fn(history, 14).iloc[-1])
        return value if value > 0 else None

    @staticmethod
    def _deadline(forecast: Forecast, ref_ts: pd.Timestamp, n_steps: int) -> pd.Timestamp:
        exchange = exchange_of(forecast.instrument)
        times = future_bar_times(exchange, forecast.tf, ref_ts, n_steps)
        last = times[-1] if times else ref_ts
        return get_calendar().bar_close_time(exchange, last, forecast.tf) + VOID_AFTER

    def entries(
        self,
        instrument: str | None = None,
        tf: str | None = None,
        status: str | None = None,
        method: str | None = None,
        limit: int = 100,
    ) -> list[LedgerEntry]:
        clauses, params = [], []
        for column, value in (
            ("f.instrument", instrument),
            ("f.tf", tf),
            ("o.status", status),
            ("f.method", method),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._rows(f"{where} ORDER BY f.ref_time DESC, f.id DESC LIMIT ?", (*params, int(limit)))
        return [self._entry(r) for r in rows]

    @staticmethod
    def _entry(row: sqlite3.Row) -> LedgerEntry:
        forecast = Forecast.model_validate_json(row["payload"])
        return LedgerEntry(
            id=row["id"],
            instrument=forecast.instrument,
            tf=forecast.tf,
            method=forecast.method,
            made_at=forecast.made_at,
            ref_time=forecast.ref_time,
            ref_close=forecast.ref_close,
            horizon_bars=forecast.horizon_bars,
            p_up=forecast.p_up,
            abstain=forecast.abstain,
            predicted=forecast.ghost_candles,
            bands=forecast.bands,
            actual=[Candle(**c) for c in json.loads(row["actual"])],
            status=row["status"],
            grade=Grade.model_validate_json(row["grade"]) if row["grade"] else None,
        )

    def forecast(self, forecast_id: int) -> Forecast | None:
        rows = self._rows("WHERE f.id = ?", (forecast_id,))
        return Forecast.model_validate_json(rows[0]["payload"]) if rows else None

    def frame(
        self,
        instrument: str | None = None,
        tf: str | None = None,
        method: str | None = None,
        since: int | None = None,
    ) -> pd.DataFrame:
        clauses, params = [], []
        for column, value in (("f.instrument", instrument), ("f.tf", tf), ("f.method", method)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if since is not None:
            clauses.append("f.made_at >= ?")
            params.append(since)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._rows(where, tuple(params))
        return pd.DataFrame([dict(r) for r in rows])

    def accuracy(
        self,
        instrument: str | None = None,
        tf: str | None = None,
        days: int = 90,
        method: str | None = "analog_v1",
        now: pd.Timestamp | None = None,
    ) -> AccuracyResponse:
        now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        since = to_unix(now - pd.Timedelta(days=days))
        return accuracy_from_frame(self.frame(instrument, tf, method, since))
