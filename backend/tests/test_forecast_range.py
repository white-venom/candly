import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import candly.research
from candly.api.routes import analytics
from candly.core.calendar import get_calendar, reload_calendar
from candly.core.schema import empty_candles
from candly.data import clock
from candly.forecast import latest
from candly.forecast import range as range_forecast
from candly.forecast.jobs import grade_pending_job, run_forecast_cycle
from candly.forecast.models import Band, Candle
from candly.forecast.range import DIRECTION_UNCLEAR, RangeUnavailable, make_range_forecast
from candly.forecast.timing import future_bar_times
from candly.ledger import Ledger
from candly.ledger.accuracy import category_shares
from candly.ledger.grading import candle_category, grade
from candly.research import range_model
from candly.research.config import load_research_config
from candly.research.lgbm import lgb  # noqa: F401  (loads LightGBM the safe way before anything else)
from candly.research.range_features import feature_columns, range_features
from candly.research.range_model import build_panel, fit_production, load_model, save_model
from candly.research.synthetic import synthetic_candles

FULL = {
    ("NSE:RELIANCE", "1D"): synthetic_candles("1D", "2012-01-01", "2024-07-31", seed=91),
    ("NSE:TCS", "1D"): synthetic_candles("1D", "2012-01-01", "2024-07-31", seed=92),
    ("NSE:NIFTY50", "1D"): synthetic_candles("1D", "2012-01-01", "2024-07-31", seed=93, price=20000.0),
    ("NSE:INDIAVIX", "1D"): synthetic_candles(
        "1D", "2012-01-01", "2024-07-31", seed=94, price=15.0, vol=0.05
    ),
}
CUT = pd.Timestamp("2024-06-28 03:45", tz="UTC")
FORECAST_KEYS = {
    "trade", "instrument", "tf", "method", "made_at", "ref_time", "ref_close", "horizon_bars", "p_up",
    "p_up_ci", "base_rate", "abstain", "abstain_reason", "confidence", "expected_move_pct", "ghost_candles",
    "bands", "invalidation", "drivers", "n_analogs", "explanation",
}  # fmt: skip


def loader(until: pd.Timestamp):
    def load(instrument_id, tf, start=None, end=None):
        df = FULL.get((instrument_id, tf))
        if df is None:
            return empty_candles()
        df = df[df["ts"] <= until]
        return (df[df["ts"] < end] if end is not None else df).reset_index(drop=True)

    return load


def now_after(ts: pd.Timestamp) -> pd.Timestamp:
    return get_calendar().bar_close_time("NSE", ts, "1D") + pd.Timedelta(minutes=2)


@pytest.fixture(autouse=True)
def no_kept_forecasts():
    latest.clear()
    yield
    latest.clear()


@pytest.fixture
def saved_model(tmp_data_dir, monkeypatch):
    """A small NSE 1D range_v1 model trained on synthetic bars before CUT, saved to the test data dir."""
    reload_calendar()
    range_forecast.clear_cache()
    monkeypatch.setattr(range_model, "NUM_ROUNDS", 5)
    monkeypatch.setattr(
        range_model, "HYPERPARAMETERS", {**range_model.HYPERPARAMETERS, "min_data_in_leaf": 20}
    )
    panel = build_panel("NSE", "1D", load=loader(CUT), instruments=["NSE:RELIANCE", "NSE:TCS"])
    save_model(fit_production(panel), "NSE", "1D")
    yield load_model("NSE", "1D")
    range_forecast.clear_cache()
    reload_calendar()


@pytest.fixture
def no_regime(monkeypatch):
    fake = ModuleType("candly.research.regime")
    monkeypatch.setitem(sys.modules, "candly.research.regime", fake)
    monkeypatch.setattr(candly.research, "regime", fake, raising=False)
    return fake


def _forecast(instrument="NSE:RELIANCE", until=CUT, **kw):
    df = loader(until)(instrument, "1D")
    return make_range_forecast(instrument, "1D", df, now_after(until), load=loader(until), **kw)


