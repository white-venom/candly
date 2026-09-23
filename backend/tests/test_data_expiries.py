import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from candly.core import expiry as core_expiry
from candly.core.calendar import IST
from candly.core.expiry import ExpiryInfo
from candly.data import clock, expiries
from candly.data.sources import fyers


def ist(y, m, d, hh=12, mm=0) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm), tz=IST).tz_convert("UTC")


NOW = ist(2026, 9, 23, 8, 30)  # Wednesday
SEGMENTS = ("NSE_FO", "BSE_FO", "MCX_COM")
D = date


def fut(underlying: str, *days: date) -> list[tuple[str, str, date]]:
    return [(f"{underlying}{d:%y%m%d}FUT", underlying, d) for d in days]


def opt(underlying: str, *days: date, side: str = "CE") -> list[tuple[str, str, date]]:
    return [(f"{underlying}{d:%y%m%d}25000{side}", underlying, d) for d in days]


# 24 Nov is Guru Nanak Jayanti, so November's monthly expiry is on Monday the 23rd.
NIFTY_FUTURES = (D(2026, 9, 29), D(2026, 10, 27), D(2026, 11, 23))
NIFTY_OPTIONS = (
    D(2026, 9, 29), D(2026, 10, 6), D(2026, 10, 13), D(2026, 10, 19), D(2026, 12, 29), D(2027, 3, 30)
)


def nse_fo(nifty_futures=NIFTY_FUTURES, nifty_options=NIFTY_OPTIONS):
    return (
        fut("NIFTY", *nifty_futures)
        + opt("NIFTY", *nifty_options)
        + opt("NIFTY", *nifty_options, side="PE")
        + fut("BANKNIFTY", D(2026, 9, 29), D(2026, 10, 27), D(2026, 11, 23))
        + opt("BANKNIFTY", D(2026, 12, 29), side="PE")
        + fut("RELIANCE", D(2026, 9, 29), D(2026, 10, 27), D(2026, 11, 23))
        + opt("RELIANCE", D(2026, 9, 29))
        + fut("NIFTYNXT50", D(2026, 9, 29))
    )


BSE_FO = fut("SENSEX", D(2026, 9, 24), D(2026, 10, 29), D(2026, 11, 26)) + opt(
    "SENSEX", D(2026, 10, 1), D(2026, 10, 8)
)
MCX_COM = (
    fut("CRUDEOIL", D(2026, 9, 18), D(2026, 10, 19), D(2026, 11, 18))
    + opt("CRUDEOIL", D(2026, 10, 15))  # MCX options expire before the future: not an expiry of the series
    + fut("CRUDEOILM", D(2026, 10, 16))
    + fut("NATURALGAS", D(2026, 10, 27))
)


def master_csv(segment: str, rows) -> str:
    """A Fyers symbol master: expiry epoch in column 8, ticker in 9, underlying in 13."""
    close = (23, 30) if segment == "MCX_COM" else (15, 30)
    lines = []
    for i, (ticker, underlying, day) in enumerate(rows):
        e = clock.epoch_seconds(ist(day.year, day.month, day.day, *close))
        lines.append(
            f"10{i:06d},{ticker} desc,11,75,0.05,,0915-1530|1815-1915:,2026-09-23,{e},{segment[:3]}:{ticker},"
            f"10,11,{i},{underlying},26000,-1.0,XX,10100000{i},{e},0,0.0"
        )
    return "\n".join(lines) + "\n"


@dataclass
class Exchange:
    monkeypatch: pytest.MonkeyPatch
    data_dir: Path
    routes: dict

    def on(self, now: pd.Timestamp, **published) -> None:
        """Moves the clock to `now`; each segment publishes the standard master unless given rows, an
        httpx.Response or an exception."""
        self.monkeypatch.setattr(clock, "utc_now", lambda: now)
        masters = {"NSE_FO": nse_fo(), "BSE_FO": BSE_FO, "MCX_COM": MCX_COM} | published
        for segment, value in masters.items():
            route = self.routes[segment]
            if isinstance(value, Exception):
                route.mock(side_effect=value)
            elif isinstance(value, httpx.Response):
                route.mock(return_value=value)
            else:
                route.mock(return_value=httpx.Response(200, text=master_csv(segment, value)))

    def refresh(self) -> dict:
        result = expiries.refresh_expiries()
        stamp = clock.epoch_seconds(clock.utc_now())
        for path in (self.data_dir / "cache" / "fyers").glob("*.csv"):
            os.utime(path, (stamp, stamp))  # dated by the fake clock, like a real download that day
        return result

    def saved(self) -> dict:
        return json.loads((self.data_dir / "expiries" / "live.json").read_text(encoding="utf-8"))


