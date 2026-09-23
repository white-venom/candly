"""Acceptance against the running API (http://127.0.0.1:8000). Run with: pytest backend -m network -k acc_api.

Contract shapes (docs/CONTRACTS.md), error codes, host check, latency, freshness, and no secrets in responses.
"""

from __future__ import annotations

import re
import statistics
import time

import pandas as pd
import pytest
from acceptance_helpers import leaks

from candly.forecast.timing import last_expected_closed_bar

pytestmark = pytest.mark.network

SHAPES = {
    "Health": {"status", "version", "time", "data_source", "keys", "markets", "ingest"},
    "Instrument": {"id", "exchange", "symbol", "name", "kind", "tradable", "timeframes", "data"},
    "Candles": {"instrument", "tf", "source", "candles", "forming"},
    "Candle": {"time", "open", "high", "low", "close", "volume"},
    "NewsItem": {
        "id",
        "title",
        "url",
        "source",
        "published_at",
        "fetched_at",
        "instruments",
        "sentiment",
        "sentiment_method",
        "event_type",
        "summary",
    },
    "IndicatorInfo": {"name", "label", "pane", "group"},
    "Indicators": {"instrument", "tf", "series"},
    "IndicatorSeries": {"name", "label", "pane", "points"},
    "PatternSignal": {
        "id",
        "instrument",
        "tf",
        "time",
        "pattern",
        "label",
        "direction",
        "state",
        "bars",
        "invalidation",
        "context",
        "stats",
    },
    "PatternContext": {"trend", "vol_regime", "session_phase", "rel_volume", "near_level", "rsi14"},
    "Levels": {"instrument", "tf", "levels"},
    "Level": {"price", "label", "kind"},
    "Forecast": {
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
    },
    "ScannerRow": {
        "instrument",
        "name",
        "tf",
        "time",
        "last_close",
        "change_pct",
        "p_up",
        "base_rate",
        "abstain",
        "score",
        "direction",
        "top_signal",
        "rel_volume",
        "trend",
    },
    "Accuracy": {"summary", "calibration", "rolling", "by_group"},
}


def _keys(name: str, obj: dict) -> None:
    assert set(obj) == SHAPES[name], (
        f"{name}: missing {SHAPES[name] - set(obj)}, extra {set(obj) - SHAPES[name]}"
    )


def test_every_get_endpoint_matches_the_contract(api):
    _keys("Health", api.get("/api/health").json())
    for inst in api.get("/api/instruments").json():
        _keys("Instrument", inst)
    candles = api.get("/api/candles", params={"instrument": "NSE:RELIANCE", "tf": "1D", "limit": 20}).json()
    _keys("Candles", candles)
    for c in candles["candles"]:
        _keys("Candle", c)
    for n in api.get("/api/news", params={"limit": 20}).json():
        _keys("NewsItem", n)
    for i in api.get("/api/indicators/catalog").json():
        _keys("IndicatorInfo", i)
    ind = api.get(
        "/api/indicators", params={"instrument": "NSE:TCS", "tf": "1D", "names": "macd,rsi14", "limit": 20}
    ).json()
    _keys("Indicators", ind)
    for s in ind["series"]:
        _keys("IndicatorSeries", s)
    for p in api.get("/api/patterns", params={"instrument": "NSE:TCS", "tf": "1D", "limit": 50}).json():
        _keys("PatternSignal", p)
        _keys("PatternContext", p["context"])
        assert p["id"] == f"{p['instrument']}|{p['tf']}|{p['time']}|{p['pattern']}"
    lv = api.get("/api/levels", params={"instrument": "NSE:TCS", "tf": "1D"}).json()
    _keys("Levels", lv)
    for x in lv["levels"]:
        _keys("Level", x)
    fc = api.get("/api/forecast", params={"instrument": "NSE:RELIANCE", "tf": "1D"}).json()
    _keys("Forecast", fc)
    for row in api.get("/api/scanner", params={"tf": "1D"}).json():
        _keys("ScannerRow", row)
    acc = api.get("/api/accuracy").json()
    _keys("Accuracy", acc)
    assert len(acc["calibration"]) == 10
    assert isinstance(api.get("/api/ledger").json(), list)


@pytest.mark.parametrize(
    ("path", "params", "status"),
    [
        ("/api/candles", {"instrument": "NSE:RELIANCE", "tf": "2h"}, 400),
        ("/api/candles", {"instrument": "NSE:RELIANCE", "tf": "1D", "limit": "0"}, 400),
        ("/api/candles", {"tf": "1D"}, 400),
        ("/api/candles", {"instrument": "NSE:NOPE", "tf": "1D"}, 404),
        ("/api/indicators", {"instrument": "NSE:RELIANCE", "tf": "1D", "names": "foo"}, 400),
        ("/api/patterns", {"instrument": "NSE:NOPE", "tf": "1D"}, 404),
        ("/api/levels", {"instrument": "NSE:NOPE", "tf": "1D"}, 404),
        ("/api/forecast", {"instrument": "NSE:RELIANCE", "tf": "1D", "steps": "11"}, 400),
        ("/api/forecast", {"instrument": "NSE:RELIANCE", "tf": "1D", "steps": "x"}, 400),
        ("/api/scanner", {"tf": "7m"}, 400),
        ("/api/ledger", {"status": "bogus"}, 400),
        ("/api/accuracy", {"days": "0"}, 400),
        ("/api/news", {"instrument": "NSE:NOPE"}, 404),
    ],
)
def test_errors_use_the_contract_codes(api, path, params, status):
    r = api.get(path, params=params)
    assert r.status_code == status
    assert isinstance(r.json().get("detail"), str)


