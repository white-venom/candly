import hashlib
import json
import sys
from dataclasses import asdict
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pandas as pd
import pytest
import respx

from candly.core.calendar import IST
from candly.core.instruments import get_instrument
from candly.data import clock
from candly.data.sources import fyers

NOW = pd.Timestamp(datetime(2026, 9, 23, 12, 2), tz=IST).tz_convert("UTC")
NOW_S = clock.epoch_seconds(NOW)
HISTORY = {"method": "GET", "host": "api-t1.fyers.in", "path": "/data/history"}
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


def ist(y, m, d, hh, mm) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm), tz=IST).tz_convert("UTC")


def row(ts: pd.Timestamp, price: float = 100.0, *extra) -> list:
    return [clock.epoch_seconds(ts), price, price + 1, price - 1, price + 0.5, 1000, *extra]


def ok(candles: list) -> httpx.Response:
    return httpx.Response(200, json={"s": "ok", "code": 200, "candles": candles})


def store_token(expires_in: int = 3600, refresh: bool = True) -> None:
    fyers.save_token(
        fyers.FyersToken(
            app_id="TESTAPP-100",
            access_token="access-1",
            refresh_token="refresh-1" if refresh else None,
            created_at=NOW_S - 100,
            expires_at=NOW_S + expires_in,
            refresh_expires_at=NOW_S + 86400 if refresh else None,
        )
    )


@pytest.fixture
def sleeps(monkeypatch):
    recorded: list[float] = []
    monkeypatch.setattr(fyers, "_sleep", recorded.append)
    return recorded


@pytest.fixture
def fy(monkeypatch, tmp_data_dir, fake_fyers_keys, sleeps):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    monkeypatch.setattr(fyers, "_limiter", fyers.RateLimiter(per_second=10_000, per_minute=10_000))
    monkeypatch.setattr(fyers, "_refresh_failure", None)
    fyers._master_cache.clear()
    yield tmp_data_dir
    fyers._master_cache.clear()


@pytest.fixture
def pin(monkeypatch, fy):
    monkeypatch.setenv("FYERS_PIN", "1234")
    fyers.get_settings.cache_clear()


def refresh_ok(access: str = "access-2") -> httpx.Response:
    return httpx.Response(200, json={"s": "ok", "code": 200, "access_token": access})


def test_login_url(fy):
    url = fyers.login_url("state-123")
    assert url.startswith(fyers.AUTHCODE_URL + "?")
    query = parse_qs(urlparse(url).query)
    assert query == {
        "client_id": ["TESTAPP-100"],
        "redirect_uri": ["https://trade.fyers.in/api-login/redirect-uri/index.html"],
        "response_type": ["code"],
        "state": ["state-123"],
    }


def test_login_needs_keys(tmp_data_dir, no_keys):
    with pytest.raises(fyers.FyersNotConnected, match="keys are not set"):
        fyers.login_url("x")
    with pytest.raises(fyers.FyersNotConnected):
        fyers.ensure_token()
    assert fyers.connection_status() == {"connected": False, "expires_at": None}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("eyJraw.code", "eyJraw.code"),
        ("  eyJraw.code \n", "eyJraw.code"),
        (
            "https://trade.fyers.in/api-login/redirect-uri/index.html?s=ok&code=200&auth_code=eyJabc&state=x",
            "eyJabc",
        ),
        ("http://127.0.0.1:8000/api/auth/fyers/callback?code=eyJfallback&state=x", "eyJfallback"),
        ("?s=ok&auth_code=eyJquery", "eyJquery"),
    ],
)
def test_extract_auth_code(text, expected):
    assert fyers.extract_auth_code(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "https://trade.fyers.in/x?s=error&code=-413&state=x", "https://trade.fyers.in/x?s=ok&code=200"],
)
def test_extract_auth_code_rejects(text):
    with pytest.raises(ValueError):
        fyers.extract_auth_code(text)


