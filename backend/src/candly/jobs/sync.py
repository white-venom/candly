"""The one-step switch to Fyers data, run after the first Fyers login.

run_fyers_sync() refreshes the expiry lists, archives the Yahoo dev candles (moved, never deleted),
backfills 1D from 2005 and 5m from 2017-07-03, builds 15m and 1h from the stored 5m, re-reads 1D to fill
gaps, writes the quality report, derives observed holidays and rebuilds the scorecards.

One sync runs at a time, on a background thread (start_fyers_sync). Progress is saved to
data/sync/state.json after every instrument, so a failed or interrupted run resumes where it stopped:
steps and instruments already finished today are skipped. A failing instrument is recorded and skipped;
a Fyers login problem stops the run until the user logs in again.
"""

import copy
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from candly.core.calendar import IST, reload_calendar
from candly.core.instruments import EXCHANGES, Instrument, load_watchlist
from candly.core.settings import get_settings
from candly.data import clock
from candly.data.expiries import refresh_expiries
from candly.data.holidays import derive_observed_holidays
from candly.data.ingest import backfill, rebuild_from_5m, run_ingest
from candly.data.jsonfile import read_json, write_json
from candly.data.quality import build_report, missing_daily_sessions, write_report
from candly.data.sources import SOURCES, fyers
from candly.data.store import archive_source, candle_source, series_stats

log = logging.getLogger(__name__)

SCORECARD_TFS = ("1D", "1h", "15m", "5m")
INTERRUPTED = "the server stopped during the sync; it resumes at the next server start or Fyers login"
PUBLIC_FIELDS = (
    "status", "step", "progress", "message", "started_at", "finished_at", "error",
    "run_date", "completed_steps", "failures", "scorecards", "summary",
)


class SyncRunning(RuntimeError):
    pass


class SyncAborted(RuntimeError):
    """The run can't continue until the user acts (e.g. logs in to Fyers again)."""


# --- state -----------------------------------------------------------------------------------

_run_lock = threading.Lock()  # held for the whole of a run
_state_lock = threading.Lock()
_current: tuple[Path, dict] | None = None  # the live state of this process's latest run


def state_path() -> Path:
    return get_settings().data_dir / "sync" / "state.json"


def _idle() -> dict:
    return {
        "status": "idle", "step": None, "progress": 0.0, "message": None,
        "started_at": None, "finished_at": None, "error": None,
        "run_date": None, "completed_steps": [], "done": {}, "failures": {}, "scorecards": {}, "summary": {},
    }


def _read_state() -> dict:
    raw = read_json(state_path())
    return _idle() | raw if isinstance(raw, dict) else _idle()


def _publish(path: Path, state: dict) -> None:
    """Make the state visible to get_sync_status() and save it for resuming. A failed save is logged,
    never fatal: the run itself is fine, only a later resume would redo a little work."""
    global _current
    with _state_lock:
        _current = (path, copy.deepcopy(state))
    try:
        write_json(path, state)
    except OSError as exc:
        log.warning("could not save the sync state: %s", exc)


def is_running() -> bool:
    return _run_lock.locked()


def get_sync_status() -> dict:
    """status is idle | running | done | error; progress runs 0-1 over the whole run."""
    with _state_lock:
        current = _current
    if current is not None and current[0] == state_path():
        state = copy.deepcopy(current[1])
    else:
        state = _read_state()
    if state["status"] == "running" and not is_running():
        state |= {"status": "error", "error": INTERRUPTED, "message": INTERRUPTED}
    return {"steps": list(STEPS)} | {key: state[key] for key in PUBLIC_FIELDS}


# --- run bookkeeping -------------------------------------------------------------------------


def _now() -> int:
    return clock.epoch_seconds(clock.utc_now())


def _today() -> str:
    return clock.utc_now().tz_convert(IST).date().isoformat()


class _Run:
    def __init__(self, state: dict, path: Path):
        self.state = state
        self.path = path  # fixed for the run, so its state never lands in another data dir
        self.step = ""
        self._base = 0.0

    def publish(self) -> None:
        _publish(self.path, self.state)

    def enter(self, step: str) -> None:
        names = list(STEPS)
        self.step = step
        self._base = sum(WEIGHTS[s] for s in names[: names.index(step)])
        self.state["step"] = step
        self.report(0.0, f"{step.replace('_', ' ')}: starting")

    def report(self, fraction: float, message: str) -> None:
        self.state["progress"] = round(self._base + WEIGHTS[self.step] * fraction, 4)
        self.state["message"] = message
        self.publish()

    def finish(self) -> None:
        self.state["completed_steps"].append(self.step)
        self.state["progress"] = round(self._base + WEIGHTS[self.step], 4)
        self.publish()

    def done(self) -> set[str]:
        return set(self.state["done"].get(self.step, []))

    def item_done(self, item: str) -> None:
        self.state["done"].setdefault(self.step, []).append(item)
        failures = self.state["failures"]
        if item in failures.get(self.step, {}):
            del failures[self.step][item]
            if not failures[self.step]:
                del failures[self.step]

    def item_failed(self, item: str, reason: str) -> None:
        self.state["failures"].setdefault(self.step, {})[item] = reason
        log.warning("sync %s: %s failed: %s", self.step, item, reason)


