import json
import threading
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST, get_calendar, reload_calendar
from candly.core.settings import get_settings
from candly.data import clock, ingest
from candly.data.holidays import derive_observed_holidays, holidays_path, observed_holidays
from candly.data.sources import fyers
from candly.data.store import candle_source, save_candles, series_stats
from candly.jobs import sync

NOW = pd.Timestamp(datetime(2026, 9, 23, 16, 0), tz=IST).tz_convert("UTC")  # Wednesday, after the NSE close
ALL_STEPS = [
    "refresh_expiries", "archive", "backfill_1d", "backfill_5m", "build_15m_1h",
    "fill_1d_gaps", "quality_report", "holidays", "scorecards",
]


def bars(opens: list[pd.Timestamp]) -> pd.DataFrame:
    n = len(opens)
    base = np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "ts": pd.DatetimeIndex(opens),
            "open": 100 + base,
            "high": 102 + base,
            "low": 99 + base,
            "close": 101 + base,
            "volume": np.full(n, 10.0),
            "oi": np.nan,
        }
    )


def daily(exchange: str, days: list[date]) -> pd.DataFrame:
    cal = get_calendar()
    return bars([cal.session_times(exchange, d)[0] for d in days])


@pytest.fixture
def connected(monkeypatch, fake_fyers_keys, tmp_data_dir):
    """Fyers keys plus a stored session valid for weeks, with the clock frozen at NOW."""
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    now = clock.epoch_seconds(NOW)
    fyers.save_token(fyers.FyersToken("TESTAPP-100", "acc", "ref", now, now + 30 * 86400, now + 30 * 86400))
    return tmp_data_dir


def recording_step(calls: list[str], name: str):
    def step(run):
        calls.append(name)
        run.report(0.5, f"{name} halfway")

    return step


@pytest.fixture
def fake_steps(monkeypatch):
    """Every step replaced by one that records its name."""
    calls: list[str] = []
    for name in list(sync.STEPS):
        monkeypatch.setitem(sync.STEPS, name, recording_step(calls, name))
    return calls


@pytest.fixture
def fresh_calendar():
    yield
    reload_calendar()  # drop a calendar built from a test's holidays file


