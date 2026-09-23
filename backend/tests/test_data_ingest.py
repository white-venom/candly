import logging
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST, get_calendar
from candly.core.settings import get_settings
from candly.data import clock, ingest
from candly.data.sources import SourceError, fyers
from candly.data.store import candle_source, load_candles, save_candles, series_stats

cal = get_calendar()
NOW = pd.Timestamp(datetime(2026, 9, 23, 16, 0), tz=IST).tz_convert("UTC")  # after the NSE close


def ist(y, m, d, hh, mm) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm), tz=IST).tz_convert("UTC")


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


def trading_days(exchange: str, start: date, end: date) -> list[date]:
    return [d.date() for d in pd.date_range(start, end) if cal.is_trading_day(exchange, d.date())]


class FakeSource:
    """Serves daily bars for 1-23 Sep (minus `hidden_days`) and 5m bars for 22-23 Sep, filtered by the
    requested start and end."""

    def __init__(self):
        self.calls: list[tuple[str, str, str, pd.Timestamp]] = []
        self.fail: set[str] = set()
        self.fail_starts: set[pd.Timestamp] = set()
        self.hidden_days: set[date] = set()
        self.close_bump: dict[date, float] = {}

    def __call__(self, source, instrument, tf, start=None, end=None, *, include_forming=False):
        self.calls.append((source, instrument.id, tf, start))
        if instrument.id in self.fail or start in self.fail_starts:
            raise SourceError("boom")
        ex = instrument.exchange
        if tf == "1D":
            days = trading_days(ex, date(2026, 9, 1), date(2026, 9, 23))
            opens = [cal.session_times(ex, d)[0] for d in days]
        else:
            opens = [
                ts for d in (date(2026, 9, 22), date(2026, 9, 23)) for ts in cal.expected_bar_opens(ex, d, tf)
            ]
        df = bars(opens)
        for day, bump in self.close_bump.items():
            df.loc[df["ts"] == cal.session_times(ex, day)[0], "close"] += bump
        hidden = [cal.session_times(ex, d)[0] for d in self.hidden_days]
        df = df[~df["ts"].isin(hidden)]
        if start is not None:
            df = df[df["ts"] >= start]
        if end is not None:
            df = df[df["ts"] < end]
        return df.reset_index(drop=True)


@pytest.fixture
def fake(monkeypatch, tmp_data_dir, no_keys):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    source = FakeSource()
    monkeypatch.setattr(ingest, "fetch_candles", source)
    return source


def test_backfill_then_daily_runs_always_reread_the_last_five_sessions(fake, monkeypatch):
    counts = ingest.ingest("1D", instruments=["NSE:RELIANCE"])
    assert counts == {"NSE:RELIANCE": 16}
    assert fake.calls[-1] == ("yahoo", "NSE:RELIANCE", "1D", clock.ist_midnight(date(2005, 1, 1)))

    # Up to date, but the last 5 sessions (17, 18, 21, 22, 23 Sep) are still re-read.
    assert ingest.ingest("1D", instruments=["NSE:RELIANCE"]) == {"NSE:RELIANCE": 0}
    assert fake.calls[-1][3] == ist(2026, 9, 17, 0, 0)

    # A provisional close that the source later corrects is overwritten.
    fake.close_bump[date(2026, 9, 22)] = 0.5
    assert ingest.ingest("1D", instruments=["NSE:RELIANCE"]) == {"NSE:RELIANCE": 1}
    stored = load_candles("NSE:RELIANCE", "1D").set_index("ts")["close"]
    assert stored[ist(2026, 9, 22, 9, 15)] == 101 + 14 + 0.5

    monkeypatch.setattr(clock, "utc_now", lambda: NOW + pd.Timedelta(days=1))
    ingest.ingest("1D", instruments=["NSE:RELIANCE"])
    assert fake.calls[-1][3] == ist(2026, 9, 18, 0, 0)

    # After a long pause the re-read starts at the last stored session instead.
    monkeypatch.setattr(clock, "utc_now", lambda: NOW + pd.Timedelta(days=30))
    ingest.ingest("1D", instruments=["NSE:RELIANCE"])
    assert fake.calls[-1][3] == ist(2026, 9, 23, 0, 0)