def test_missing_data_is_503_not_500(api):
    stats = {i["id"]: i["data"] for i in api.get("/api/instruments").json()}
    empty = [(inst, tf) for inst, data in stats.items() for tf, s in data.items() if s["bars"] == 0][:4]
    if not empty:
        pytest.skip("every series has data")
    for inst, tf in empty:
        for path in ("/api/candles", "/api/levels", "/api/forecast"):
            r = api.get(path, params={"instrument": inst, "tf": tf})
            assert r.status_code == 503, (path, inst, tf, r.status_code)


@pytest.mark.parametrize("host", ["evil.example.com", "127.0.0.1.nip.io", "attacker.localhost"])
def test_foreign_host_header_is_rejected(api, host):
    assert api.get("/api/health", headers={"Host": host}).status_code == 400


def test_nothing_can_place_orders(api):
    paths = api.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if re.search(r"order|position|trade|place", p, re.I)]
    writes = {p: sorted(m) for p, v in paths.items() for m in [set(v) - {"get"}] if m}
    assert writes == {"/api/auth/fyers/code": ["post"]}


def test_ingest_banner_matches_the_fyers_state(api):
    h = api.get("/api/health").json()
    if h["data_source"] == "fyers" and not h["keys"]["fyers_connected"]:
        assert h["ingest"]["status"] == "blocked"
        assert "Fyers" in h["ingest"]["reason"]
    else:
        assert h["ingest"]["status"] == "ok"


def test_stale_data_makes_forecasts_abstain(api):
    now = pd.Timestamp.now(tz="UTC")
    for row in api.get("/api/scanner", params={"tf": "1D"}).json():
        exchange = row["instrument"].split(":")[0]
        expected = last_expected_closed_bar(exchange, "1D", now)
        if expected is not None and pd.Timestamp(row["time"], unit="s", tz="UTC") < expected:
            assert row["abstain"] and row["score"] == 0 and row["direction"] == "neutral", row["instrument"]


@pytest.mark.xfail(
    reason="report D1/F1: index and LT 1D series end a session early; levels still say 'Prev day'",
    strict=False,
)
def test_daily_levels_come_from_the_last_completed_session(api):
    now = pd.Timestamp.now(tz="UTC")
    stale = []
    for inst in api.get("/api/instruments").json():
        if inst["data"].get("1D", {}).get("bars", 0) == 0:
            continue
        expected = last_expected_closed_bar(inst["exchange"], "1D", now)
        last = api.get("/api/candles", params={"instrument": inst["id"], "tf": "1D", "limit": 1}).json()[
            "candles"
        ][-1]
        if expected is not None and pd.Timestamp(last["time"], unit="s", tz="UTC") < expected:
            stale.append(inst["id"])
    assert not stale, f"1D levels built from an old session for {stale}"


def _median_ms(api, path, params, n=3) -> float:
    api.get(path, params=params)  # warm
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        assert api.get(path, params=params).status_code == 200
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


@pytest.mark.parametrize(
    ("path", "params", "budget_ms"),
    [
        ("/api/candles", {"instrument": "NSE:NIFTY50", "tf": "1D", "limit": 500}, 500),
        (
            "/api/indicators",
            {"instrument": "NSE:NIFTY50", "tf": "1D", "names": "ema20,rsi14,macd", "limit": 500},
            500,
        ),
        ("/api/patterns", {"instrument": "NSE:RELIANCE", "tf": "1h", "limit": 200}, 500),
        ("/api/levels", {"instrument": "NSE:RELIANCE", "tf": "1h"}, 500),
        ("/api/forecast", {"instrument": "NSE:RELIANCE", "tf": "1h"}, 500),
        ("/api/scanner", {"tf": "1D"}, 3000),
    ],
)
def test_chart_endpoints_are_fast_enough(api, path, params, budget_ms):
    assert _median_ms(api, path, params) < budget_ms


def test_no_secrets_in_api_responses(api):
    for path, params in [
        ("/api/health", None),
        ("/api/instruments", None),
        ("/api/news", {"limit": 100}),
        ("/api/candles", {"instrument": "NSE:RELIANCE", "tf": "1D", "limit": 5}),
        ("/api/forecast", {"instrument": "NSE:RELIANCE", "tf": "1D"}),
        ("/api/scanner", {"tf": "1D"}),
        ("/openapi.json", None),
    ]:
        found = leaks(api.get(path, params=params).text)
        assert not found, f"{path}: {found}"