@pytest.fixture
def exchange(monkeypatch, tmp_data_dir):
    monkeypatch.setattr(fyers, "_limiter", fyers.RateLimiter(per_second=10_000, per_minute=10_000))
    monkeypatch.setattr(fyers, "_sleep", lambda seconds: None)
    monkeypatch.setattr(expiries, "_unlisted_warned", set())
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    with respx.mock(assert_all_called=False) as mock:
        routes = {s: mock.get(fyers.SYMBOL_MASTER_URL.format(segment=s)) for s in SEGMENTS}
        yield Exchange(monkeypatch, tmp_data_dir, routes)


def test_refresh_reads_every_segment_and_tags_weekly_and_monthly(exchange):
    exchange.on(NOW)
    result = exchange.refresh()
    assert result["downloads"] == {"BSE_FO": "ok", "MCX_COM": "ok", "NSE_FO": "ok"}
    assert result["status"] == "ok" and result["mismatches"] == []
    assert result["checked_at"] == clock.epoch_seconds(NOW)

    m, w, c = "monthly", "weekly", "contract"
    assert expiries.exchange_expiries("NSE:NIFTY50") == [
        (D(2026, 9, 29), m), (D(2026, 10, 6), w), (D(2026, 10, 13), w), (D(2026, 10, 19), w),
        (D(2026, 10, 27), m), (D(2026, 11, 23), m),
        (D(2026, 12, 29), m), (D(2027, 3, 30), m),  # long-dated options, beyond the listed futures
    ]
    assert expiries.exchange_expiries("NSE:BANKNIFTY") == [
        (D(2026, 9, 29), m), (D(2026, 10, 27), m), (D(2026, 11, 23), m), (D(2026, 12, 29), m)
    ]
    assert expiries.exchange_expiries("BSE:SENSEX") == [
        (D(2026, 9, 24), m), (D(2026, 10, 1), w), (D(2026, 10, 8), w),
        (D(2026, 10, 29), m), (D(2026, 11, 26), m),
    ]
    assert expiries.exchange_expiries("NSE:RELIANCE") == [
        (D(2026, 9, 29), m), (D(2026, 10, 27), m), (D(2026, 11, 23), m)
    ]
    assert expiries.exchange_expiries("MCX:CRUDEOIL") == [
        (D(2026, 9, 18), c), (D(2026, 10, 19), c), (D(2026, 11, 18), c)
    ]
    assert expiries.exchange_expiries("MCX:NATURALGAS") == [(D(2026, 10, 27), c)]
    assert expiries.exchange_expiries("NSE:NIFTY50", D(2026, 11, 1))[0] == (D(2026, 11, 23), m)
    for unlisted in ("NSE:INDIAVIX", "NSE:HDFCBANK", "MCX:GOLD"):
        assert expiries.exchange_expiries(unlisted) == []

    saved = exchange.saved()
    assert set(saved["instruments"]) == {
        "NSE:NIFTY50", "NSE:BANKNIFTY", "NSE:RELIANCE", "BSE:SENSEX", "MCX:CRUDEOIL", "MCX:NATURALGAS"
    }
    nifty = saved["instruments"]["NSE:NIFTY50"]
    assert (nifty["segment"], nifty["underlying"], nifty["since"], nifty["last_ok"]) == (
        "NSE_FO", "NIFTY", "2026-09-23", "2026-09-23"
    )
    assert nifty["expiries"]["2026-10-06"] == {"first_seen": "2026-09-23", "kind": "weekly"}
    assert saved["instruments"]["MCX:CRUDEOIL"]["underlying"] == "CRUDEOIL"
    ok = {"ok_at": clock.epoch_seconds(NOW), "failed_at": None, "error": None}
    assert saved["downloads"] == dict.fromkeys(SEGMENTS, ok)


