"""Calendar-aligned synthetic candles for tests and demos. Never used for results."""

from __future__ import annotations

import numpy as np
import pandas as pd

from candly.core.calendar import get_calendar
from candly.core.schema import validate_candles


def bar_times(tf: str, start: str, end: str, exchange: str = "NSE") -> pd.DatetimeIndex:
    cal = get_calendar()
    opens: list[pd.Timestamp] = []
    for d in pd.date_range(start, end, freq="D"):
        opens.extend(cal.expected_bar_opens(exchange, d.date(), tf))
    return pd.DatetimeIndex(opens).tz_convert("UTC")


def synthetic_candles(
    tf: str = "1D",
    start: str = "2020-01-01",
    end: str = "2020-12-31",
    exchange: str = "NSE",
    seed: int = 0,
    price: float = 100.0,
    vol: float = 0.01,
    p_up: float = 0.5,
    gap: float = 0.1,
    plain: bool = False,
    volume: float = 1e5,
) -> pd.DataFrame:
    """Random candles on the exchange's bar grid.

    `vol` is the typical bar range as a fraction of price. `plain=True` keeps every bar's body at
    30-60 % of its range with both wicks present, so single-bar patterns never occur by chance.
    """
    ts = bar_times(tf, start, end, exchange)
    n = len(ts)
    rng = np.random.default_rng(seed)
    log_vol = np.zeros(n)
    shocks = rng.normal(0.0, 0.08, n)
    for i in range(1, n):
        log_vol[i] = 0.97 * log_vol[i - 1] + shocks[i]
    bar_range = vol * np.exp(log_vol) * rng.uniform(0.6, 1.4, n)
    if plain:
        body_frac = rng.uniform(0.3, 0.6, n)
        upper_share = rng.uniform(0.4, 0.6, n)
    else:
        body_frac = rng.uniform(0.0, 1.0, n)
        upper_share = rng.uniform(0.0, 1.0, n)
    sign = np.where(rng.uniform(size=n) < p_up, 1.0, -1.0)
    gaps = rng.normal(0.0, gap, n) * bar_range
    gaps[0] = 0.0
    body = sign * body_frac * bar_range
    log_close = np.log(price) + np.cumsum(gaps + body)
    log_open = log_close - body
    wick = (1.0 - body_frac) * bar_range
    log_high = np.maximum(log_open, log_close) + upper_share * wick
    log_low = np.minimum(log_open, log_close) - (1.0 - upper_share) * wick
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": np.exp(log_open),
            "high": np.exp(log_high),
            "low": np.exp(log_low),
            "close": np.exp(log_close),
            "volume": np.round(volume * rng.lognormal(0.0, 0.4, n)),
            "oi": np.nan,
        }
    )
    return validate_candles(df)


def plant_marubozu_edge(
    df: pd.DataFrame, every: int = 25, p_follow: float = 0.85, seed: int = 0, first: int = 60
) -> pd.DataFrame:
    """Known-answer signal: a bullish marubozu every `every` bars, followed by an up bar with
    probability `p_follow` (else a smaller down bar). Later bars are rescaled to stay continuous."""
    rng = np.random.default_rng(seed)
    o, h, lo, c = (df[x].to_numpy(dtype=float).copy() for x in ("open", "high", "low", "close"))
    for t in range(first, len(df), every):
        typical = h[t - 1] - lo[t - 1]
        body = 1.5 * typical
        o[t], c[t] = c[t - 1], c[t - 1] + body
        h[t], lo[t] = c[t] + 0.02 * body, o[t] - 0.02 * body
        if t + 1 >= len(df):
            continue
        move = typical if rng.uniform() < p_follow else -0.5 * typical
        old_close = c[t + 1]
        o[t + 1], c[t + 1] = c[t], c[t] + move
        wick = abs(move) * 0.6
        h[t + 1], lo[t + 1] = max(o[t + 1], c[t + 1]) + wick, min(o[t + 1], c[t + 1]) - wick
        ratio = c[t + 1] / old_close
        for arr in (o, h, lo, c):
            arr[t + 2 :] *= ratio
    return validate_candles(df.assign(open=o, high=h, low=lo, close=c))