@respx.mock
def test_exchange_auth_code_stores_token(fy):
    route = respx.post(fyers.VALIDATE_AUTHCODE_URL).mock(
        return_value=httpx.Response(
            200, json={"s": "ok", "code": 200, "access_token": "acc-new", "refresh_token": "ref-new"}
        )
    )
    token = fyers.exchange_auth_code("eyJcode")
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "grant_type": "authorization_code",
        "appIdHash": hashlib.sha256(b"TESTAPP-100:test-secret-key").hexdigest(),
        "code": "eyJcode",
    }
    assert token.expires_at == clock.epoch_seconds(ist(2026, 9, 24, 6, 0))
    assert token.refresh_expires_at == NOW_S + 15 * 86400
    saved = fyers.load_token()
    assert saved.access_token == "acc-new" and saved.refresh_token == "ref-new"
    assert "acc-new" not in repr(token)
    assert fyers.connection_status() == {"connected": True, "expires_at": token.expires_at}


@respx.mock
def test_exchange_failure_never_echoes_the_code(fy):
    respx.post(fyers.VALIDATE_AUTHCODE_URL).mock(
        return_value=httpx.Response(
            400, json={"s": "error", "code": -413, "message": "invalid code eyJsecret"}
        )
    )
    with pytest.raises(fyers.FyersAuthError) as excinfo:
        fyers.exchange_auth_code("eyJsecret")
    assert "eyJsecret" not in str(excinfo.value)
    assert not (fy / "secrets" / "fyers_token.json").exists()


def test_access_expiry_is_next_six_am_ist():
    assert fyers.access_expiry(ist(2026, 9, 23, 5, 0)) == ist(2026, 9, 23, 6, 0)
    assert fyers.access_expiry(ist(2026, 9, 23, 6, 0)) == ist(2026, 9, 24, 6, 0)
    assert fyers.access_expiry(ist(2026, 9, 23, 22, 0)) == ist(2026, 9, 24, 6, 0)


def test_ensure_token_states(fy, monkeypatch):
    with pytest.raises(fyers.FyersNotConnected):
        fyers.ensure_token()
    store_token()
    assert fyers.ensure_token().access_token == "access-1"
    store_token(expires_in=-10, refresh=False)
    with pytest.raises(fyers.FyersNotConnected, match="expired"):
        fyers.ensure_token()
    store_token(expires_in=-10, refresh=True)  # refreshable, but FYERS_PIN is empty
    with pytest.raises(fyers.FyersNotConnected, match="FYERS_PIN"):
        fyers.ensure_token()


@respx.mock
def test_expired_session_auto_refreshes_with_pin(fy, monkeypatch):
    monkeypatch.setenv("FYERS_PIN", "1234")
    fyers.get_settings.cache_clear()
    store_token(expires_in=-10)
    route = respx.post(fyers.REFRESH_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"s": "ok", "code": 200, "access_token": "access-2"})
    )
    token = fyers.ensure_token()
    assert token.access_token == "access-2" and token.refresh_token == "refresh-1"
    sent = json.loads(route.calls.last.request.content)
    assert (
        sent["grant_type"] == "refresh_token"
        and sent["refresh_token"] == "refresh-1"
        and sent["pin"] == "1234"
    )
    assert fyers.load_token().access_token == "access-2"


@respx.mock
def test_rejected_refresh_drops_refresh_token(fy, monkeypatch):
    monkeypatch.setenv("FYERS_PIN", "1234")
    fyers.get_settings.cache_clear()
    store_token(expires_in=-10)
    respx.post(fyers.REFRESH_TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"s": "error", "code": -501, "message": "invalid refresh token"}
        )
    )
    with pytest.raises(fyers.FyersNotConnected, match="log in again"):
        fyers.ensure_token()
    assert fyers.load_token().refresh_token is None
    assert fyers.connection_status()["connected"] is False


@respx.mock
def test_refresh_retries_a_503_and_keeps_the_refresh_token(pin, sleeps):
    store_token(expires_in=-10)
    route = respx.post(fyers.REFRESH_TOKEN_URL).mock(side_effect=[httpx.Response(503), refresh_ok()])
    token = fyers.ensure_token()
    assert route.call_count == 2 and len(sleeps) == 1
    assert token.access_token == "access-2"
    assert fyers.load_token().refresh_token == "refresh-1"


