from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from candly.api.routes import analytics
from candly.core.calendar import get_calendar
from candly.core.schema import empty_candles
from candly.data import clock
from candly.forecast.jobs import run_forecast_cycle
from candly.indicators import INDICATOR_CATALOG
from candly.indicators.functions import atr as atr_fn
from candly.ledger import Ledger, default_ledger_path
from candly.patterns import PATTERN_INFO
from candly.research.scorecard import ScoreStats, build_scorecard
from candly.research.synthetic import synthetic_candles

FRAMES = {
    ("NSE:RELIANCE", "1D"): synthetic_candles("1D", "2012-01-01", "2024-06-28", seed=71),
    ("NSE:TCS", "1D"): synthetic_candles("1D", "2012-01-01", "2024-06-28", seed=72),
    ("NSE:RELIANCE", "15m"): synthetic_candles("15m", "2024-05-01", "2024-06-28", seed=73, vol=0.003),
}
LAST = FRAMES[("NSE:RELIANCE", "1D")]["ts"].iloc[-1]
NOW = get_calendar().bar_close_time("NSE", LAST, "1D") + pd.Timedelta(minutes=2)
FORECAST_KEYS = {
    "trade",
    "instrument",
    "tf",
    "method",
    "made_at",
    "ref_time",
    "ref_close",
    "horizon_bars",
    "p_up",
    "p_up_ci",
    "base_rate",
    "abstain",
    "abstain_reason",
    "confidence",
    "expected_move_pct",
    "ghost_candles",
    "bands",
    "invalidation",
    "drivers",
    "n_analogs",
    "explanation",
}


def load(instrument_id, tf, start=None, end=None):
    return FRAMES.get((instrument_id, tf), empty_candles())


@pytest.fixture
def forming(monkeypatch):
    state = {"bar": None}
    monkeypatch.setattr(
        analytics, "_get_forming", lambda i, tf: state["bar"] if i == "NSE:RELIANCE" else None
    )
    return state


@pytest.fixture
def client(tmp_data_dir, no_keys, monkeypatch, forming):
    monkeypatch.setattr(analytics, "_load_candles", lambda i, tf: load(i, tf))
    monkeypatch.setattr(analytics, "_now", lambda: NOW)
    app = FastAPI()
    app.include_router(analytics.router, prefix="/api")
    return TestClient(app)


def test_app_mounts_analytics_routes():
    from candly.api.app import create_app

    paths = set(create_app().openapi()["paths"])
    assert {
        "/api/indicators/catalog",
        "/api/indicators",
        "/api/patterns",
        "/api/levels",
        "/api/forecast",
        "/api/scanner",
        "/api/scorecard",
        "/api/ledger",
        "/api/accuracy",
    } <= paths


def test_indicator_catalog(client):
    body = client.get("/api/indicators/catalog").json()
    assert len(body) == len(INDICATOR_CATALOG)
    assert set(body[0]) == {"name", "label", "pane", "group"}


def test_indicators(client):
    r = client.get(
        "/api/indicators",
        params={"instrument": "NSE:RELIANCE", "tf": "1D", "names": "ema20,macd", "limit": 50},
    )
    assert r.status_code == 200
    body = r.json()
    assert [s["name"] for s in body["series"]] == ["ema20", "macd", "macd_signal", "macd_hist"]
    assert all(len(s["points"]) == 50 for s in body["series"])
    assert body["series"][0]["points"][-1]["time"] == int(LAST.timestamp())
    first = client.get(
        "/api/indicators",
        params={"instrument": "NSE:RELIANCE", "tf": "15m", "names": "vwap,ema200", "limit": 5000},
    ).json()
    assert first["series"][1]["points"][0]["value"] is None


@pytest.mark.parametrize(
    "params,status",
    [
        ({"instrument": "NSE:NOPE", "tf": "1D"}, 404),
        ({"instrument": "NSE:RELIANCE", "tf": "2m"}, 400),
        ({"instrument": "NSE:RELIANCE", "tf": "1D", "names": "bogus"}, 400),
        ({"instrument": "NSE:RELIANCE", "tf": "1D", "limit": 0}, 400),
        ({"instrument": "NSE:INFY", "tf": "1D"}, 503),
    ],
)
def test_indicator_errors(client, params, status):
    r = client.get("/api/indicators", params=params)
    assert r.status_code == status and "detail" in r.json()


