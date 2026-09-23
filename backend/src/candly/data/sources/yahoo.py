"""Yahoo Finance via yfinance: a dev-only fallback for NSE/BSE until Fyers is connected."""

import logging

import numpy as np
import pandas as pd

from candly.core.calendar import IST, get_calendar
from candly.core.instruments import Instrument
from candly.core.schema import empty_candles
from candly.core.settings import get_settings
from candly.core.timeframes import validate_tf
from candly.data import clock
from candly.data.clean import clean_candles, closed_only
from candly.data.sources import SourceError, SourceUnavailable

log = logging.getLogger(__name__)

INTERVALS = {"5m": "5m", "15m": "15m", "1h": "60m", "1D": "1d"}
# Yahoo serves 5m/15m for about the last 60 days and 60m for about 730; stay a little inside both.
LOOKBACK = {"5m": pd.Timedelta(days=58), "15m": pd.Timedelta(days=58), "1h": pd.Timedelta(days=725)}


def fetch_candles(
    instrument: Instrument,
    tf: str,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    *,
    include_forming: bool = False,
) -> pd.DataFrame:
    tf = validate_tf(tf)
    symbol = instrument.source_symbol("yahoo")
    if not symbol:
        raise SourceUnavailable(f"{instrument.id} is not available on yahoo")
    now = clock.utc_now()
    if tf in LOOKBACK:
        earliest = now - LOOKBACK[tf]
        start = earliest if start is None else max(clock.to_utc(start), earliest)
    raw = _download(symbol, INTERVALS[tf], start, end)
    return to_candles(
        raw, instrument.exchange, tf, now, include_forming=include_forming, kind=instrument.kind
    )


def _download(
    symbol: str, interval: str, start: pd.Timestamp | None, end: pd.Timestamp | None
) -> pd.DataFrame:
    import yfinance as yf
    from yfinance.exceptions import YFException, YFPricesMissingError

    cache_dir = get_settings().data_dir / "cache" / "yfinance"
    cache_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache_dir))
    kwargs: dict = {"interval": interval, "auto_adjust": False, "actions": False, "raise_errors": True}
    if start is None:
        kwargs["period"] = "max"
    else:
        kwargs["start"] = clock.to_utc(start).to_pydatetime()
    if end is not None:
        kwargs["end"] = clock.to_utc(end).to_pydatetime()
    try:
        return yf.Ticker(symbol).history(**kwargs)
    except YFPricesMissingError:
        return pd.DataFrame()
    except YFException as exc:
        raise SourceError(f"yahoo {symbol} {interval}: {exc}") from exc


def to_candles(
    raw: pd.DataFrame,
    exchange: str,
    tf: str,
    now: pd.Timestamp,
    *,
    include_forming: bool = False,
    kind: str | None = None,
) -> pd.DataFrame:
    """yfinance frame (index in exchange time, Open/High/Low/Close/Volume) -> candle frame.
    Yahoo dates daily rows at local midnight; they move to the session open. Its raw OHLC is
    already split-adjusted, and `oi` doesn't exist there. `kind` enables the fake-bar filter
    (Yahoo invents flat zero-volume bars on holidays)."""
    if raw is None or raw.empty:
        return empty_candles()
    index = pd.DatetimeIndex(raw.index)
    if index.tz is None:
        index = index.tz_localize(IST)
    if tf == "1D":
        cal = get_calendar()
        ts = pd.DatetimeIndex([cal.session_times(exchange, d)[0] for d in index.tz_convert(IST).date])
    else:
        ts = index.tz_convert("UTC")
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": raw["Open"].to_numpy(dtype="float64"),
            "high": raw["High"].to_numpy(dtype="float64"),
            "low": raw["Low"].to_numpy(dtype="float64"),
            "close": raw["Close"].to_numpy(dtype="float64"),
            "volume": raw["Volume"].to_numpy(dtype="float64"),
            "oi": np.nan,
        }
    )
    df = clean_candles(df, tf, exchange, kind=kind)
    return df if include_forming else closed_only(df, exchange, tf, now)