@pytest.mark.parametrize(
    "outage",
    [
        httpx.Response(503),
        httpx.Response(429),
        httpx.Response(200, text="<html>Fyers is under maintenance</html>"),
        httpx.ConnectError("connection refused"),
    ],
    ids=["503", "429", "html-page", "network"],
)
@respx.mock
def test_refresh_outage_keeps_the_refresh_token(pin, outage):
    store_token(expires_in=-10)
    respx.post(fyers.REFRESH_TOKEN_URL).mock(side_effect=outage)
    with pytest.raises(fyers.FyersError) as excinfo:
        fyers.ensure_token()
    assert not isinstance(excinfo.value, fyers.FyersNotConnected)
    token = fyers.load_token()
    assert token.refresh_token == "refresh-1" and token.refresh_expires_at == NOW_S + 86400


@respx.mock
def test_failed_refresh_is_not_retried_for_a_few_minutes(pin):
    store_token(expires_in=-10)
    route = respx.post(fyers.REFRESH_TOKEN_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(fyers.FyersError):
        fyers.ensure_token()
    calls = route.call_count
    with pytest.raises(fyers.FyersError, match="retrying in a few minutes"):
        fyers.ensure_token()
    assert fyers.connection_status() == {"connected": False, "expires_at": None}
    assert route.call_count == calls  # neither the job nor the health poll hit Fyers again

    failed_at, reason = fyers._refresh_failure
    fyers._refresh_failure = (failed_at - fyers.REFRESH_RETRY_SECONDS, reason)
    route.mock(return_value=refresh_ok())
    assert fyers.connection_status()["connected"] is True
    assert fyers._refresh_failure is None


@pytest.mark.parametrize(
    ("body", "cleared"),
    [
        ({"s": "error", "code": -16, "message": "Server was unable to authenticate your token"}, True),
        ({"s": "error", "code": -8, "message": "Your token has expired"}, True),
        ({"s": "error", "code": -1, "message": "Invalid PIN"}, True),
        ({"s": "error", "code": -99, "message": "Something went wrong, please try again"}, False),
    ],
)
@respx.mock
def test_only_an_explicit_rejection_clears_the_refresh_token(pin, body, cleared):
    store_token(expires_in=-10)
    respx.post(fyers.REFRESH_TOKEN_URL).mock(return_value=httpx.Response(400, json=body))
    expected = fyers.FyersNotConnected if cleared else fyers.FyersError
    with pytest.raises(expected):
        fyers.ensure_token()
    assert (fyers.load_token().refresh_token is None) is cleared


@windows_only
def test_token_is_encrypted_at_rest(fy):
    store_token()
    raw = (fy / "secrets" / "fyers_token.json").read_bytes()
    assert raw.startswith(fyers.TOKEN_MAGIC)
    assert b"access-1" not in raw and b"refresh-1" not in raw and b"TESTAPP" not in raw
    token = fyers.load_token()
    assert (token.access_token, token.refresh_token) == ("access-1", "refresh-1")
    assert token.expires_at == NOW_S + 3600


@windows_only
def test_plaintext_token_file_is_migrated_to_encrypted(fy):
    legacy = fyers.FyersToken("TESTAPP-100", "access-old", "refresh-old", NOW_S, NOW_S + 3600, NOW_S + 86400)
    path = fy / "secrets" / "fyers_token.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(asdict(legacy)), encoding="utf-8")
    assert fyers.load_token() == legacy
    assert path.read_bytes().startswith(fyers.TOKEN_MAGIC)
    assert fyers.load_token() == legacy
    assert fyers.ensure_token().access_token == "access-old"


def test_plaintext_fallback_without_dpapi(fy, monkeypatch):
    monkeypatch.setattr(fyers, "DPAPI", False)
    store_token()
    path = fy / "secrets" / "fyers_token.json"
    assert json.loads(path.read_text(encoding="utf-8"))["access_token"] == "access-1"
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600
    assert fyers.load_token().refresh_token == "refresh-1"


def test_failed_token_write_leaves_no_temp_file(fy, monkeypatch):
    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(fyers.os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        store_token()
    assert list((fy / "secrets").iterdir()) == []


def test_unreadable_token_file_is_ignored(fy):
    path = fy / "secrets" / "fyers_token.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(fyers.TOKEN_MAGIC + b"not a dpapi blob")
    assert fyers.load_token() is None
    assert fyers.connection_status() == {"connected": False, "expires_at": None}


@respx.mock
def test_history_is_chunked_and_authorized(fy):
    store_token()
    route = respx.route(**HISTORY).mock(return_value=ok([]))
    fyers.history("NSE:SBIN-EQ", "5m", NOW - pd.Timedelta(days=250), NOW)
    assert route.call_count == 3
    first = route.calls[0].request
    assert first.headers["Authorization"] == "TESTAPP-100:access-1"
    params = dict(first.url.params)
    assert params["symbol"] == "NSE:SBIN-EQ" and params["resolution"] == "5"
    assert params["date_format"] == "0" and params["cont_flag"] == "1" and "oi_flag" not in params
    spans = [
        int(c.request.url.params["range_to"]) - int(c.request.url.params["range_from"]) for c in route.calls
    ]
    assert max(spans) <= 100 * 86400
    assert int(route.calls[-1].request.url.params["range_to"]) == NOW_S


@respx.mock
def test_rate_limit_and_server_errors_are_retried(fy, sleeps):
    store_token()
    route = respx.route(**HISTORY).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(503),
            ok([row(ist(2026, 9, 23, 11, 50))]),
        ]
    )
    rows = fyers.history("NSE:SBIN-EQ", "5m", NOW - pd.Timedelta(hours=1), NOW)
    assert len(rows) == 1 and route.call_count == 3
    assert sleeps[0] == 2.0 and len(sleeps) == 2