def test_recent_sessions_start_skips_holidays_and_the_forming_day():
    during = ist(2026, 9, 15, 12, 0)  # Tuesday session in progress; Monday 14 Sep is a holiday
    assert ingest.recent_sessions_start("NSE", 5, during) == ist(2026, 9, 7, 0, 0)  # 7, 8, 9, 10, 11 Sep


def test_daily_ingest_refetches_sessions_missing_next_to_intraday_data(fake, monkeypatch, caplog):
    fake.hidden_days = {date(2026, 9, 2), date(2026, 9, 3)}
    ingest.ingest("1D", instruments=["NSE:RELIANCE"])
    one_hour = cal.expected_bar_opens("NSE", date(2026, 9, 2), "1h") + cal.expected_bar_opens(
        "NSE", date(2026, 9, 3), "1h"
    )
    save_candles("NSE:RELIANCE", "1h", bars(one_hour))

    fake.hidden_days = {date(2026, 9, 3)}  # the source now has 2 Sep, but still not 3 Sep
    fake.calls.clear()
    with caplog.at_level(logging.WARNING, logger="candly.data.ingest"):
        counts = ingest.ingest("1D", instruments=["NSE:RELIANCE"])
    assert counts == {"NSE:RELIANCE": 1}
    assert [call[3] for call in fake.calls[1:]] == [ist(2026, 9, 2, 0, 0), ist(2026, 9, 3, 0, 0)]
    assert ist(2026, 9, 2, 9, 15) in set(load_candles("NSE:RELIANCE", "1D")["ts"])
    assert "still missing: ['2026-09-03']" in caplog.text


def test_missing_session_refetch_is_capped_and_best_effort(fake):
    fake.hidden_days = set(trading_days("NSE", date(2026, 9, 1), date(2026, 9, 16)))  # 11 sessions
    ingest.ingest("1D", instruments=["NSE:RELIANCE"])
    days = sorted(fake.hidden_days)
    save_candles("NSE:RELIANCE", "1h", bars([cal.session_times("NSE", d)[0] for d in days]))
    fake.calls.clear()
    fake.fail_starts = {ist(2026, 9, 16, 0, 0)}

    report = ingest.run_ingest("1D", instruments=["NSE:RELIANCE"])
    assert report.failed == {} and report.counts == {"NSE:RELIANCE": 0}
    refetched = [call[3] for call in fake.calls[1:]]
    assert refetched == [clock.ist_midnight(d) for d in days[-ingest.MISSING_REFETCH_LIMIT :]]


def test_archive_yahoo_then_fyers_backfills_from_scratch(fake, monkeypatch, fake_fyers_keys, capsys):
    monkeypatch.setattr(ingest, "setup_logging", lambda: None)
    monkeypatch.setattr(fyers, "ensure_token", lambda: None)
    for tf in ("1D", "5m", "1h"):
        ingest.ingest(tf, instruments=["NSE:RELIANCE", "NSE:INFY"], source="yahoo")
    save_candles("NSE:TCS", "1D", bars([ist(2026, 9, 22, 9, 15)]), source="fyers")
    save_candles("NSE:INFY", "1D", bars([ist(2026, 9, 22, 9, 15)]), source="fyers")  # now fyers+yahoo

    assert ingest.main(["--archive-source", "yahoo"]) == 0
    out = capsys.readouterr().out
    assert "6 yahoo series archived" in out
    archives = list((get_settings().data_dir / "archive").iterdir())
    assert len(archives) == 1 and archives[0].name.startswith("yahoo-")
    assert (archives[0] / "NSE" / "1D" / "INFY.parquet").exists()
    assert (archives[0] / "NSE" / "5m" / "RELIANCE.parquet").exists()
    assert series_stats("NSE:RELIANCE", "1D")["bars"] == 0 and series_stats("NSE:INFY", "1h")["bars"] == 0
    assert series_stats("NSE:TCS", "1D")["bars"] == 1  # fyers-only series stay

    fake.calls.clear()
    for tf in ("1D", "5m", "15m", "1h"):
        ingest.ingest(tf, instruments=["NSE:RELIANCE"], source="fyers")
    assert [call[2:] for call in fake.calls] == [
        ("1D", clock.ist_midnight(date(2005, 1, 1))),
        ("5m", clock.ist_midnight(date(2017, 7, 3))),
    ]  # 15m and 1h are resampled from the stored 5m, not fetched
    for tf, n in (("1D", 16), ("5m", 150), ("15m", 50), ("1h", 14)):
        assert series_stats("NSE:RELIANCE", tf)["bars"] == n
        assert candle_source("NSE:RELIANCE", tf) == "fyers"