def test_patterns_newest_first_with_forming(client, forming):
    df = FRAMES[("NSE:RELIANCE", "1D")]
    atr = atr_fn(df, 14).iloc[-1]
    o = df["close"].iloc[-1]
    forming["bar"] = pd.Series(
        {
            "ts": get_calendar().expected_bar_opens("NSE", pd.Timestamp("2024-07-01").date(), "1D")[0],
            "open": o,
            "high": o + 3 * atr + 0.01,
            "low": o - 0.01,
            "close": o + 3 * atr,
            "volume": 1e5,
        }
    )
    body = client.get("/api/patterns", params={"instrument": "NSE:RELIANCE", "tf": "1D", "limit": 30}).json()
    assert len(body) == 30
    assert body[0]["state"] == "forming"
    assert any(s["pattern"] == "bullish_marubozu" and s["state"] == "forming" for s in body)
    times = [s["time"] for s in body]
    assert times == sorted(times, reverse=True)
    sig = body[-1]
    assert sig["id"] == f"NSE:RELIANCE|1D|{sig['time']}|{sig['pattern']}"
    assert set(sig["context"]) == {
        "trend",
        "vol_regime",
        "session_phase",
        "rel_volume",
        "near_level",
        "rsi14",
        "expiry_day",
        "days_to_expiry",
    }
    assert sig["stats"] is None
    assert all(isinstance(s["context"]["expiry_day"], bool) for s in body)
    assert all(s["context"]["days_to_expiry"] >= 0 for s in body)


class CertifyingCard:
    """Scorecard stand-in whose stats certify one pattern only."""

    def __init__(self, certified: str):
        self.certified = certified

    def stats_for(self, pattern, instrument, trend, horizon, min_samples):
        return ScoreStats(
            horizon_bars=horizon,
            n=100,
            hit_rate=0.6,
            base_rate=0.5,
            ci_low=0.5,
            ci_high=0.7,
            posterior=0.58,
            q_value=0.01,
            expectancy_after_cost_pct=0.2,
            certified=pattern == self.certified,
        )


def test_patterns_can_be_filtered(client, monkeypatch):
    params = {"instrument": "NSE:RELIANCE", "tf": "1D", "limit": 40}
    every = client.get("/api/patterns", params=params).json()
    assert any(s["direction"] == "neutral" for s in every)
    directional = client.get("/api/patterns", params={**params, "directional_only": "true"}).json()
    assert len(directional) == 40 and all(s["direction"] != "neutral" for s in directional)
    assert client.get("/api/patterns", params={**params, "certified_only": "true"}).json() == []

    monkeypatch.setattr(analytics, "load_scorecard", lambda tf: CertifyingCard("bullish_engulfing"))
    certified = client.get("/api/patterns", params={**params, "certified_only": "true", "limit": 3}).json()
    assert len(certified) == 3
    assert all(s["pattern"] == "bullish_engulfing" and s["stats"]["certified"] for s in certified)
    times = [s["time"] for s in certified]
    assert times == sorted(times, reverse=True)


def test_index_signals_have_no_relative_volume(client, monkeypatch):
    frames = {("NSE:NIFTY50", "1D"): FRAMES[("NSE:RELIANCE", "1D")]}
    monkeypatch.setattr(analytics, "_load_candles", lambda i, tf: frames.get((i, tf), empty_candles()))
    sigs = client.get("/api/patterns", params={"instrument": "NSE:NIFTY50", "tf": "1D", "limit": 20}).json()
    assert sigs and all(s["context"]["rel_volume"] is None for s in sigs)
    series = client.get(
        "/api/indicators", params={"instrument": "NSE:NIFTY50", "tf": "1D", "names": "rel_volume"}
    ).json()["series"][0]
    assert all(p["value"] is None for p in series["points"])


def test_levels(client):
    body = client.get("/api/levels", params={"instrument": "NSE:RELIANCE", "tf": "15m"}).json()
    kinds = {lv["kind"] for lv in body["levels"]}
    assert {"pdh", "pdl", "pdc", "pivot", "vwap"} <= kinds
    assert all(set(lv) == {"price", "label", "kind"} for lv in body["levels"])
    assert set(body) == {"instrument", "tf", "levels", "as_of", "stale"}
    last_day = FRAMES[("NSE:RELIANCE", "15m")]["ts"].iloc[-1].tz_convert("Asia/Kolkata").date()
    assert body["as_of"] == int(get_calendar().session_times("NSE", last_day)[0].timestamp())
    assert body["stale"] is False


