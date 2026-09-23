from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import numpy as np
import pandas as pd
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from candly.api.routes import platform
from candly.core.calendar import IST, get_calendar
from candly.core.instruments import load_watchlist
from candly.data import clock
from candly.data.sources import fyers
from candly.data.store import save_candles
from candly.news.models import NewsItem, news_id
from candly.news.store import NewsStore

cal = get_calendar()
NOW = pd.Timestamp(datetime(2026, 9, 23, 12, 2), tz=IST).tz_convert("UTC")  # Wednesday, markets open
PASTED_URL = (
    "https://trade.fyers.in/api-login/redirect-uri/index.html?s=ok&code=200&auth_code=eyJsecretcode&state=abc"
)


def ist(y, m, d, hh, mm) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm), tz=IST).tz_convert("UTC")


def daily(days: list[date]) -> pd.DataFrame:
    n = len(days)
    return pd.DataFrame(
        {
            "ts": pd.DatetimeIndex([cal.session_times("NSE", d)[0] for d in days]),
            "open": np.full(n, 100.0),
            "high": np.full(n, 110.0),
            "low": np.full(n, 99.0),
            "close": np.arange(n, dtype=float) + 100.5,
            "volume": np.full(n, 1000.0),
            "oi": np.nan,
        }
    )


@pytest.fixture
def api(monkeypatch, tmp_data_dir):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    monkeypatch.setattr(platform, "get_forming", lambda instrument_id, tf: None)
    app = FastAPI()
    app.include_router(platform.router, prefix="/api")
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def client(api, no_keys):
    return api


@pytest.fixture
def fyers_client(api, fake_fyers_keys, monkeypatch):
    monkeypatch.setattr(fyers, "_limiter", fyers.RateLimiter(per_second=10_000, per_minute=10_000))
    return api


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["time"] == clock.epoch_seconds(NOW)
    assert body["data_source"] == "yahoo"
    assert body["keys"] == {
        "fyers": False,
        "fyers_connected": False,
        "kotak_neo": False,
        "anthropic": False,
        "telegram": False,
    }
    assert body["markets"] == [
        {"exchange": "NSE", "open": True, "phase": "midday"},
        {"exchange": "BSE", "open": True, "phase": "midday"},
        {"exchange": "MCX", "open": True, "phase": "day"},
    ]


def test_health_on_a_weekend(client, monkeypatch):
    monkeypatch.setattr(clock, "utc_now", lambda: ist(2026, 9, 26, 12, 0))
    markets = client.get("/api/health").json()["markets"]
    assert all(m == {"exchange": m["exchange"], "open": False, "phase": "closed"} for m in markets)


def test_instruments(client):
    save_candles("NSE:RELIANCE", "1D", daily([date(2026, 9, 21), date(2026, 9, 22)]))
    body = client.get("/api/instruments").json()
    assert [i["id"] for i in body] == [i.id for i in load_watchlist()]
    reliance = next(i for i in body if i["id"] == "NSE:RELIANCE")
    assert reliance["exchange"] == "NSE" and reliance["symbol"] == "RELIANCE" and reliance["kind"] == "equity"
    assert reliance["timeframes"] == ["5m", "15m", "1h", "1D"]
    assert reliance["data"]["1D"] == {
        "bars": 2,
        "first": clock.epoch_seconds(ist(2026, 9, 21, 9, 15)),
        "last": clock.epoch_seconds(ist(2026, 9, 22, 9, 15)),
    }
    assert reliance["data"]["5m"] == {"bars": 0, "first": None, "last": None}


def test_candles(client, monkeypatch):
    days = [date(2026, 9, d) for d in (15, 16, 17, 18, 21, 22)]
    save_candles("NSE:RELIANCE", "1D", daily(days), source="yahoo")
    body = client.get("/api/candles", params={"instrument": "NSE:RELIANCE", "tf": "1D"}).json()
    assert body["instrument"] == "NSE:RELIANCE" and body["tf"] == "1D" and body["source"] == "yahoo"
    assert len(body["candles"]) == 6 and body["forming"] is None
    assert body["candles"][0] == {
        "time": clock.epoch_seconds(ist(2026, 9, 15, 9, 15)),
        "open": 100.0,
        "high": 110.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1000.0,
    }
    limited = client.get("/api/candles", params={"instrument": "NSE:RELIANCE", "tf": "1D", "limit": 2}).json()
    assert [c["close"] for c in limited["candles"]] == [104.5, 105.5]
    end = clock.epoch_seconds(ist(2026, 9, 17, 12, 0))
    ended = client.get("/api/candles", params={"instrument": "NSE:RELIANCE", "tf": "1D", "end": end}).json()
    assert len(ended["candles"]) == 3

    forming = pd.Series(
        {
            "ts": ist(2026, 9, 23, 9, 15),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 7.0,
            "oi": np.nan,
        }
    )
    monkeypatch.setattr(platform, "get_forming", lambda instrument_id, tf: forming)
    live = client.get("/api/candles", params={"instrument": "NSE:RELIANCE", "tf": "1D"}).json()
    assert live["forming"] == {
        "time": clock.epoch_seconds(ist(2026, 9, 23, 9, 15)),
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 7.0,
    }


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"tf": "1D"}, 400),
        ({"instrument": "NSE:NOPE", "tf": "1D"}, 404),
        ({"instrument": "NSE:RELIANCE", "tf": "2h"}, 400),
        ({"instrument": "NSE:RELIANCE"}, 400),
        ({"instrument": "NSE:RELIANCE", "tf": "1D", "limit": "0"}, 400),
        ({"instrument": "NSE:RELIANCE", "tf": "1D", "limit": "5001"}, 400),
        ({"instrument": "NSE:RELIANCE", "tf": "1D", "limit": "abc"}, 400),
        ({"instrument": "NSE:RELIANCE", "tf": "1D", "end": "yesterday"}, 400),
        ({"instrument": "NSE:RELIANCE", "tf": "1D"}, 503),
    ],
)
def test_candles_errors(client, params, status):
    response = client.get("/api/candles", params=params)
    assert response.status_code == status
    assert isinstance(response.json()["detail"], str)


