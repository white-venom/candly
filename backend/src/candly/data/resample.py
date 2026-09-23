"""Build 15m / 1h (and a provisional 1D) bars from 5m bars, anchored to each session's open."""

import pandas as pd

from candly.core.schema import validate_candles
from candly.core.timeframes import tf_delta, validate_tf
from candly.data.clean import clean_candles, session_bounds

_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "oi": "last"}


def resample_candles(df_5m: pd.DataFrame, tf: str, exchange: str) -> pd.DataFrame:
    """Aggregate 5m bars into `tf` bars. Buckets start at the session open, so NSE 1h bars open at
    09:15, 10:15 ... 15:15 (the last one is 15 minutes long). The last bucket may be incomplete;
    callers drop it with `clean.closed_only` when they need closed bars only.

    1D here is only for the live forming bar: stored daily bars come from the broker's daily
    endpoint because NSE's official close is not the last 5m close."""
    tf = validate_tf(tf)
    bars = clean_candles(df_5m, "5m", exchange)
    if tf == "5m" or bars.empty:
        return bars
    opens, _ = session_bounds(bars["ts"], exchange)
    if tf == "1D":
        bucket = opens
    else:
        step = tf_delta(tf)
        bucket = opens + ((bars["ts"] - opens) // step) * step
    out = bars.groupby(bucket.rename("ts"), sort=True).agg(_AGG).reset_index()
    return validate_candles(out)
