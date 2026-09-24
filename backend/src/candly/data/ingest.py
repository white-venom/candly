"""Candle ingestion: backfill empty series, then update incrementally from the last stored bar.
Every 1D run also re-reads the last few sessions and re-fetches daily bars missing next to intraday data.

CLI: python -m candly.data.ingest --tf 1D [--instrument NSE:RELIANCE ... | --universe nifty200]
     [--source auto|fyers|yahoo] [--since 2015-01-01] [--max-per-minute 100]
     python -m candly.data.ingest --tf 1D --clean-existing   (re-clean stored NSE/BSE series, no download)
     python -m candly.data.ingest --archive-source yahoo     (move Yahoo series to data/archive/, no download)
"""

import argparse
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from candly.core.calendar import IST, get_calendar
from candly.core.instruments import Instrument, UnknownInstrument, get_instrument, load_watchlist
from candly.core.log import setup_logging
from candly.core.settings import get_settings
from candly.core.timeframes import TIMEFRAMES, validate_tf
from candly.data import clock
from candly.data.clean import closed_only
from candly.data.quality import missing_daily_sessions
from candly.data.resample import resample_candles
from candly.data.sources import SOURCES, SourceError, fetch_candles, fyers, resolve_source
from candly.data.store import archive_source, clean_existing, load_candles, save_candles, series_stats

log = logging.getLogger(__name__)

BACKFILL_START = {
    "1D": date(2005, 1, 1),
    "5m": date(2017, 7, 3),
    "15m": date(2017, 7, 3),
    "1h": date(2017, 7, 3),
}
DAILY_REFETCH_SESSIONS = 5  # every 1D run re-reads at least this many recent sessions
MISSING_REFETCH_LIMIT = 10  # most recent missing daily sessions re-fetched per 1D run
# One fetch-and-save per window during a full backfill: whole multiples of Fyers' per-request maximum.
BACKFILL_WINDOW = {"1D": pd.Timedelta(days=365), "5m": pd.Timedelta(days=396)}


@dataclass
class IngestReport:
    counts: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def _since_utc(since: date | str | pd.Timestamp | None) -> pd.Timestamp | None:
    if since is None:
        return None
    if isinstance(since, str):
        since = date.fromisoformat(since)
    if isinstance(since, datetime):
        return clock.to_utc(since)
    return clock.ist_midnight(since)


def latest_closed_open(exchange: str, tf: str, now: pd.Timestamp) -> pd.Timestamp | None:
    """Open time of the most recent bar that has closed by `now`."""
    cal = get_calendar()
    day = cal.local_date(now)
    for _ in range(15):
        closed = [
            ts
            for ts in cal.expected_bar_opens(exchange, day, tf)
            if cal.bar_close_time(exchange, ts, tf) <= now
        ]
        if closed:
            return closed[-1]
        day -= timedelta(days=1)
    return None


def _session_open_on_or_before(exchange: str, ts: pd.Timestamp) -> pd.Timestamp:
    """A start inside a session moves back to that session's open, so the first 15m/1h bucket built
    from it is complete and never overwrites a correct stored bar with a partial one."""
    cal = get_calendar()
    return min(ts, cal.session_times(exchange, cal.local_date(ts))[0])


def recent_sessions_start(exchange: str, sessions: int, now: pd.Timestamp) -> pd.Timestamp:
    """IST midnight of the `sessions`-th most recent session whose daily bar has closed by `now`."""
    cal = get_calendar()
    latest = latest_closed_open(exchange, "1D", now)
    day = cal.local_date(latest if latest is not None else now)
    found = 0 if latest is None else 1
    while found < sessions:
        day -= timedelta(days=1)
        found += cal.is_trading_day(exchange, day)
    return clock.ist_midnight(day)


def _update_start(inst: Instrument, tf: str, since: pd.Timestamp | None) -> pd.Timestamp | None:
    """Where to start fetching, or None when an intraday series already holds the latest closed bar."""
    if since is not None:
        return _session_open_on_or_before(inst.exchange, since)
    last = series_stats(inst.id, tf)["last"]
    if last is None:
        return clock.ist_midnight(BACKFILL_START[tf])
    now = clock.utc_now()
    if tf == "1D":
        # Always re-read the last few sessions: a late or provisional daily close gets corrected.
        last_day = clock.ist_midnight(get_calendar().local_date(last))
        return min(last_day, recent_sessions_start(inst.exchange, DAILY_REFETCH_SESSIONS, now))
    latest = latest_closed_open(inst.exchange, tf, now)
    if latest is not None and last >= latest:
        return None
    # Re-read the last stored session so late intraday corrections are picked up.
    return _session_open_on_or_before(inst.exchange, last)


