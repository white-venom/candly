import json
import os
from datetime import date, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from candly.core import expiry as core_expiry
from candly.core.calendar import IST
from candly.data import clock, expiries
from candly.data.sources import fyers


def ist(y, m, d, hh=12, mm=0) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm), tz=IST).tz_convert("UTC")


NOW = ist(2026, 9, 23, 12, 2)
MASTER_URL = fyers.SYMBOL_MASTER_URL.format(segment=expiries.MCX_SEGMENT)
ROWS = [
    ("CRUDEOIL 18 Sep 26 FUT", "MCX:CRUDEOIL26SEPFUT", "CRUDEOIL", ist(2026, 9, 18, 23, 30)),
    ("CRUDEOIL 19 Oct 26 FUT", "MCX:CRUDEOIL26OCTFUT", "CRUDEOIL", ist(2026, 10, 19, 23, 30)),
    ("CRUDEOIL 18 Nov 26 FUT", "MCX:CRUDEOIL26NOVFUT", "CRUDEOIL", ist(2026, 11, 18, 23, 30)),
    ("CRUDEOILM 16 Oct 26 FUT", "MCX:CRUDEOILM26OCTFUT", "CRUDEOILM", ist(2026, 10, 16, 23, 30)),
    ("CRUDEOIL 15 Oct 26 5000 CE", "MCX:CRUDEOIL26OCT5000CE", "CRUDEOIL", ist(2026, 10, 15, 23, 30)),
    ("NATURALGAS 27 Oct 26 FUT", "MCX:NATURALGAS26OCTFUT", "NATURALGAS", ist(2026, 10, 27, 23, 30)),
]


def master_csv(rows) -> str:
    lines = []
    for i, (desc, ticker, underlying, expiry) in enumerate(rows):
        e = clock.epoch_seconds(expiry)
        lines.append(
            f"11202610{i},{desc},30,100,1.0,,0900-2330|1815-1915:,2026-09-23,{e},{ticker},11,20,{i},"
            f"{underlying},294,-1.0,XX,1120000000294,{e},0,0.0"
        )
    return "\n".join(lines) + "\n"


def write_master(data_dir: Path, rows, downloaded: pd.Timestamp) -> None:
    """A cached symbol master as fyers.symbol_master leaves it, dated by its mtime."""
    path = data_dir / "cache" / "fyers" / f"{expiries.MCX_SEGMENT}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(master_csv(rows), encoding="utf-8")
    epoch = clock.epoch_seconds(downloaded)
    os.utime(path, (epoch, epoch))


@pytest.fixture
def mcx(monkeypatch, tmp_data_dir):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    monkeypatch.setattr(fyers, "_limiter", fyers.RateLimiter(per_second=10_000, per_minute=10_000))
    monkeypatch.setattr(fyers, "_sleep", lambda seconds: None)
    return tmp_data_dir


@respx.mock  # any download attempt would fail the test
def test_mcx_expiry_from_the_cached_master(mcx):
    write_master(mcx, ROWS, NOW)
    info = expiries.expiry_info("MCX:CRUDEOIL", date(2026, 9, 23))
    assert info == core_expiry.ExpiryInfo(
        next_expiry=date(2026, 10, 19),
        kind="contract",
        days_to_expiry=17,  # MCX trading days 24 Sep .. 19 Oct, without the 2 Oct holiday
        is_expiry_day=False,
        is_monthly_expiry_day=False,
    )
    on_the_day = expiries.expiry_info("MCX:CRUDEOIL", date(2026, 10, 19))
    assert on_the_day.is_expiry_day and on_the_day.days_to_expiry == 0 and on_the_day.is_monthly_expiry_day
    assert expiries.expiry_info("MCX:CRUDEOIL", date(2026, 10, 20)).next_expiry == date(2026, 11, 18)
    assert expiries.expiry_info("MCX:NATURALGAS", date(2026, 9, 23)).next_expiry == date(2026, 10, 27)
    assert expiries.expiry_info("MCX:GOLD", date(2026, 9, 23)) is None  # not in this master
    assert expiries.expiry_info("MCX:CRUDEOIL", date(2026, 9, 10)) is None  # before the first master seen
    assert expiries.expiry_info("MCX:CRUDEOIL", date(2026, 12, 1)) is None  # after the last known expiry
    assert respx.calls.call_count == 0


