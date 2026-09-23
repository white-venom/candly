"""Background jobs. Nothing starts on import: call start_scheduler() from the app's lifespan.

The lead adds quant jobs with get_scheduler().add_job(...), e.g. run_forecast_cycle after the intraday
ingest, or rebuild_scorecard nightly.
"""

import logging
import threading
from datetime import datetime

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from candly.core.calendar import IST, get_calendar
from candly.core.instruments import EXCHANGES, load_watchlist
from candly.core.settings import get_settings
from candly.data import clock
from candly.data.expiries import refresh_expiries as _refresh_expiries
from candly.data.ingest import ingest
from candly.data.sources import SourceError, fyers
from candly.jobs import sync
from candly.news.fetch import poll_news as _poll_news

log = logging.getLogger(__name__)

INTRADAY_TFS = ("5m", "15m", "1h")
_JOB_DEFAULTS = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 120}
_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()
_blocked_by: str | None = None  # exception class that is currently stopping every ingest run
_blocked_lock = threading.Lock()


def _note_blocked(tf: str, exc: SourceError) -> None:
    """Warn once when ingest stops (e.g. Fyers not logged in), not on every 5-minute run."""
    global _blocked_by
    kind = type(exc).__name__
    with _blocked_lock:
        first = _blocked_by != kind
        _blocked_by = kind
    if first:
        log.warning("ingest paused: %s (repeats are logged at debug level until it resumes)", exc)
    else:
        log.debug("ingest %s skipped: %s", tf, exc)


def _note_resumed() -> None:
    global _blocked_by
    with _blocked_lock:
        was_blocked, _blocked_by = _blocked_by is not None, None
    if was_blocked:
        log.info("ingest resumed")


def ingest_incremental(tf: str, exchanges: tuple[str, ...] | None = None) -> dict[str, int]:
    """Incremental update of `tf` for every watchlist instrument (optionally only some exchanges).
    Skipped while the Fyers sync runs: it is fetching the same series, and it archives old ones first."""
    if sync.is_running():
        log.debug("ingest %s skipped: the Fyers sync is running", tf)
        return {}
    ids = [
        i.id
        for i in load_watchlist()
        if tf in i.timeframes and (exchanges is None or i.exchange in exchanges)
    ]
    if not ids:
        return {}
    try:
        counts = ingest(tf, instruments=ids)
    except SourceError as exc:
        _note_blocked(tf, exc)
        return {}
    _note_resumed()
    return counts


def exchanges_with_closed_bar(now: pd.Timestamp) -> tuple[str, ...]:
    """Exchanges whose 5m bar ending at the latest 5-minute boundary was inside the session."""
    boundary = now.floor("5min")
    cal = get_calendar()
    return tuple(ex for ex in EXCHANGES if cal.is_open(ex, boundary - pd.Timedelta(seconds=1)))


def intraday_cycle() -> None:
    """Runs shortly after every 5m close: 5m first, then 15m and 1h (resampled locally on Fyers)."""
    exchanges = exchanges_with_closed_bar(clock.utc_now())
    if not exchanges:
        return
    for tf in INTRADAY_TFS:
        ingest_incremental(tf, exchanges)


def daily_ingest(exchanges: tuple[str, ...]) -> None:
    ingest_incremental("1D", exchanges)


def poll_news() -> int:
    try:
        return _poll_news()
    except Exception:
        log.exception("news poll failed")
        return 0


def refresh_expiries() -> dict | None:
    try:
        return _refresh_expiries()
    except Exception:
        log.exception("expiry refresh failed")
        return None


def refresh_fyers_session() -> None:
    """Swap an expired access token for a new one via the refresh token, before the market opens."""
    if not get_settings().has_fyers:
        return
    try:
        fyers.ensure_token()
    except SourceError as exc:
        log.warning("Fyers session not refreshed: %s", exc)


def resume_fyers_sync() -> None:
    """At startup: resume a Fyers sync that failed or that the last server stopped in the middle of."""
    try:
        if sync.resume_if_unfinished():
            log.info("unfinished Fyers sync resumed at scheduler start")
    except Exception:
        log.exception("could not resume the Fyers sync")


def get_scheduler() -> BackgroundScheduler:
    """The process-wide scheduler with the platform jobs registered (not started)."""
    global _scheduler
    with _lock:
        if _scheduler is None:
            scheduler = BackgroundScheduler(timezone=IST, job_defaults=_JOB_DEFAULTS)
            scheduler.add_job(poll_news, IntervalTrigger(minutes=5, timezone=IST), id="poll_news")
            scheduler.add_job(
                intraday_cycle,
                CronTrigger(day_of_week="mon-fri", hour="9-23", minute="*/5", second=30, timezone=IST),
                id="intraday_ingest",
            )
            scheduler.add_job(
                daily_ingest,
                CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=IST),
                args=[("NSE", "BSE")],
                id="daily_ingest_nse_bse",
            )
            # MCX's session ends at 23:30/23:55 IST, so its daily bar is ingested just after midnight.
            scheduler.add_job(
                daily_ingest,
                CronTrigger(day_of_week="tue-sat", hour=0, minute=10, timezone=IST),
                args=[("MCX",)],
                id="daily_ingest_mcx",
            )
            # Before the open, pick up daily closes a source published late, so "previous day" levels
            # never come from an older session (acceptance report F1). Pure ingest: no forecasts.
            scheduler.add_job(
                daily_ingest,
                CronTrigger(day_of_week="mon-fri", hour=8, minute=40, timezone=IST),
                args=[EXCHANGES],
                id="daily_ingest_catchup",
            )
            scheduler.add_job(
                refresh_fyers_session,
                CronTrigger(hour=6, minute=5, timezone=IST),
                id="refresh_fyers_session",
            )
            # Every day, weekends included: expiry circulars come out on any day.
            scheduler.add_job(
                refresh_expiries,
                CronTrigger(hour=8, minute=30, timezone=IST),
                id="refresh_expiries",
            )
            _scheduler = scheduler
        return _scheduler


def start_scheduler() -> BackgroundScheduler:
    """Starts the jobs, runs the expiry refresh once right away (then daily at 08:30 IST), and resumes
    or starts the Fyers sync if needed (a one-off job, so startup never waits on Fyers)."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        if scheduler.get_job("refresh_expiries") is not None:
            scheduler.modify_job("refresh_expiries", next_run_time=datetime.now(IST))
        scheduler.add_job(resume_fyers_sync, id="resume_fyers_sync", replace_existing=True)
        log.info("scheduler started with jobs: %s", [job.id for job in scheduler.get_jobs()])
    return scheduler


def stop_scheduler(wait: bool = False) -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.running:
            _scheduler.shutdown(wait=wait)
        _scheduler = None
