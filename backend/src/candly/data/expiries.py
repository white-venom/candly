"""Derivatives expiries for every instrument: the single entry point (docs/CONTRACTS.md).

The exchanges' own contract lists are the source of truth. refresh_expiries() (daily, from the scheduler)
reads the public Fyers symbol masters NSE_FO, BSE_FO and MCX_COM and records every expiry date each
watchlist instrument has listed in data/expiries/live.json. The file is append-only: a date is never
dropped, only marked `withdrawn` when the exchange delists it before it arrives (a moved expiry).

Lookups answer from those dates for days on or after an instrument's first refresh, while the data is at
most STALE_DAYS old. Otherwise NSE/BSE fall back to the rule engine in candly.core.expiry; MCX has no
rules, so it answers only from live data (whatever its age).
"""

import json
import logging
import os
import tempfile
import threading
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from candly.core import expiry as core_expiry
from candly.core.calendar import IST, get_calendar
from candly.core.expiry import ExpiryInfo
from candly.core.instruments import Instrument, exchange_of, load_watchlist
from candly.core.settings import get_settings
from candly.data import clock
from candly.data.sources import fyers

log = logging.getLogger(__name__)

SEGMENTS = fyers.FRONT_SEGMENTS  # exchange -> Fyers derivatives symbol master
STALE_DAYS = 3
CHECK_COUNT = 3  # upcoming expiries compared with the rule engine on every refresh
RULES_HORIZON_DAYS = 120
UNDERLYINGS = {"NSE:NIFTY50": "NIFTY"}  # Fyers underlying names that differ from the watchlist symbol


@dataclass(frozen=True)
class LiveSeries:
    since: date                 # first refresh that listed this instrument
    last_ok: date               # latest refresh that listed it
    days: tuple[date, ...]      # every expiry ever listed, sorted, without withdrawn ones
    kinds: tuple[str, ...]      # "weekly" | "monthly" | "contract", aligned with days


@dataclass(frozen=True)
class _Live:
    series: dict[str, LiveSeries] = field(default_factory=dict)
    check: dict = field(default_factory=dict)


_NO_LIVE = _Live()
_cache: tuple[tuple, _Live] | None = None
_cache_lock = threading.Lock()
_refresh_lock = threading.Lock()
_unlisted_warned: set[str] = set()


def _live_path() -> Path:
    return get_settings().data_dir / "expiries" / "live.json"


# --- reading ---------------------------------------------------------------------------------


def _series(record: dict) -> LiveSeries:
    listed = sorted(
        (date.fromisoformat(day), entry["kind"])
        for day, entry in record.get("expiries", {}).items()
        if "withdrawn" not in entry
    )
    return LiveSeries(
        since=date.fromisoformat(record["since"]),
        last_ok=date.fromisoformat(record["last_ok"]),
        days=tuple(d for d, _ in listed),
        kinds=tuple(k for _, k in listed),
    )


