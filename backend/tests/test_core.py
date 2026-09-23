import logging

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from candly.api.app import create_app
from candly.core.calendar import IST
from candly.core.instruments import UnknownInstrument, get_instrument, load_watchlist
from candly.core.log import RedactSecrets
from candly.core.schema import CANDLE_COLUMNS, CandleSchemaError, empty_candles, validate_candles
from candly.core.settings import Settings
from candly.core.timeframes import is_intraday, tf_delta, validate_tf


def candles(**overrides) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "ts": pd.date_range("2026-09-21 03:45", periods=3, freq="D", tz="UTC"),
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
            "volume": [1000.0, 1100.0, 900.0],
            "oi": [float("nan")] * 3,
        }
    )
    for column, values in overrides.items():
        df[column] = values
    return df


def test_validate_accepts_clean_frame():
    out = validate_candles(candles())
    assert list(out.columns) == CANDLE_COLUMNS
    assert str(out["ts"].dtype) == "datetime64[ns, UTC]"


def test_validate_converts_other_timezones_to_utc():
    df = candles()
    df["ts"] = df["ts"].dt.tz_convert(IST)
    assert validate_candles(df)["ts"].iloc[0] == pd.Timestamp("2026-09-21 03:45", tz="UTC")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda df: df.assign(high=[100.5, 103.0, 104.0]),
        lambda df: df.assign(ts=df["ts"].dt.tz_localize(None)),
        lambda df: df.assign(ts=[df["ts"][0], df["ts"][0], df["ts"][2]]),
        lambda df: df.assign(ts=df["ts"][::-1].to_numpy()),
        lambda df: df.assign(close=[101.0, None, 103.0]),
        lambda df: df.drop(columns=["oi"]),
    ],
    ids=["high-below-close", "naive-ts", "duplicate-ts", "unsorted", "nan-price", "missing-column"],
)
def test_validate_rejects_bad_frames(mutate):
    with pytest.raises(CandleSchemaError):
        validate_candles(mutate(candles()))


def test_empty_candles_is_valid():
    assert validate_candles(empty_candles()).empty


def test_timeframes():
    assert validate_tf("15m") == "15m"
    assert is_intraday("1h") and not is_intraday("1D")
    assert tf_delta("5m").total_seconds() == 300
    with pytest.raises(ValueError):
        validate_tf("2m")


def test_watchlist_is_consistent():
    items = load_watchlist()
    assert len({i.id for i in items}) == len(items)
    assert all(i.sources.get("fyers") for i in items)
    assert {i.exchange for i in items} == {"NSE", "BSE", "MCX"}


def test_get_instrument():
    reliance = get_instrument("NSE:RELIANCE")
    assert reliance.exchange == "NSE" and reliance.symbol == "RELIANCE"
    assert reliance.source_symbol("yahoo") == "RELIANCE.NS"
    with pytest.raises(UnknownInstrument):
        get_instrument("NSE:DOESNOTEXIST")


def test_settings_default_to_yahoo_without_fyers_keys():
    settings = Settings(_env_file=None)
    assert settings.resolved_data_source() == "yahoo"
    assert not settings.has_fyers


def test_secrets_are_hidden_in_repr():
    settings = Settings(_env_file=None, anthropic_api_key="sk-ant-test-secret-value")
    assert "sk-ant-test-secret-value" not in repr(settings)
    assert settings.has_anthropic
    assert settings.secret_values() == ["sk-ant-test-secret-value"]


def test_log_filter_redacts_secrets():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "token=%s", ("abcdef123456",), None)
    RedactSecrets(["abcdef123456"]).filter(record)
    assert record.getMessage() == "token=***"


def test_app_starts():
    client = TestClient(create_app())
    assert client.get("/openapi.json").status_code == 200
