"""Resumable backfill of a research universe (e.g. config/universe_nse_eq.yaml) from Fyers.

CLI: python -m candly.data.universe_backfill --universe nse_eq [--tf 1D] [--since 1999-01-01]
         [--max-per-minute 120]
     python -m candly.data.universe_backfill --universe nifty200 --tf 5m --equities-only
     python -m candly.data.universe_backfill --universe nse_eq [--tf 1D] --status

Each instrument goes through candly.data.ingest.run_ingest. A series already stored up to the latest
closed bar is skipped without a request, a stored one that is behind is topped up from its last bar, and
a missing one is backfilled from --since. A series is saved only once all its history has arrived, so a
killed run loses at most the instrument it was on. The run stops (it never refreshes the Fyers session)
shortly before the access token expires. Outcomes are appended to data/logs/backfill_<universe>_<tf>.jsonl,
which --status reads together with the store.
"""

import argparse
import json
import logging
import sys
from collections import Counter
from contextlib import ExitStack
from datetime import date
from pathlib import Path

import pandas as pd

from candly.core.calendar import IST
from candly.core.instruments import Instrument, UnknownInstrument
from candly.core.log import RedactSecrets
from candly.core.settings import get_settings
from candly.data import clock
from candly.data.ingest import BACKFILL_START, latest_closed_open, load_universe, run_ingest
from candly.data.sources import SourceError, fyers
from candly.data.store import series_lock, series_stats

log = logging.getLogger(__name__)

SINCE = {
    "1D": date(1999, 1, 1),  # where the stored Nifty 200 and NIFTY 50 daily series (xs_v1's data) begin
    "5m": BACKFILL_START["5m"],  # Fyers' first minute bars
}
# Each process has its own limiter but Fyers counts them together (200/min): leave the API server room.
PER_MINUTE = 120
MAX_FAILURES_IN_A_ROW = 10  # an outage or a rate-limit block: stop rather than mark everything failed
SESSION_MARGIN = pd.Timedelta(minutes=5)  # one instrument takes well under a minute
RATE_WINDOW = pd.Timedelta(minutes=30)
LIST_LIMIT = 20


def progress_path(name: str, tf: str) -> Path:
    return get_settings().logs_dir / f"backfill_{name}_{tf}.jsonl"


def _lock_path(name: str, tf: str) -> Path:
    return get_settings().data_dir / "run" / f"backfill_{name}_{tf}"


def _epoch(ts: pd.Timestamp | None) -> int | None:
    return None if ts is None else clock.epoch_seconds(ts)


