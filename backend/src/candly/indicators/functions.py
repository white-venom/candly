"""Causal indicator primitives. Every value at row t uses rows 0..t only."""

from __future__ import annotations

import numpy as np
import pandas as pd

from candly.core.calendar import IST


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (RMA), as used by ATR, RSI and ADX."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    )
    return ranges.max(axis=1, skipna=True)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return wilder(true_range(df), period)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0.0), period)
    loss = wilder((-delta).clip(lower=0.0), period)
    out = 100.0 - 100.0 / (1.0 + gain / loss)
    out = out.where(loss > 0, pd.Series(np.where(gain > 0, 100.0, 50.0), index=close.index))
    return out.where(gain.notna() & loss.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, pd.Series]:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return {"macd": line, "macd_signal": sig, "macd_hist": line - sig}


def stochastic(df: pd.DataFrame, k: int = 14, smooth_k: int = 3, d: int = 3) -> dict[str, pd.Series]:
    lowest = df["low"].rolling(k, min_periods=k).min()
    highest = df["high"].rolling(k, min_periods=k).max()
    span = highest - lowest
    raw = (100.0 * (df["close"] - lowest) / span).where(span > 0, 50.0).where(span.notna())
    slow_k = sma(raw, smooth_k)
    return {"stoch_k": slow_k, "stoch_d": sma(slow_k, d)}


def adx(df: pd.DataFrame, period: int = 14) -> dict[str, pd.Series]:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0).where(up.notna())
    minus_dm = down.where((down > up) & (down > 0), 0.0).where(down.notna())
    atr_ = wilder(true_range(df).where(up.notna()), period)
    plus_di = 100.0 * wilder(plus_dm, period) / atr_
    minus_di = 100.0 * wilder(minus_dm, period) / atr_
    di_sum = plus_di + minus_di
    dx = (100.0 * (plus_di - minus_di).abs() / di_sum).where(di_sum > 0, 0.0).where(di_sum.notna())
    return {"adx14": wilder(dx, period), "plus_di14": plus_di, "minus_di14": minus_di}


def bollinger(close: pd.Series, window: int = 20, width: float = 2.0) -> dict[str, pd.Series]:
    mid = sma(close, window)
    std = close.rolling(window, min_periods=window).std(ddof=0)
    upper, lower = mid + width * std, mid - width * std
    return {"bb_upper": upper, "bb_mid": mid, "bb_lower": lower, "bb_width": (upper - lower) / mid}


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> dict[str, pd.Series]:
    atr_ = atr(df, period).to_numpy()
    high, low, close = (df[c].to_numpy(dtype=float) for c in ("high", "low", "close"))
    mid = (high + low) / 2.0
    basic_upper, basic_lower = mid + multiplier * atr_, mid - multiplier * atr_
    n = len(df)
    line = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    upper = lower = np.nan
    for i in range(n):
        if np.isnan(atr_[i]):
            continue
        if np.isnan(upper):
            upper, lower = basic_upper[i], basic_lower[i]
            direction[i] = 1.0 if close[i] > upper else -1.0
        else:
            prev_close = close[i - 1]
            upper = basic_upper[i] if basic_upper[i] < upper or prev_close > upper else upper
            lower = basic_lower[i] if basic_lower[i] > lower or prev_close < lower else lower
            if direction[i - 1] < 0:
                direction[i] = 1.0 if close[i] > upper else -1.0
            else:
                direction[i] = -1.0 if close[i] < lower else 1.0
        line[i] = lower if direction[i] > 0 else upper
    return {
        "supertrend": pd.Series(line, index=df.index),
        "supertrend_dir": pd.Series(direction, index=df.index),
    }


def obv(df: pd.DataFrame) -> pd.Series:
    step = np.sign(df["close"].diff()).fillna(0.0)
    return (step * df["volume"]).cumsum()


def session_day(ts: pd.Series) -> pd.Series:
    return ts.dt.tz_convert(IST).dt.date


def vwap(df: pd.DataFrame) -> pd.Series:
    day = session_day(df["ts"])
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_pv = (typical * df["volume"]).groupby(day).cumsum()
    cum_v = df["volume"].groupby(day).cumsum()
    return (cum_pv / cum_v).where(cum_v > 0)


def rel_volume(df: pd.DataFrame, intraday: bool, sessions: int = 20) -> pd.Series:
    volume = df["volume"]
    if intraday:
        local = df["ts"].dt.tz_convert(IST)
        slot = local.dt.hour * 60 + local.dt.minute
        baseline = volume.groupby(slot).transform(
            lambda s: s.shift(1).rolling(sessions, min_periods=sessions).mean()
        )
    else:
        baseline = volume.shift(1).rolling(sessions, min_periods=sessions).mean()
    return (volume / baseline).where(baseline > 0)