def _auth_error(exc: Exception) -> bool:
    return isinstance(exc, fyers.FyersNotConnected | fyers.FyersAuthError | SyncAborted)


def _each(run: _Run, items: list[Instrument], label: str, work: Callable[[Instrument], object]) -> None:
    """Run `work` per instrument, skipping those done earlier today. Errors are recorded per instrument;
    a Fyers login problem stops the run."""
    done = run.done()
    for i, inst in enumerate(items):
        if inst.id in done:
            continue
        run.report(i / len(items), f"{label}: {inst.id} ({i + 1}/{len(items)})")
        try:
            work(inst)
        except Exception as exc:
            if _auth_error(exc):
                raise
            run.item_failed(inst.id, str(exc) or type(exc).__name__)
            continue
        run.item_done(inst.id)


def _targets(tf: str) -> list[Instrument]:
    return [i for i in load_watchlist() if tf in i.timeframes and i.source_symbol("fyers")]


# --- steps -----------------------------------------------------------------------------------


def _step_refresh_expiries(run: _Run) -> None:
    run.report(0, "checking the exchanges' expiry lists")
    try:
        result = refresh_expiries()
    except Exception as exc:  # expiries don't block the candle sync
        log.exception("expiry refresh failed")
        run.item_failed("expiries", str(exc))
        return
    for segment, outcome in result["downloads"].items():
        if outcome != "ok":
            run.item_failed(segment, outcome)


def _step_archive(run: _Run) -> None:
    run.report(0, "moving the Yahoo dev candles to data/archive (nothing is deleted)")
    moved = [path for source in SOURCES if source != "fyers" for path in archive_source(source)]
    run.state["summary"]["archived"] = run.state["summary"].get("archived", 0) + len(moved)


def _step_backfill_1d(run: _Run) -> None:
    _each(run, _targets("1D"), "1D history from 2005", lambda inst: backfill(inst, "1D", "fyers"))


def _step_backfill_5m(run: _Run) -> None:
    _each(run, _targets("5m"), "5m history from 2017-07-03", lambda inst: backfill(inst, "5m", "fyers"))


def _step_build_15m_1h(run: _Run) -> None:
    def work(inst: Instrument) -> None:
        for tf in fyers.DERIVED_TFS:
            if tf in inst.timeframes:
                rebuild_from_5m(inst, tf)

    _each(run, _targets("5m"), "building 15m and 1h from 5m", work)


def _fill_daily(inst: Instrument) -> None:
    """Incremental 1D update, starting at the oldest session the 5m data has but 1D lacks (or a full
    backfill if the series is still empty)."""
    if series_stats(inst.id, "1D")["bars"] == 0:
        backfill(inst, "1D", "fyers")
        return
    missing = missing_daily_sessions(inst.id)
    report = run_ingest("1D", [inst], source="fyers", since=missing[0] if missing else None)
    if inst.id in report.failed:
        raise RuntimeError(report.failed[inst.id])


def _step_fill_1d_gaps(run: _Run) -> None:
    targets = _targets("1D")
    _each(run, targets, "second 1D pass (gaps)", _fill_daily)
    run.state["summary"]["missing_daily_sessions"] = sum(len(missing_daily_sessions(i.id)) for i in targets)


def _step_quality_report(run: _Run) -> None:
    run.report(0, "writing the data-quality report")
    try:
        report = build_report()
        write_report(report)
    except Exception as exc:  # the report is informational
        log.exception("quality report failed")
        run.item_failed("report", str(exc))
        return
    run.state["summary"]["quality"] = report["summary"]


def _step_holidays(run: _Run) -> None:
    run.report(0, "deriving exchange holidays from the stored daily bars")
    observed = derive_observed_holidays()
    reload_calendar()  # the scorecards (and the rest of this process) must use the new holidays
    run.state["summary"]["holidays"] = {exchange: len(days) for exchange, days in observed.items()}


def _step_scorecards(run: _Run) -> None:
    """One scorecard per timeframe and exchange; results are keyed "<tf> <exchange>", e.g. "1D MCX"."""
    results = run.state["scorecards"]
    builds = [(tf, exchange) for tf in SCORECARD_TFS for exchange in EXCHANGES]
    for i, (tf, exchange) in enumerate(builds):
        key = f"{tf} {exchange}"
        if results.get(key) == "ok":
            continue
        run.report(i / len(builds), f"building the {tf} {exchange} scorecard")
        try:
            from candly.research.scorecard import build_scorecard

            build_scorecard(tf, exchange=exchange)
        except Exception as exc:
            log.exception("%s %s scorecard build failed", tf, exchange)
            results[key] = f"failed: {exc}"
        else:
            results[key] = "ok"