def test_range_forecast_draws_the_expected_candles(saved_model, no_regime):
    fc = _forecast()
    df = loader(CUT)("NSE:RELIANCE", "1D")
    ref = float(df["close"].iloc[-1])
    assert set(fc.model_dump(mode="json")) == FORECAST_KEYS
    assert fc.method == "range_v1" and fc.horizon_bars == 3 and fc.n_analogs == 0
    assert fc.abstain and fc.abstain_reason == DIRECTION_UNCLEAR and fc.p_up is None and fc.trade is None
    assert fc.ref_time == int(CUT.timestamp()) and fc.ref_close == ref
    times = [int(t.timestamp()) for t in future_bar_times("NSE", "1D", CUT, 3)]
    assert [g.time for g in fc.ghost_candles] == [b.time for b in fc.bands] == times
    prev = ref
    for g, b in zip(fc.ghost_candles, fc.bands, strict=True):
        assert g.open == pytest.approx(prev) and g.close == pytest.approx(b.p50) and g.volume == 0
        assert g.high >= max(g.open, g.close) >= min(g.open, g.close) >= g.low
        assert b.p10 <= b.p50 <= b.p90
        prev = g.close
    assert fc.expected_move_pct == pytest.approx(100 * (fc.ghost_candles[-1].close - ref) / ref)
    assert fc.context.atr > 0 and {d.name for d in fc.drivers} >= {
        "Expected range",
        "Volatility",
        "Direction",
    }


def test_range_forecast_equals_the_model_on_full_history_features(saved_model, no_regime):
    """Live features are built on a tail of the series; they must match the full-history features."""
    fc = _forecast()
    df = loader(CUT)("NSE:RELIANCE", "1D")
    from candly.research.range_features import RangeContext

    ctx = RangeContext(
        market=loader(CUT)("NSE:NIFTY50", "1D"),
        vix=loader(CUT)("NSE:INDIAVIX", "1D"),
        vix_daily=loader(CUT)("NSE:INDIAVIX", "1D"),
    )
    feats = range_features(df, "1D", "NSE:RELIANCE", ctx).iloc[-1]
    X = np.append(feats[feature_columns()].to_numpy(dtype=np.float32), 0.0)[None, :].astype(np.float32)
    q = saved_model.predict(X)[0]
    ref, atr = float(df["close"].iloc[-1]), float(feats["atr"])
    np.testing.assert_allclose([b.p10 for b in fc.bands], ref + q[:, 2, 0] * atr, rtol=1e-6)
    np.testing.assert_allclose([b.p90 for b in fc.bands], ref + q[:, 2, 2] * atr, rtol=1e-6)


def test_range_forecast_unavailable_outside_the_saved_models(saved_model):
    with pytest.raises(RangeUnavailable):  # not one of the model's instruments
        make_range_forecast("NSE:INFY", "1D", FULL[("NSE:TCS", "1D")], now_after(CUT))
    with pytest.raises(RangeUnavailable):  # no 15m model saved
        make_range_forecast("NSE:RELIANCE", "15m", FULL[("NSE:RELIANCE", "1D")], now_after(CUT))


def test_stale_range_forecast_has_no_candles(saved_model, no_regime):
    df = loader(CUT)("NSE:RELIANCE", "1D")
    fc = make_range_forecast(
        "NSE:RELIANCE", "1D", df, now_after(CUT) + pd.Timedelta(days=6), load=loader(CUT)
    )
    assert fc.abstain and fc.abstain_reason.startswith("stale data") and not fc.ghost_candles


def test_predictions_are_cached_per_reference_bar(saved_model, no_regime, monkeypatch):
    calls = []
    real = range_forecast._predict
    monkeypatch.setattr(range_forecast, "_predict", lambda *a: calls.append(1) or real(*a))
    first, second = _forecast(), _forecast()
    assert len(calls) == 1 and first.bands == second.bands
    _forecast(until=CUT - pd.Timedelta(days=1))
    assert len(calls) == 2