def _refetch_missing_daily(inst: Instrument, source: str) -> int:
    """Re-fetch the 1D bar of sessions the intraday series have but the daily series lacks, e.g. a close
    the source published late. Best effort: the main update has already been saved."""
    missing = missing_daily_sessions(inst)[-MISSING_REFETCH_LIMIT:]
    count = 0
    for day in missing:
        start, end = clock.ist_midnight(day), clock.ist_midnight(day + timedelta(days=1))
        try:
            count += save_candles(inst.id, "1D", fetch_candles(source, inst, "1D", start, end), source=source)
        except fyers.FyersNotConnected:
            raise
        except Exception as exc:
            log.warning("%s 1D: re-fetching %s failed: %s", inst.id, day, exc)
    if missing:
        still = sorted(set(missing) & set(missing_daily_sessions(inst)))
        log.log(
            logging.WARNING if still else logging.INFO,
            "%s 1D: re-fetched %d sessions present intraday but missing daily; still missing: %s",
            inst.id,
            len(missing),
            [d.isoformat() for d in still] or "none",
        )
    return count


def _ingest_series(inst: Instrument, tf: str, source: str, since: pd.Timestamp | None) -> int:
    start = _update_start(inst, tf, since)
    if start is None:
        return 0
    count = save_candles(inst.id, tf, fetch_candles(source, inst, tf, start), source=source)
    if tf == "1D":
        count += _refetch_missing_daily(inst, source)
    return count


def rebuild_from_5m(inst: Instrument, tf: str, start: pd.Timestamp | None = None) -> int:
    """Resample the stored 5m bars from `start` (default: all of them) into closed `tf` bars and store
    them as Fyers data. `start` must be a session open, so the first bucket is complete."""
    base = load_candles(inst.id, "5m", start=start)
    derived = closed_only(resample_candles(base, tf, inst.exchange), inst.exchange, tf, clock.utc_now())
    return save_candles(inst.id, tf, derived, source="fyers")


def _ingest_derived(inst: Instrument, tf: str, since: pd.Timestamp | None) -> int:
    """Fyers 15m/1h: bring 5m up to date, then resample the stored 5m bars (no extra API calls)."""
    _ingest_series(inst, "5m", "fyers", since)
    last = series_stats(inst.id, tf)["last"]
    if since is not None:
        start = _session_open_on_or_before(inst.exchange, since)
    elif last is None:
        start = None
    else:
        start = _session_open_on_or_before(inst.exchange, last)
    return rebuild_from_5m(inst, tf, start)


def backfill(inst: Instrument, tf: str, source: str, start: date | None = None) -> int:
    """Fetch and store `tf` from `start` (default BACKFILL_START) up to now, one window at a time, so years
    of 5m bars never sit in memory at once. Idempotent: stored bars are upserted."""
    window_start = clock.ist_midnight(start or BACKFILL_START[tf])
    now = clock.utc_now()
    count = 0
    while window_start < now:
        window_end = min(window_start + BACKFILL_WINDOW[tf], now)
        count += save_candles(
            inst.id, tf, fetch_candles(source, inst, tf, window_start, window_end), source=source
        )
        window_start = window_end
    return count


def load_universe(name: str) -> list[Instrument]:
    """A research universe from config/universe_{name}.yaml (same format as the watchlist). Its instruments
    share the candle store but are not on the dashboard."""
    path = get_settings().config_dir / f"universe_{name}.yaml"
    if not path.exists():
        raise UnknownInstrument(f"no universe {name!r} ({path.name} not found)")
    return load_watchlist(path)


def _instruments(instruments: Iterable[str | Instrument] | None) -> list[Instrument]:
    if instruments is None:
        return load_watchlist()
    return [i if isinstance(i, Instrument) else get_instrument(i) for i in instruments]


