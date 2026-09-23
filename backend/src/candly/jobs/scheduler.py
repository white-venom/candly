"""Background jobs. Nothing starts on import: call start_scheduler() from the app's lifespan.

The lead adds quant jobs with get_scheduler().add_job(...), e.g. run_forecast_cycle after the intraday
ingest, or rebuild_scorecard nightly.
"""

import logging
import threading

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from candly.core.calendar import IST, get_calendar
from candly.core.instruments import EXCHANGES, load_watchlist
from candly.core.settings import get_settings
from candly.data import clock
from candly.data.ingest import ingest
from candly.data.sources import SourceError, fyers
from candly.news.fetch import poll_news as _poll_news

log = logging.getLogger(__name__)

INTRADAY_TFS = ("5m", "15m", "1h")
_JOB_DEFAULTS = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 120}
_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()


def ingest_incremental(tf: str, exchanges: tuple[str, ...] | None = None) -> dict[str, int]:
    """Incremental update of `tf` for every watchlist instrument (optionally only some exchanges)."""
    ids = [
        i.id
        for i in load_watchlist()
        if tf in i.timeframes and (exchanges is None or i.exchange in exchanges)
    ]
    if not ids:
        return {}
    try:
        return ingest(tf, instruments=ids)
    except SourceError as exc:
        log.warning("ingest %s skipped: %s", tf, exc)
        return {}


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


def refresh_fyers_session() -> None:
    """Swap an expired access token for a new one via the refresh token, before the market opens."""
    if not get_settings().has_fyers:
        return
    try:
        fyers.ensure_token()
    except SourceError as exc:
        log.warning("Fyers session not refreshed: %s", exc)


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
            scheduler.add_job(
                refresh_fyers_session,
                CronTrigger(hour=6, minute=5, timezone=IST),
                id="refresh_fyers_session",
            )
            _scheduler = scheduler
        return _scheduler


def start_scheduler() -> BackgroundScheduler:
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        log.info("scheduler started with jobs: %s", [job.id for job in scheduler.get_jobs()])
    return scheduler


def stop_scheduler(wait: bool = False) -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.running:
            _scheduler.shutdown(wait=wait)
        _scheduler = None