def test_archive_source_cli_runs_on_its_own(fake, monkeypatch, capsys):
    monkeypatch.setattr(ingest, "setup_logging", lambda: None)
    assert ingest.main(["--archive-source", "yahoo"]) == 0
    assert "nothing to archive" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        ingest.main(["--archive-source", "yahoo", "--tf", "1D"])
    with pytest.raises(SystemExit):
        ingest.main([])


def test_intraday_overlap_starts_at_last_session_open(fake, monkeypatch):
    monkeypatch.setattr(clock, "utc_now", lambda: ist(2026, 9, 22, 16, 0))
    ingest.ingest("5m", instruments=["NSE:RELIANCE"])
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    ingest.ingest("5m", instruments=["NSE:RELIANCE"])
    assert fake.calls[-1][3] == ist(2026, 9, 22, 9, 15)
    assert series_stats("NSE:RELIANCE", "5m")["bars"] == 150


def test_since_overrides_and_accepts_dates(fake):
    ingest.ingest("1D", instruments=["NSE:RELIANCE"], since="2026-09-15")
    assert fake.calls[-1][3] == ist(2026, 9, 15, 0, 0)
    assert load_candles("NSE:RELIANCE", "1D")["ts"].iloc[0] == ist(2026, 9, 15, 9, 15)


def test_yahoo_skips_instruments_without_a_symbol(fake):
    report = ingest.run_ingest("1D", source="yahoo")
    assert set(report.skipped) == {"MCX:CRUDEOIL", "MCX:NATURALGAS", "MCX:GOLD", "MCX:SILVER"}
    assert len(report.counts) == 14
    assert all(call[1].split(":")[0] != "MCX" for call in fake.calls)


def test_one_failure_does_not_stop_the_batch(fake):
    fake.fail.add("NSE:TCS")
    report = ingest.run_ingest("1D", instruments=["NSE:TCS", "NSE:INFY"], source="yahoo")
    assert report.failed == {"NSE:TCS": "boom"}
    assert report.counts == {"NSE:INFY": 16}


def test_auto_source_follows_settings(fake, monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "fyers")
    get_settings.cache_clear()
    with pytest.raises(fyers.FyersNotConnected):
        ingest.ingest("1D", instruments=["NSE:RELIANCE"])
    assert fake.calls == []


def test_fyers_derived_timeframes_resample_stored_5m(fake, monkeypatch, fake_fyers_keys):
    monkeypatch.setattr(fyers, "ensure_token", lambda: None)
    counts = ingest.ingest("1h", instruments=["NSE:RELIANCE"], source="fyers")
    assert [call[2] for call in fake.calls] == ["5m"]
    assert counts == {"NSE:RELIANCE": 14}
    hourly = load_candles("NSE:RELIANCE", "1h")
    assert list(hourly["ts"][:7]) == cal.expected_bar_opens("NSE", date(2026, 9, 22), "1h")
    assert hourly["volume"].iloc[6] == 30.0  # 15:15 bar: three 5m bars

    ingest.ingest("15m", instruments=["NSE:RELIANCE"], source="fyers")
    assert len(fake.calls) == 1  # 5m already current: no second download
    assert series_stats("NSE:RELIANCE", "15m")["bars"] == 50


