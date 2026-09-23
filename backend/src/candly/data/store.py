"""Parquet candle store: data/candles/{EXCHANGE}/{tf}/{SYMBOL}.parquet, one file per series.

Writers (the API's scheduler and the ingest CLI can run at the same time) hold a cross-process lock
on {SYMBOL}.parquet.lock for each read-modify-write. Readers need no lock: files are replaced atomically.
"""

import logging
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from candly.core.instruments import EXCHANGES, load_watchlist
from candly.core.schema import PRICE_COLUMNS, empty_candles, validate_candles
from candly.core.settings import get_settings
from candly.core.timeframes import validate_tf
from candly.data import clock
from candly.data.clean import clean_candles, closed_only

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

log = logging.getLogger(__name__)

_SOURCE_KEY = b"candly.source"
_VALUE_COLUMNS = [*PRICE_COLUMNS, "volume", "oi"]
LOCK_TIMEOUT = 30.0
_stats_cache: dict[Path, tuple[tuple[int, int], dict]] = {}
_sleep = time.sleep
T = TypeVar("T")


def candle_path(instrument_id: str, tf: str) -> Path:
    exchange, _, symbol = instrument_id.partition(":")
    if exchange not in EXCHANGES or not symbol:
        raise ValueError(f"instrument id {instrument_id!r} must look like EXCHANGE:SYMBOL")
    return get_settings().candles_dir / exchange / validate_tf(tf) / f"{symbol}.parquet"


def _try_lock(fd: int) -> None:
    """Take an exclusive lock on the open lock file, or raise OSError if another handle holds it."""
    if sys.platform == "win32":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def series_lock(path: Path, timeout: float = LOCK_TIMEOUT) -> Iterator[None]:
    """Exclusive lock on one series across processes and threads (each call opens its own handle)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try_lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"{path.name} stayed locked by another writer for {timeout:.0f}s"
                    ) from None
                _sleep(0.05)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def _quarantine(path: Path, seen: os.stat_result, error: Exception) -> None:
    """Move an unreadable series to data/quarantine/ so the next ingest re-fetches it from scratch."""
    try:
        now = path.stat()
    except FileNotFoundError:
        return
    if (now.st_mtime_ns, now.st_size) != (seen.st_mtime_ns, seen.st_size):
        return  # a writer replaced it since we read it
    target_dir = get_settings().data_dir / "quarantine"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = clock.utc_now().strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{path.parent.parent.name}_{path.parent.name}_{path.stem}.{stamp}.parquet"
    try:
        os.replace(path, target)
    except OSError as exc:
        log.error("%s is corrupt (%s) and could not be quarantined: %s", path, error, exc)
        return
    log.error("%s is corrupt (%s); moved to %s, the series now reads as empty", path, error, target)


def _read_arrow(path: Path, read: Callable[[Path], T]) -> T | None:
    """Run a pyarrow read. A missing file reads as None; so does a corrupt one, after quarantining it."""
    try:
        seen = path.stat()
        return read(path)
    except FileNotFoundError:
        return None
    except pa.ArrowException as exc:
        _quarantine(path, seen, exc)
        return None


def _read(path: Path) -> tuple[pd.DataFrame, set[str]]:
    table = _read_arrow(path, pq.read_table)
    if table is None:
        return empty_candles(), set()
    raw_sources = (table.schema.metadata or {}).get(_SOURCE_KEY, b"").decode()
    return validate_candles(table.to_pandas()), {s for s in raw_sources.split(",") if s}


def load_candles(
    instrument_id: str,
    tf: str,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Closed bars with start <= ts <= end (both optional, tz-aware), ascending."""
    df, _ = _read(candle_path(instrument_id, tf))
    if start is not None:
        df = df[df["ts"] >= clock.to_utc(start)]
    if end is not None:
        df = df[df["ts"] <= clock.to_utc(end)]
    df = closed_only(df, instrument_id.partition(":")[0], tf, clock.utc_now())
    return df.reset_index(drop=True)


def candle_source(instrument_id: str, tf: str) -> str | None:
    """Which source(s) wrote the series, e.g. "yahoo" or "fyers+yahoo"."""
    schema = _read_arrow(candle_path(instrument_id, tf), pq.read_schema)
    if schema is None:
        return None
    raw = (schema.metadata or {}).get(_SOURCE_KEY, b"").decode()
    return "+".join(sorted(s for s in raw.split(",") if s)) or None


