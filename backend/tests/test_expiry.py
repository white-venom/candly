from datetime import date

import pytest

from candly.core.expiry import expiry_info, get_expiry_rules


def expiries(instrument_id: str, start: date, end: date) -> list[tuple[date, str]]:
    return get_expiry_rules().expiries_between(instrument_id, start, end)


@pytest.mark.parametrize(
    ("instrument", "day", "kind"),
    [
        ("NSE:NIFTY50", date(2025, 8, 21), "weekly"),     # Thursday era
        ("NSE:NIFTY50", date(2025, 8, 28), "monthly"),    # last Thursday of August 2025
        ("NSE:NIFTY50", date(2025, 9, 2), "weekly"),      # first Tuesday expiry
        ("NSE:NIFTY50", date(2026, 9, 22), "weekly"),
        ("NSE:NIFTY50", date(2026, 9, 29), "monthly"),    # last Tuesday of September 2026
        ("NSE:BANKNIFTY", date(2023, 8, 24), "weekly"),   # Thursday-era weekly
        ("NSE:BANKNIFTY", date(2023, 8, 31), "monthly"),  # last Thursday of August 2023
        ("NSE:BANKNIFTY", date(2023, 9, 6), "weekly"),    # first Wednesday weekly
        ("NSE:BANKNIFTY", date(2024, 11, 13), "weekly"),  # final weekly
        ("NSE:BANKNIFTY", date(2024, 11, 27), "monthly"), # monthly on last Wednesday in 2024
        ("NSE:BANKNIFTY", date(2025, 1, 30), "monthly"),  # back to last Thursday
        ("NSE:BANKNIFTY", date(2026, 9, 29), "monthly"),
        ("BSE:SENSEX", date(2024, 6, 14), "weekly"),      # Friday era
        ("BSE:SENSEX", date(2025, 1, 7), "weekly"),       # Tuesday era
        ("BSE:SENSEX", date(2025, 9, 4), "weekly"),       # first Thursday expiry
        ("NSE:RELIANCE", date(2025, 8, 28), "monthly"),
        ("NSE:RELIANCE", date(2026, 9, 29), "monthly"),
    ],
)
def test_known_expiry_days(instrument, day, kind):
    info = expiry_info(instrument, day)
    assert info is not None and info.is_expiry_day and info.kind == kind
    assert info.is_monthly_expiry_day == (kind == "monthly")


def test_bank_nifty_weekly_discontinued_after_november_2024():
    days = expiries("NSE:BANKNIFTY", date(2024, 11, 14), date(2024, 12, 31))
    assert [kind for _, kind in days] == ["monthly", "monthly"]
    assert days[0][0] == date(2024, 11, 27)
    assert not expiry_info("NSE:BANKNIFTY", date(2024, 11, 20)).is_expiry_day


def test_stocks_have_no_weekly_expiry():
    assert all(kind == "monthly" for _, kind in expiries("NSE:TCS", date(2026, 1, 1), date(2026, 12, 31)))
    assert len(expiries("NSE:TCS", date(2026, 1, 1), date(2026, 12, 31))) == 12


def test_holiday_moves_expiry_to_previous_trading_day():
    assert expiry_info("NSE:NIFTY50", date(2026, 3, 2)).is_expiry_day          # Holi Tuesday -> Monday
    assert expiry_info("NSE:NIFTY50", date(2026, 3, 30)).is_monthly_expiry_day # Mahavir Jayanti Tuesday
    assert expiry_info("BSE:SENSEX", date(2026, 3, 25)).is_expiry_day          # Ram Navami Thursday


def test_days_to_expiry_counts_trading_days():
    info = expiry_info("NSE:NIFTY50", date(2026, 9, 23))  # Wednesday
    assert info.next_expiry == date(2026, 9, 29) and info.kind == "monthly"
    assert info.days_to_expiry == 4  # Thu, Fri, Mon, Tue
    assert not info.is_expiry_day


def test_instruments_without_rule_based_expiry():
    assert expiry_info("NSE:INDIAVIX", date(2026, 9, 23)) is None
    assert expiry_info("MCX:CRUDEOIL", date(2026, 9, 23)) is None