def _call(ref_time, **kw):
    base = dict(
        validated=True, direction="up", p_up=0.58, base_rate=0.53, confidence="medium", horizon_days=5,
        ref_time=ref_time, reason="validated on 2019-2025",
    )  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def test_a_validated_regime_call_gives_direction(saved_model, no_regime):
    no_regime.predict_regime = lambda instrument_id, candles, now, **kw: _call(int(CUT.timestamp()))
    fc = _forecast()
    cfg = load_research_config()
    assert not fc.abstain and fc.abstain_reason is None
    assert fc.p_up == 0.58 and fc.base_rate == 0.53 and fc.confidence == "medium" and fc.horizon_bars == 5
    assert len(fc.ghost_candles) == 3 and fc.drivers[0].name.startswith("Regime")
    assert fc.invalidation == pytest.approx(fc.ref_close - max(cfg.fallback_stop_atr, 0.5) * fc.context.atr)


@pytest.mark.parametrize(
    "call",
    [
        _call(int(CUT.timestamp()), validated=False, direction=None, reason="not validated"),
        _call(int(CUT.timestamp()), confidence="low", direction=None),
        _call(int(CUT.timestamp()) - 86400),  # another reference bar
        None,
    ],
)
def test_no_validated_call_means_range_only(saved_model, no_regime, call):
    no_regime.predict_regime = lambda *a, **kw: call
    fc = _forecast()
    assert fc.abstain and fc.abstain_reason == DIRECTION_UNCLEAR and fc.p_up is None and fc.base_rate is None
    assert fc.ghost_candles and fc.bands and fc.horizon_bars == 3
    regime = [d for d in fc.drivers if d.name.startswith("Regime")]
    if call is None or call.ref_time != int(CUT.timestamp()):
        assert not regime  # no call, or one made on another bar
    elif not call.validated:
        assert regime[0].effect == "neutral" and "p(up)" not in regime[0].detail  # never show its number
        assert "makes no call" in regime[0].detail and call.reason in regime[0].detail


def test_a_model_with_other_features_is_unavailable(saved_model, no_regime):
    import dataclasses

    stale = dataclasses.replace(saved_model, feature_names=[*saved_model.feature_names, "retired_feature"])
    with pytest.raises(RangeUnavailable, match="retrain"):
        _forecast(model=stale)


def test_live_tail_features_equal_full_history_intraday():
    """Intraday the live forecast builds features on the last TAIL_SESSIONS sessions (instrument, market
    index and VIX); the reference bar's features must equal those built on the full history."""
    reload_calendar()
    tf, start, end = "5m", "2024-01-01", "2024-03-28"
    df = synthetic_candles(tf, start, end, seed=41, vol=0.004)
    frames = {
        ("NSE:NIFTY50", tf): synthetic_candles(tf, start, end, seed=42, price=20000.0, vol=0.004),
        ("NSE:INDIAVIX", tf): synthetic_candles(tf, start, end, seed=43, price=15.0, vol=0.03),
        ("NSE:INDIAVIX", "1D"): synthetic_candles("1D", "2023-01-01", end, seed=44, price=15.0, vol=0.05),
    }
    ref_ts = df["ts"].iloc[-1]
    from candly.research.range_features import load_context

    full = range_features(df, tf, "NSE:RELIANCE", load_context("NSE", tf, lambda i, t: frames.get((i, t))))
    tail = range_forecast._tail(df, tf)
    assert len(tail) < len(df) and tail["ts"].dt.tz_convert("Asia/Kolkata").dt.date.nunique() == 25
    ctx = range_forecast._context_series(lambda i, t: frames.get((i, t)), "NSE", tf, ref_ts)
    live = range_features(tail, tf, "NSE:RELIANCE", ctx).iloc[-1]
    expected = full.iloc[-1]
    assert expected[feature_columns()].notna().all()
    np.testing.assert_allclose(live[feature_columns()], expected[feature_columns()], rtol=1e-6, atol=1e-9)


def test_regime_failures_never_break_the_range_forecast(saved_model, no_regime):
    def boom(*a, **kw):
        raise RuntimeError("model file missing")

    no_regime.predict_regime = boom
    assert _forecast().abstain_reason == DIRECTION_UNCLEAR


# ---------------------------------------------------------------- grading


def _candle(o, h, lo, c):
    return Candle(time=0, open=o, high=h, low=lo, close=c, volume=0)


