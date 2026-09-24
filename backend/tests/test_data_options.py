import logging
from datetime import date, datetime

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from candly.core.calendar import IST
from candly.data import clock, options
from candly.data.sources import fyers
from candly.jobs import scheduler

NOW = pd.Timestamp(datetime(2026, 9, 23, 11, 2, 50), tz=IST).tz_convert("UTC")  # a Wednesday session
CHAIN = {"method": "GET", "host": "api-t1.fyers.in", "path": "/data/options-chain-v3"}


def expiry_epoch(d: date) -> int:
    return clock.epoch_seconds(pd.Timestamp(datetime(d.year, d.month, d.day, 15, 30), tz=IST))


EXPIRIES = [date(2026, 9, 22), date(2026, 9, 29), date(2026, 10, 6), date(2026, 10, 13)]
EXPIRY_DATA = [
    {"date": d.strftime("%d-%m-%Y"), "expiry": str(expiry_epoch(d)), "expiry_flag": "W"} for d in EXPIRIES
]
# strike, ce_oi, pe_oi, ce_volume, pe_volume, ce_delta, pe_delta, ce_iv, pe_iv
TABLE = [
    (90, 10, 140, 1, 11, 0.9, -0.1, 20, 20),
    (95, 20, 80, 2, 4, 0.7, -0.26, 18, 18),
    (100, 50, 50, 3, 3, 0.5, -0.5, 15, 16),
    (105, 80, 20, 4, 2, 0.27, -0.73, 14, 14),
    (110, 100, 10, 5, 1, 0.1, -0.9, 13, 13),
]


def option_row(strike, side, oi, volume, delta, iv, oich) -> dict:
    return {
        "symbol": f"NSE:TEST{strike}{side}", "option_type": side, "strike_price": strike,
        "ltp": 10.0, "bid": 9.9, "ask": 10.1, "oi": oi, "oich": oich, "prev_oi": oi - oich, "volume": volume,
        "greeks": {"delta": delta, "iv": iv, "gamma": 0.01, "theta": -1.0, "vega": 2.0},
    }


def chain_data(table=TABLE, spot=100.4, future=100.9) -> dict:
    rows = [{"symbol": "NSE:NIFTY50-INDEX", "option_type": "", "strike_price": -1, "ltp": spot, "fp": future}]
    for strike, ce_oi, pe_oi, ce_vol, pe_vol, ce_delta, pe_delta, ce_iv, pe_iv in table:
        rows.append(option_row(strike, "CE", ce_oi, ce_vol, ce_delta, ce_iv, 1))
        rows.append(option_row(strike, "PE", pe_oi, pe_vol, pe_delta, pe_iv, -2))
    return {"callOi": 260, "putOi": 300, "expiryData": EXPIRY_DATA, "optionsChain": rows, "indiavixData": {}}


def ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"s": "ok", "code": 200, "message": "", "data": data})


@pytest.fixture
def fy(monkeypatch, tmp_data_dir, fake_fyers_keys):
    now = {"ts": NOW}
    monkeypatch.setattr(clock, "utc_now", lambda: now["ts"])
    monkeypatch.setattr(fyers, "_limiter", fyers.RateLimiter(per_second=10_000, per_minute=10_000))
    monkeypatch.setattr(fyers, "_sleep", lambda s: None)
    now_s = clock.epoch_seconds(NOW)
    fyers.save_token(
        fyers.FyersToken("TESTAPP-100", "access-1", None, now_s - 100, now_s + 36_000, None)
    )
    return now