def test_backfill_fetches_one_window_at_a_time(fake):
    from candly.core.instruments import get_instrument

    inst = get_instrument("NSE:RELIANCE")
    assert ingest.backfill(inst, "1D", "yahoo", start=date(2024, 9, 1)) == 16
    starts = [call[3] for call in fake.calls]
    first = clock.ist_midnight(date(2024, 9, 1))
    assert starts == [first, first + pd.Timedelta(days=365), first + pd.Timedelta(days=730)]
    assert ingest.backfill(inst, "1D", "yahoo", start=date(2024, 9, 1)) == 0  # idempotent
    assert series_stats("NSE:RELIANCE", "1D")["bars"] == 16 and candle_source("NSE:RELIANCE", "1D") == "yahoo"

    fake.calls.clear()
    ingest.backfill(inst, "5m", "yahoo")
    assert fake.calls[0][3] == clock.ist_midnight(date(2017, 7, 3))
    assert fake.calls[1][3] == fake.calls[0][3] + pd.Timedelta(days=396)
    assert series_stats("NSE:RELIANCE", "5m")["bars"] == 150


def test_mid_session_since_snaps_to_the_session_open(fake, monkeypatch, fake_fyers_keys):
    monkeypatch.setattr(fyers, "ensure_token", lambda: None)
    ingest.ingest("1h", instruments=["NSE:RELIANCE"], source="fyers")
    stored = load_candles("NSE:RELIANCE", "1h")

    counts = ingest.ingest("1h", instruments=["NSE:RELIANCE"], source="fyers", since=ist(2026, 9, 23, 12, 7))
    assert fake.calls[-1][2:] == ("5m", ist(2026, 9, 23, 9, 15))
    assert counts == {"NSE:RELIANCE": 0}  # the 11:15 bar was not replaced by a 12:10-12:15 fragment
    pd.testing.assert_frame_equal(load_candles("NSE:RELIANCE", "1h"), stored)

    ingest.ingest("15m", instruments=["NSE:INFY"], source="yahoo", since=ist(2026, 9, 22, 10, 40))
    assert fake.calls[-1] == ("yahoo", "NSE:INFY", "15m", ist(2026, 9, 22, 9, 15))


def test_cli_clean_existing(fake, monkeypatch, capsys):
    monkeypatch.setattr(ingest, "setup_logging", lambda: None)
    ingest.ingest("1D", instruments=["NSE:RELIANCE"], source="yahoo")
    assert ingest.main(["--tf", "1D", "--clean-existing"]) == 0
    out = capsys.readouterr().out
    assert "NSE:RELIANCE" in out and "dropped" in out
    assert fake.calls[-1][1] == "NSE:RELIANCE" and len(fake.calls) == 1  # no download


def test_cli_reports_a_fyers_outage_cleanly(fake, monkeypatch, capsys, fake_fyers_keys):
    def outage():
        raise fyers.FyersError("could not reach Fyers (ConnectError)")

    monkeypatch.setattr(ingest, "setup_logging", lambda: None)
    monkeypatch.setattr(fyers, "ensure_token", outage)
    assert ingest.main(["--tf", "1D", "--source", "fyers"]) == 2
    assert "could not reach Fyers" in capsys.readouterr().err


def test_cli(fake, monkeypatch, capsys):
    monkeypatch.setattr(ingest, "setup_logging", lambda: None)
    code = ingest.main(["--tf", "1D", "--instrument", "NSE:RELIANCE", "MCX:GOLD", "--source", "yahoo"])
    out = capsys.readouterr().out
    assert code == 0
    assert "NSE:RELIANCE" in out and "16" in out
    assert "MCX:GOLD" in out and "not available on yahoo" in out
    assert ingest.main(["--tf", "1D", "--instrument", "NSE:NOPE"]) == 2
