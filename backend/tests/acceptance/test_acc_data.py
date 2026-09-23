"""Acceptance: stored candles tell the truth (docs/test-reports/2026-09-23-first-acceptance.md).

Reads data/candles directly and skips without data. Known data defects are xfail with the report's id,
so the suite stays green while the defect is open and shows XPASS once it is fixed.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from acceptance_helpers import local, read_real, stored_ids

from candly.core.calendar import get_calendar

# Official exchange closes, checked against published close reports (sources in the report, §3.1).
OFFICIAL_CLOSES = [
    ("NSE:NIFTY50", "2026-09-21", 23414.30),
    ("NSE:NIFTY50", "2026-09-18", 23346.40),
    ("NSE:NIFTY50", "2026-02-02", 25088.40),
    ("NSE:NIFTY50", "2024-06-04", 21884.50),
    ("NSE:NIFTY50", "2020-03-23", 7610.25),
    ("BSE:SENSEX", "2026-09-21", 74858.99),
    ("BSE:SENSEX", "2026-02-02", 81666.46),
    ("BSE:SENSEX", "2024-06-04", 72079.05),
    ("BSE:SENSEX", "2020-03-23", 25981.24),
    ("NSE:BANKNIFTY", "2026-09-21", 56470.65),
    ("NSE:RELIANCE", "2026-09-22", 1240.40),
    ("NSE:HDFCBANK", "2026-09-22", 738.60),
    ("NSE:TCS", "2026-09-22", 2105.00),
    # back-adjusted for the 1:1 bonus (ex 2024-10-28): official 2,655.70 / 2
    ("NSE:RELIANCE", "2024-10-25", 1327.85),
]

# Ex-dates of splits/bonuses: an adjusted series shows no mechanical gap on these days.
CORPORATE_ACTIONS = [
    ("NSE:RELIANCE", "2017-09-07"),
    ("NSE:RELIANCE", "2024-10-28"),
    ("NSE:HDFCBANK", "2011-07-14"),
    ("NSE:HDFCBANK", "2019-09-19"),
    ("NSE:HDFCBANK", "2025-08-26"),
    ("NSE:TCS", "2009-06-16"),
    ("NSE:TCS", "2018-05-31"),
]

# Real weekend sessions (Budget Saturdays/Sunday, Muhurat weekends, special live sessions).
WEEKEND_SESSIONS = {
    "2006-10-21",
    "2009-10-17",
    "2013-11-03",
    "2015-02-28",
    "2016-10-30",
    "2019-10-27",
    "2020-02-01",
    "2020-11-14",
    "2023-11-12",
    "2024-01-20",
    "2024-03-02",
    "2024-05-18",
    "2025-02-01",
    "2026-02-01",
}

# Bars known to be bad in the Yahoo history (report D3). Anything else that looks like this is new.
KNOWN_BAD_BARS = {("NSE:LT", "2006-09-27")}


def _by_date(df: pd.DataFrame) -> pd.DataFrame:
    return df.set_index(local(df["ts"]).dt.date.astype(str))


@pytest.mark.parametrize(("instrument", "day", "official"), OFFICIAL_CLOSES)
def test_daily_close_matches_official_close(real_candles, instrument, day, official):
    df = _by_date(real_candles(instrument, "1D"))
    if day not in df.index:
        if pd.Timestamp(day).date() > local(real_candles(instrument, "1D")["ts"]).iloc[-1].date():
            pytest.skip(f"{instrument} 1D not ingested up to {day}")
        pytest.fail(f"{instrument} has no 1D bar on {day}")
    assert df.loc[day, "close"] == pytest.approx(official, rel=5e-4)


@pytest.mark.parametrize(("instrument", "ex_date"), CORPORATE_ACTIONS)
def test_split_and_bonus_ex_dates_have_no_mechanical_gap(real_candles, instrument, ex_date):
    df = real_candles(instrument, "1D")
    days = local(df["ts"]).dt.date.astype(str)
    pos = np.flatnonzero(days.to_numpy() == ex_date)
    assert pos.size == 1, f"{instrument} has no bar on ex-date {ex_date}"
    i = int(pos[0])
    gap = df["open"].iloc[i] / df["close"].iloc[i - 1] - 1
    assert abs(gap) < 0.10, f"{instrument} {ex_date}: overnight gap {gap:+.1%} looks unadjusted"


def _spike_reversals(df: pd.DataFrame, threshold: float = 0.35) -> list[str]:
    gap = np.log(df["open"] / df["close"].shift(1))
    back = gap.shift(-1)
    hit = (gap.abs() > threshold) & (back.abs() > threshold) & (np.sign(gap) != np.sign(back))
    return local(df.loc[hit, "ts"]).dt.date.astype(str).tolist()


def test_no_new_one_day_spike_reversals():
    ids = [i for i in stored_ids("1D", kinds=("equity", "index")) if i != "NSE:INDIAVIX"]
    if not ids:
        pytest.skip("no stored 1D data")
    found = set()
    for inst in ids:
        found |= {(inst, d) for d in _spike_reversals(read_real(inst, "1D"))}
    assert found <= KNOWN_BAD_BARS, f"new spike-and-reverse bars: {sorted(found - KNOWN_BAD_BARS)}"


@pytest.mark.xfail(
    reason="report D3/F8: LT 2006-09-27 is stored at half price (-68.5% / +73.3% gaps)", strict=False
)
def test_no_one_day_spike_reversals_at_all(real_candles):
    assert _spike_reversals(real_candles("NSE:LT", "1D")) == []


@pytest.mark.parametrize("tf", ["1D", "1h"])
def test_ohlc_is_consistent_sorted_and_positive(tf):
    ids = stored_ids(tf)
    if not ids:
        pytest.skip(f"no stored {tf} data")
    for inst in ids:
        df = read_real(inst, tf)
        assert df["ts"].is_monotonic_increasing and df["ts"].is_unique, inst
        assert (df[["open", "high", "low", "close"]] > 0).all().all(), inst
        assert (df["high"] >= df[["open", "close"]].max(axis=1)).all(), inst
        assert (df["low"] <= df[["open", "close"]].min(axis=1)).all(), inst


def test_daily_bars_are_stamped_at_the_session_open():
    ids = stored_ids("1D")
    if not ids:
        pytest.skip("no stored 1D data")
    for inst in ids:
        times = set(local(read_real(inst, "1D")["ts"]).dt.strftime("%H:%M"))
        assert times == {"09:15"}, f"{inst}: 1D bars at {sorted(times)}"


@pytest.mark.parametrize("tf", ["1D", "1h"])
def test_no_bars_on_configured_holidays(tf):
    cal = get_calendar()
    ids = stored_ids(tf)
    if not ids:
        pytest.skip(f"no stored {tf} data")
    for inst in ids:
        exchange = inst.split(":")[0]
        closed = {d for d in cal.spec(exchange).holidays if not cal.is_trading_day(exchange, d)}
        days = set(local(read_real(inst, tf)["ts"]).dt.date)
        assert not (days & closed), f"{inst} {tf} has bars on holidays {sorted(days & closed)}"


@pytest.mark.parametrize("tf", ["1D", "1h"])
def test_weekend_bars_are_real_special_sessions(tf):
    ids = stored_ids(tf)
    if not ids:
        pytest.skip(f"no stored {tf} data")
    per_inst = {}
    for inst in ids:
        loc = local(read_real(inst, tf)["ts"])
        per_inst[inst] = set(loc[loc.dt.weekday >= 5].dt.date.astype(str))
    # Fyers history carries real Saturday sessions we don't list (e.g. 2012-03-03). A weekend day is real
    # when it's a known special session or at least 3 instruments traded that day; one stray series isn't.
    counts: dict[str, int] = {}
    for days in per_inst.values():
        for d in days:
            counts[d] = counts.get(d, 0) + 1
    real = WEEKEND_SESSIONS | {d for d, n in counts.items() if n >= 3}
    for inst, weekend in per_inst.items():
        assert weekend <= real, f"{inst} {tf}: unexplained weekend bars {sorted(weekend - real)}"


def test_hourly_bars_sit_on_the_session_grid():
    grid = {"09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"}
    ids = stored_ids("1h")
    if not ids:
        pytest.skip("no stored 1h data")
    for inst in ids:
        loc = local(read_real(inst, "1h")["ts"])
        assert set(loc.dt.strftime("%H:%M")) <= grid, inst
        per_session = loc.groupby(loc.dt.date).size()
        assert per_session.max() <= 7, f"{inst}: {per_session.idxmax()} has {per_session.max()} 1h bars"


def test_most_hourly_sessions_are_complete():
    ids = stored_ids("1h")
    if not ids:
        pytest.skip("no stored 1h data")
    for inst in ids:
        loc = local(read_real(inst, "1h")["ts"])
        per_session = loc.groupby(loc.dt.date).size().iloc[:-1]  # the last session may still be running
        assert (per_session == 7).mean() >= 0.95, (
            f"{inst}: only {(per_session == 7).mean():.1%} of sessions have 7 bars"
        )


@pytest.mark.xfail(
    reason="report D1/D2/F1: 1D misses sessions that 1h has (indices and LT 2026-09-22, stocks 2025-03-18)",
    strict=False,
)
def test_daily_series_cover_every_completed_hourly_session():
    ids = stored_ids("1h")
    if not ids:
        pytest.skip("no stored 1h data")
    missing = {}
    for inst in ids:
        daily = read_real(inst, "1D")
        if daily is None:
            continue
        hourly_loc = local(read_real(inst, "1h")["ts"])
        per_session = hourly_loc.groupby(hourly_loc.dt.date).size()
        completed = [d for d, n in per_session.items() if n == 7]
        gaps = sorted(set(completed) - set(local(daily["ts"]).dt.date))
        if gaps:
            missing[inst] = [str(d) for d in gaps]
    assert not missing, missing


@pytest.mark.xfail(
    reason="report D2/F2: the Sunday 2026-02-01 Budget session is not in markets.yaml", strict=True
)
def test_calendar_knows_the_2026_budget_sunday_session():
    cal = get_calendar()
    assert all(cal.is_trading_day(ex, date(2026, 2, 1)) for ex in ("NSE", "BSE", "MCX"))


@pytest.mark.xfail(reason="report D2: Yahoo history lacks the Sunday 2026-02-01 Budget session", strict=False)
def test_budget_sunday_2026_session_is_stored(real_candles):
    days = set(local(real_candles("NSE:NIFTY50", "1D")["ts"]).dt.date.astype(str))
    assert "2026-02-01" in days


@pytest.mark.xfail(
    reason="report D4/F6: Yahoo's historical 09:15 1h bar has zero volume for stocks", strict=False
)
def test_opening_hour_carries_volume_on_stocks():
    ids = stored_ids("1h", kinds=("equity",))
    if not ids:
        pytest.skip("no stored 1h equity data")
    for inst in ids:
        df = read_real(inst, "1h")
        opening = local(df["ts"]).dt.strftime("%H:%M") == "09:15"
        assert (df.loc[opening, "volume"] == 0).mean() < 0.05, inst


@pytest.mark.xfail(
    reason="report D6/F7: recent 15:15 bars are synthetic open-to-official-close stubs", strict=False
)
def test_recent_closing_hour_bars_are_real_bars():
    ids = stored_ids("1h")
    if not ids:
        pytest.skip("no stored 1h data")
    for inst in ids:
        df = read_real(inst, "1h")
        last = df[local(df["ts"]).dt.strftime("%H:%M") == "15:15"].tail(20)
        stub = (last["close"] - last["open"]).abs() >= (last["high"] - last["low"]) - 1e-9
        assert stub.mean() < 0.5, f"{inst}: {stub.mean():.0%} of the last 20 15:15 bars are stubs"