def wait_until_idle(timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while sync.is_running():
        assert time.monotonic() < deadline, "sync thread did not finish"
        time.sleep(0.01)


@pytest.fixture(autouse=True)
def no_leaked_sync():
    """A sync thread that outlives its test would write into the next test's (or the real) data dir."""
    assert not sync.is_running()
    yield
    wait_until_idle()


def test_steps_run_in_order(connected, fake_steps):
    state = sync.run_fyers_sync()
    assert fake_steps == ALL_STEPS == list(sync.STEPS)
    assert state["status"] == "done" and state["progress"] == 1.0 and state["error"] is None
    assert state["started_at"] == state["finished_at"] == clock.epoch_seconds(NOW)
    status = sync.get_sync_status()
    assert status["completed_steps"] == ALL_STEPS and status["steps"] == ALL_STEPS
    assert status["run_date"] == "2026-09-23" and status["message"] == "Fyers data is ready"
    saved = json.loads((connected / "sync" / "state.json").read_text(encoding="utf-8"))
    assert saved["status"] == "done" and saved["completed_steps"] == ALL_STEPS


def test_weights_cover_every_step():
    assert set(sync.WEIGHTS) == set(sync.STEPS)
    assert sum(sync.WEIGHTS.values()) == pytest.approx(1.0)


def test_a_failed_run_resumes_at_the_failed_step(connected, fake_steps, monkeypatch):
    def broken(run):
        raise RuntimeError("disk full")

    monkeypatch.setitem(sync.STEPS, "backfill_5m", broken)
    state = sync.run_fyers_sync()
    assert state["status"] == "error" and state["error"] == "backfill_5m failed: disk full"
    assert state["step"] == "backfill_5m" and state["completed_steps"] == ALL_STEPS[:3]
    assert state["progress"] == pytest.approx(0.25)  # the first three steps' share
    assert fake_steps == ALL_STEPS[:3]

    fake_steps.clear()
    monkeypatch.setitem(sync.STEPS, "backfill_5m", recording_step(fake_steps, "backfill_5m"))
    assert sync.run_fyers_sync()["status"] == "done"
    assert fake_steps == ALL_STEPS[3:]

    fake_steps.clear()
    assert sync.run_fyers_sync()["status"] == "done"
    assert fake_steps == ALL_STEPS  # a finished run is never resumed: a new one starts from the top


def test_an_unfinished_run_from_an_earlier_day_starts_over(connected, fake_steps, monkeypatch):
    monkeypatch.setitem(sync.STEPS, "holidays", lambda run: 1 / 0)
    assert sync.run_fyers_sync()["status"] == "error"
    fake_steps.clear()
    monkeypatch.setitem(sync.STEPS, "holidays", recording_step(fake_steps, "holidays"))
    monkeypatch.setattr(clock, "utc_now", lambda: NOW + timedelta(days=1))
    sync.run_fyers_sync()
    assert fake_steps == ALL_STEPS


def test_an_interrupted_run_reads_as_error_and_resumes(connected, fake_steps):
    path = sync.state_path()
    path.parent.mkdir(parents=True)
    stale = sync._idle() | {
        "status": "running", "step": "backfill_1d", "progress": 0.1, "run_date": "2026-09-23",
        "completed_steps": ["refresh_expiries", "archive"],
    }
    path.write_text(json.dumps(stale), encoding="utf-8")
    status = sync.get_sync_status()
    assert status["status"] == "error" and status["error"] == sync.INTERRUPTED
    assert sync.sync_needed()
    sync.run_fyers_sync()
    assert fake_steps == ALL_STEPS[2:]


def test_login_is_checked_before_anything_is_archived(fake_steps, fake_fyers_keys, monkeypatch):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    state = sync.run_fyers_sync()  # keys but no session
    assert fake_steps == []
    assert state["status"] == "error" and state["error"].startswith("Fyers login needed")
    assert "Connect Fyers" in state["error"] and state["completed_steps"] == []


def test_instrument_errors_are_counted_and_a_login_error_stops_the_run(connected, fake_steps, monkeypatch):
    monkeypatch.setitem(sync.STEPS, "backfill_1d", sync._step_backfill_1d)
    ids = [i.id for i in sync._targets("1D")]
    seen: list[str] = []
    healthy = {"NSE:TCS": False, "MCX:GOLD": False}

    def fake_backfill(inst, tf, source, start=None):
        assert (tf, source, start) == ("1D", "fyers", None)
        seen.append(inst.id)
        if inst.id == "NSE:TCS" and not healthy["NSE:TCS"]:
            raise fyers.FyersError("Fyers returned HTTP 500 after 4 retries")
        if inst.id == "MCX:GOLD" and not healthy["MCX:GOLD"]:
            raise fyers.FyersNotConnected("Fyers rejected the session")
        return 1

    monkeypatch.setattr(sync, "backfill", fake_backfill)
    state = sync.run_fyers_sync()
    gold = ids.index("MCX:GOLD")
    assert seen == ids[: gold + 1]
    assert state["status"] == "error"
    assert state["error"].startswith("Fyers login needed (Fyers rejected the session)")
    assert state["completed_steps"] == ["refresh_expiries", "archive"]
    assert state["failures"] == {"backfill_1d": {"NSE:TCS": "Fyers returned HTTP 500 after 4 retries"}}

    healthy.update({"NSE:TCS": True, "MCX:GOLD": True})
    seen.clear()
    state = sync.run_fyers_sync()
    assert seen == ["NSE:TCS", *ids[gold:]]  # done instruments are skipped, the failed one is retried
    assert state["status"] == "done" and state["failures"] == {}


def test_a_failing_instrument_does_not_fail_the_run(connected, fake_steps, monkeypatch):
    monkeypatch.setitem(sync.STEPS, "backfill_5m", sync._step_backfill_5m)

    def fake_backfill(inst, tf, source, start=None):
        if inst.id == "NSE:INFY":
            raise ValueError("bad rows")
        return 0

    monkeypatch.setattr(sync, "backfill", fake_backfill)
    state = sync.run_fyers_sync()
    assert state["status"] == "done" and state["failures"] == {"backfill_5m": {"NSE:INFY": "bad rows"}}
    assert state["message"] == "Fyers data is ready, with 1 failure (see failures and scorecards)"


def test_a_failing_scorecard_is_recorded_per_timeframe(connected, fake_steps, monkeypatch):
    from candly.research import scorecard

    monkeypatch.setitem(sync.STEPS, "scorecards", sync._step_scorecards)
    built: list[str] = []

    def fake_build(tf):
        built.append(tf)
        if tf == "1h":
            raise ValueError("no events")

    monkeypatch.setattr(scorecard, "build_scorecard", fake_build)
    state = sync.run_fyers_sync()
    assert built == ["1D", "1h", "15m", "5m"]
    assert state["status"] == "done"
    assert state["scorecards"] == {"1D": "ok", "1h": "failed: no events", "15m": "ok", "5m": "ok"}


def test_only_one_sync_runs_at_a_time(connected, fake_steps, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow(run):
        entered.set()
        release.wait(10)

    monkeypatch.setitem(sync.STEPS, "archive", slow)
    try:
        assert sync.start_fyers_sync() is True
        assert entered.wait(10)
        status = sync.get_sync_status()
        assert (status["status"], status["step"]) == ("running", "archive")
        assert sync.start_fyers_sync() is False
        with pytest.raises(sync.SyncRunning):
            sync.run_fyers_sync()
    finally:
        release.set()
        wait_until_idle()
    assert sync.get_sync_status()["status"] == "done"


def test_a_run_keeps_its_state_in_the_data_dir_it_started_in(connected, fake_steps, monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()

    def slow(run):
        entered.set()
        release.wait(10)

    monkeypatch.setitem(sync.STEPS, "archive", slow)
    try:
        assert sync.start_fyers_sync()
        assert entered.wait(10)
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "elsewhere"))
        get_settings.cache_clear()
    finally:
        release.set()
        wait_until_idle()
    assert not sync.state_path().exists()
    assert sync.get_sync_status()["status"] == "idle"
    saved = json.loads((connected / "sync" / "state.json").read_text(encoding="utf-8"))
    assert saved["status"] == "done"


def test_sync_needed(connected):
    assert sync.sync_needed()  # empty store: no Fyers 1D data
    for inst in sync.load_watchlist():
        save_candles(inst.id, "1D", daily(inst.exchange, [date(2026, 9, 22)]), source="fyers")
    assert not sync.sync_needed()
    save_candles("NSE:TCS", "1h", bars([pd.Timestamp("2026-09-22 03:45", tz="UTC")]), source="yahoo")
    assert sync.sync_needed()


# --- holidays --------------------------------------------------------------------------------


def test_observed_holidays_skip_weekends_and_special_sessions():
    days = [date(2026, 9, d) for d in (7, 8, 10, 11, 12, 14, 16, 17, 18)]  # 12th is a Saturday session
    ts = daily("NSE", days)["ts"]
    special = {date(2026, 9, 12), date(2026, 9, 15)}  # a weekend session, and a weekday one with no bar
    assert observed_holidays(ts, special) == [date(2026, 9, 9)]
    assert observed_holidays(ts, set()) == [date(2026, 9, 9), date(2026, 9, 15)]
    assert observed_holidays(ts.iloc[:0], special) == []


def test_holiday_step_writes_the_file_and_reloads_the_calendar(
    connected, fake_steps, monkeypatch, fresh_calendar
):
    monkeypatch.setitem(sync.STEPS, "holidays", sync._step_holidays)
    # 2024-01-20 (Saturday) is a configured special session; the 22nd and 26th have no bar.
    days = [date(2024, 1, d) for d in (15, 16, 17, 18, 19, 20, 23, 24, 25)]
    save_candles("NSE:NIFTY50", "1D", daily("NSE", days), source="fyers")
    save_candles("BSE:SENSEX", "1D", daily("BSE", [date(2024, 1, 15), date(2024, 1, 17)]), source="yahoo")
    save_candles("NSE:NIFTY50", "1D", daily("NSE", [date(2024, 1, 29)]), source="fyers")
    assert get_calendar().is_trading_day("NSE", date(2024, 1, 26))

    state = sync.run_fyers_sync()
    written = json.loads(holidays_path().read_text(encoding="utf-8"))
    assert written == {"NSE": ["2024-01-22", "2024-01-26"], "BSE": [], "MCX": []}  # Yahoo SENSEX is ignored
    assert state["summary"]["holidays"] == {"NSE": 2, "BSE": 0, "MCX": 0}
    assert not get_calendar().is_trading_day("NSE", date(2024, 1, 26))
    assert get_calendar().is_trading_day("BSE", date(2024, 1, 26))


def test_holidays_keep_an_exchange_whose_reference_has_no_fyers_data(connected, fresh_calendar):
    holidays_path().parent.mkdir(parents=True)
    holidays_path().write_text(json.dumps({"MCX": ["2024-03-08"]}), encoding="utf-8")
    assert derive_observed_holidays() == {"NSE": [], "BSE": [], "MCX": ["2024-03-08"]}


# --- the real steps, end to end ----------------------------------------------------------------


class FakeFyers:
    """Daily bars for 10-23 Sep 2026 and 5m bars for 22-23 Sep, filtered to [start, end)."""

    def __init__(self):
        self.calls: list[tuple[str, str, pd.Timestamp]] = []

    def __call__(self, source, instrument, tf, start=None, end=None, *, include_forming=False):
        assert source == "fyers"
        self.calls.append((instrument.id, tf, start))
        cal, ex = get_calendar(), instrument.exchange
        if tf == "1D":
            days = [d.date() for d in pd.date_range("2026-09-10", "2026-09-23")]
            opens = [cal.session_times(ex, d)[0] for d in days if cal.is_trading_day(ex, d)]
        else:
            days = [date(2026, 9, 22), date(2026, 9, 23)]
            opens = [ts for d in days for ts in cal.expected_bar_opens(ex, d, tf)]
        df = bars(opens)
        df = df[(df["ts"] >= start) & (df["ts"] < end)] if end is not None else df[df["ts"] >= start]
        return df.reset_index(drop=True)


def test_full_sync_with_the_real_steps(connected, monkeypatch, fresh_calendar):
    from candly.research import scorecard

    source = FakeFyers()
    monkeypatch.setattr(ingest, "fetch_candles", source)
    expiries = {"downloads": {"NSE_FO": "ok", "MCX_COM": "failed: 503"}}
    monkeypatch.setattr(sync, "refresh_expiries", lambda: expiries)
    built: list[str] = []
    monkeypatch.setattr(scorecard, "build_scorecard", built.append)
    save_candles("NSE:RELIANCE", "1D", daily("NSE", [date(2026, 9, 1)]), source="yahoo")
    save_candles("NSE:RELIANCE", "1h", bars([pd.Timestamp("2026-09-01 03:45", tz="UTC")]), source="yahoo")

    state = sync.run_fyers_sync()
    assert state["status"] == "done", state["error"]
    assert state["completed_steps"] == ALL_STEPS
    assert state["failures"] == {"refresh_expiries": {"MCX_COM": "failed: 503"}}
    assert state["summary"]["archived"] == 2
    archived = list((connected / "archive").glob("yahoo-*/NSE/*/RELIANCE.parquet"))
    assert len(archived) == 2

    backfill_starts = [start for inst, tf, start in source.calls if inst == "NSE:RELIANCE" and tf == "1D"]
    assert backfill_starts[0] == clock.ist_midnight(date(2005, 1, 1))
    assert backfill_starts[1] == backfill_starts[0] + pd.Timedelta(days=365)
    five = [start for inst, tf, start in source.calls if inst == "NSE:RELIANCE" and tf == "5m"]
    assert five[0] == clock.ist_midnight(date(2017, 7, 3))
    assert not [call for call in source.calls if call[1] in ("15m", "1h")]  # resampled, never fetched

    for tf, n in (("1D", 9), ("5m", 150), ("15m", 50), ("1h", 14)):
        assert series_stats("NSE:RELIANCE", tf)["bars"] == n, tf
        assert candle_source("NSE:RELIANCE", tf) == "fyers"
    assert series_stats("NSE:RELIANCE", "1D")["first"] == pd.Timestamp("2026-09-10 03:45", tz="UTC")

    assert (connected / "quality" / "report.json").exists()
    assert state["summary"]["quality"]["instruments_with_data"] == 18
    observed = json.loads(holidays_path().read_text(encoding="utf-8"))
    assert observed["NSE"] == ["2026-09-14"]  # Ganesh Chaturthi
    assert built == ["1D", "1h", "15m", "5m"]
    assert state["scorecards"] == dict.fromkeys(built, "ok")
    assert not sync.sync_needed()
