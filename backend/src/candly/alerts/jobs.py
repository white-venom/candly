"""Scheduler entry points for alerts. None of them raises, and all do nothing without Telegram keys.

- `run_alert_checks()`: chain after every pipeline cycle (candly.jobs.pipeline).
- `send_pre_market_brief()` / `send_post_market_review()`: daily at the config times, via
  `register_alert_jobs(scheduler)`; they skip days the calendar says are not trading days.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pandas as pd
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger

from candly.alerts import telegram
from candly.alerts.briefs import post_market_review, pre_market_brief
from candly.alerts.config import load_alerts_config
from candly.alerts.delivery import AlertStore, deliver
from candly.alerts.rules import check_and_send
from candly.core.calendar import IST, get_calendar
from candly.core.settings import get_settings
from candly.data import clock

log = logging.getLogger(__name__)

NOT_CONFIGURED = "Telegram is not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"


def run_alert_checks() -> dict:
    try:
        return check_and_send()
    except Exception:
        log.exception("alert checks failed")
        return {"errors": 1}


def _send_brief(
    kind: str, build: Callable[[pd.Timestamp], str], now: pd.Timestamp | None, *, respect_quiet: bool
) -> bool:
    now = clock.utc_now() if now is None else clock.to_utc(now)
    cfg = load_alerts_config()
    if not (cfg.enabled and get_settings().has_telegram and getattr(cfg.types, kind)):
        return False
    cal = get_calendar()
    today = cal.local_date(now)
    if not cal.is_trading_day(cfg.briefs.calendar, today):
        return False
    store = AlertStore()
    key = f"{kind}:{today.isoformat()}"
    if store.seen(key):
        return False
    try:
        text = build(now)
    except Exception:
        log.exception("%s could not be built", kind)
        return False
    outcome = deliver(store, cfg, key, kind, text, now, respect_quiet=respect_quiet, capped=False)
    return outcome == "sent"


def send_pre_market_brief(now: pd.Timestamp | None = None) -> bool:
    return _send_brief("pre_market_brief", pre_market_brief, now, respect_quiet=False)


def send_post_market_review(now: pd.Timestamp | None = None) -> bool:
    return _send_brief("post_market_review", post_market_review, now, respect_quiet=True)


def send_test_alert() -> dict:
    """For a "send test alert" button: ignores the config switches, quiet hours and the hourly cap."""
    if not get_settings().has_telegram:
        return {"sent": False, "detail": NOT_CONFIGURED}
    sent = telegram.send("<b>candly</b>: test alert. Telegram alerts are working.")
    return {"sent": sent, "detail": None if sent else "Telegram did not accept the message; see the log"}


def register_alert_jobs(scheduler: BaseScheduler) -> None:
    """Adds the two briefs at their config times (IST), every day; each job checks the trading calendar."""
    times = load_alerts_config().briefs
    for job_id, func, at in (
        ("pre_market_brief", send_pre_market_brief, times.pre_market),
        ("post_market_review", send_post_market_review, times.post_market),
    ):
        scheduler.add_job(
            func, CronTrigger(hour=at.hour, minute=at.minute, timezone=IST), id=job_id, replace_existing=True
        )