def test_candle_categories():
    pred = _candle(100, 104, 97, 101)
    band = Band(time=0, p10=96, p50=101, p90=106)
    atr = 4.0  # "same" = close within 1.0 of the median
    assert candle_category(pred, _candle(100, 103, 98, 101.5), band, atr, 0.25) == "same"
    assert candle_category(pred, _candle(100, 105, 98, 101.5), band, atr, 0.25) == "close"  # high pokes out
    assert candle_category(pred, _candle(100, 103, 98, 103.0), band, atr, 0.25) == "close"  # close too far
    assert candle_category(pred, _candle(100, 108, 98, 107.0), band, atr, 0.25) == "wrong"
    assert candle_category(pred, _candle(100, 103, 98, 101.5), None, atr, 0.25) is None


def test_grades_carry_categories_and_accuracy_shares(saved_model, no_regime):
    fc = _forecast()
    later = loader(pd.Timestamp("2024-07-31", tz="UTC"))("NSE:RELIANCE", "1D")
    times = [pd.Timestamp(g.time, unit="s", tz="UTC") for g in fc.ghost_candles]
    actual = [
        _candle(*later.loc[later["ts"] == t, ["open", "high", "low", "close"]].iloc[0].tolist())
        for t in times
    ]
    g = grade(fc, actual, fc.context.atr)
    assert [s.category for s in g.steps] and all(s.category in {"same", "close", "wrong"} for s in g.steps)
    assert all((s.category == "wrong") == (not s.in_band_80) for s in g.steps)
    shares = category_shares([s.model_dump() for s in g.steps])
    assert shares.same + shares.close + shares.wrong == pytest.approx(1.0)
    assert category_shares([{"step": 1}]) is None


# ---------------------------------------------------------------- ledger job and API


def test_cycle_records_range_forecasts_and_grades_them(tmp_path, saved_model, no_regime, monkeypatch):
    now = now_after(CUT)
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    book = Ledger(tmp_path / "ledger.sqlite")
    ids = ["NSE:RELIANCE", "NSE:TCS", "NSE:SBIN"]
    counts = run_forecast_cycle("1D", ids, load=loader(CUT), ledger=book, now=now)
    assert counts["range_recorded"] == 2 and counts["errors"] == 0 and counts["no_data"] == 1
    ranged = book.entries(method="range_v1")
    assert len(ranged) == 2 and all(e.abstain and e.p_up is None and len(e.predicted) == 3 for e in ranged)

    later = pd.Timestamp("2024-07-15 03:45", tz="UTC")
    grade_pending_job(load=loader(later), ledger=book, now=get_calendar().bar_close_time("NSE", later, "1D"))
    graded = book.entries(method="range_v1", status="graded")
    assert len(graded) == 2
    assert all(s.category in {"same", "close", "wrong"} for e in graded for s in e.grade.steps)
    acc = book.accuracy(method="range_v1", now=later + pd.Timedelta(days=1))
    assert acc.summary.n_graded == 2 and acc.summary.n_abstained == 2
    shares = acc.category_shares
    assert shares is not None and shares.same + shares.close + shares.wrong == pytest.approx(1.0)
    steps = [s for e in graded for s in e.grade.steps]
    assert shares.wrong == pytest.approx(sum(not s.in_band_80 for s in steps) / len(steps))
    assert acc.summary.direction_hit_rate is None and acc.summary.band_coverage_80 is not None


@pytest.fixture
def client(saved_model, no_regime, monkeypatch):
    now = now_after(CUT)
    monkeypatch.setattr(analytics, "_load_candles", lambda i, tf: loader(CUT)(i, tf))
    monkeypatch.setattr(analytics, "_get_forming", lambda i, tf: None)
    monkeypatch.setattr(analytics, "_now", lambda: now)
    app = FastAPI()
    app.include_router(analytics.router, prefix="/api")
    return TestClient(app)


