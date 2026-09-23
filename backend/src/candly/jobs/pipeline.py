"""Chains ingest -> forecast -> grade so forecasts are always made on freshly closed bars."""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from candly.core.calendar import IST
from candly.core.instruments import load_watchlist
from candly.data import clock
from candly.forecast.jobs import grade_pending_job, rebuild_scorecard, run_forecast_cycle
from candly.jobs.scheduler import INTRADAY_TFS, exchanges_with_closed_bar, ingest_incremental

log = logging.getLogger(__name__)

SCORECARD_TFS = ("1D", "1h", "15m", "5m")


def _ids(tf: str, exchanges: tuple[str, ...]) -> list[str]:
    return [i.id for i in load_watchlist() if tf in i.timeframes and i.exchange in exchanges]


def _grade() -> None:
    try:
        grade_pending_job()
    except Exception:
        log.exception("grading failed")


def intraday_pipeline() -> None:
    exchanges = exchanges_with_closed_bar(clock.utc_now())
    if not exchanges:
        return
    for tf in INTRADAY_TFS:
        ingest_incremental(tf, exchanges)
        run_forecast_cycle(tf, _ids(tf, exchanges))
    _grade()


def daily_pipeline(exchanges: tuple[str, ...]) -> None:
    ingest_incremental("1D", exchanges)
    run_forecast_cycle("1D", _ids("1D", exchanges))
    _grade()


def nightly_scorecards() -> None:
    for tf in SCORECARD_TFS:
        try:
            rebuild_scorecard(tf)
        except Exception:
            log.exception("scorecard rebuild failed for %s", tf)


def register_pipeline_jobs(scheduler: BackgroundScheduler) -> None:
    # Same ids and triggers as the platform ingest jobs, so these replace them.
    scheduler.add_job(
        intraday_pipeline,
        CronTrigger(day_of_week="mon-fri", hour="9-23", minute="*/5", second=30, timezone=IST),
        id="intraday_ingest",
        replace_existing=True,
    )
    scheduler.add_job(
        daily_pipeline,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=IST),
        args=[("NSE", "BSE")],
        id="daily_ingest_nse_bse",
        replace_existing=True,
    )
    scheduler.add_job(
        daily_pipeline,
        CronTrigger(day_of_week="tue-sat", hour=0, minute=10, timezone=IST),
        args=[("MCX",)],
        id="daily_ingest_mcx",
        replace_existing=True,
    )
    scheduler.add_job(
        nightly_scorecards, CronTrigger(hour=2, minute=0, timezone=IST), id="nightly_scorecards"
    )