def _append(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def read_progress(path: Path) -> list[dict]:
    """Every recorded outcome, oldest first. A torn last line (from a killed run) is skipped."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def targets(instruments: list[Instrument], tf: str, equities_only: bool) -> list[Instrument]:
    """The instruments to backfill. A universe file lists the timeframes its daily job keeps current; a
    backfill of another timeframe adds it."""
    chosen = [i for i in instruments if i.kind == "equity"] if equities_only else instruments
    return [
        i if tf in i.timeframes else i.model_copy(update={"timeframes": (*i.timeframes, tf)}) for i in chosen
    ]


def _is_current(
    inst: Instrument, tf: str, last: pd.Timestamp | None, latest: dict[str, pd.Timestamp | None]
) -> bool:
    if inst.exchange not in latest:
        latest[inst.exchange] = latest_closed_open(inst.exchange, tf, clock.utc_now())
    newest = latest[inst.exchange]
    return last is not None and newest is not None and last >= newest


def _session_expiring() -> bool:
    """The stored Fyers access token is gone or expires within SESSION_MARGIN. Past its expiry the adapter
    would refresh the session by itself, and an unattended run must leave that to the user's next login."""
    token = fyers.load_token()
    return token is None or not token.access_valid(clock.epoch_seconds(clock.utc_now() + SESSION_MARGIN))


def backfill_universe(instruments: list[Instrument], tf: str, since: date, progress: Path) -> Counter:
    """Counts of outcomes: current (skipped, no request), done, empty (Fyers has no bars), failed, and
    stopped (the run ended early; a rerun resumes)."""
    outcomes: Counter = Counter()
    latest: dict[str, pd.Timestamp | None] = {}
    failures_in_a_row = 0
    for n, inst in enumerate(instruments, 1):
        last = series_stats(inst.id, tf)["last"]
        if _is_current(inst, tf, last, latest):
            outcomes["current"] += 1
            continue
        if _session_expiring():
            log.error("the Fyers session expires within %s: stopping; rerun after logging in", SESSION_MARGIN)
            outcomes["stopped"] += 1
            break
        report = run_ingest(tf, [inst], source="fyers", since=None if last is not None else since)
        stats = series_stats(inst.id, tf)
        error = report.failed.get(inst.id) or report.skipped.get(inst.id)
        status = "failed" if error else "done" if stats["bars"] else "empty"
        rows = report.counts.get(inst.id, 0)
        _append(
            progress,
            {
                "at": clock.epoch_seconds(clock.utc_now()),
                "id": inst.id,
                "status": status,
                "rows": rows,
                "bars": stats["bars"],
                "first": _epoch(stats["first"]),
                "last": _epoch(stats["last"]),
                "error": error,
            },
        )
        outcomes[status] += 1
        span = f"{_day(stats['first'])} -> {_day(stats['last'])}" if stats["bars"] else "no bars"
        log.log(
            logging.WARNING if error else logging.INFO,
            "[%d/%d] %s %s %s: %d rows added/changed, %d bars %s%s",
            n,
            len(instruments),
            inst.id,
            tf,
            status,
            rows,
            stats["bars"],
            span,
            f" ({error})" if error else "",
        )
        failures_in_a_row = failures_in_a_row + 1 if error else 0
        if failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
            log.error("%d failures in a row: stopping; rerun to resume", failures_in_a_row)
            outcomes["stopped"] += 1
            break
    return outcomes


def _day(ts: pd.Timestamp | None) -> str:
    return "-" if ts is None else ts.tz_convert(IST).date().isoformat()


def _ist(epoch: int) -> str:
    return pd.Timestamp(epoch, unit="s", tz="UTC").tz_convert(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def is_running(name: str, tf: str) -> bool:
    try:
        with series_lock(_lock_path(name, tf), timeout=0):
            return False
    except TimeoutError:
        return True


def status_report(name: str, tf: str, instruments: list[Instrument]) -> str:
    records = read_progress(progress_path(name, tf))
    outcome = {r["id"]: r for r in records}
    latest: dict[str, pd.Timestamp | None] = {}
    stored = current = pending = 0
    failed: list[str] = []
    empty: list[str] = []
    for inst in instruments:
        last = series_stats(inst.id, tf)["last"]
        if last is not None:
            stored += 1
            current += _is_current(inst, tf, last, latest)
        elif inst.id not in outcome:
            pending += 1
        elif outcome[inst.id]["status"] == "empty":
            empty.append(inst.id)
        else:
            failed.append(f"{inst.id}  {outcome[inst.id].get('error') or outcome[inst.id]['status']}")
    running = is_running(name, tf)
    lines = [
        f"{name} {tf} backfill: {'RUNNING' if running else 'not running'}",
        f"  done      {stored}/{len(instruments)} stored ({current} up to the latest closed bar)",
        f"  failed    {len(failed)}",
        f"  empty     {len(empty)} (Fyers returned no bars)",
        f"  pending   {pending} (not tried yet)",
    ]
    if records:
        newest = records[-1]
        lines.append(f"  last      {_ist(newest['at'])}  {newest['id']} {newest['status']}")
        now = clock.epoch_seconds(clock.utc_now())
        recent = [r["at"] for r in records if r["at"] >= now - RATE_WINDOW.total_seconds()]
        minutes = (now - min(recent)) / 60 if recent else 0
        if running and minutes >= 1:
            rate = len(recent) / minutes
            eta = now + pending / rate * 60
            lines.append(
                f"  rate      {rate:.1f} instruments/min over the last {minutes:.0f} min; "
                f"ETA {_ist(int(eta))} for the pending ones"
            )
    for title, items in (("failed", failed), ("empty", empty)):
        if items:
            lines.append(f"{title} (a rerun retries them):")
            lines += [f"  {item}" for item in items[:LIST_LIMIT]]
            if len(items) > LIST_LIMIT:
                lines.append(f"  ... and {len(items) - LIST_LIMIT} more")
    return "\n".join(lines)


def _console_logging() -> None:
    """Console only, redirected to the run's own log file: a second writer holding data/logs/candly.log
    open would make the API server's log rotation fail on Windows during a run of several hours."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactSecrets(get_settings().secret_values()))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # a line per request would bury the progress
    logging.getLogger("candly.data.ingest").setLevel(logging.WARNING)  # this module logs each instrument


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m candly.data.universe_backfill",
        description="Backfill a research universe from Fyers; rerun it to resume.",
    )
    parser.add_argument("--universe", required=True, metavar="NAME", help="config/universe_NAME.yaml")
    parser.add_argument("--tf", choices=list(SINCE), default="1D")
    parser.add_argument("--equities-only", action="store_true", help="skip the universe's indices")
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="start of a missing series (default 1999-01-01 for 1D, 2017-07-03 for 5m); stored series are "
        "only topped up",
    )
    parser.add_argument(
        "--max-per-minute",
        type=int,
        default=PER_MINUTE,
        metavar="N",
        help=f"Fyers requests per minute for this process (default {PER_MINUTE}, which leaves the API "
        "server room: every process has its own limiter, but Fyers counts them together)",
    )
    parser.add_argument("--status", action="store_true", help="print the backfill's progress and exit")
    args = parser.parse_args(argv)
    if not 1 <= args.max_per_minute <= fyers.RATE_PER_MINUTE:
        parser.error(f"--max-per-minute must be 1..{fyers.RATE_PER_MINUTE}")
    try:
        instruments = targets(load_universe(args.universe), args.tf, args.equities_only)
    except UnknownInstrument as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.status:
        print(status_report(args.universe, args.tf, instruments))
        return 0
    since = args.since or SINCE[args.tf]
    _console_logging()
    fyers.set_rate_limit(args.max_per_minute)
    with ExitStack() as stack:
        try:
            stack.enter_context(series_lock(_lock_path(args.universe, args.tf), timeout=0))
        except TimeoutError:
            print(f"error: a {args.tf} backfill of {args.universe} is already running", file=sys.stderr)
            return 2
        log.info(
            "backfill %s %s: %d instruments, missing series from %s, %d requests/min",
            args.universe,
            args.tf,
            len(instruments),
            since,
            args.max_per_minute,
        )
        try:
            outcomes = backfill_universe(instruments, args.tf, since, progress_path(args.universe, args.tf))
        except SourceError as exc:  # the Fyers session is gone: every later request would fail too
            log.error("backfill stopped: %s; rerun it to resume once Fyers is connected", exc)
            return 2
    log.info("backfill %s %s finished: %s", args.universe, args.tf, dict(outcomes))
    return 1 if outcomes["failed"] or outcomes["stopped"] else 0


if __name__ == "__main__":
    sys.exit(main())
