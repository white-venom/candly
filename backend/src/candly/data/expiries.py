"""Derivatives expiries for every instrument: the single entry point (docs/CONTRACTS.md, wave 2).

NSE/BSE come from the rule engine in candly.core.expiry. MCX futures expire on contract-specific dates
taken from the Fyers MCX symbol master. The master lists only live contracts, so every expiry it has
shown is also kept in data/expiries/mcx.json; MCX answers are only given from the day a master was
first seen, because earlier (expired) contracts are unknown.
"""

import json
import logging
import os
import tempfile
import threading
import time
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from candly.core import expiry as core_expiry
from candly.core.calendar import IST, get_calendar
from candly.core.expiry import ExpiryInfo
from candly.core.instruments import get_instrument
from candly.core.settings import get_settings
from candly.data import clock
from candly.data.sources import fyers

log = logging.getLogger(__name__)

MCX_SEGMENT = "MCX_COM"
RETRY_SECONDS = 15 * 60  # after a failed master download, serve the cached copy this long before retrying


@dataclass(frozen=True)
class KnownExpiries:
    since: date                   # expiries on or after this day are complete
    expiries: tuple[date, ...]    # sorted


@dataclass
class _Cached:
    key: tuple[Path, date]
    loaded_at: float
    fresh: bool
    data: dict[str, KnownExpiries]


_cache: _Cached | None = None
_lock = threading.Lock()


def _registry_path() -> Path:
    return get_settings().data_dir / "expiries" / "mcx.json"


def _read_registry() -> dict[str, KnownExpiries]:
    try:
        raw = json.loads(_registry_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable MCX expiry registry: %s", exc)
        return {}
    return {
        root: KnownExpiries(
            since=date.fromisoformat(item["since"]),
            expiries=tuple(sorted(date.fromisoformat(e) for e in item["expiries"])),
        )
        for root, item in raw.items()
    }


def _write_registry(data: dict[str, KnownExpiries]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        root: {"since": k.since.isoformat(), "expiries": [e.isoformat() for e in k.expiries]}
        for root, k in sorted(data.items())
    }
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mcx.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _master(today: date) -> tuple[pd.DataFrame | None, date | None, bool]:
    """(master table, IST day it was downloaded, fresh). Falls back to an older cached file when the
    download fails; (None, None, False) when there is none."""
    try:
        return fyers.symbol_master(MCX_SEGMENT, retries=0), today, True
    except Exception as exc:  # the API must keep working without the master
        log.warning("MCX symbol master unavailable (%s); using the cached copy if any", exc)
    path = fyers.master_path(MCX_SEGMENT)
    try:
        return fyers.read_master(path), date.fromisoformat(fyers.master_day(path)), False
    except Exception:
        return None, None, False


def _merge(
    registry: dict[str, KnownExpiries], table: pd.DataFrame, seen_on: date
) -> dict[str, KnownExpiries]:
    futures = table[table["ticker"].str.endswith("FUT") & table["expiry"].notna()]
    merged = dict(registry)
    for root, rows in futures.groupby("underlying"):
        days = {datetime.fromtimestamp(int(e), IST).date() for e in rows["expiry"]}
        old = registry.get(root)
        if old is not None:
            days |= set(old.expiries)
        since = seen_on if old is None else min(old.since, seen_on)
        merged[root] = KnownExpiries(since=since, expiries=tuple(sorted(days)))
    return merged


def known_mcx_expiries() -> dict[str, KnownExpiries]:
    """{underlying: KnownExpiries} for every MCX future seen in a symbol master, cached for the IST day."""
    global _cache
    today = clock.utc_now().tz_convert(IST).date()
    key = (get_settings().data_dir, today)
    with _lock:
        cached = _cache
        if cached and cached.key == key:
            if cached.fresh or time.monotonic() - cached.loaded_at < RETRY_SECONDS:
                return cached.data
        registry = _read_registry()
        table, seen_on, fresh = _master(today)
        if table is not None and seen_on is not None:
            merged = _merge(registry, table, seen_on)
            if merged != registry:
                try:
                    _write_registry(merged)
                except OSError as exc:
                    log.warning("could not save the MCX expiry registry: %s", exc)
            registry = merged
        _cache = _Cached(key=key, loaded_at=time.monotonic(), fresh=fresh, data=registry)
        return registry


def _mcx_root(instrument_id: str) -> str:
    inst = get_instrument(instrument_id)
    symbol = inst.source_symbol("fyers") or ""
    if symbol.endswith("@FRONT"):
        return symbol.removesuffix("@FRONT").partition(":")[2]
    return inst.symbol


def _trading_days_until(exchange: str, d: date, until: date) -> int:
    """Trading days after `d` up to and including `until` (0 when they are the same day)."""
    cal = get_calendar()
    days = (d + timedelta(days=i) for i in range(1, (until - d).days + 1))
    return sum(1 for day in days if cal.is_trading_day(exchange, day))


def _mcx_expiry_info(instrument_id: str, d: date) -> ExpiryInfo | None:
    known = known_mcx_expiries().get(_mcx_root(instrument_id))
    if known is None or d < known.since:
        return None
    i = bisect_left(known.expiries, d)
    if i == len(known.expiries):
        return None
    nxt = known.expiries[i]
    return ExpiryInfo(
        next_expiry=nxt,
        kind="contract",
        days_to_expiry=_trading_days_until("MCX", d, nxt),
        is_expiry_day=nxt == d,
        is_monthly_expiry_day=nxt == d,  # a front-month contract expiry is the MCX analogue of a monthly one
    )


def expiry_info(instrument_id: str, d: date) -> ExpiryInfo | None:
    """Next expiry on or after `d` (an IST date). None for instruments without derivatives (INDIAVIX),
    and for MCX when no symbol master is available or `d` is before the first one seen."""
    if instrument_id.partition(":")[0] != "MCX":
        return core_expiry.expiry_info(instrument_id, d)
    try:
        return _mcx_expiry_info(instrument_id, d)
    except Exception:
        log.exception("MCX expiry lookup failed for %s", instrument_id)
        return None


def mcx_roll_dates(instrument_id: str) -> list[date]:
    """First MCX trading day after each known contract expiry: the day the continuous front-month series
    switches to the next contract. Empty for non-MCX instruments; rolls before the first master seen are
    unknown."""
    if instrument_id.partition(":")[0] != "MCX":
        return []
    known = known_mcx_expiries().get(_mcx_root(instrument_id))
    if known is None:
        return []
    cal = get_calendar()
    rolls = []
    for expiry in known.expiries:
        day = expiry + timedelta(days=1)
        while not cal.is_trading_day("MCX", day):
            day += timedelta(days=1)
        rolls.append(day)
    return rolls