def test_forecast_route_defaults_to_range_v1(client):
    params = {"instrument": "NSE:RELIANCE", "tf": "1D"}
    body = client.get("/api/forecast", params=params).json()
    assert body["method"] == "range_v1" and body["abstain"] and len(body["ghost_candles"]) == 3
    assert (
        client.get("/api/forecast", params={**params, "method": "analog_v1"}).json()["method"] == "analog_v1"
    )
    assert (
        client.get("/api/forecast", params={**params, "method": "range_v1", "steps": 2}).json()[
            "horizon_bars"
        ]
        == 2
    )
    assert client.get("/api/forecast", params={**params, "method": "magic"}).status_code == 400
    sbin = {"instrument": "NSE:SBIN", "tf": "1D"}
    FULL[("NSE:SBIN", "1D")] = FULL[("NSE:TCS", "1D")]
    try:
        assert client.get("/api/forecast", params=sbin).json()["method"] == "analog_v1"  # not in the model
        assert client.get("/api/forecast", params={**sbin, "method": "range_v1"}).status_code == 503
    finally:
        del FULL[("NSE:SBIN", "1D")]


def test_scanner_shows_expected_moves_without_direction_scores(client):
    rows = client.get("/api/scanner", params={"tf": "1D"}).json()
    by_id = {r["instrument"]: r for r in rows}
    for instrument in ("NSE:RELIANCE", "NSE:TCS"):
        row = by_id[instrument]
        assert row["abstain"] and row["score"] == 0 and row["expected_move_pct"] is not None
        assert row["abstain_reason"] == DIRECTION_UNCLEAR


def test_accuracy_route_reports_category_shares(client, tmp_path, monkeypatch):
    now = now_after(CUT)
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    from candly.ledger import default_ledger_path

    book = Ledger(default_ledger_path())
    run_forecast_cycle("1D", ["NSE:RELIANCE"], load=loader(CUT), ledger=book, now=now)
    later = pd.Timestamp("2024-07-15 03:45", tz="UTC")
    grade_pending_job(load=loader(later), ledger=book, now=get_calendar().bar_close_time("NSE", later, "1D"))
    monkeypatch.setattr(analytics, "_now", lambda: later)
    acc = client.get("/api/accuracy", params={"method": "range_v1"}).json()
    assert acc["summary"]["n_graded"] == 1 and "category_shares" not in acc["summary"]
    assert set(acc["category_shares"]) == {"same", "close", "wrong"}  # top level, as frontend/types.ts reads
    entries = client.get("/api/ledger", params={"method": "range_v1"}).json()
    assert len(entries) == 1 and all("category" in s for s in entries[0]["grade"]["steps"])


def test_intraday_cycles_skip_analog_v1(tmp_path, tmp_data_dir, monkeypatch):
    """analog_v1 scans every past bar; intraday cycles record range_v1 and baselines only."""
    bars = synthetic_candles("5m", "2024-06-24", "2024-06-28", seed=95, vol=0.003)
    now = get_calendar().bar_close_time("NSE", bars["ts"].iloc[-1], "5m") + pd.Timedelta(seconds=30)
    monkeypatch.setattr(clock, "utc_now", lambda: now)

    def analog(*a, **kw):
        raise AssertionError("analog_v1 must not run on intraday cycles")

    monkeypatch.setattr("candly.forecast.jobs.make_forecast", analog)
    frames = {("NSE:RELIANCE", "5m"): bars}
    counts = run_forecast_cycle(
        "5m",
        ["NSE:RELIANCE"],
        load=lambda i, tf, start=None, end=None: frames.get((i, tf), empty_candles()),
        ledger=Ledger(tmp_path / "ledger.sqlite"),
        now=now,
    )
    assert counts["errors"] == 0 and counts["range_unavailable"] == 1 and counts["recorded"] == 0
    assert counts["seconds"] >= 0


def test_scanner_serves_the_cycles_forecasts_without_reading_candles(client, tmp_path, monkeypatch):
    now = now_after(CUT)
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    run_forecast_cycle(
        "1D", ["NSE:RELIANCE", "NSE:TCS"], load=loader(CUT), ledger=Ledger(tmp_path / "l.db"), now=now
    )
    kept = latest.latest("NSE:RELIANCE", "1D")
    assert kept is not None and kept.forecast.method == "range_v1" and len(kept.tail) == latest.TAIL_BARS

    def no_reads(*a, **kw):
        raise AssertionError("the scanner re-read candles that the forecast cycle already kept")

    monkeypatch.setattr(
        analytics,
        "_load_candles",
        lambda i, tf: empty_candles() if i not in {"NSE:RELIANCE", "NSE:TCS"} else no_reads(),
    )
    rows = {r["instrument"]: r for r in client.get("/api/scanner", params={"tf": "1D"}).json()}
    df = loader(CUT)("NSE:RELIANCE", "1D")
    row = rows["NSE:RELIANCE"]
    assert row["time"] == int(CUT.timestamp()) and row["expected_move_pct"] == kept.forecast.expected_move_pct
    assert row["change_pct"] == pytest.approx(100 * (df["close"].iloc[-1] / df["close"].iloc[-2] - 1))


