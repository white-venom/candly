from datetime import date, datetime

import pandas as pd

from candly.core.calendar import IST, get_calendar

cal = get_calendar()


def ist(y: int, m: int, d: int, hh: int, mm: int) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm), tz=IST).tz_convert("UTC")


def test_nse_regular_session():
    assert cal.session("NSE", date(2026, 9, 23)) == (ist(2026, 9, 23, 9, 15), ist(2026, 9, 23, 15, 30))


def test_weekend_and_holiday_are_closed():
    assert cal.session("NSE", date(2026, 9, 26)) is None  # Saturday
    assert cal.session("NSE", date(2026, 10, 2)) is None  # Gandhi Jayanti
    assert not cal.is_trading_day("MCX", date(2026, 12, 25))


def test_muhurat_without_timing_is_not_scheduled():
    assert not cal.is_trading_day("NSE", date(2026, 11, 8))


def test_mcx_close_follows_us_daylight_saving():
    assert cal.session("MCX", date(2026, 3, 6))[1] == ist(2026, 3, 6, 23, 55)
    assert cal.session("MCX", date(2026, 3, 9))[1] == ist(2026, 3, 9, 23, 30)
    assert cal.session("MCX", date(2026, 9, 23))[1] == ist(2026, 9, 23, 23, 30)
    assert cal.session("MCX", date(2026, 11, 2))[1] == ist(2026, 11, 2, 23, 55)


def test_bar_close_times():
    assert cal.bar_close_time("NSE", ist(2026, 9, 23, 9, 15), "5m") == ist(2026, 9, 23, 9, 20)
    assert cal.bar_close_time("NSE", ist(2026, 9, 23, 15, 15), "1h") == ist(2026, 9, 23, 15, 30)
    assert cal.bar_close_time("NSE", ist(2026, 9, 23, 9, 15), "1D") == ist(2026, 9, 23, 15, 30)


def test_expected_bar_counts():
    d = date(2026, 9, 23)
    assert len(cal.expected_bar_opens("NSE", d, "5m")) == 75
    assert len(cal.expected_bar_opens("NSE", d, "15m")) == 25
    assert cal.expected_bar_opens("NSE", d, "1h")[-1] == ist(2026, 9, 23, 15, 15)
    assert len(cal.expected_bar_opens("NSE", d, "1h")) == 7
    assert cal.expected_bar_opens("NSE", date(2026, 10, 2), "5m") == []


def test_session_phases():
    assert cal.session_phase("NSE", ist(2026, 9, 23, 9, 5)) == "pre_open"
    assert cal.session_phase("NSE", ist(2026, 9, 23, 9, 20)) == "open"
    assert cal.session_phase("NSE", ist(2026, 9, 23, 12, 0)) == "midday"
    assert cal.session_phase("NSE", ist(2026, 9, 23, 15, 10)) == "close"
    assert cal.session_phase("NSE", ist(2026, 9, 23, 16, 0)) == "closed"
    assert cal.session_phase("MCX", ist(2026, 9, 23, 22, 0)) == "evening"
    assert cal.session_phase("MCX", ist(2026, 9, 23, 23, 10)) == "close"


def test_is_open():
    assert cal.is_open("NSE", ist(2026, 9, 23, 10, 0))
    assert not cal.is_open("NSE", ist(2026, 9, 23, 15, 30))
    assert cal.is_open("MCX", ist(2026, 9, 23, 21, 0))


def test_weekly_expiry_regular_and_holiday_shift():
    assert cal.is_expiry_day("NSE", date(2026, 9, 22))  # Tuesday
    assert not cal.is_expiry_day("NSE", date(2026, 9, 23))
    assert cal.is_expiry_day("BSE", date(2026, 9, 24))  # Thursday
    assert cal.next_expiry("NSE", date(2026, 3, 2)) == date(2026, 3, 2)  # Holi Tuesday -> Monday
    assert cal.next_expiry("BSE", date(2026, 3, 23)) == date(2026, 3, 25)  # Ram Navami Thursday -> Wednesday


def test_monthly_expiry_holiday_shift():
    # Last Tuesday of March 2026 is Mahavir Jayanti -> Monday 30 March
    assert cal.next_expiry("NSE", date(2026, 3, 25), monthly=True) == date(2026, 3, 30)
    assert cal.next_expiry("NSE", date(2026, 9, 23), monthly=True) == date(2026, 9, 29)
