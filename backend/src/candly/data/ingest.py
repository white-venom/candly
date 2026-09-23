"""Candle ingestion: backfill empty series, then update incrementally from the last stored bar.

CLI: python -m candly.data.ingest --tf 1D [--instrument NSE:RELIANCE ...] [--source auto|fyers|yahoo]
     [--since 2015-01-01]
     python -m candly.data.ingest --tf 1D --clean-existing   (re-clean stored NSE/BSE series, no download)
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
from candly.core.timeframes import TIMEFRAMES, is_intraday, validate_tf
from candly.data import clock
from candly.data.clean import closed_only
from candly.data.resample import resample_candles
from candly.data.sources import SOURCES, SourceError, fetch_candles, fyers, resolve_source
from candly.data.store import clean_existing, load_candles, save_candles, series_stats

log = logging.getLogger(__name__)

BACKFILL_START = {
    "1D": date(2005, 1, 1),
    "5m": date(2017, 7, 3),
    "15m": date(2017, 7, 3),
    "1h": date(2017, 7, 3),
}
DAILY_OVERLAP = pd.Timedelta(days=10)


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


def _overlap_start(exchange: str, tf: str, last: pd.Timestamp) -> pd.Timestamp:
    """Re-read a little history before the last stored bar so late corrections are picked up."""
    if is_intraday(tf):
        return _session_open_on_or_before(exchange, last)
    return last - DAILY_OVERLAP


def _update_start(inst: Instrument, tf: str, since: pd.Timestamp | None) -> pd.Timestamp | None:
    """Where to start fetching, or None when the series already holds the latest closed bar."""
    if since is not None:
        return _session_open_on_or_before(inst.exchange, since)
    last = series_stats(inst.id, tf)["last"]
    if last is None:
        return clock.ist_midnight(BACKFILL_START[tf])
    latest = latest_closed_open(inst.exchange, tf, clock.utc_now())
    if latest is not None and last >= latest:
        return None
    return _overlap_start(inst.exchange, tf, last)


def _ingest_series(inst: Instrument, tf: str, source: str, since: pd.Timestamp | None) -> int:
    start = _update_start(inst, tf, since)
    if start is None:
        return 0
    df = fetch_candles(source, inst, tf, start)
    return save_candles(inst.id, tf, df, source=source)


def _ingest_derived(inst: Instrument, tf: str, since: pd.Timestamp | None) -> int:
    """Fyers 15m/1h: bring 5m up to date, then resample the stored 5m bars (no extra API calls)."""
    _ingest_series(inst, "5m", "fyers", since)
    last = series_stats(inst.id, tf)["last"]
    if since is not None:
        start = _session_open_on_or_before(inst.exchange, since)
    elif last is None:
        start = None
    else:
        start = _overlap_start(inst.exchange, tf, last)
    base = load_candles(inst.id, "5m", start=start)
    derived = closed_only(resample_candles(base, tf, inst.exchange), inst.exchange, tf, clock.utc_now())
    return save_candles(inst.id, tf, derived, source="fyers")


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
    for inst in targets:
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
            "%s %s via %s: %d rows added/changed; %d bars %s -> %s",
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
    parser.add_argument("--tf", required=True, choices=list(TIMEFRAMES))
    parser.add_argument("--instrument", dest="instruments", nargs="+", action="extend", metavar="ID")
    parser.add_argument("--source", choices=["auto", *SOURCES], default="auto")
    parser.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD")
    parser.add_argument(
        "--clean-existing",
        action="store_true",
        help="re-run the candle cleaning over the stored NSE/BSE series of --tf instead of downloading",
    )
    args = parser.parse_args(argv)
    setup_logging()
    if args.clean_existing:
        dropped = clean_existing(args.tf)
        print(f"\n{'instrument':<18} {'dropped':>7}")
        for instrument_id, count in dropped.items():
            print(f"{instrument_id:<18} {count:>7}")
        return 0
    try:
        report = run_ingest(args.tf, args.instruments, args.source, args.since)
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
