import os
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
    monkeypatch.setattr(fyers, "_refresh_failure", None)
    monkeypatch.setattr(fyers, "_sleep", lambda seconds: None)
    return api


def token_ok() -> httpx.Response:
    return httpx.Response(200, json={"s": "ok", "code": 200, "access_token": "a", "refresh_token": "r"})


def issued_state(client: TestClient) -> str:
    login = client.get("/api/auth/fyers/login").headers["location"]
    return parse_qs(urlparse(login).query)["state"][0]


def pasted_url(state: str) -> str:
    page = "https://trade.fyers.in/api-login/redirect-uri/index.html"
    return f"{page}?s=ok&code=200&auth_code=eyJsecretcode&state={state}"


def store_token(expires_in: int) -> None:
    now = clock.epoch_seconds(NOW)
    fyers.save_token(fyers.FyersToken("TESTAPP-100", "acc", "ref", now - 100, now + expires_in, now + 86400))


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
    assert body["ingest"] == {"status": "ok", "reason": None}  # Yahoo needs no login


def test_health_reports_ingest_blocked_until_fyers_is_connected(fyers_client):
    body = fyers_client.get("/api/health").json()
    assert body["data_source"] == "fyers" and body["keys"]["fyers_connected"] is False
    assert body["ingest"] == {"status": "blocked", "reason": platform.INGEST_NOT_CONNECTED}
    store_token(expires_in=3600)
    assert fyers_client.get("/api/health").json()["ingest"] == {"status": "ok", "reason": None}


def test_health_blocked_when_fyers_is_forced_without_keys(client, monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "fyers")
    platform.get_settings.cache_clear()
    ingest = client.get("/api/health").json()["ingest"]
    assert ingest == {"status": "blocked", "reason": platform.INGEST_NO_KEYS}


@respx.mock
def test_health_polls_do_not_hammer_a_failing_refresh(fyers_client, monkeypatch):
    monkeypatch.setenv("FYERS_PIN", "1234")
    platform.get_settings.cache_clear()
    store_token(expires_in=-10)
    route = respx.post(fyers.REFRESH_TOKEN_URL).mock(return_value=httpx.Response(503))
    first = fyers_client.get("/api/health").json()
    assert first["ingest"]["status"] == "blocked"
    calls = route.call_count
    for _ in range(5):
        assert fyers_client.get("/api/health").json()["ingest"]["status"] == "blocked"
    assert route.call_count == calls
    assert fyers.load_token().refresh_token == "ref"  # an outage never forces a new login


def test_health_on_a_weekend(client, monkeypatch):
    monkeypatch.setattr(clock, "utc_now", lambda: ist(2026, 9, 26, 12, 0))
    markets = client.get("/api/health").json()["markets"]
    assert all(m == {"exchange": m["exchange"], "open": False, "phase": "closed"} for m in markets)


def write_mcx_master(data_dir, rows: list[tuple[str, str, pd.Timestamp]]) -> None:
    """A Fyers MCX symbol master cached today: (ticker, underlying, expiry) rows."""
    path = data_dir / "cache" / "fyers" / "MCX_COM.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"1,{ticker},30,100,1.0,,0900-2330,2026-09-23,{clock.epoch_seconds(expiry)},{ticker},11,20,1,{root}"
        for ticker, root, expiry in rows
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.utime(path, (clock.epoch_seconds(NOW), clock.epoch_seconds(NOW)))


def test_instruments(client, tmp_data_dir):
    write_mcx_master(tmp_data_dir, [("MCX:CRUDEOIL26OCTFUT", "CRUDEOIL", ist(2026, 10, 19, 23, 30))])
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

    expiry = {i["id"]: i["expiry"] for i in body}
    assert expiry["NSE:NIFTY50"] == {
        "next": "2026-09-29", "kind": "monthly", "days_to_expiry": 4, "is_expiry_day": False
    }
    assert expiry["BSE:SENSEX"] == {
        "next": "2026-09-24", "kind": "monthly", "days_to_expiry": 1, "is_expiry_day": False
    }
    assert expiry["MCX:CRUDEOIL"] == {
        "next": "2026-10-19", "kind": "contract", "days_to_expiry": 17, "is_expiry_day": False
    }
    assert expiry["NSE:INDIAVIX"] is None and expiry["MCX:GOLD"] is None


