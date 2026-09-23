import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from candly.api.routes import analytics
from candly.core.calendar import get_calendar
from candly.core.schema import empty_candles
from candly.forecast.jobs import run_forecast_cycle
from candly.indicators import INDICATOR_CATALOG
from candly.indicators.functions import atr as atr_fn
from candly.ledger import Ledger, default_ledger_path
from candly.research.scorecard import build_scorecard
from candly.research.synthetic import synthetic_candles

FRAMES = {
    ("NSE:RELIANCE", "1D"): synthetic_candles("1D", "2012-01-01", "2024-06-28", seed=71),
    ("NSE:TCS", "1D"): synthetic_candles("1D", "2012-01-01", "2024-06-28", seed=72),
    ("NSE:RELIANCE", "15m"): synthetic_candles("15m", "2024-05-01", "2024-06-28", seed=73, vol=0.003),
}
LAST = FRAMES[("NSE:RELIANCE", "1D")]["ts"].iloc[-1]
NOW = get_calendar().bar_close_time("NSE", LAST, "1D") + pd.Timedelta(minutes=2)
FORECAST_KEYS = {
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
    }
    assert sig["stats"] is None


def test_levels(client):
    body = client.get("/api/levels", params={"instrument": "NSE:RELIANCE", "tf": "15m"}).json()
    kinds = {lv["kind"] for lv in body["levels"]}
    assert {"pdh", "pdl", "pdc", "pivot", "vwap"} <= kinds
    assert all(set(lv) == {"price", "label", "kind"} for lv in body["levels"])


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


def test_scanner(client):
    rows = client.get("/api/scanner", params={"tf": "1D"}).json()
    assert {r["instrument"] for r in rows} == {"NSE:RELIANCE", "NSE:TCS"}
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    for r in rows:
        assert (
            r["score"] == 0 if r["abstain"] else r["score"] == pytest.approx(abs(r["p_up"] - r["base_rate"]))
        )
        assert r["trend"] in {"up", "down", "sideways", None}
        assert r["time"] == int(LAST.timestamp())


def test_scorecard_route(client):
    assert client.get("/api/scorecard", params={"tf": "1D"}).status_code == 503
    build_scorecard("1D", ["NSE:RELIANCE", "NSE:TCS"], load=load)
    body = client.get("/api/scorecard", params={"tf": "1D"}).json()
    assert body["meta"]["n_tests"] == len(body["rows"]) > 0
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


def test_ledger_and_accuracy_routes(client):
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