def test_daily_levels_are_stale_once_another_store_has_a_newer_closed_session(client, monkeypatch):
    cal = get_calendar()
    last = {}
    monkeypatch.setattr(analytics, "_series_last", lambda i, tf: last.get(tf))

    def levels_at(now):
        monkeypatch.setattr(analytics, "_now", lambda: now)
        return client.get("/api/levels", params={"instrument": "NSE:RELIANCE", "tf": "1D"}).json()

    body = levels_at(NOW)
    assert body["as_of"] == int(LAST.timestamp()) and body["stale"] is False
    last["1h"] = cal.expected_bar_opens("NSE", LAST.tz_convert("Asia/Kolkata").date(), "1h")[-1]
    assert levels_at(NOW)["stale"] is False
    monday = cal.expected_bar_opens("NSE", date(2024, 7, 1), "1h")
    last["1h"] = monday[2]
    # during Monday's session Friday's daily bar is still the previous day
    assert levels_at(monday[3])["stale"] is False
    # after Monday's close, a 1D series that still ends on Friday is a session behind
    assert levels_at(cal.session_times("NSE", date(2024, 7, 1))[1] + pd.Timedelta(hours=1))["stale"] is True


def test_forecast_is_read_only(client):
    r = client.get("/api/forecast", params={"instrument": "NSE:RELIANCE", "tf": "1D", "steps": 2})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == FORECAST_KEYS
    assert body["horizon_bars"] == 2 and body["method"] == "analog_v1"
    assert body["ref_time"] == int(LAST.timestamp())
    assert not default_ledger_path().exists()
    assert (
        client.get("/api/forecast", params={"instrument": "NSE:RELIANCE", "tf": "1D", "steps": 0}).status_code
        == 400
    )


# seen on Fri 28 Jun 2024: stock F&O's monthly expiry is the last Thursday, 25 Jul, 19 trading days on
JULY_2024_EXPIRY = {"next": "2024-07-25", "kind": "monthly", "days_to_expiry": 19, "is_expiry_day": False}


def test_scanner(client, monkeypatch):
    frames = {
        **FRAMES,
        ("NSE:INDIAVIX", "1D"): FRAMES[("NSE:TCS", "1D")],
        ("NSE:SBIN", "1D"): FRAMES[("NSE:TCS", "1D")].iloc[:-1],
    }
    monkeypatch.setattr(analytics, "_load_candles", lambda i, tf: frames.get((i, tf), empty_candles()))
    rows = client.get("/api/scanner", params={"tf": "1D"}).json()
    assert {r["instrument"] for r in rows} == {"NSE:RELIANCE", "NSE:TCS", "NSE:SBIN"}  # VIX isn't tradable
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    for r in rows:
        assert (
            r["score"] == 0 if r["abstain"] else r["score"] == pytest.approx(abs(r["p_up"] - r["base_rate"]))
        )
        assert r["abstain"] == (r["abstain_reason"] is not None)
        assert r["trend"] in {"up", "down", "sideways", None}
        assert r["expiry"] == JULY_2024_EXPIRY
        if r["top_signal"] is not None:
            label = r["top_signal"]["label"]
            assert next(p for p in PATTERN_INFO.values() if p.label == label).direction != "neutral"
    by_id = {r["instrument"]: r for r in rows}
    assert by_id["NSE:RELIANCE"]["time"] == by_id["NSE:TCS"]["time"] == int(LAST.timestamp())
    sbin = by_id["NSE:SBIN"]
    assert sbin["abstain"] and sbin["abstain_reason"].startswith("stale data") and sbin["score"] == 0
    assert sbin["time"] < int(LAST.timestamp())


def _row(instrument: str, time: int, reason: str | None = None, score: float = 0.1) -> analytics.ScannerRow:
    return analytics.ScannerRow(
        instrument=instrument,
        name=instrument,
        tf="1D",
        time=time,
        last_close=100.0,
        change_pct=0.5,
        p_up=0.6,
        base_rate=0.5,
        abstain=reason is not None,
        abstain_reason=reason,
        score=0.0 if reason else score,
        direction="neutral" if reason else "bullish",
        top_signal=None,
        rel_volume=None,
        trend="up",
        expiry=None,
    )


