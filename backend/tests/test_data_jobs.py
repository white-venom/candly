import logging
from datetime import datetime

import pandas as pd
import pytest

from candly.core.calendar import IST
from candly.core.instruments import load_watchlist
from candly.data import clock
from candly.data.sources import fyers
from candly.jobs import scheduler


def ist(y, m, d, hh, mm, ss=0) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm, ss), tz=IST).tz_convert("UTC")


def ids(*exchanges: str) -> list[str]:
    return [i.id for i in load_watchlist() if i.exchange in exchanges]


@pytest.fixture
def fake_ingest(monkeypatch):
    """Records (tf, instrument ids) for every ingest call the jobs make."""
    calls: list[tuple[str, list[str]]] = []

    def record(tf, instruments):
        calls.append((tf, list(instruments)))
        return dict.fromkeys(instruments, 0)

    monkeypatch.setattr(scheduler, "ingest", record)
    monkeypatch.setattr(scheduler, "_blocked_by", None)
    return calls


@pytest.mark.parametrize(
    ("now", "exchanges"),
    [
        (ist(2026, 9, 23, 10, 0, 30), ("NSE", "BSE", "MCX")),  # Wednesday morning: everything is open
        (ist(2026, 9, 23, 15, 30, 30), ("NSE", "BSE", "MCX")),  # the NSE/BSE 15:25 bar just closed
        (ist(2026, 9, 23, 18, 0, 30), ("MCX",)),  # evening: MCX only
        (ist(2026, 9, 14, 11, 0, 30), ()),  # Ganesh Chaturthi: all three closed
        (ist(2026, 9, 26, 11, 0, 30), ()),  # Saturday
    ],
)
def test_intraday_cycle_ingests_each_timeframe_for_open_exchanges(fake_ingest, monkeypatch, now, exchanges):
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    scheduler.intraday_cycle()
    expected = [(tf, ids(*exchanges)) for tf in ("5m", "15m", "1h")] if exchanges else []
    assert fake_ingest == expected


def test_daily_ingest_runs_only_the_given_exchanges(fake_ingest):
    scheduler.daily_ingest(("NSE", "BSE"))
    scheduler.daily_ingest(("MCX",))
    assert fake_ingest == [("1D", ids("NSE", "BSE")), ("1D", ids("MCX"))]
    assert "BSE:SENSEX" in fake_ingest[0][1] and "MCX:GOLD" not in fake_ingest[0][1]


def test_a_blocked_ingest_is_logged_once_until_it_resumes(fake_ingest, monkeypatch, caplog):
    def not_connected(tf, instruments):
        raise fyers.FyersNotConnected()

    monkeypatch.setattr(scheduler, "ingest", not_connected)
    with caplog.at_level(logging.DEBUG, logger="candly.jobs.scheduler"):
        for _ in range(4):
            assert scheduler.ingest_incremental("5m", ("NSE",)) == {}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "Connect Fyers" in warnings[0].getMessage()

    caplog.clear()
    monkeypatch.setattr(scheduler, "ingest", lambda tf, instruments: {})
    with caplog.at_level(logging.INFO, logger="candly.jobs.scheduler"):
        scheduler.ingest_incremental("5m", ("NSE",))
        monkeypatch.setattr(scheduler, "ingest", not_connected)
        scheduler.ingest_incremental("5m", ("NSE",))
    assert [r.levelname for r in caplog.records] == ["INFO", "WARNING"]  # resumed, then blocked again


def test_exchanges_with_closed_bar():
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 23, 10, 0, 30)) == ("NSE", "BSE", "MCX")
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 23, 15, 30, 30)) == ("NSE", "BSE", "MCX")
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 23, 15, 35, 30)) == ("MCX",)
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 23, 23, 30, 30)) == ("MCX",)
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 23, 23, 35, 30)) == ()
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 23, 9, 15, 30)) == ("MCX",)
    assert scheduler.exchanges_with_closed_bar(ist(2026, 9, 26, 11, 0, 30)) == ()  # Saturday


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
