import sqlite3

import pandas as pd
import pytest

from candly.core.calendar import get_calendar
from candly.forecast.models import Band, Candle, Forecast, ForecastContext
from candly.ledger import DuplicateForecast, LateForecast, Ledger
from candly.ledger.grading import interval_iou, match_score
from candly.research.synthetic import synthetic_candles

cal = get_calendar()
CANDLES = synthetic_candles("1D", "2024-01-01", "2024-06-28", seed=7)


def unix(ts) -> int:
    return int(pd.Timestamp(ts).timestamp())


def close_of(k: int) -> pd.Timestamp:
    return cal.bar_close_time("NSE", CANDLES["ts"].iloc[k], "1D")


def as_candle(k: int) -> Candle:
    r = CANDLES.iloc[k]
    return Candle(
        time=unix(r["ts"]), open=r["open"], high=r["high"], low=r["low"], close=r["close"], volume=r["volume"]
    )


def make(
    k: int,
    p_up=0.6,
    base=0.5,
    abstain=False,
    perfect=True,
    method="analog_v1",
    steps=3,
    instrument="NSE:RELIANCE",
    patterns=("hammer",),
) -> Forecast:
    ghosts = (
        [as_candle(k + s) for s in range(1, steps + 1)]
        if perfect
        else [
            Candle(time=unix(CANDLES["ts"].iloc[k + s]), open=1.0, high=2.0, low=0.5, close=1.5, volume=0)
            for s in range(1, steps + 1)
        ]
    )
    bands = [Band(time=g.time, p10=g.close - 1, p50=g.close, p90=g.close + 1) for g in ghosts]
    return Forecast(
        instrument=instrument,
        tf="1D",
        method=method,
        made_at=unix(close_of(k)) + 60,
        ref_time=unix(CANDLES["ts"].iloc[k]),
        ref_close=float(CANDLES["close"].iloc[k]),
        horizon_bars=steps,
        p_up=p_up,
        p_up_ci=None,
        base_rate=base,
        abstain=abstain,
        abstain_reason="edge below minimum" if abstain else None,
        confidence=None,
        expected_move_pct=None,
        ghost_candles=ghosts,
        bands=bands,
        invalidation=None,
        drivers=[],
        n_analogs=40,
        explanation=None,
        context=ForecastContext(patterns=list(patterns), trend="up", vol_regime="normal", atr=2.0),
    )


def loader_until(k_last: int):
    def load(instrument_id, tf, start=None, end=None):
        return CANDLES.iloc[: k_last + 1]

    return load


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "db" / "ledger.sqlite")


def test_record_is_write_once(ledger):
    fc = make(50)
    now = close_of(50) + pd.Timedelta(minutes=1)
    fid = ledger.record(fc, now=now)
    assert fid == 1
    with pytest.raises(DuplicateForecast):
        ledger.record(fc, now=now)
    assert ledger.record(make(50, method="baseline_base_rate"), now=now) == 2
    with sqlite3.connect(ledger.path) as con:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        with pytest.raises(sqlite3.DatabaseError, match="write-once"):
            con.execute("UPDATE forecasts SET p_up = 0.9 WHERE id = 1")
        with pytest.raises(sqlite3.DatabaseError, match="write-once"):
            con.execute("DELETE FROM forecasts WHERE id = 1")
    stored = ledger.forecast(fid)
    assert stored.model_dump() == fc.model_dump()


def test_late_forecasts_are_rejected(ledger):
    with pytest.raises(LateForecast):
        ledger.record(make(50), now=close_of(51) + pd.Timedelta(minutes=1))


def test_grading_waits_for_all_steps_then_grades(ledger):
    fc = make(60, p_up=0.6, base=0.5)
    fid = ledger.record(fc, now=close_of(60) + pd.Timedelta(minutes=1))
    assert ledger.grade_pending(loader_until(61), now=close_of(61)) == 0
    entry = ledger.entries()[0]
    assert entry.status == "pending" and len(entry.actual) == 1 and entry.grade is None

    assert ledger.grade_pending(loader_until(70), now=close_of(70)) == 1
    entry = ledger.entries()[0]
    assert entry.id == fid and entry.status == "graded"
    assert [c.time for c in entry.actual] == [g.time for g in fc.ghost_candles]
    g = entry.grade
    assert g.match_score == pytest.approx(100.0)
    assert all(
        s.range_iou == 1.0 and s.body_iou == 1.0 and s.close_err_atr == 0 and s.color_match and s.in_band_80
        for s in g.steps
    )
    up = CANDLES["close"].iloc[63] > CANDLES["close"].iloc[60]
    assert g.direction_hit == up
    assert g.brier == pytest.approx((0.6 - float(up)) ** 2)
    with sqlite3.connect(ledger.path) as con, pytest.raises(sqlite3.DatabaseError, match="final"):
        con.execute("UPDATE outcomes SET status = 'pending' WHERE forecast_id = ?", (fid,))