@respx.mock
def test_instruments_without_an_mcx_master(client):
    master = fyers.SYMBOL_MASTER_URL.format(segment="MCX_COM")
    route = respx.get(master).mock(return_value=httpx.Response(503))
    for _ in range(3):
        response = client.get("/api/instruments")
        assert response.status_code == 200
        expiry = {i["id"]: i["expiry"] for i in response.json()}
        assert expiry["MCX:CRUDEOIL"] is None and expiry["NSE:RELIANCE"]["kind"] == "monthly"
    assert route.call_count == 1  # a failed download is not retried on every request


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
    route = respx.post(fyers.VALIDATE_AUTHCODE_URL).mock(return_value=token_ok())
    url = pasted_url(issued_state(fyers_client))
    response = fyers_client.post("/api/auth/fyers/code", json={"code": url})
    assert response.status_code == 200
    expires = clock.epoch_seconds(ist(2026, 9, 24, 6, 0))
    assert response.json() == {"connected": True, "expires_at": expires}
    assert b"eyJsecretcode" in route.calls.last.request.content
    assert fyers_client.get("/api/auth/fyers/status").json() == {"connected": True, "expires_at": expires}
    health = fyers_client.get("/api/health").json()
    assert health["keys"]["fyers_connected"] is True and health["ingest"]["status"] == "ok"


@respx.mock
def test_fyers_code_accepts_a_raw_auth_code(fyers_client):
    route = respx.post(fyers.VALIDATE_AUTHCODE_URL).mock(return_value=token_ok())
    assert fyers_client.post("/api/auth/fyers/code", json={"code": "eyJrawcode"}).status_code == 200
    assert b"eyJrawcode" in route.calls.last.request.content


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded"])
@respx.mock
def test_fyers_code_requires_a_json_content_type(fyers_client, content_type):
    route = respx.post(fyers.VALIDATE_AUTHCODE_URL).mock(return_value=token_ok())
    headers = {"content-type": content_type} if content_type else {}
    response = fyers_client.post("/api/auth/fyers/code", content=b'{"code": "eyJrawcode"}', headers=headers)
    assert response.status_code == 415 and isinstance(response.json()["detail"], str)
    assert route.call_count == 0
    ok = fyers_client.post(
        "/api/auth/fyers/code",
        content=b'{"code": "eyJrawcode"}',
        headers={"content-type": "application/json; charset=utf-8"},
    )
    assert ok.status_code == 200


@pytest.mark.parametrize("state", ["abc", "", "expired"])
@respx.mock
def test_fyers_code_rejects_a_state_the_server_did_not_issue(fyers_client, monkeypatch, state):
    route = respx.post(fyers.VALIDATE_AUTHCODE_URL).mock(return_value=token_ok())
    if state == "expired":
        state = issued_state(fyers_client)
        issued = platform._login_states[state]
        monkeypatch.setitem(platform._login_states, state, issued - platform.LOGIN_STATE_SECONDS - 1)
    response = fyers_client.post("/api/auth/fyers/code", json={"code": pasted_url(state)})
    assert response.status_code == 400 and response.json()["detail"] == platform.BAD_STATE
    assert route.call_count == 0


@respx.mock
def test_fyers_code_state_is_accepted_once(fyers_client):
    route = respx.post(fyers.VALIDATE_AUTHCODE_URL).mock(return_value=token_ok())
    url = pasted_url(issued_state(fyers_client))
    assert fyers_client.post("/api/auth/fyers/code", json={"code": url}).status_code == 200
    replay = fyers_client.post("/api/auth/fyers/code", json={"code": url})
    assert replay.status_code == 400 and replay.json()["detail"] == platform.BAD_STATE
    assert route.call_count == 1


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
    route = respx.post(fyers.VALIDATE_AUTHCODE_URL).mock(return_value=token_ok())
    bad = fyers_client.get("/api/auth/fyers/callback", params={"auth_code": "eyJx", "state": "forged"})
    assert bad.status_code == 307 and bad.headers["location"] == "http://localhost:5173/?fyers=error"
    assert route.call_count == 0

    state = issued_state(fyers_client)
    good = fyers_client.get(
        "/api/auth/fyers/callback", params={"s": "ok", "code": "200", "auth_code": "eyJx", "state": state}
    )
    assert good.headers["location"] == "http://localhost:5173/?fyers=connected"
    assert route.call_count == 1
    replay = fyers_client.get("/api/auth/fyers/callback", params={"auth_code": "eyJx", "state": state})
    assert replay.headers["location"].endswith("fyers=error")