@respx.mock
def test_persistent_server_error_gives_up(fy, sleeps):
    store_token()
    respx.route(**HISTORY).mock(return_value=httpx.Response(500))
    with pytest.raises(fyers.FyersError, match="HTTP 500"):
        fyers.history("NSE:SBIN-EQ", "5m", NOW - pd.Timedelta(hours=1), NOW)
    assert len(sleeps) == fyers.MAX_RETRIES


@respx.mock
def test_rejected_token_triggers_refresh_then_retry(fy, monkeypatch):
    monkeypatch.setenv("FYERS_PIN", "1234")
    fyers.get_settings.cache_clear()
    store_token()
    respx.post(fyers.REFRESH_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"s": "ok", "code": 200, "access_token": "access-2"})
    )
    route = respx.route(**HISTORY).mock(
        side_effect=[
            httpx.Response(200, json={"s": "error", "code": -16, "message": "token invalid"}),
            ok([row(ist(2026, 9, 23, 11, 50))]),
        ]
    )
    assert len(fyers.history("NSE:SBIN-EQ", "5m", NOW - pd.Timedelta(hours=1), NOW)) == 1
    assert route.calls[1].request.headers["Authorization"] == "TESTAPP-100:access-2"


@respx.mock
def test_fetch_5m_drops_forming_bar(fy):
    store_token()
    respx.route(**HISTORY).mock(
        return_value=ok(
            [row(ist(2026, 9, 23, 11, 50)), row(ist(2026, 9, 23, 11, 55)), row(ist(2026, 9, 23, 12, 0))]
        )
    )
    sbi = get_instrument("NSE:SBIN")
    df = fyers.fetch_candles(sbi, "5m", start=ist(2026, 9, 23, 9, 15))
    assert list(df["ts"]) == [ist(2026, 9, 23, 11, 50), ist(2026, 9, 23, 11, 55)]
    assert df["oi"].isna().all()


@respx.mock
def test_fetch_daily_maps_to_session_open(fy):
    store_token()
    route = respx.route(**HISTORY).mock(
        return_value=ok(
            [row(ist(2026, 9, 21, 0, 0)), row(ist(2026, 9, 22, 0, 0)), row(ist(2026, 9, 23, 0, 0))]
        )
    )
    df = fyers.fetch_candles(get_instrument("NSE:SBIN"), "1D", start=ist(2026, 9, 1, 0, 0))
    assert list(df["ts"]) == [ist(2026, 9, 21, 9, 15), ist(2026, 9, 22, 9, 15)]  # 23 Sep is still forming
    assert route.calls.last.request.url.params["resolution"] == "D"