def test_nse_and_bse_delegate_to_the_rule_engine_and_vix_has_none(mcx):
    for instrument in ("NSE:NIFTY50", "NSE:BANKNIFTY", "BSE:SENSEX", "NSE:RELIANCE"):
        day = date(2026, 9, 23)
        assert expiries.expiry_info(instrument, day) == core_expiry.expiry_info(instrument, day)
    assert expiries.expiry_info("NSE:NIFTY50", date(2026, 9, 23)).next_expiry == date(2026, 9, 29)
    assert expiries.expiry_info("NSE:INDIAVIX", date(2026, 9, 23)) is None
    assert expiries.mcx_roll_dates("NSE:NIFTY50") == []


@respx.mock
def test_no_master_gives_none_and_is_not_downloaded_on_every_call(mcx, monkeypatch):
    route = respx.get(MASTER_URL).mock(return_value=httpx.Response(503))
    assert expiries.expiry_info("MCX:CRUDEOIL", date(2026, 9, 23)) is None
    assert expiries.mcx_roll_dates("MCX:CRUDEOIL") == []
    assert route.call_count == 1

    route.mock(return_value=httpx.Response(200, text=master_csv(ROWS)))
    assert expiries.expiry_info("MCX:CRUDEOIL", date(2026, 9, 23)) is None  # still inside the retry pause
    monkeypatch.setattr(expiries, "RETRY_SECONDS", 0)
    assert expiries.expiry_info("MCX:CRUDEOIL", date(2026, 9, 23)).next_expiry == date(2026, 10, 19)
    assert route.call_count == 2


@respx.mock
def test_an_older_cached_master_is_used_when_the_download_fails(mcx):
    write_master(mcx, ROWS, ist(2026, 9, 21))
    respx.get(MASTER_URL).mock(side_effect=httpx.ConnectError("offline"))
    assert expiries.expiry_info("MCX:CRUDEOIL", date(2026, 9, 23)).next_expiry == date(2026, 10, 19)
    assert expiries.expiry_info("MCX:CRUDEOIL", date(2026, 9, 21)).days_to_expiry == 19
    assert expiries.expiry_info("MCX:CRUDEOIL", date(2026, 9, 18)) is None  # before that master's day


@respx.mock
def test_expired_contracts_are_remembered_for_roll_dates(mcx, monkeypatch):
    # 18 Sep was a Friday; 20 Oct (Dussehra) is an MCX holiday.
    rolls = [date(2026, 9, 21), date(2026, 10, 21), date(2026, 11, 19)]
    write_master(mcx, ROWS, NOW)
    assert expiries.mcx_roll_dates("MCX:CRUDEOIL") == rolls

    later = ist(2026, 10, 21, 8, 0)
    monkeypatch.setattr(clock, "utc_now", lambda: later)
    write_master(mcx, [ROWS[2]], later)  # the October contract has left the master
    assert expiries.mcx_roll_dates("MCX:CRUDEOIL") == rolls
    assert expiries.expiry_info("MCX:CRUDEOIL", date(2026, 10, 19)).is_expiry_day
    saved = json.loads((mcx / "expiries" / "mcx.json").read_text(encoding="utf-8"))
    assert saved["CRUDEOIL"] == {
        "since": "2026-09-23",
        "expiries": ["2026-09-18", "2026-10-19", "2026-11-18"],
    }
    assert "CRUDEOILM" in saved and "NATURALGAS" in saved
