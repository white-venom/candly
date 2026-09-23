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
    drift: float | np.ndarray = 0.0,
) -> pd.DataFrame:
    """Random candles on the exchange's bar grid.

    `vol` is the typical bar range as a fraction of price. `plain=True` keeps every bar's body at
    30-60 % of its range with both wicks present, so single-bar patterns never occur by chance.
    `drift` shifts each bar's opening gap by that many bar ranges (a scalar, or one value per bar for
    regimes); it moves prices without changing any bar's shape.
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
    gaps = (rng.normal(0.0, gap, n) + drift) * bar_range
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
    df: pd.DataFrame,
    every: int = 25,
    p_follow: float = 0.85,
    seed: int = 0,
    first: int = 60,
    follow_bars: int = 1,
    bearish_share: float = 0.0,
) -> pd.DataFrame:
    """Known-answer signal: a marubozu every `every` bars, then `follow_bars` bars that each move one
    typical range its way with probability `p_follow` (else half a range against it). Marubozus are
    bullish, or bearish with probability `bearish_share`. Later bars are rescaled to stay continuous."""
    rng = np.random.default_rng(seed)
    o, h, lo, c = (df[x].to_numpy(dtype=float).copy() for x in ("open", "high", "low", "close"))
    n = len(df)
    for t in range(first, n, every):
        sign = -1.0 if bearish_share > 0 and rng.uniform() < bearish_share else 1.0
        typical = h[t - 1] - lo[t - 1]
        body = 1.5 * typical
        o[t], c[t] = c[t - 1], c[t - 1] + sign * body
        h[t], lo[t] = max(o[t], c[t]) + 0.02 * body, min(o[t], c[t]) - 0.02 * body
        for j in range(t + 1, min(t + 1 + follow_bars, n)):
            move = sign * (typical if rng.uniform() < p_follow else -0.5 * typical)
            old_close = c[j]
            o[j], c[j] = c[j - 1], c[j - 1] + move
            wick = abs(move) * 0.6
            h[j], lo[j] = max(o[j], c[j]) + wick, min(o[j], c[j]) - wick
            ratio = c[j] / old_close
            for arr in (o, h, lo, c):
                arr[j + 1 :] *= ratio
    return validate_candles(df.assign(open=o, high=h, low=lo, close=c))