@respx.mock
def test_fetch_hourly_is_resampled_from_5m(fy):
    store_token()
    opens = pd.date_range(ist(2026, 9, 23, 9, 15), ist(2026, 9, 23, 12, 0), freq="5min")
    route = respx.route(**HISTORY).mock(return_value=ok([row(ts, 100.0 + i) for i, ts in enumerate(opens)]))
    df = fyers.fetch_candles(get_instrument("NSE:SBIN"), "1h", start=ist(2026, 9, 23, 9, 15))
    assert route.calls.last.request.url.params["resolution"] == "5"
    assert list(df["ts"]) == [ist(2026, 9, 23, 9, 15), ist(2026, 9, 23, 10, 15)]  # 11:15 bar still forming
    assert df["volume"].tolist() == [12000.0, 12000.0]
    assert df["open"].iloc[0] == 100.0 and df["close"].iloc[0] == 111.5


MASTER_ROWS = [
    ("CRUDEOIL 18 Sep 26 FUT", "MCX:CRUDEOIL26SEPFUT", "CRUDEOIL", ist(2026, 9, 18, 23, 30)),
    ("CRUDEOIL 19 Oct 26 FUT", "MCX:CRUDEOIL26OCTFUT", "CRUDEOIL", ist(2026, 10, 19, 23, 30)),
    ("CRUDEOIL 18 Nov 26 FUT", "MCX:CRUDEOIL26NOVFUT", "CRUDEOIL", ist(2026, 11, 18, 23, 30)),
    ("CRUDEOILM 19 Oct 26 FUT", "MCX:CRUDEOILM26OCTFUT", "CRUDEOILM", ist(2026, 10, 19, 23, 30)),
    ("CRUDEOIL 15 Oct 26 5000 CE", "MCX:CRUDEOIL26OCT5000CE", "CRUDEOIL", ist(2026, 10, 15, 23, 30)),
]


def master_csv() -> str:
    lines = []
    for i, (desc, ticker, underlying, expiry) in enumerate(MASTER_ROWS):
        e = clock.epoch_seconds(expiry)
        lines.append(
            f"11202610{i},{desc},30,100,1.0,,0900-2330|1815-1915:,2026-09-23,{e},{ticker},11,20,{i},"
            f"{underlying},294,-1.0,XX,1120000000294,{e},0,0.0"
        )
    return "\n".join(lines) + "\n"


@respx.mock
def test_front_month_future_resolution_and_oi(fy):
    store_token()
    master = respx.get(fyers.SYMBOL_MASTER_URL.format(segment="MCX_COM")).mock(
        return_value=httpx.Response(200, text=master_csv())
    )
    route = respx.route(**HISTORY).mock(
        return_value=ok([row(ist(2026, 9, 21, 0, 0), 5000, 12345), row(ist(2026, 9, 22, 0, 0), 5010, 12400)])
    )
    crude = get_instrument("MCX:CRUDEOIL")
    assert fyers.resolve_symbol(crude) == "MCX:CRUDEOIL26OCTFUT"
    df = fyers.fetch_candles(crude, "1D", start=ist(2026, 9, 1, 0, 0))
    params = route.calls.last.request.url.params
    assert (
        params["symbol"] == "MCX:CRUDEOIL26OCTFUT" and params["oi_flag"] == "1" and params["cont_flag"] == "1"
    )
    assert list(df["ts"]) == [ist(2026, 9, 21, 9, 0), ist(2026, 9, 22, 9, 0)]
    assert df["oi"].tolist() == [12345.0, 12400.0]
    assert master.call_count == 1  # cached for the day
    assert (fy / "cache" / "fyers" / "MCX_COM.csv").exists()


def test_data_calls_need_a_session(fy):
    with pytest.raises(fyers.FyersNotConnected, match="Connect Fyers"):
        fyers.history("NSE:SBIN-EQ", "5m", NOW - pd.Timedelta(hours=1), NOW)


def test_rate_limiter_paces_requests(monkeypatch):
    waits: list[float] = []
    clock_value = [0.0]
    monkeypatch.setattr(fyers.time, "monotonic", lambda: clock_value[0])

    def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        clock_value[0] += seconds

    monkeypatch.setattr(fyers, "_sleep", fake_sleep)
    limiter = fyers.RateLimiter(per_second=2, per_minute=3)
    for _ in range(4):
        limiter.wait()
    assert waits == [1.0, 59.0]