def run_ingest(
    tf: str,
    instruments: Iterable[str | Instrument] | None = None,
    source: str = "auto",
    since: date | str | pd.Timestamp | None = None,
) -> IngestReport:
    tf = validate_tf(tf)
    source = resolve_source(source)
    since_ts = _since_utc(since)
    targets = _instruments(instruments)
    if source == "fyers":
        fyers.ensure_token()  # fail once, clearly, instead of once per instrument
    report = IngestReport()
    for n, inst in enumerate(targets, 1):
        if tf not in inst.timeframes:
            report.skipped[inst.id] = f"{tf} is not configured for this instrument"
            continue
        if not inst.source_symbol(source):
            report.skipped[inst.id] = f"not available on {source}"
            log.warning("%s %s: skipped, not available on %s", inst.id, tf, source)
            continue
        try:
            if source == "fyers" and tf in fyers.DERIVED_TFS:
                count = _ingest_derived(inst, tf, since_ts)
            else:
                count = _ingest_series(inst, tf, source, since_ts)
        except fyers.FyersNotConnected:
            raise
        except Exception as exc:  # one bad series must not stop the batch
            report.failed[inst.id] = str(exc) or type(exc).__name__
            log.error("%s %s: ingest failed: %s", inst.id, tf, exc, exc_info=not isinstance(exc, SourceError))
            continue
        report.counts[inst.id] = count
        stats = series_stats(inst.id, tf)
        log.info(
            "[%d/%d] %s %s via %s: %d rows added/changed; %d bars %s -> %s",
            n,
            len(targets),
            inst.id,
            tf,
            source,
            count,
            stats["bars"],
            stats["first"],
            stats["last"],
        )
    return report


def ingest(
    tf: str,
    instruments: Iterable[str | Instrument] | None = None,
    source: str = "auto",
    since: date | str | pd.Timestamp | None = None,
) -> dict[str, int]:
    """Returns {instrument_id: rows added or changed}. Skipped and failed instruments are logged."""
    return run_ingest(tf, instruments, source, since).counts


def _fmt(ts: pd.Timestamp | None) -> str:
    return "-" if ts is None else ts.tz_convert(IST).strftime("%Y-%m-%d %H:%M")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m candly.data.ingest", description="Download candles.")
    parser.add_argument("--tf", choices=list(TIMEFRAMES))
    parser.add_argument("--instrument", dest="instruments", nargs="+", action="extend", metavar="ID")
    parser.add_argument(
        "--universe",
        metavar="NAME",
        help="every instrument in config/universe_NAME.yaml (research only, e.g. nifty200) instead of the "
        "watchlist",
    )
    parser.add_argument("--source", choices=["auto", *SOURCES], default="auto")
    parser.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD")
    parser.add_argument(
        "--max-per-minute",
        type=int,
        metavar="N",
        help=f"Fyers requests per minute for this process (default {fyers.RATE_PER_MINUTE}). Lower it when "
        "the API server is fetching too: every process has its own limiter, but Fyers counts them together",
    )
    parser.add_argument(
        "--clean-existing",
        action="store_true",
        help="re-run the candle cleaning over the stored NSE/BSE series of --tf instead of downloading",
    )
    parser.add_argument(
        "--archive-source",
        choices=SOURCES,
        metavar="SOURCE",
        help="move every stored series that came from SOURCE to data/archive/ (nothing is deleted), so the "
        "next ingest backfills from scratch; runs on its own",
    )
    args = parser.parse_args(argv)
    if args.archive_source and (args.tf or args.instruments or args.since or args.clean_existing):
        parser.error("--archive-source runs on its own; run the ingest afterwards")
    if not args.archive_source and not args.tf:
        parser.error("--tf is required")
    if args.universe and args.instruments:
        parser.error("use --universe or --instrument, not both")
    if args.max_per_minute is not None and not 1 <= args.max_per_minute <= fyers.RATE_PER_MINUTE:
        parser.error(f"--max-per-minute must be 1..{fyers.RATE_PER_MINUTE}")
    setup_logging()
    if args.max_per_minute:
        fyers.set_rate_limit(args.max_per_minute)
    if args.archive_source:
        moved = archive_source(args.archive_source)
        data_dir = get_settings().data_dir
        for path in moved:
            print(f"archived {path.relative_to(data_dir)}")
        print(f"\n{len(moved)} {args.archive_source} series archived" if moved else "nothing to archive")
        return 0
    if args.clean_existing:
        dropped = clean_existing(args.tf)
        print(f"\n{'instrument':<18} {'dropped':>7}")
        for instrument_id, count in dropped.items():
            print(f"{instrument_id:<18} {count:>7}")
        return 0
    try:
        targets = load_universe(args.universe) if args.universe else args.instruments
        report = run_ingest(args.tf, targets, args.source, args.since)
    except (SourceError, UnknownInstrument) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"\n{'instrument':<18} {'new':>7} {'bars':>8}  first (IST)       last (IST)")
    for instrument_id, count in report.counts.items():
        stats = series_stats(instrument_id, args.tf)
        first, last = _fmt(stats["first"]), _fmt(stats["last"])
        print(f"{instrument_id:<18} {count:>7} {stats['bars']:>8}  {first:<17} {last}")
    for instrument_id, reason in report.skipped.items():
        print(f"{instrument_id:<18} skipped: {reason}")
    for instrument_id, reason in report.failed.items():
        print(f"{instrument_id:<18} FAILED: {reason}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