def test_history_is_append_only_and_remembers_moved_dates(exchange, caplog):
    exchange.on(NOW)
    exchange.refresh()

    # A week later the September contracts have expired, the exchange moved 13 Oct to 12 Oct and
    # listed a new weekly.
    later = ist(2026, 9, 30, 8, 30)
    exchange.on(
        later,
        NSE_FO=nse_fo(
            nifty_futures=(D(2026, 10, 27), D(2026, 11, 23), D(2026, 12, 29)),
            nifty_options=(D(2026, 10, 6), D(2026, 10, 12), D(2026, 10, 19), D(2026, 11, 3), D(2027, 3, 30)),
        ),
    )
    with caplog.at_level(logging.WARNING, logger="candly.data.expiries"):
        result = exchange.refresh()
    assert result["new_dates"] == 2
    assert any("2026-10-13" in r.getMessage() and "no longer lists" in r.getMessage() for r in caplog.records)

    nifty = exchange.saved()["instruments"]["NSE:NIFTY50"]
    assert (nifty["since"], nifty["last_ok"]) == ("2026-09-23", "2026-09-30")
    assert nifty["expiries"]["2026-09-29"] == {"first_seen": "2026-09-23", "kind": "monthly"}  # expired, kept
    assert nifty["expiries"]["2026-10-13"] == {
        "first_seen": "2026-09-23", "kind": "weekly", "withdrawn": "2026-09-30"
    }
    assert nifty["expiries"]["2026-10-12"] == {"first_seen": "2026-09-30", "kind": "weekly"}
    assert nifty["expiries"]["2026-11-03"] == {"first_seen": "2026-09-30", "kind": "weekly"}
    assert nifty["expiries"]["2026-12-29"] == {"first_seen": "2026-09-23", "kind": "monthly"}
    days = [d for d, _ in expiries.exchange_expiries("NSE:NIFTY50")]
    assert D(2026, 9, 29) in days and D(2026, 10, 12) in days and D(2026, 10, 13) not in days
    info, source = expiries.expiry_with_source("NSE:NIFTY50", D(2026, 10, 1))
    assert (info.next_expiry, info.kind, source) == (D(2026, 10, 6), "weekly", "exchange")

    # Calling it again the same day re-uses the day's masters and changes no history.
    before = exchange.saved()["instruments"]
    calls = [route.call_count for route in exchange.routes.values()]
    assert exchange.refresh()["new_dates"] == 0
    assert exchange.saved()["instruments"] == before
    assert [route.call_count for route in exchange.routes.values()] == calls


def moved_nifty_monthly() -> list:
    """The exchange moved September's NIFTY expiry from Tuesday 29 to Monday 28 (not in the rules)."""
    return nse_fo(
        nifty_futures=(D(2026, 9, 28), D(2026, 10, 27), D(2026, 11, 23)),
        nifty_options=(D(2026, 9, 28), D(2026, 10, 6), D(2026, 10, 13)),
    )


