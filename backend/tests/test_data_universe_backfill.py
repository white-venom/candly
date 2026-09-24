import json
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST, get_calendar
from candly.core.instruments import Instrument
from candly.core.schema import empty_candles
from candly.data import clock, ingest, universe_backfill
from candly.data.sources import SourceError, fyers
from candly.data.store import save_candles, series_lock, series_stats

cal = get_calendar()
NOW = pd.Timestamp(datetime(2026, 9, 23, 16, 0), tz=IST).tz_convert("UTC")  # after the NSE close
SINCE = date(2026, 9, 1)


def session_opens(start: date, end: date) -> list[pd.Timestamp]:
    days = [d.date() for d in pd.date_range(start, end) if cal.is_trading_day("NSE", d.date())]
    return [cal.session_times("NSE", d)[0] for d in days]


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


ALL_DAYS = session_opens(SINCE, date(2026, 9, 23))
FIVE_MINUTE = [ts for d in ALL_DAYS[-2:] for ts in cal.expected_bar_opens("NSE", cal.local_date(d), "5m")]


class FakeFyers:
    """Fyers stand-in: daily bars for 1-23 Sep and 5m bars for 22-23 Sep, from the requested start."""

    def __init__(self):
        self.calls: list[tuple[str, str, pd.Timestamp]] = []
        self.fail: set[str] = set()
        self.empty: set[str] = set()
        self.disconnect: set[str] = set()

    def __call__(self, source, instrument, tf, start=None, end=None, *, include_forming=False):
        assert source == "fyers"
        self.calls.append((instrument.id, tf, start))
        if instrument.id in self.disconnect:
            raise fyers.FyersNotConnected()
        if instrument.id in self.fail:
            raise SourceError("boom")
        if instrument.id in self.empty:
            return empty_candles()
        df = bars(ALL_DAYS if tf == "1D" else FIVE_MINUTE)
        return df[df["ts"] >= start].reset_index(drop=True) if start is not None else df


def stock(symbol: str) -> Instrument:
    sources = {"fyers": f"NSE:{symbol}-EQ"}
    return Instrument(id=f"NSE:{symbol}", name=symbol, kind="equity", timeframes=("1D",), sources=sources)


@pytest.fixture
def fake(monkeypatch, tmp_data_dir):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    monkeypatch.setattr(fyers, "ensure_token", lambda: None)
    monkeypatch.setattr(universe_backfill, "_session_expiring", lambda: False)
    source = FakeFyers()
    monkeypatch.setattr(ingest, "fetch_candles", source)
    return source


@pytest.fixture
def cli(fake, monkeypatch):
    """main() on a universe named "test"; returns the list of rate limits it set."""
    universe: list[Instrument] = []
    monkeypatch.setattr(universe_backfill, "load_universe", {"test": universe}.__getitem__)
    monkeypatch.setattr(universe_backfill, "_console_logging", lambda: None)
    limits: list[int] = []
    monkeypatch.setattr(fyers, "set_rate_limit", limits.append)
    return universe, limits


def test_skips_current_tops_up_stale_and_backfills_missing_then_resumes(fake):
    save_candles("NSE:AAA", "1D", bars(ALL_DAYS))
    save_candles("NSE:BBB", "1D", bars(session_opens(SINCE, date(2026, 9, 10))))
    universe = [stock(s) for s in ("AAA", "BBB", "CCC", "BAD", "NEW")]
    fake.fail, fake.empty = {"NSE:BAD"}, {"NSE:NEW"}
    progress = universe_backfill.progress_path("test", "1D")

    outcomes = universe_backfill.backfill_universe(universe, "1D", SINCE, progress)
    assert outcomes == {"current": 1, "done": 2, "failed": 1, "empty": 1}
    since = clock.ist_midnight(SINCE)
    assert [(i, start) for i, _, start in fake.calls] == [
        ("NSE:BBB", clock.ist_midnight(date(2026, 9, 10))),  # topped up from its last stored session
        ("NSE:CCC", since),
        ("NSE:BAD", since),
        ("NSE:NEW", since),
    ]
    assert series_stats("NSE:CCC", "1D")["bars"] == series_stats("NSE:BBB", "1D")["bars"] == len(ALL_DAYS)
    records = {r["id"]: r for r in universe_backfill.read_progress(progress)}
    assert {i: r["status"] for i, r in records.items()} == {
        "NSE:BBB": "done", "NSE:CCC": "done", "NSE:BAD": "failed", "NSE:NEW": "empty"
    }
    assert records["NSE:BAD"]["error"] == "boom"
    assert records["NSE:CCC"]["rows"] == len(ALL_DAYS)
    assert records["NSE:CCC"]["first"] == clock.epoch_seconds(ALL_DAYS[0])

    report = universe_backfill.status_report("test", "1D", universe)
    assert "test 1D backfill: not running" in report
    assert "done      3/5 stored (3 up to the latest closed bar)" in report
    assert "failed    1" in report and "empty     1" in report and "pending   0" in report
    assert "  NSE:BAD  boom" in report and "  NSE:NEW" in report

    # A rerun sends no request for anything already stored; the failed and empty ones are retried.
    fake.calls.clear()
    fake.fail.clear()
    outcomes = universe_backfill.backfill_universe(universe, "1D", SINCE, progress)
    assert outcomes == {"current": 3, "done": 1, "empty": 1}
    assert [c[0] for c in fake.calls] == ["NSE:BAD", "NSE:NEW"]


