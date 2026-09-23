"""Candle hygiene: session filtering, de-duplication, closed-bar checks and missing-bar reports."""

import logging
from datetime import date

import numpy as np
import pandas as pd

from candly.core.calendar import IST, get_calendar
from candly.core.schema import CANDLE_COLUMNS, PRICE_COLUMNS, empty_candles, validate_candles
from candly.core.timeframes import is_intraday, tf_delta, validate_tf
from candly.data.clock import epoch_seconds, epoch_series

log = logging.getLogger(__name__)


def session_bounds(ts: pd.Series, exchange: str) -> tuple[pd.Series, pd.Series]:
    """Session open and close (UTC) of each timestamp's IST trading date."""
    cal = get_calendar()
    local_dates = ts.dt.tz_convert(IST).dt.date
    bounds = {d: cal.session_times(exchange, d) for d in local_dates.unique()}
    opens = local_dates.map(lambda d: bounds[d][0]).astype("datetime64[ns, UTC]")
    closes = local_dates.map(lambda d: bounds[d][1]).astype("datetime64[ns, UTC]")
    return opens, closes


def fake_bar_mask(df: pd.DataFrame, exchange: str, kind: str) -> pd.Series:
    """True for bars a source invented rather than traded (Yahoo emits flat, zero-volume bars on
    exchange holidays):
    - equities and futures: any flat bar (open == high == low == close) with zero volume;
    - indices, which always have zero volume on Yahoo: flat bars on a configured holiday;
    - any kind: every bar on one of this exchange's configured holidays, unless the day shows a real
      session (a special session in markets.yaml, or at least one traded bar). Only the exchange's own
      holidays count, so e.g. an MCX evening session on an NSE holiday is kept."""
    cal = get_calendar()
    holidays = cal.spec(exchange).holidays
    local = df["ts"].dt.tz_convert(IST).dt.date
    closed_days = {d: d in holidays and not cal.is_trading_day(exchange, d) for d in local.unique()}
    on_holiday = local.map(closed_days).astype(bool)
    flat = (df["open"] == df["high"]) & (df["high"] == df["low"]) & (df["low"] == df["close"])
    if kind == "index":
        fake, traded = flat & on_holiday, ~flat
    else:
        fake, traded = flat & (df["volume"] == 0), df["volume"] > 0
    real_day = traded.groupby(local).transform("any").astype(bool)
    return fake | (on_holiday & ~real_day)


def clean_candles(df: pd.DataFrame, tf: str, exchange: str, kind: str | None = None) -> pd.DataFrame:
    """Keep bars inside the session and on the tf grid, drop duplicates (last wins), repair OHLC.
    With the instrument's `kind`, also drop fake holiday bars (see fake_bar_mask). Leave `kind` unset
    only for frames that were already cleaned with it, e.g. stored 5m bars being resampled."""
    tf = validate_tf(tf)
    if df.empty:
        return empty_candles()
    out = df[CANDLE_COLUMNS].copy()
    out["ts"] = pd.to_datetime(out["ts"], utc=True).astype("datetime64[ns, UTC]")
    out[PRICE_COLUMNS + ["volume", "oi"]] = out[PRICE_COLUMNS + ["volume", "oi"]].astype("float64")

    # A price <= 0 is a source placeholder, e.g. Fyers INDIAVIX 1D has open = low = -1 on some days.
    no_price = out[PRICE_COLUMNS].isna().any(axis=1) | (out[PRICE_COLUMNS] <= 0).any(axis=1)
    if no_price.any():
        log.info("%s %s: dropped %d bars without a valid price", exchange, tf, int(no_price.sum()))
        out = out[~no_price]

    opens, closes = session_bounds(out["ts"], exchange)
    if is_intraday(tf):
        offset = out["ts"] - opens
        in_session = (out["ts"] >= opens) & (out["ts"] < closes) & (offset % tf_delta(tf) == pd.Timedelta(0))
    else:
        in_session = out["ts"] == opens
    if not in_session.all():
        log.info("%s %s: dropped %d bars outside the session grid", exchange, tf, int((~in_session).sum()))
        out = out[in_session]

    out = out.sort_values("ts", kind="stable").drop_duplicates("ts", keep="last")
    out["volume"] = out["volume"].fillna(0.0)

    if kind is not None and not out.empty:
        fake = fake_bar_mask(out, exchange, kind)
        if fake.any():
            log.info(
                "%s %s: dropped %d fake bars (flat zero-volume or on a holiday)",
                exchange, tf, int(fake.sum()),
            )
            out = out[~fake]

    body_high = out[["open", "close"]].max(axis=1)
    body_low = out[["open", "close"]].min(axis=1)
    broken = (out["high"] < body_high) | (out["low"] > body_low)
    if broken.any():
        log.warning("%s %s: repaired %d bars with inconsistent OHLC", exchange, tf, int(broken.sum()))
        out["high"] = np.maximum(out["high"], body_high)
        out["low"] = np.minimum(out["low"], body_low)
    return validate_candles(out)


def forming_mask(df: pd.DataFrame, exchange: str, tf: str, now: pd.Timestamp) -> pd.Series:
    """True for bars whose close time is still in the future."""
    mask = pd.Series(False, index=df.index)
    # A bar always closes within tf_delta of its open, so only the most recent rows need the calendar.
    candidates = df["ts"] + tf_delta(tf) > now
    if candidates.any():
        cal = get_calendar()
        closes = df.loc[candidates, "ts"].map(lambda ts: cal.bar_close_time(exchange, ts, tf))
        mask.loc[candidates] = closes > now
    return mask


def closed_only(df: pd.DataFrame, exchange: str, tf: str, now: pd.Timestamp) -> pd.DataFrame:
    if df.empty:
        return df
    forming = forming_mask(df, exchange, tf, now)
    return df[~forming].reset_index(drop=True) if forming.any() else df


def missing_bars(
    df: pd.DataFrame, tf: str, exchange: str, start: date | None = None, end: date | None = None
) -> pd.DataFrame:
    """Per trading day between start and end (default: the data's first and last day), the bars the
    calendar expects versus the bars present. Only days with at least one missing bar are returned."""
    columns = ["date", "expected", "present", "missing"]
    if df.empty and (start is None or end is None):
        return pd.DataFrame(columns=columns)
    cal = get_calendar()
    local = df["ts"].dt.tz_convert(IST)
    start = start or local.iloc[0].date()
    end = end or local.iloc[-1].date()
    present = set(epoch_series(df["ts"]).tolist())
    rows = []
    for day in pd.date_range(start, end, freq="D"):
        expected = cal.expected_bar_opens(exchange, day.date(), tf)
        if not expected:
            continue
        have = sum(1 for ts in expected if epoch_seconds(ts) in present)
        if have < len(expected):
            rows.append((day.date(), len(expected), have, len(expected) - have))
    return pd.DataFrame(rows, columns=columns)