def test_the_exchange_date_wins_over_the_rules(exchange):
    exchange.on(NOW, NSE_FO=moved_nifty_monthly())
    exchange.refresh()
    today = D(2026, 9, 23)
    assert core_expiry.expiry_info("NSE:NIFTY50", today).next_expiry == D(2026, 9, 29)
    info, source = expiries.expiry_with_source("NSE:NIFTY50", today)
    assert source == "exchange"
    assert info == ExpiryInfo(
        next_expiry=D(2026, 9, 28),
        kind="monthly",
        days_to_expiry=3,  # 24, 25 and 28 Sep
        is_expiry_day=False,
        is_monthly_expiry_day=False,
    )
    assert expiries.expiry_info("NSE:NIFTY50", today) == info
    assert expiries.expiry_with_source("NSE:RELIANCE", today)[0].next_expiry == D(2026, 9, 29)
    sensex, source = expiries.expiry_with_source("BSE:SENSEX", today)
    assert (sensex.next_expiry, sensex.kind, source) == (D(2026, 9, 24), "monthly", "exchange")

    # Before the first refresh only the rules know; INDIAVIX has no derivatives at all.
    before = D(2026, 9, 21)
    assert expiries.expiry_with_source("NSE:NIFTY50", before) == (
        core_expiry.expiry_info("NSE:NIFTY50", before),
        "rules",
    )
    assert expiries.expiry_info("NSE:NIFTY50", before).next_expiry == D(2026, 9, 22)
    assert expiries.expiry_with_source("NSE:INDIAVIX", today) == (None, None)

    exchange.on(ist(2026, 9, 28, 8, 30), NSE_FO=moved_nifty_monthly())
    exchange.refresh()
    on_the_day = expiries.expiry_info("NSE:NIFTY50", D(2026, 9, 28))
    assert on_the_day.is_expiry_day and on_the_day.is_monthly_expiry_day and on_the_day.days_to_expiry == 0


def test_rules_answer_when_live_data_is_missing_or_stale(exchange, tmp_data_dir):
    today = D(2026, 9, 23)
    for instrument in ("NSE:NIFTY50", "NSE:BANKNIFTY", "BSE:SENSEX", "NSE:RELIANCE"):
        assert expiries.expiry_with_source(instrument, today) == (
            core_expiry.expiry_info(instrument, today),
            "rules",
        )
    assert expiries.expiry_with_source("MCX:CRUDEOIL", today) == (None, None)
    assert expiries.mcx_roll_dates("MCX:CRUDEOIL") == []
    assert expiries.expiry_check() == {"status": "unavailable", "checked_at": None, "mismatches": []}

    exchange.on(NOW, NSE_FO=moved_nifty_monthly())
    exchange.refresh()
    assert expiries.expiry_with_source("NSE:NIFTY50", D(2026, 9, 26))[0].next_expiry == D(2026, 9, 28)
    stale, source = expiries.expiry_with_source("NSE:NIFTY50", D(2026, 9, 27))  # more than 3 days on
    assert (stale.next_expiry, source) == (D(2026, 9, 29), "rules")
    crude, source = expiries.expiry_with_source("MCX:CRUDEOIL", D(2026, 10, 1))  # MCX has no rules to use
    assert (crude.next_expiry, source) == (D(2026, 10, 19), "exchange")

    exchange.monkeypatch.setattr(clock, "utc_now", lambda: ist(2026, 9, 26, 8, 0))
    assert expiries.expiry_check()["status"] == "mismatch"
    exchange.monkeypatch.setattr(clock, "utc_now", lambda: ist(2026, 9, 27, 8, 0))
    assert expiries.expiry_check()["status"] == "unavailable"

    live = tmp_data_dir / "expiries" / "live.json"
    live.write_text("{not json", encoding="utf-8")
    rules = core_expiry.expiry_info("NSE:NIFTY50", today)
    assert expiries.expiry_with_source("NSE:NIFTY50", today) == (rules, "rules")
    exchange.on(ist(2026, 9, 27, 8, 30))
    exchange.refresh()  # sets the unreadable file aside and starts a new history
    assert list(live.parent.glob("live.unreadable-*.json"))
    assert exchange.saved()["instruments"]["NSE:NIFTY50"]["since"] == "2026-09-27"


def test_a_mismatch_with_the_rules_is_reported_and_logged_once(exchange, caplog):
    exchange.on(NOW, NSE_FO=moved_nifty_monthly())
    expected = [{"instrument": "NSE:NIFTY50", "rules": "2026-09-29", "exchange": "2026-09-28"}]
    with caplog.at_level(logging.WARNING, logger="candly.data.expiries"):
        first = exchange.refresh()
        second = exchange.refresh()
    assert first["status"] == second["status"] == "mismatch"
    assert first["mismatches"] == second["mismatches"] == expected
    warnings = [r for r in caplog.records if "expiry mismatch" in r.getMessage()]
    assert len(warnings) == 1 and "2026-09-28" in warnings[0].getMessage()
    assert expiries.expiry_check() == {
        "status": "mismatch",
        "checked_at": clock.epoch_seconds(NOW),
        "mismatches": expected,
    }