def test_progress_skips_a_torn_last_line(tmp_path):
    path = tmp_path / "p.jsonl"
    done = {"id": "NSE:A", "status": "done", "at": 1}
    path.write_text(json.dumps(done) + '\n{"id": "NSE:B", "sta', encoding="utf-8")
    assert universe_backfill.read_progress(path) == [done]


def test_cli_stops_when_the_fyers_session_is_gone(cli, fake):
    universe, limits = cli
    universe += [stock("CCC"), stock("DIS"), stock("DDD")]
    fake.disconnect = {"NSE:DIS"}
    assert universe_backfill.main(["--universe", "test"]) == 2
    assert limits == [universe_backfill.PER_MINUTE]
    assert [c[0] for c in fake.calls] == ["NSE:CCC", "NSE:DIS"]
    assert series_stats("NSE:CCC", "1D")["bars"] == len(ALL_DAYS)
    assert "pending   2" in universe_backfill.status_report("test", "1D", universe)


def test_stops_after_too_many_failures_in_a_row(fake, monkeypatch):
    monkeypatch.setattr(universe_backfill, "MAX_FAILURES_IN_A_ROW", 2)
    universe = [stock(s) for s in ("F1", "F2", "F3")]
    fake.fail = {i.id for i in universe}
    progress = universe_backfill.progress_path("test", "1D")
    outcomes = universe_backfill.backfill_universe(universe, "1D", SINCE, progress)
    assert outcomes == {"failed": 2, "stopped": 1}
    assert [c[0] for c in fake.calls] == ["NSE:F1", "NSE:F2"]


def test_stops_before_the_fyers_session_expires_instead_of_refreshing_it(fake, monkeypatch):
    checks = iter([False, True])
    monkeypatch.setattr(universe_backfill, "_session_expiring", lambda: next(checks))
    save_candles("NSE:AAA", "1D", bars(ALL_DAYS))
    universe = [stock(s) for s in ("AAA", "CCC", "DDD")]
    progress = universe_backfill.progress_path("test", "1D")
    outcomes = universe_backfill.backfill_universe(universe, "1D", SINCE, progress)
    assert outcomes == {"current": 1, "done": 1, "stopped": 1}  # a stored series needs no session check
    assert [c[0] for c in fake.calls] == ["NSE:CCC"]


def test_session_expiring_reads_the_stored_token_without_refreshing(monkeypatch):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    monkeypatch.setattr(fyers, "ensure_token", lambda **_: pytest.fail("must not refresh"))

    def token(minutes_left: int) -> fyers.FyersToken:
        expires = clock.epoch_seconds(NOW + pd.Timedelta(minutes=minutes_left))
        return fyers.FyersToken("APP", "access", "refresh", 0, expires, expires + 86400)

    for stored, expiring in ((token(30), False), (token(4), True), (None, True)):
        monkeypatch.setattr(fyers, "load_token", lambda stored=stored: stored)
        assert universe_backfill._session_expiring() is expiring


def test_one_backfill_per_universe_and_status_shows_it_running(cli, fake, capsys):
    universe, _ = cli
    universe += [stock("CCC"), stock("DDD")]
    progress = universe_backfill.progress_path("test", "1D")
    progress.parent.mkdir(parents=True, exist_ok=True)
    ats = [clock.epoch_seconds(NOW - pd.Timedelta(minutes=m)) for m in (10, 5)]
    progress.write_text("".join(json.dumps({"at": at, "id": "NSE:X", "status": "done"}) + "\n" for at in ats))
    with series_lock(universe_backfill._lock_path("test", "1D")):
        assert universe_backfill.main(["--universe", "test"]) == 2
        assert "already running" in capsys.readouterr().err
        assert universe_backfill.main(["--universe", "test", "--status"]) == 0
        report = capsys.readouterr().out
    assert fake.calls == []
    assert "RUNNING" in report and "pending   2" in report
    assert "rate      0.2 instruments/min over the last 10 min; ETA 2026-09-23 16:10:00 IST" in report
    assert universe_backfill.main(["--universe", "test"]) == 0
    assert [c[0] for c in fake.calls] == ["NSE:CCC", "NSE:DDD"]


def test_cli_rejects_a_rate_above_the_default_limiter(cli):
    with pytest.raises(SystemExit):
        universe_backfill.main(["--universe", "test", "--max-per-minute", "500"])


def test_5m_backfill_of_a_daily_universe_adds_the_timeframe_and_skips_indices(cli, fake, capsys):
    universe, _ = cli
    sources = {"fyers": "NSE:NIFTY50-INDEX"}
    index = Instrument(id="NSE:NIFTY50", name="Nifty 50", kind="index", timeframes=("1D",), sources=sources)
    universe += [index, stock("CCC"), stock("DDD")]
    save_candles("NSE:DDD", "5m", bars(FIVE_MINUTE))
    assert universe_backfill.main(["--universe", "test", "--tf", "5m", "--equities-only"]) == 0
    assert fake.calls == [("NSE:CCC", "5m", clock.ist_midnight(universe_backfill.SINCE["5m"]))]
    assert series_stats("NSE:CCC", "5m")["bars"] == len(FIVE_MINUTE)
    assert series_stats("NSE:CCC", "1D")["bars"] == 0
    records = universe_backfill.read_progress(universe_backfill.progress_path("test", "5m"))
    assert [r["id"] for r in records] == ["NSE:CCC"]
    assert universe_backfill.main(["--universe", "test", "--tf", "5m", "--equities-only", "--status"]) == 0
    assert "test 5m backfill: not running\n  done      2/2 stored" in capsys.readouterr().out