def test_a_kept_forecast_goes_stale_when_no_newer_bar_arrives(client, tmp_path, monkeypatch):
    now = now_after(CUT)
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    run_forecast_cycle("1D", ["NSE:RELIANCE"], load=loader(CUT), ledger=Ledger(tmp_path / "l.db"), now=now)
    monkeypatch.setattr(analytics, "_now", lambda: now + pd.Timedelta(days=6))
    rows = {r["instrument"]: r for r in client.get("/api/scanner", params={"tf": "1D"}).json()}
    row = rows["NSE:RELIANCE"]
    assert row["abstain"] and row["abstain_reason"].startswith("stale data") and row["score"] == 0


def test_a_slow_broker_cannot_hold_up_the_scanner(client, tmp_path, monkeypatch):
    import time

    now = now_after(CUT)
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    run_forecast_cycle("1D", ["NSE:RELIANCE"], load=loader(CUT), ledger=Ledger(tmp_path / "l.db"), now=now)
    monkeypatch.setattr(analytics, "FORMING_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(analytics, "_get_forming", lambda i, tf: time.sleep(2.0))
    started = time.perf_counter()
    rows = client.get("/api/scanner", params={"tf": "1D"}).json()
    assert time.perf_counter() - started < 1.5
    assert any(r["instrument"] == "NSE:RELIANCE" for r in rows)


def test_a_cycle_reads_each_series_once(tmp_path, saved_model, no_regime, monkeypatch):
    now = now_after(CUT)
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    reads = []
    base = loader(CUT)

    def counting(instrument_id, tf, start=None, end=None):
        reads.append((instrument_id, tf))
        return base(instrument_id, tf, start, end)

    ids = ["NSE:RELIANCE", "NSE:TCS"]
    counts = run_forecast_cycle("1D", ids, load=counting, ledger=Ledger(tmp_path / "l.db"), now=now)
    assert counts["range_recorded"] == 2
    assert sorted(set(reads)) == sorted(reads)  # the market index and VIX once, not once per instrument
    assert {("NSE:RELIANCE", "1D"), ("NSE:TCS", "1D"), ("NSE:NIFTY50", "1D")} <= set(reads)


def test_warm_up_fills_the_scanner_cache(client, monkeypatch):
    took = analytics.warm_scanner(["1D"])
    assert set(took) == {"1D"} and took["1D"] >= 0
    kept = latest.latest("NSE:RELIANCE", "1D")
    assert kept is not None and kept.forecast.method == "range_v1"
    warmed = {"NSE:RELIANCE", "NSE:TCS"}
    monkeypatch.setattr(
        analytics,
        "_load_candles",
        lambda i, tf: pytest.fail(f"the scanner re-read {i}") if i in warmed else empty_candles(),
    )
    rows = {r["instrument"]: r for r in client.get("/api/scanner", params={"tf": "1D"}).json()}
    assert rows["NSE:RELIANCE"]["expected_move_pct"] == kept.forecast.expected_move_pct


def test_memoized_loader_reads_each_series_once_and_cuts_like_the_store():
    from candly.forecast.jobs import memoized

    reads = []
    bars = FULL[("NSE:RELIANCE", "1D")]
    load = memoized(lambda i, tf: reads.append((i, tf)) or bars)
    start, end = bars["ts"].iloc[10], bars["ts"].iloc[20]
    cut = load("NSE:RELIANCE", "1D", start=start, end=end)
    assert len(load("NSE:RELIANCE", "1D")) == len(bars) and reads == [("NSE:RELIANCE", "1D")]
    assert cut["ts"].iloc[0] == start and cut["ts"].iloc[-1] == end and len(cut) == 11