def test_a_failed_download_keeps_the_last_good_data(exchange, tmp_data_dir):
    exchange.on(NOW)
    exchange.refresh()
    good = exchange.saved()
    cached = (tmp_data_dir / "cache" / "fyers" / "NSE_FO.csv").read_text(encoding="utf-8")

    later = ist(2026, 9, 24, 8, 30)
    exchange.on(
        later,
        NSE_FO=httpx.Response(200, text="<html>maintenance</html>"),
        BSE_FO=httpx.Response(503),
        MCX_COM=httpx.ConnectError("offline"),
    )
    result = exchange.refresh()
    assert all(v.startswith("failed: ") for v in result["downloads"].values()) and result["new_dates"] == 0
    saved = exchange.saved()
    assert saved["instruments"] == good["instruments"] and saved["check"] == good["check"]
    assert saved["downloads"]["NSE_FO"]["ok_at"] == clock.epoch_seconds(NOW)
    assert saved["downloads"]["NSE_FO"]["failed_at"] == clock.epoch_seconds(later)
    assert "unreadable symbol master" in saved["downloads"]["NSE_FO"]["error"]
    assert (tmp_data_dir / "cache" / "fyers" / "NSE_FO.csv").read_text(encoding="utf-8") == cached
    assert result["status"] == "ok" and result["checked_at"] == clock.epoch_seconds(NOW)
    info, source = expiries.expiry_with_source("NSE:NIFTY50", D(2026, 9, 24))
    assert (info.next_expiry, source) == (D(2026, 9, 29), "exchange")


def test_mcx_contract_expiries_and_roll_dates(exchange):
    exchange.on(NOW)
    exchange.refresh()
    assert expiries.expiry_info("MCX:CRUDEOIL", D(2026, 9, 23)) == ExpiryInfo(
        next_expiry=D(2026, 10, 19),
        kind="contract",
        days_to_expiry=17,  # MCX trading days 24 Sep .. 19 Oct, without the 2 Oct holiday
        is_expiry_day=False,
        is_monthly_expiry_day=False,
    )
    on_the_day = expiries.expiry_info("MCX:CRUDEOIL", D(2026, 10, 19))
    assert on_the_day.is_expiry_day and on_the_day.days_to_expiry == 0 and on_the_day.is_monthly_expiry_day
    assert expiries.expiry_info("MCX:CRUDEOIL", D(2026, 10, 20)).next_expiry == D(2026, 11, 18)
    assert expiries.expiry_info("MCX:NATURALGAS", D(2026, 9, 23)).next_expiry == D(2026, 10, 27)
    assert expiries.expiry_info("MCX:GOLD", D(2026, 9, 23)) is None  # not in this master
    assert expiries.expiry_info("MCX:CRUDEOIL", D(2026, 9, 10)) is None  # before the first refresh
    assert expiries.expiry_info("MCX:CRUDEOIL", D(2026, 12, 1)) is None  # after the last known expiry

    # 18 Sep was a Friday; 20 Oct (Dussehra) is an MCX holiday.
    rolls = [D(2026, 9, 21), D(2026, 10, 21), D(2026, 11, 19)]
    assert expiries.mcx_roll_dates("MCX:CRUDEOIL") == rolls
    exchange.on(ist(2026, 10, 21, 8, 30), MCX_COM=fut("CRUDEOIL", D(2026, 11, 18), D(2026, 12, 16)))
    exchange.refresh()  # the September and October contracts have left the master
    assert expiries.mcx_roll_dates("MCX:CRUDEOIL") == rolls + [D(2026, 12, 17)]
    assert expiries.expiry_info("MCX:CRUDEOIL", D(2026, 10, 19)).is_expiry_day
    assert expiries.mcx_roll_dates("NSE:NIFTY50") == []