# Run order. Tests swap entries with monkeypatch.setitem.
STEPS: dict[str, Callable[[_Run], None]] = {
    "refresh_expiries": _step_refresh_expiries,
    "archive": _step_archive,
    "backfill_1d": _step_backfill_1d,
    "backfill_5m": _step_backfill_5m,
    "build_15m_1h": _step_build_15m_1h,
    "fill_1d_gaps": _step_fill_1d_gaps,
    "quality_report": _step_quality_report,
    "holidays": _step_holidays,
    "scorecards": _step_scorecards,
}
# Share of the whole run's progress bar (roughly the share of its time).
WEIGHTS = {
    "refresh_expiries": 0.02, "archive": 0.01, "backfill_1d": 0.22, "backfill_5m": 0.45, "build_15m_1h": 0.07,
    "fill_1d_gaps": 0.08, "quality_report": 0.04, "holidays": 0.01, "scorecards": 0.10,
}


# --- running ---------------------------------------------------------------------------------


def _preflight() -> None:
    """Check the Fyers login before anything is archived."""
    if not get_settings().has_fyers:
        raise SyncAborted("Fyers keys are not set: add FYERS_APP_ID and FYERS_SECRET_KEY to .env")
    fyers.ensure_token()


def _begin() -> _Run:
    """A new run, carrying over finished steps and instruments of today's unfinished run.
    Called with the run lock held, so a stored "running" status is a run that died with its process."""
    previous = _read_state()
    resume = previous["run_date"] == _today() and previous["status"] in ("running", "error")
    state = (previous if resume else _idle()) | {
        "status": "running",
        "step": None,
        "message": "resuming the Fyers sync" if resume else "starting the Fyers sync",
        "started_at": _now(),
        "finished_at": None,
        "error": None,
        "run_date": _today(),
    }
    run = _Run(state, state_path())
    run.publish()
    log.info("Fyers sync %s", "resumed" if resume else "started")
    return run


def _execute(run: _Run) -> dict:
    state = run.state
    try:
        _preflight()
        for name, step in STEPS.items():
            if name in state["completed_steps"]:
                continue
            run.enter(name)
            step(run)
            run.finish()
    except Exception as exc:
        if _auth_error(exc):
            error = f"Fyers login needed ({exc}). Click Connect Fyers; the sync resumes where it stopped."
            log.warning("Fyers sync stopped at %s: %s", run.step or "the start", exc)
        else:
            error = f"{run.step or 'start'} failed: {exc or type(exc).__name__}"
            log.exception("Fyers sync failed at %s", run.step or "the start")
        state |= {"status": "error", "error": error, "message": error}
    else:
        failed = sum(len(items) for items in state["failures"].values())
        failed += sum(result != "ok" for result in state["scorecards"].values())
        message = "Fyers data is ready" + (
            f", with {failed} failure{'s' * (failed != 1)} (see failures and scorecards)" if failed else ""
        )
        state |= {"status": "done", "step": None, "progress": 1.0, "message": message}
        log.info("Fyers sync finished: %s", message)
    state["finished_at"] = _now()
    run.publish()
    return state


def run_fyers_sync() -> dict:
    """Run the whole sync in this thread (resuming today's unfinished run) and return its final state.
    Raises SyncRunning when a sync is already running."""
    if not _run_lock.acquire(blocking=False):
        raise SyncRunning("a Fyers sync is already running")
    try:
        return _execute(_begin())
    finally:
        _run_lock.release()


def _background(run: _Run) -> None:
    try:
        _execute(run)
    except Exception:
        log.exception("Fyers sync crashed")
    finally:
        _run_lock.release()


def start_fyers_sync() -> bool:
    """Start run_fyers_sync on a background thread; False when one is already running."""
    if not _run_lock.acquire(blocking=False):
        return False
    try:
        run = _begin()
        threading.Thread(target=_background, args=(run,), name="fyers-sync", daemon=True).start()
    except BaseException:
        _run_lock.release()
        raise
    return True


def sync_needed() -> bool:
    """An earlier sync didn't finish, the store still holds non-Fyers candles, or a watchlist instrument
    has no Fyers 1D series."""
    if get_sync_status()["status"] == "error":
        return True
    for inst in load_watchlist():
        sources = {tf: candle_source(inst.id, tf) for tf in inst.timeframes}
        if any(source not in (None, "fyers") for source in sources.values()):
            return True
        if "1D" in sources and sources["1D"] is None:
            return True
    return False


def start_if_needed() -> bool:
    """After a Fyers login: start the sync in the background when Fyers is the data source and
    sync_needed(). True if started."""
    if get_settings().resolved_data_source() != "fyers" or not sync_needed():
        return False
    return start_fyers_sync()


def resume_if_unfinished() -> bool:
    """At server start: resume a run that failed or was interrupted, if Fyers is connected. A first sync is
    never started here, only by a login or POST /api/sync/fyers. True if started."""
    if get_settings().resolved_data_source() != "fyers" or get_sync_status()["status"] != "error":
        return False
    return fyers.connection_status()["connected"] and start_fyers_sync()