def _count_changes(existing: pd.DataFrame, incoming: pd.DataFrame) -> int:
    if existing.empty:
        return len(incoming)
    merged = incoming.merge(existing, on="ts", how="left", suffixes=("", "_old"), indicator=True)
    added = merged["_merge"] == "left_only"
    same = np.ones(len(merged), dtype=bool)
    for column in _VALUE_COLUMNS:
        same &= np.isclose(merged[column], merged[f"{column}_old"], rtol=1e-10, atol=0.0, equal_nan=True)
    return int(added.sum() + ((~added) & ~same).sum())


def _replace(tmp: str, path: Path) -> None:
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # Windows: antivirus scanners and concurrent readers briefly lock the target file.
            if attempt == 4:
                raise
            _sleep(0.2 * (attempt + 1))


def _write_atomic(path: Path, df: pd.DataFrame, sources: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    metadata = dict(table.schema.metadata or {})
    metadata[_SOURCE_KEY] = ",".join(sorted(sources)).encode()
    table = table.replace_schema_metadata(metadata)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            pq.write_table(table, fh)
            fh.flush()
            os.fsync(fh.fileno())  # the data must be on disk before the rename makes it the series
        _replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_candles(instrument_id: str, tf: str, df: pd.DataFrame, source: str | None = None) -> int:
    """Validate, then upsert by ts (incoming rows win) with an atomic write.
    Returns the number of rows added or changed. Bars that are still forming are never stored."""
    path = candle_path(instrument_id, tf)
    exchange = instrument_id.partition(":")[0]
    incoming = validate_candles(df)
    closed = closed_only(incoming, exchange, tf, clock.utc_now())
    if len(closed) < len(incoming):
        log.warning("%s %s: refused %d forming bars", instrument_id, tf, len(incoming) - len(closed))
    if closed.empty:
        return 0
    with series_lock(path):
        existing, sources = _read(path)
        changed = _count_changes(existing, closed)
        if changed == 0 and (source is None or source in sources):
            return 0
        kept = existing[~existing["ts"].isin(closed["ts"])]
        merged = validate_candles(pd.concat([kept, closed], ignore_index=True).sort_values("ts"))
        if source:
            sources.add(source)
        _write_atomic(path, merged, sources)
    return changed


def series_stats(instrument_id: str, tf: str) -> dict:
    """{"bars": int, "first": Timestamp | None, "last": Timestamp | None} for one stored series."""
    path = candle_path(instrument_id, tf)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"bars": 0, "first": None, "last": None}
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _stats_cache.get(path)
    if cached and cached[0] == key:
        return dict(cached[1])
    table = _read_arrow(path, lambda p: pq.read_table(p, columns=["ts"]))
    if table is None:
        return {"bars": 0, "first": None, "last": None}
    ts = pd.to_datetime(table.column("ts").to_pandas(), utc=True)
    stats = {
        "bars": len(ts),
        "first": ts.min() if len(ts) else None,
        "last": ts.max() if len(ts) else None,
    }
    _stats_cache[path] = (key, stats)
    return dict(stats)


def data_summary() -> dict[str, dict[str, dict]]:
    """{instrument_id: {tf: {"bars", "first", "last"}}} for every watchlist instrument and timeframe."""
    return {inst.id: {tf: series_stats(inst.id, tf) for tf in inst.timeframes} for inst in load_watchlist()}


def clean_existing(tf: str, exchanges: tuple[str, ...] = ("NSE", "BSE")) -> dict[str, int]:
    """Re-run clean_candles over stored series, e.g. after a cleaning rule was added.
    Returns {instrument_id: bars dropped} for every stored series it checked."""
    tf = validate_tf(tf)
    dropped: dict[str, int] = {}
    for inst in load_watchlist():
        path = candle_path(inst.id, tf)
        if inst.exchange not in exchanges or tf not in inst.timeframes or not path.exists():
            continue
        with series_lock(path):
            existing, sources = _read(path)
            cleaned = clean_candles(existing, tf, inst.exchange, kind=inst.kind)
            dropped[inst.id] = len(existing) - len(cleaned)
            if not cleaned.equals(existing):
                _write_atomic(path, cleaned, sources)
                log.info("%s %s: re-cleaned, %d bars dropped", inst.id, tf, dropped[inst.id])
    return dropped
