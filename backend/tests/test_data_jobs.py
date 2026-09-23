from datetime import datetime

import pandas as pd

from candly.core.calendar import IST
from candly.data import clock
from candly.jobs import scheduler


def ist(y, m, d, hh, mm, ss=0) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm, ss), tz=IST).tz_convert("UTC")


def test_exchanges_with_closed_bar():
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 23, 10, 0, 30)) == ("NSE", "BSE", "MCX")
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 23, 15, 30, 30)) == ("NSE", "BSE", "MCX")
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 23, 15, 35, 30)) == ("MCX",)
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 23, 23, 30, 30)) == ("MCX",)
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 23, 23, 35, 30)) == ()
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 23, 9, 15, 30)) == ("MCX",)
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 26, 11, 0, 30)) == ()  # Saturday


def test_intraday_cycle_ingests_open_exchanges(monkeypatch):
    calls = []
    monkeypatch.setattr(clock, "utc_now", lambda: ist(2026, 9, 23, 16, 0, 30))
    monkeypatch.setattr(scheduler, "ingest_incremental", lambda tf, exchanges: calls.append((tf, exchanges)))
    scheduler.intraday_cycle()
    assert calls == [("5m", ("MCX",)), ("15m", ("MCX",)), ("1h", ("MCX",))]


def test_ingest_incremental_filters_by_exchange(monkeypatch):
    seen = {}

    def fake_ingest(tf, instruments):
        seen[tf] = instruments
        return {}

    monkeypatch.setattr(scheduler, "ingest", fake_ingest)
    scheduler.ingest_incremental("1D", ("MCX",))
    assert seen["1D"] == ["MCX:CRUDEOIL", "MCX:NATURALGAS", "MCX:GOLD", "MCX:SILVER"]


def test_scheduler_is_not_started_on_import_and_registers_jobs():
    scheduler.stop_scheduler()
    assert scheduler._scheduler is None
    sched = scheduler.get_scheduler()
    try:
        assert not sched.running
        assert {job.id for job in sched.get_jobs()} == {
            "poll_news",
            "intraday_ingest",
            "daily_ingest_nse_bse",
            "daily_ingest_mcx",
            "refresh_fyers_session",
        }
        assert scheduler.get_scheduler() is sched
    finally:
        scheduler.stop_scheduler()


def test_news_job_never_raises(monkeypatch):
    def broken():
        raise RuntimeError("feed parser exploded")

    monkeypatch.setattr(scheduler, "_poll_news", broken)
    assert scheduler.poll_news() == 0