def test_news(client):
    assert client.get("/api/news").json() == []
    fetched = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
    NewsStore().add(
        [
            NewsItem(
                id=news_id("TCS wins order", "https://x.test/1"),
                title="TCS wins order",
                url="https://x.test/1",
                source="Test",
                published_at=None,
                fetched_at=fetched,
                instruments=["NSE:TCS"],
                sentiment=0.5,
                sentiment_method="lexicon",
                event_type="order_win",
            )
        ]
    )
    body = client.get("/api/news", params={"instrument": "NSE:TCS"}).json()
    assert body == [
        {
            "id": news_id("TCS wins order", "https://x.test/1"),
            "title": "TCS wins order",
            "url": "https://x.test/1",
            "source": "Test",
            "published_at": None,
            "fetched_at": int(fetched.timestamp()),
            "instruments": ["NSE:TCS"],
            "sentiment": 0.5,
            "sentiment_method": "lexicon",
            "event_type": "order_win",
            "summary": None,
        }
    ]
    assert client.get("/api/news", params={"instrument": "NSE:INFY"}).json() == []
    assert client.get("/api/news", params={"instrument": "NSE:NOPE"}).status_code == 404
    assert client.get("/api/news", params={"limit": "-1"}).status_code == 400


def test_fyers_login_without_keys(client):
    response = client.get("/api/auth/fyers/login")
    assert response.status_code == 400 and "FYERS_APP_ID" in response.json()["detail"]
    assert client.get("/api/auth/fyers/status").json() == {"connected": False, "expires_at": None}


def test_fyers_login_redirects(fyers_client):
    response = fyers_client.get("/api/auth/fyers/login")
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith(fyers.AUTHCODE_URL)
    assert parse_qs(urlparse(location).query)["client_id"] == ["TESTAPP-100"]


@respx.mock
def test_fyers_code_exchange(fyers_client):
    route = respx.post(fyers.VALIDATE_AUTHCODE_URL).mock(
        return_value=httpx.Response(
            200, json={"s": "ok", "code": 200, "access_token": "a", "refresh_token": "r"}
        )
    )
    response = fyers_client.post("/api/auth/fyers/code", json={"code": PASTED_URL})
    assert response.status_code == 200
    expires = clock.epoch_seconds(ist(2026, 9, 24, 6, 0))
    assert response.json() == {"connected": True, "expires_at": expires}
    assert b"eyJsecretcode" in route.calls.last.request.content
    assert fyers_client.get("/api/auth/fyers/status").json() == {"connected": True, "expires_at": expires}
    assert fyers_client.get("/api/health").json()["keys"]["fyers_connected"] is True


@respx.mock
def test_fyers_code_failure_never_echoes_the_code(fyers_client):
    respx.post(fyers.VALIDATE_AUTHCODE_URL).mock(
        return_value=httpx.Response(
            200, json={"s": "error", "code": -413, "message": "Invalid auth code eyJsecretcode"}
        )
    )
    response = fyers_client.post("/api/auth/fyers/code", json={"code": "eyJsecretcode"})
    assert response.status_code == 400
    assert "eyJsecretcode" not in response.text and "test-secret-key" not in response.text
    assert fyers_client.get("/api/auth/fyers/status").json()["connected"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"json": {}},
        {"json": {"code": ""}},
        {"json": ["eyJ"]},
        {"content": b"not json", "headers": {"content-type": "application/json"}},
        {"json": {"code": "https://trade.fyers.in/x?s=ok&code=200"}},
    ],
)
def test_fyers_code_bad_bodies(fyers_client, kwargs):
    response = fyers_client.post("/api/auth/fyers/code", **kwargs)
    assert response.status_code == 400 and isinstance(response.json()["detail"], str)


@respx.mock
def test_fyers_callback(fyers_client):
    route = respx.post(fyers.VALIDATE_AUTHCODE_URL).mock(
        return_value=httpx.Response(
            200, json={"s": "ok", "code": 200, "access_token": "a", "refresh_token": "r"}
        )
    )
    bad = fyers_client.get("/api/auth/fyers/callback", params={"auth_code": "eyJx", "state": "forged"})
    assert bad.status_code == 307 and bad.headers["location"] == "http://localhost:5173/?fyers=error"
    assert route.call_count == 0

    login = fyers_client.get("/api/auth/fyers/login").headers["location"]
    state = parse_qs(urlparse(login).query)["state"][0]
    good = fyers_client.get(
        "/api/auth/fyers/callback", params={"s": "ok", "code": "200", "auth_code": "eyJx", "state": state}
    )
    assert good.headers["location"] == "http://localhost:5173/?fyers=connected"
    assert route.call_count == 1
    replay = fyers_client.get("/api/auth/fyers/callback", params={"auth_code": "eyJx", "state": state})
    assert replay.headers["location"].endswith("fyers=error")
