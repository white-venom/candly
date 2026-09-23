import pandas as pd

from candly.core.calendar import get_calendar
from candly.core.schema import empty_candles
from candly.forecast.jobs import grade_pending_job, rebuild_scorecard, run_forecast_cycle
from candly.ledger import Ledger
from candly.research.synthetic import synthetic_candles

cal = get_calendar()
FULL = {
    "NSE:RELIANCE": synthetic_candles("1D", "2012-01-01", "2024-07-31", seed=81),
    "NSE:SBIN": synthetic_candles("1D", "2012-01-01", "2024-07-31", seed=82),
}
CUT = pd.Timestamp("2024-06-28 03:45", tz="UTC")
NOW = cal.bar_close_time("NSE", CUT, "1D") + pd.Timedelta(minutes=2)


def loader(until: pd.Timestamp):
    def load(instrument_id, tf, start=None, end=None):
        df = FULL.get(instrument_id)
        return empty_candles() if df is None else df[df["ts"] <= until].reset_index(drop=True)

    return load


def test_cycle_records_then_grades(tmp_path, tmp_data_dir, no_keys):
    book = Ledger(tmp_path / "ledger.sqlite")
    counts = run_forecast_cycle("1D", list(FULL), load=loader(CUT), ledger=book, now=NOW)
    assert counts["recorded"] == 8 and counts["errors"] == 0
    again = run_forecast_cycle("1D", list(FULL), load=loader(CUT), ledger=book, now=NOW)
    assert again["duplicates"] == 8 and again["recorded"] == 0

    methods = {e.method for e in book.entries()}
    assert methods == {"analog_v1", "baseline_base_rate", "baseline_persistence", "baseline_random_walk"}

    later = pd.Timestamp("2024-07-15 03:45", tz="UTC")
    graded = grade_pending_job(load=loader(later), ledger=book, now=cal.bar_close_time("NSE", later, "1D"))
    assert graded == 8
    assert {e.status for e in book.entries()} == {"graded"}
    analog = book.entries(instrument="NSE:RELIANCE", method="analog_v1")[0]
    assert len(analog.actual) == analog.horizon_bars
    assert analog.actual[0].time > analog.ref_time


def test_stale_forecasts_are_not_recorded(tmp_path, tmp_data_dir, no_keys):
    book = Ledger(tmp_path / "ledger.sqlite")
    counts = run_forecast_cycle(
        "1D", ["NSE:RELIANCE"], load=loader(CUT), ledger=book, now=NOW + pd.Timedelta(days=5)
    )
    assert counts["not_recorded"] == 1 and counts["recorded"] == 0 and book.entries() == []


def test_rebuild_scorecard_job(tmp_data_dir, no_keys):
    summary = rebuild_scorecard("1D", list(FULL), load=loader(CUT))
    assert summary["tf"] == "1D" and summary["n_tests"] > 0
    assert (tmp_data_dir / "derived" / "scorecard_1D.parquet").exists()