def _live() -> _Live:
    """The parsed live.json, re-read only when the file changes."""
    global _cache
    path = _live_path()
    try:
        stat = path.stat()
    except OSError:
        return _NO_LIVE
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        if _cache is not None and _cache[0] == key:
            return _cache[1]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        live = _Live(
            series={i: _series(r) for i, r in raw.get("instruments", {}).items()},
            check=raw.get("check") or {},
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("ignoring unreadable %s (%s); expiries fall back to the rules", path, exc)
        live = _NO_LIVE
    with _cache_lock:
        _cache = (key, live)
    return live


def _trading_days_until(exchange: str, d: date, until: date) -> int:
    """Trading days after `d` up to and including `until` (0 when they are the same day)."""
    cal = get_calendar()
    days = (d + timedelta(days=i) for i in range(1, (until - d).days + 1))
    return sum(1 for day in days if cal.is_trading_day(exchange, day))


def _from_exchange(instrument_id: str, exchange: str, d: date) -> ExpiryInfo | None:
    series = _live().series.get(instrument_id)
    if series is None or d < series.since:
        return None
    if exchange != "MCX" and d > series.last_ok + timedelta(days=STALE_DAYS):
        return None
    i = bisect_left(series.days, d)
    if i == len(series.days):
        return None
    nxt, kind = series.days[i], series.kinds[i]
    return ExpiryInfo(
        next_expiry=nxt,
        kind=kind,
        days_to_expiry=_trading_days_until(exchange, d, nxt),
        is_expiry_day=nxt == d,
        # a front-month MCX contract expiry is the analogue of a monthly one
        is_monthly_expiry_day=nxt == d and kind in ("monthly", "contract"),
    )


def expiry_with_source(instrument_id: str, d: date) -> tuple[ExpiryInfo | None, str | None]:
    """(next expiry on or after the IST date `d`, "exchange" | "rules"). (None, None) for instruments
    without derivatives (INDIAVIX), and for MCX without live data covering `d`."""
    exchange = exchange_of(instrument_id)
    info = _from_exchange(instrument_id, exchange, d)
    if info is not None:
        return info, "exchange"
    if exchange == "MCX":
        return None, None
    info = core_expiry.expiry_info(instrument_id, d)
    return info, None if info is None else "rules"


def expiry_info(instrument_id: str, d: date) -> ExpiryInfo | None:
    """Next expiry on or after the IST date `d`; see expiry_with_source for where it comes from."""
    return expiry_with_source(instrument_id, d)[0]


def exchange_expiries(instrument_id: str, start: date = date.min) -> list[tuple[date, str]]:
    """(date, kind) of every expiry the exchange has listed for the instrument on or after `start`,
    withdrawn dates excluded. Empty without live data."""
    series = _live().series.get(instrument_id)
    if series is None:
        return []
    i = bisect_left(series.days, start)
    return list(zip(series.days[i:], series.kinds[i:], strict=True))


def mcx_roll_dates(instrument_id: str) -> list[date]:
    """First MCX trading day after each known contract expiry: the day the continuous front-month series
    switches to the next contract. Empty for non-MCX instruments; rolls before the first refresh are
    unknown."""
    if exchange_of(instrument_id) != "MCX":
        return []
    cal = get_calendar()
    rolls = []
    for expiry, _ in exchange_expiries(instrument_id):
        day = expiry + timedelta(days=1)
        while not cal.is_trading_day("MCX", day):
            day += timedelta(days=1)
        rolls.append(day)
    return rolls


def expiry_check() -> dict:
    """The latest comparison of exchange dates with config/expiry.yaml, as /api/health reports it.
    "unavailable" when no refresh has reached an exchange within STALE_DAYS."""
    check = _live().check
    checked_at = check.get("checked_at")
    mismatches = [dict(m) for m in check.get("mismatches", [])]
    now = clock.epoch_seconds(clock.utc_now())
    if checked_at is None or now - checked_at > STALE_DAYS * 86400:
        status = "unavailable"
    else:
        status = "mismatch" if mismatches else "ok"
    return {"status": status, "checked_at": checked_at, "mismatches": mismatches}


# --- refreshing ------------------------------------------------------------------------------


def _mcx_root(inst: Instrument) -> str:
    symbol = inst.source_symbol("fyers") or ""
    if symbol.endswith("@FRONT"):
        return symbol.removesuffix("@FRONT").partition(":")[2]
    return inst.symbol


def _targets() -> dict[str, tuple[str, str]]:
    """{instrument id: (symbol master segment, underlying)} for watchlist instruments that may have
    derivatives. Whether they do is up to the exchange's list."""
    targets = {}
    for inst in load_watchlist():
        if inst.exchange == "MCX":
            targets[inst.id] = (SEGMENTS["MCX"], _mcx_root(inst))
        elif inst.kind in ("index", "equity"):
            targets[inst.id] = (SEGMENTS[inst.exchange], UNDERLYINGS.get(inst.id, inst.symbol))
    return targets


def _tag(days: set[date], future_days: set[date]) -> dict[date, str]:
    """Monthly = the day a future expires. In months beyond the listed futures (long-dated options
    only), the month's last expiry is the monthly one. Everything else is weekly."""
    future_months = {(d.year, d.month) for d in future_days}
    last_in_month: dict[tuple[int, int], date] = {}
    for d in sorted(days):
        last_in_month[(d.year, d.month)] = d
    monthly = future_days | {d for m, d in last_in_month.items() if m not in future_months}
    return {d: "monthly" if d in monthly else "weekly" for d in days}


def _listed(table: pd.DataFrame, underlyings: set[str], mcx: bool) -> dict[str, dict[date, str]]:
    """{underlying: {expiry date: kind}} for the contracts in one symbol master. MCX counts futures only
    (its options expire before the future); NSE/BSE count futures and options."""
    rows = table[table["underlying"].isin(underlyings) & table["expiry"].notna()]
    rows = rows[rows["ticker"].str.endswith(("FUT",) if mcx else ("FUT", "CE", "PE"))]
    listed = {}
    for underlying, group in rows.groupby("underlying"):
        days = pd.to_datetime(group["expiry"].astype("int64"), unit="s", utc=True).dt.tz_convert(IST).dt.date
        if mcx:
            listed[underlying] = dict.fromkeys(set(days), "contract")
        else:
            futures = set(days[group["ticker"].str.endswith("FUT")])
            listed[underlying] = _tag(set(days), futures)
    return listed


def _merge(record: dict, listed: dict[date, str], today: date) -> tuple[int, list[str]]:
    """Adds one listing to an instrument's history: (dates added, dates withdrawn by the exchange)."""
    known = record.setdefault("expiries", {})
    stamp = today.isoformat()
    added = 0
    for d, kind in listed.items():
        entry = known.get(d.isoformat())
        if entry is None:
            entry = known[d.isoformat()] = {"first_seen": stamp}
            added += 1
        entry["kind"] = kind
        entry.pop("withdrawn", None)
    current = {d.isoformat() for d in listed}
    withdrawn = []
    for day, entry in known.items():
        # an expiry after today that is no longer listed was moved or cancelled, not traded out
        if day > stamp and day not in current and "withdrawn" not in entry:
            entry["withdrawn"] = stamp
            withdrawn.append(day)
    record["since"] = min(record.get("since", stamp), stamp)
    record["last_ok"] = max(record.get("last_ok", stamp), stamp)
    return added, withdrawn


def _mismatch_key(m: dict) -> tuple[str, str, str]:
    return m["instrument"], m["rules"], m["exchange"]


def _check(state: dict, targets: dict, today: date, stamp: int) -> dict:
    """Compares the next CHECK_COUNT expiries of the rule engine and the exchange, per instrument whose
    live data is fresh. New mismatches are logged once."""
    rules = core_expiry.get_expiry_rules()
    previous = {_mismatch_key(m) for m in state["check"].get("mismatches", [])}
    horizon = today + timedelta(days=RULES_HORIZON_DAYS)
    mismatches = []
    for instrument_id in targets:
        record = state["instruments"].get(instrument_id)
        if record is None or rules.product_key(instrument_id) is None:
            continue
        series = _series(record)
        if today > series.last_ok + timedelta(days=STALE_DAYS):
            continue
        listed = [d for d in series.days if d >= today][:CHECK_COUNT]
        expected = [d for d, _ in rules.expiries_between(instrument_id, today, horizon)][:CHECK_COUNT]
        for rule_day, exchange_day in zip(expected, listed, strict=False):
            if rule_day == exchange_day:
                continue
            mismatch = {
                "instrument": instrument_id,
                "rules": rule_day.isoformat(),
                "exchange": exchange_day.isoformat(),
            }
            mismatches.append(mismatch)
            if _mismatch_key(mismatch) not in previous:
                log.warning(
                    "expiry mismatch for %s: config/expiry.yaml gives %s, the exchange lists %s",
                    instrument_id,
                    mismatch["rules"],
                    mismatch["exchange"],
                )
    return {"checked_at": stamp, "mismatches": mismatches}


def _read_state() -> dict:
    path = _live_path()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    except (OSError, ValueError) as exc:
        aside = path.with_name(f"live.unreadable-{clock.epoch_seconds(clock.utc_now())}.json")
        log.error("%s is unreadable (%s); moved to %s and starting a new history", path, exc, aside.name)
        path.replace(aside)
        state = {}
    for key in ("instruments", "downloads", "check"):
        state.setdefault(key, {})
    return state


def _write_state(state: dict) -> None:
    path = _live_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".live.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _note_unlisted(instrument_id: str) -> None:
    """The rules expect expiries but the exchange lists none, e.g. a stock that left F&O."""
    if instrument_id in _unlisted_warned or core_expiry.get_expiry_rules().product_key(instrument_id) is None:
        return
    _unlisted_warned.add(instrument_id)
    log.warning("%s has expiry rules but no contracts in the exchange's list", instrument_id)


def refresh_expiries() -> dict:
    """Reads today's symbol masters, adds every listed expiry to data/expiries/live.json and compares the
    next few with the rule engine. Safe to call often: the masters are cached for the IST day, and a
    failed download keeps the last good dates and is recorded. Returns expiry_check() plus
    `downloads` ({segment: "ok" | "failed: <reason>"}) and `new_dates`."""
    global _cache
    with _refresh_lock:
        now = clock.utc_now()
        today = now.tz_convert(IST).date()
        stamp = clock.epoch_seconds(now)
        state = _read_state()
        targets = _targets()
        downloads: dict[str, str] = {}
        added = 0
        for segment in sorted({segment for segment, _ in targets.values()}):
            wanted = {i: u for i, (s, u) in targets.items() if s == segment}
            mcx = segment == SEGMENTS["MCX"]
            try:
                listed = _listed(fyers.symbol_master(segment), set(wanted.values()), mcx)
            except Exception as exc:  # any failure keeps the last good dates
                log.warning("%s symbol master unavailable (%s); keeping the last expiry dates", segment, exc)
                state["downloads"][segment] = state["downloads"].get(segment, {}) | {
                    "failed_at": stamp,
                    "error": str(exc),
                }
                downloads[segment] = f"failed: {exc}"
                continue
            state["downloads"][segment] = {"ok_at": stamp, "failed_at": None, "error": None}
            downloads[segment] = "ok"
            for instrument_id, underlying in wanted.items():
                if underlying not in listed:
                    _note_unlisted(instrument_id)
                    continue
                record = state["instruments"].setdefault(instrument_id, {})
                record.update(segment=segment, underlying=underlying)
                new, withdrawn = _merge(record, listed[underlying], today)
                added += new
                for day in withdrawn:
                    log.warning("%s: the exchange no longer lists the %s expiry (moved?)", instrument_id, day)
        if "ok" in downloads.values():
            state["check"] = _check(state, targets, today, stamp)
        _write_state(state)
        with _cache_lock:
            _cache = None
    return expiry_check() | {"downloads": downloads, "new_dates": added}
