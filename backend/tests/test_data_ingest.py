from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST, get_calendar
from candly.core.settings import get_settings
from candly.data import clock, ingest
from candly.data.sources import SourceError, fyers
from candly.data.store import load_candles, series_stats

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
    """Serves daily bars up to 23 Sep and 5m bars for 22-23 Sep, filtered by the requested start."""

    def __init__(self):
        self.calls: list[tuple[str, str, str, pd.Timestamp]] = []
        self.fail: set[str] = set()

    def __call__(self, source, instrument, tf, start=None, end=None, *, include_forming=False):
        self.calls.append((source, instrument.id, tf, start))
        if instrument.id in self.fail:
            raise SourceError("boom")
        ex = instrument.exchange
        if tf == "1D":
            opens = [
                cal.session_times(ex, d)[0] for d in trading_days(ex, date(2026, 9, 1), date(2026, 9, 23))
            ]
        else:
            opens = [
                ts for d in (date(2026, 9, 22), date(2026, 9, 23)) for ts in cal.expected_bar_opens(ex, d, tf)
            ]
        df = bars(opens)
        return df[df["ts"] >= start].reset_index(drop=True) if start is not None else df


@pytest.fixture
def fake(monkeypatch, tmp_data_dir, no_keys):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    source = FakeSource()
    monkeypatch.setattr(ingest, "fetch_candles", source)
    return source


def test_backfill_then_up_to_date_then_incremental(fake, monkeypatch):
    counts = ingest.ingest("1D", instruments=["NSE:RELIANCE"])
    assert counts == {"NSE:RELIANCE": 16}
    assert fake.calls[-1] == ("yahoo", "NSE:RELIANCE", "1D", clock.ist_midnight(date(2005, 1, 1)))

    assert ingest.ingest("1D", instruments=["NSE:RELIANCE"]) == {"NSE:RELIANCE": 0}
    assert len(fake.calls) == 1  # already holds the latest closed bar: no fetch

    monkeypatch.setattr(clock, "utc_now", lambda: NOW + pd.Timedelta(days=1))
    ingest.ingest("1D", instruments=["NSE:RELIANCE"])
    assert fake.calls[-1][3] == ist(2026, 9, 23, 9, 15) - ingest.DAILY_OVERLAP


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