def test_scanner_ranks_rows_as_of_their_peers_latest_session():
    day = 86_400
    now = pd.Timestamp("2026-09-23 12:00", tz="UTC")
    rows = [
        _row("NSE:RELIANCE", 10 * day),
        _row("BSE:SENSEX", 9 * day),  # BSE keeps NSE's hours, so it is compared with NSE
        _row("NSE:TCS", 9 * day, reason="edge below minimum: 0.01"),
        _row("MCX:GOLD", 8 * day),  # MCX has its own session, so no NSE peer makes it stale
    ]
    out = {r.instrument: r for r in analytics._mark_stale(rows, now)}
    assert not out["NSE:RELIANCE"].abstain and not out["MCX:GOLD"].abstain
    for inst in ("BSE:SENSEX", "NSE:TCS"):
        assert out[inst].abstain and out[inst].score == 0 and out[inst].direction == "neutral"
        assert out[inst].abstain_reason.startswith("stale data: last closed bar")


def test_top_signal_prefers_certified_directional_signals():
    fc = SimpleNamespace(context=SimpleNamespace(trend="up", patterns=["inside_bar", "bullish_engulfing"]))
    forming = pd.DataFrame({"pattern": ["hammer", "doji"]})
    plain = analytics._top_signal(fc, forming, None, "NSE:RELIANCE")
    assert (plain.label, plain.state, plain.certified) == ("Bullish engulfing", "confirmed", False)
    best = analytics._top_signal(fc, forming, CertifyingCard("hammer"), "NSE:RELIANCE")
    assert (best.label, best.state, best.certified) == ("Hammer", "forming", True)
    neutral_only = SimpleNamespace(context=SimpleNamespace(trend="up", patterns=["inside_bar"]))
    certified_neutral = CertifyingCard("inside_bar")
    assert analytics._top_signal(neutral_only, pd.DataFrame({"pattern": []}), certified_neutral, "X") is None


def test_scorecard_route(client):
    assert client.get("/api/scorecard", params={"tf": "1D"}).status_code == 503
    build_scorecard("1D", ["NSE:RELIANCE", "NSE:TCS"], load=load)
    body = client.get("/api/scorecard", params={"tf": "1D"}).json()
    assert body["meta"]["n_rows"] == len(body["rows"]) > body["meta"]["n_tests"] > 0
    tested = [r for r in body["rows"] if r["q_value"] is not None]
    assert len(tested) == body["meta"]["n_tests"]
    assert set(body["rows"][0]) == {
        "pattern",
        "label",
        "direction",
        "instrument",
        "context",
        "horizon_bars",
        "n",
        "hits",
        "hit_rate",
        "base_rate",
        "ci_low",
        "ci_high",
        "p_value",
        "q_value",
        "posterior",
        "expectancy_after_cost_pct",
        "validation_n",
        "validation_hit_rate",
        "certified",
    }
    only = client.get("/api/scorecard", params={"tf": "1D", "instrument": "ALL", "pattern": "doji"}).json()
    assert only["rows"] and {(r["instrument"], r["pattern"]) for r in only["rows"]} == {("ALL", "doji")}
    certified = client.get("/api/scorecard", params={"tf": "1D", "certified_only": "true"}).json()
    assert all(r["certified"] for r in certified["rows"])

    sigs = client.get("/api/patterns", params={"instrument": "NSE:RELIANCE", "tf": "1D", "limit": 5}).json()
    assert any(s["stats"] is not None for s in sigs)


def test_ledger_and_accuracy_routes(client, monkeypatch):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    assert client.get("/api/ledger").json() == []
    empty = client.get("/api/accuracy").json()
    assert empty["summary"]["n_forecasts"] == 0 and len(empty["calibration"]) == 10
    assert client.get("/api/ledger", params={"status": "weird"}).status_code == 400

    counts = run_forecast_cycle("1D", ["NSE:RELIANCE", "NSE:TCS", "NSE:INFY"], load=load, now=NOW)
    assert counts["no_data"] == 1 and counts["recorded"] == 8
    entries = client.get("/api/ledger", params={"instrument": "NSE:TCS"}).json()
    assert len(entries) == 4 and {e["status"] for e in entries} == {"pending"}
    only = client.get("/api/ledger", params={"method": "analog_v1"}).json()
    assert len(only) == 2
    acc = client.get("/api/accuracy", params={"days": 30}).json()
    assert acc["summary"]["n_forecasts"] == 2 and acc["summary"]["n_graded"] == 0
    assert isinstance(Ledger(default_ledger_path()).entries(), list)