def test_step_grades_known_answer(ledger):
    fc = make(80, perfect=False, steps=1)
    ledger.record(fc, now=close_of(80) + pd.Timedelta(minutes=1))
    ledger.grade_pending(loader_until(90), now=close_of(90))
    step = ledger.entries()[0].grade.steps[0]
    act = CANDLES.iloc[81]
    assert step.close_err_atr == pytest.approx(abs(1.5 - act["close"]) / 2.0)
    assert step.close_err_pct == pytest.approx(100 * abs(1.5 - act["close"]) / act["close"])
    assert step.high_err_atr == pytest.approx(abs(2.0 - act["high"]) / 2.0)
    assert step.range_iou == 0.0 and not step.in_band_80
    assert step.color_match == (act["close"] > act["open"])


def test_abstained_forecast_has_no_direction_hit(ledger):
    ledger.record(make(100, p_up=0.52, abstain=True), now=close_of(100) + pd.Timedelta(minutes=1))
    ledger.grade_pending(loader_until(110), now=close_of(110))
    g = ledger.entries()[0].grade
    assert g.direction_hit is None and g.brier is not None


def test_missing_data_is_voided_after_a_week(ledger):
    ledger.record(make(100), now=close_of(100) + pd.Timedelta(minutes=1))
    assert ledger.grade_pending(loader_until(100), now=close_of(104)) == 0
    assert ledger.entries()[0].status == "pending"
    assert ledger.grade_pending(loader_until(100), now=close_of(103) + pd.Timedelta(days=8)) == 1
    assert ledger.entries(status="void")[0].status == "void"


def test_interval_iou_and_match_score():
    assert interval_iou((0, 2), (1, 3)) == pytest.approx(1 / 3)
    assert interval_iou((0, 1), (2, 3)) == 0.0
    assert interval_iou((1, 1), (1, 1)) == 1.0
    assert interval_iou((3, 1), (1, 3)) == 1.0
    assert match_score([]) == 0.0


def test_accuracy_known_values(ledger):
    cases = [(20, 0.7, False), (25, 0.7, False), (30, 0.3, False), (35, 0.52, True)]
    for k, p, abstain in cases:
        ledger.record(
            make(k, p_up=p, base=0.5, abstain=abstain, perfect=True),
            now=close_of(k) + pd.Timedelta(minutes=1),
        )
    ledger.grade_pending(loader_until(60), now=close_of(60))
    closes = CANDLES["close"]
    ys = [float(closes.iloc[k + 3] > closes.iloc[k]) for k, _, _ in cases]
    scored = [(p, y) for (k, p, a), y in zip(cases, ys, strict=True) if not a]
    hits = [(p > 0.5) == (y == 1.0) for p, y in scored]
    brier = sum((p - y) ** 2 for p, y in scored) / 3
    acc = ledger.accuracy(now=close_of(60), days=3650)
    s = acc.summary
    assert (s.n_forecasts, s.n_graded, s.n_abstained) == (4, 4, 1)
    assert s.direction_hit_rate == pytest.approx(sum(hits) / 3)
    assert s.brier == pytest.approx(brier)
    assert s.brier_baseline == pytest.approx(0.25)
    assert s.skill == pytest.approx(1 - brier / 0.25)
    assert s.mean_match_score == pytest.approx(100.0)
    assert s.band_coverage_80 == 1.0 and s.mean_close_err_atr == 0.0
    assert len(acc.calibration) == 10 and sum(b.n for b in acc.calibration) == 3
    b7 = acc.calibration[7]
    obs7 = sum(y for p, y in scored if p == 0.7) / 2
    obs3 = sum(y for p, y in scored if p == 0.3)
    assert (b7.n, b7.mean_pred, b7.observed) == (2, pytest.approx(0.7), pytest.approx(obs7))
    assert acc.calibration[0].mean_pred is None
    assert s.ece == pytest.approx(2 / 3 * abs(0.7 - obs7) + 1 / 3 * abs(0.3 - obs3))
    assert len(acc.rolling) == 4
    groups = {(g.group_by, g.key): g for g in acc.by_group}
    assert groups[("instrument", "NSE:RELIANCE")].n == 3
    assert groups[("pattern", "hammer")].n == 3 and groups[("session_phase", "none")].n == 3

    assert ledger.accuracy(method="baseline_persistence", now=close_of(60)).summary.n_forecasts == 0
    assert ledger.accuracy(now=close_of(60) + pd.Timedelta(days=400)).summary.n_forecasts == 0
