"""Per-bar market context: trend, volatility regime, relative volume, session phase, nearby level."""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from candly.core.calendar import IST, get_calendar
from candly.core.timeframes import is_intraday, tf_delta
from candly.features.levels import level_frame, nearest_level
from candly.indicators import compute_indicators
from candly.indicators.functions import ema
from candly.patterns import load_pattern_config

CONTEXT_COLUMNS = [
    "trend",
    "trend_strength",
    "vol_regime",
    "vol_pct",
    "rel_volume",
    "session_phase",
    "near_level",
    "rsi14",
    "atr14",
]
_REFERENCE_DAY = date(2025, 1, 6)


def bars_per_session(exchange: str, tf: str) -> int:
    if not is_intraday(tf):
        return 1
    start, close = get_calendar().session_times(exchange, _REFERENCE_DAY)
    return math.ceil((close - start) / tf_delta(tf))


def trend_labels(
    close: pd.Series, atr: pd.Series, fast: int, slow: int, slope_bars: int, min_slope_atr: float
) -> pd.Series:
    ema_fast, ema_slow = ema(close, fast), ema(close, slow)
    slope = (ema_fast - ema_fast.shift(slope_bars)) / atr
    up = (close > ema_slow) & (ema_fast > ema_slow) & (slope >= min_slope_atr)
    down = (close < ema_slow) & (ema_fast < ema_slow) & (slope <= -min_slope_atr)
    labels = np.select([up, down], ["up", "down"], "sideways").astype(object)
    labels[(ema_slow.isna() | slope.isna()).to_numpy()] = None
    return pd.Series(labels, index=close.index, dtype=object)


def vol_regime(
    atr: pd.Series, window: int, min_periods: int, cuts: list[float]
) -> tuple[pd.Series, pd.Series]:
    pct = atr.rolling(window, min_periods=min_periods).rank(pct=True)
    labels = np.select([pct <= cuts[0], pct > cuts[1]], ["low", "high"], "normal").astype(object)
    labels[pct.isna().to_numpy()] = None
    return pd.Series(labels, index=atr.index, dtype=object), pct


def session_phases(ts: pd.Series, exchange: str) -> pd.Series:
    """calendar.session_phase for every bar, evaluated once per distinct (session open, close, bar time)."""
    cal = get_calendar()
    local = ts.dt.tz_convert(IST)
    day = local.dt.date
    bounds = {d: cal.session_times(exchange, d) for d in pd.unique(day)}
    midnight = local.dt.normalize()
    keys = pd.DataFrame(
        {
            "open": day.map(lambda d: bounds[d][0]) - midnight,
            "close": day.map(lambda d: bounds[d][1]) - midnight,
            "bar": local - midnight,
        },
        index=ts.index,
    )
    first = keys.drop_duplicates()
    phase = {
        tuple(k): cal.session_phase(exchange, ts.loc[i])
        for i, k in zip(first.index, first.to_numpy(), strict=True)
    }
    return pd.Series([phase[tuple(k)] for k in keys.to_numpy()], index=ts.index, dtype=object)


def compute_context(df: pd.DataFrame, tf: str, exchange: str, config: dict | None = None) -> pd.DataFrame:
    """Context for each bar, using bars up to and including that bar only."""
    cfg = config or load_pattern_config()
    ctx = cfg["context"]
    ind = compute_indicators(df, tf, ["atr14", "adx14", "rsi14", "rel_volume"])
    window = int(ctx["vol_window_days"]) * bars_per_session(exchange, tf)
    regime, pct = vol_regime(
        ind["atr14"], window, max(1, int(window * float(ctx["vol_min_fraction"]))), ctx["vol_regime_cuts"]
    )
    levels = level_frame(df, tf, exchange, cfg)
    out = pd.DataFrame(
        {
            "trend": trend_labels(
                df["close"],
                ind["atr14"],
                int(ctx["trend_fast_ema"]),
                int(ctx["trend_slow_ema"]),
                int(ctx["trend_slope_bars"]),
                float(ctx["trend_min_slope_atr"]),
            ),
            "trend_strength": ind["adx14"],
            "vol_regime": regime,
            "vol_pct": pct,
            "rel_volume": ind["rel_volume"],
            "session_phase": (
                session_phases(df["ts"], exchange)
                if is_intraday(tf) and len(df)
                else pd.Series(None, index=df.index, dtype=object)
            ),
            "near_level": nearest_level(df["close"], levels, ind["atr14"], float(ctx["near_level_atr"])),
            "rsi14": ind["rsi14"],
            "atr14": ind["atr14"],
        },
        index=df.index,
    )
    return out[CONTEXT_COLUMNS]