@pytest.fixture
def chain_api(fy):
    """Serves the expiry listing for strikecount=1 and a chain whose spot encodes the expiry otherwise."""
    requests: list[dict] = []

    def reply(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        requests.append(params)
        if params["strikecount"] == "1":
            return ok(chain_data(table=TABLE[2:3]))
        return ok(chain_data(spot=100.4 if params["timestamp"] == str(expiry_epoch(EXPIRIES[1])) else 100.6))

    with respx.mock(assert_all_called=False) as mock:
        route = mock.route(**CHAIN).mock(side_effect=reply)
        yield requests, route


def frame() -> pd.DataFrame:
    return options.chain_frame(chain_data(), NOW, "NIFTY", date(2026, 9, 29))


def test_chain_frame_puts_both_sides_of_a_strike_on_one_row():
    df = frame()
    assert list(df.columns) == options.CHAIN_COLUMNS
    assert df["strike"].tolist() == [90.0, 95.0, 100.0, 105.0, 110.0]
    row = df[df["strike"] == 100].iloc[0]
    assert (row["ts"], row["underlying"], row["expiry"]) == (NOW, "NIFTY", date(2026, 9, 29))
    assert (row["spot"], row["future"]) == (100.4, 100.9)
    assert (row["ce_oi"], row["pe_oi"], row["ce_oi_chg"], row["pe_oi_chg"]) == (50, 50, 1, -2)
    assert (row["ce_iv"], row["pe_iv"], row["ce_delta"], row["pe_delta"]) == (15, 16, 0.5, -0.5)
    assert (row["ce_bid"], row["ce_ask"], row["ce_ltp"], row["pe_volume"]) == (9.9, 10.1, 10.0, 3)
    assert str(df["ts"].dtype) == "datetime64[ns, UTC]"
    assert all(df[c].dtype == "float64" for c in options.CHAIN_COLUMNS[3:])


def test_chain_frame_stores_missing_quotes_and_greeks_as_nan():
    data = chain_data()
    ce = data["optionsChain"][1]
    ce["bid"], ce["greeks"]["iv"] = 0, 0  # Fyers' "no bid" / "no IV"
    del data["optionsChain"][2]["greeks"]
    df = options.chain_frame(data, NOW, "NIFTY", date(2026, 9, 29))
    first = df.iloc[0]
    assert np.isnan(first["ce_bid"]) and np.isnan(first["ce_iv"]) and first["ce_delta"] == 0.9
    assert np.isnan(first["pe_iv"]) and np.isnan(first["pe_delta"]) and first["pe_oi"] == 140


def test_summary_metrics():
    s = options.summarize(frame())
    assert s["atm_strike"] == 100 and s["atm_iv"] == 15.5  # mean of the ATM CE and PE IVs
    assert (s["ce_oi"], s["pe_oi"]) == (260, 300) and s["pcr_oi"] == pytest.approx(300 / 260)
    assert s["pcr_volume"] == pytest.approx(21 / 15)
    assert (s["ce_oi_chg"], s["pe_oi_chg"]) == (5, -10)
    assert s["max_pain"] == 100
    # put nearest -25 delta: 95 (-0.26, IV 18); call nearest +25 delta: 105 (0.27, IV 14)
    assert s["skew_25d"] == pytest.approx(4.0)


def test_skew_is_nan_when_the_logged_strikes_do_not_reach_25_delta():
    near_atm = options.chain_frame(chain_data(table=TABLE[1:3]), NOW, "NIFTY", date(2026, 9, 29))
    assert np.isnan(options.summarize(near_atm)["skew_25d"])  # nearest call delta is 0.5


def test_max_pain_is_the_strike_where_holders_collect_least():
    strikes = np.array([100.0, 110.0, 120.0])
    # settle 100: puts pay 150*20 = 3000; 110: 1000 + 1500; 120: calls pay 100*20 + 10*10 = 2100
    assert options.max_pain(strikes, np.array([100.0, 10.0, 0.0]), np.array([0.0, 0.0, 150.0])) == 120
    assert np.isnan(options.max_pain(strikes, np.zeros(3), np.array([np.nan] * 3)))


def test_nearest_expiries_skip_past_ones():
    assert options.nearest_expiries(EXPIRY_DATA, date(2026, 9, 23)) == [
        (expiry_epoch(EXPIRIES[1]), EXPIRIES[1]),
        (expiry_epoch(EXPIRIES[2]), EXPIRIES[2]),
    ]
    # on expiry day the expiring series is still the nearest
    expiring = options.nearest_expiries(EXPIRY_DATA, date(2026, 9, 29), n=1)
    assert expiring == [(expiry_epoch(EXPIRIES[1]), EXPIRIES[1])]


def test_snapshot_fetches_the_two_nearest_expiries_and_stores_them(chain_api, tmp_data_dir):
    requests, _ = chain_api
    chain, summary = options.snapshot("NIFTY")
    base = {"symbol": "NSE:NIFTY50-INDEX", "greeks": "1"}
    assert requests == [
        {**base, "strikecount": "1", "timestamp": ""},
        {**base, "strikecount": "15", "timestamp": str(expiry_epoch(EXPIRIES[1]))},
        {**base, "strikecount": "15", "timestamp": str(expiry_epoch(EXPIRIES[2]))},
    ]
    assert len(chain) == 10 and set(chain["expiry"]) == {EXPIRIES[1], EXPIRIES[2]}
    stored = options.load_chain("NIFTY", date(2026, 9, 23))
    assert (tmp_data_dir / "options" / "NIFTY" / "2026-09-23.parquet").exists()
    assert len(stored) == 10 and list(stored.columns) == options.CHAIN_COLUMNS
    assert stored.groupby("expiry")["spot"].first().to_dict() == {EXPIRIES[1]: 100.4, EXPIRIES[2]: 100.6}
    summary = options.load_summary("NIFTY")
    assert list(summary.columns) == options.SUMMARY_COLUMNS
    assert summary["expiry"].tolist() == [EXPIRIES[1], EXPIRIES[2]]
    assert summary["ts"].tolist() == [NOW, NOW] and summary["max_pain"].tolist() == [100.0, 100.0]


def test_snapshots_only_ever_append(chain_api, fy):
    options.snapshot("NIFTY")
    day = date(2026, 9, 23)
    first = options.load_chain("NIFTY", day)
    fy["ts"] = NOW + pd.Timedelta(minutes=5)
    options.snapshot("NIFTY")
    both = options.load_chain("NIFTY", day)
    assert len(both) == 20
    pd.testing.assert_frame_equal(both.iloc[:10], first)
    assert options.load_summary("NIFTY")["ts"].tolist() == [NOW, NOW, fy["ts"], fy["ts"]]
    # a snapshot that is already stored is never written twice or changed
    again = first.copy()
    again["ce_ltp"] = 999.0
    assert options.append_rows(options.chain_path("NIFTY", day), again) == 0
    pd.testing.assert_frame_equal(options.load_chain("NIFTY", day), both)


def test_a_failing_underlying_is_skipped(chain_api, caplog):
    _, route = chain_api
    ok_reply = route.side_effect

    def reply(request):
        if request.url.params["symbol"] == "NSE:NIFTYBANK-INDEX":
            return httpx.Response(200, json={"s": "error", "code": -300, "message": "invalid symbol"})
        return ok_reply(request)

    route.side_effect = reply
    with caplog.at_level(logging.WARNING, logger="candly.data.options"):
        counts = options.take_snapshots()
    assert counts == {"NIFTY": 10, "SENSEX": 10}
    assert "BANKNIFTY option snapshot skipped" in caplog.text


def test_option_chain_rejects_bad_input_and_bad_payloads(chain_api):
    _, route = chain_api
    with pytest.raises(ValueError):
        fyers.option_chain("NSE:NIFTY50-INDEX", 51)
    route.side_effect = lambda request: httpx.Response(200, json={"s": "ok", "code": 200, "data": {}})
    with pytest.raises(fyers.FyersError, match="without optionsChain"):
        fyers.option_chain("NSE:NIFTY50-INDEX", 15)


@pytest.fixture
def snaps(monkeypatch):
    calls: list[list[str]] = []

    def record(names):
        calls.append(list(names))
        return dict.fromkeys(names, 62)

    monkeypatch.setattr(options, "take_snapshots", record)
    monkeypatch.setattr(scheduler, "_options_blocked", None)
    return calls


@pytest.mark.parametrize(
    ("now", "closing", "expected"),
    [
        (NOW, False, ["NIFTY", "BANKNIFTY", "SENSEX"]),
        (pd.Timestamp(datetime(2026, 9, 23, 9, 10, 50), tz=IST), False, None),  # pre-open
        (pd.Timestamp(datetime(2026, 9, 23, 15, 30, 50), tz=IST), False, None),  # after the close
        (pd.Timestamp(datetime(2026, 9, 23, 15, 31, 50), tz=IST), True, ["NIFTY", "BANKNIFTY", "SENSEX"]),
        (pd.Timestamp(datetime(2026, 9, 26, 15, 31, 50), tz=IST), True, None),  # Saturday
    ],
)
def test_option_snapshots_follow_the_session(snaps, monkeypatch, now, closing, expected):
    monkeypatch.setattr(clock, "utc_now", lambda: now.tz_convert("UTC"))
    result = scheduler.option_snapshots(closing=closing)
    assert snaps == ([expected] if expected else [])
    assert result == (dict.fromkeys(expected, 62) if expected else {})


def test_option_snapshots_never_raise_and_warn_once(monkeypatch, caplog, tmp_data_dir, fake_fyers_keys):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    monkeypatch.setattr(scheduler, "_options_blocked", None)
    with caplog.at_level(logging.DEBUG, logger="candly.jobs.scheduler"):
        assert scheduler.option_snapshots() == {}  # no Fyers session stored
        assert scheduler.option_snapshots() == {}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "option snapshots skipped" in warnings[0].getMessage()

    def broken(names):
        raise RuntimeError("boom")

    monkeypatch.setattr(options, "take_snapshots", broken)
    assert scheduler.option_snapshots() == {}
