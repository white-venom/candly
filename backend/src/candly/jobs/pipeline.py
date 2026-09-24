"""Ingest runs on its own every 5 minutes; forecasting, grading and alerts run as a separate job a minute
later, so a slow forecast cycle can never make candle downloads fall behind."""

import logging
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from candly import llm
from candly.alerts.jobs import register_alert_jobs, run_alert_checks
from candly.core.calendar import IST
from candly.core.instruments import EXCHANGES, load_watchlist
from candly.data import clock
from candly.forecast.jobs import grade_pending_job, rebuild_scorecard, run_forecast_cycle
from candly.jobs.scheduler import INTRADAY_TFS, exchanges_with_closed_bar, ingest_incremental

log = logging.getLogger(__name__)

SCORECARD_TFS = ("1D", "1h", "15m", "5m")
EXPLAINED_TFS = ("1D", "1h")  # 5m/15m would burn the daily explanation cap
FORECAST_DELAY = timedelta(minutes=1)


def _ids(tf: str, exchanges: tuple[str, ...]) -> list[str]:
    return [i.id for i in load_watchlist() if tf in i.timeframes and i.exchange in exchanges]


def _grade() -> None:
    try:
        grade_pending_job()
    except Exception:
        log.exception("grading failed")


def _explain(tf: str) -> None:
    if tf in EXPLAINED_TFS:
        llm.explain_recent_calls(limit=10, tf=tf)


def intraday_ingest() -> None:
    exchanges = exchanges_with_closed_bar(clock.utc_now())
    for tf in INTRADAY_TFS if exchanges else ():
        ingest_incremental(tf, exchanges)


def intraday_forecast() -> None:
    # Runs a minute after intraday_ingest, for the bar that closed at the last 5-minute boundary.
    exchanges = exchanges_with_closed_bar(clock.utc_now() - FORECAST_DELAY)
    if not exchanges:
        return
    for tf in INTRADAY_TFS:
        run_forecast_cycle(tf, _ids(tf, exchanges))
        _explain(tf)
    _grade()
    run_alert_checks()


def daily_pipeline(exchanges: tuple[str, ...]) -> None:
    ingest_incremental("1D", exchanges)
    run_forecast_cycle("1D", _ids("1D", exchanges))
    _explain("1D")
    _grade()
    run_alert_checks()


def tag_news() -> None:
    llm.tag_pending_news()


def nightly_scorecards() -> None:
    """Every timeframe's scorecard for each exchange (built separately: stats never mix exchanges)."""
    for tf in SCORECARD_TFS:
        for exchange in EXCHANGES:
            try:
                rebuild_scorecard(tf, exchange=exchange)
            except Exception:
                log.exception("scorecard rebuild failed for %s %s", tf, exchange)


def register_pipeline_jobs(scheduler: BackgroundScheduler) -> None:
    # Same ids and triggers as the platform ingest jobs, so these replace them.
    scheduler.add_job(
        intraday_ingest,
        CronTrigger(day_of_week="mon-fri", hour="9-23", minute="*/5", second=30, timezone=IST),
        id="intraday_ingest",
        replace_existing=True,
    )
    scheduler.add_job(
        intraday_forecast,
        CronTrigger(day_of_week="mon-fri", hour="9-23", minute="1-59/5", second=30, timezone=IST),
        id="intraday_forecast",
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
    # Runs between news polls; a no-op until ANTHROPIC_API_KEY is set.
    scheduler.add_job(tag_news, IntervalTrigger(minutes=5, start_date=None, timezone=IST), id="tag_news")
    register_alert_jobs(scheduler)
