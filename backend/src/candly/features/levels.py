"""Key price levels: previous-session HLC, classic pivots + CPR, confirmed swings and session VWAP."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

from candly.core.calendar import get_calendar
from candly.core.timeframes import is_intraday
from candly.indicators.functions import session_day, vwap
from candly.patterns import load_pattern_config

LevelKind = Literal[
    "pdh",
    "pdl",
    "pdc",
    "swing_high",
    "swing_low",
    "vwap",
    "pivot",
    "r1",
    "r2",
    "s1",
    "s2",
    "cpr_top",
    "cpr_bottom",
]
LEVEL_LABELS: dict[str, str] = {
    "pdh": "Prev day high",
    "pdl": "Prev day low",
    "pdc": "Prev day close",
    "pivot": "Pivot",
    "r1": "R1",
    "r2": "R2",
    "s1": "S1",
    "s2": "S2",
    "cpr_top": "CPR top",
    "cpr_bottom": "CPR bottom",
    "swing_high": "Swing high",
    "swing_low": "Swing low",
    "vwap": "VWAP",
}
LEVEL_KINDS = list(LEVEL_LABELS)


class Level(BaseModel):
    price: float
    label: str
    kind: LevelKind


def pivots(high: pd.Series, low: pd.Series, close: pd.Series) -> dict[str, pd.Series]:
    p = (high + low + close) / 3.0
    bc = (high + low) / 2.0
    tc = 2.0 * p - bc
    return {
        "pivot": p,
        "r1": 2.0 * p - low,
        "r2": p + (high - low),
        "s1": 2.0 * p - high,
        "s2": p - (high - low),
        "cpr_top": np.maximum(tc, bc),
        "cpr_bottom": np.minimum(tc, bc),
    }


def swing_points(df: pd.DataFrame, k: int) -> tuple[pd.Series, pd.Series]:
    """Swing highs/lows as known at each bar: the value of bar t-k when it is a swing, else NaN.

    Bar j is a swing high if its high beats the k bars before it and is not exceeded by the k bars
    after it. It only becomes known at bar j+k, which keeps it causal.
    """
    high, low = df["high"], df["low"]
    cand_h, cand_l = high.shift(k), low.shift(k)
    left_h = high.shift(k + 1).rolling(k, min_periods=k).max()
    left_l = low.shift(k + 1).rolling(k, min_periods=k).min()
    right_h = high.rolling(k, min_periods=k).max()
    right_l = low.rolling(k, min_periods=k).min()
    swing_h = cand_h.where((cand_h > left_h) & (cand_h >= right_h))
    swing_l = cand_l.where((cand_l < left_l) & (cand_l <= right_l))
    return swing_h, swing_l


def _session_hlc(df: pd.DataFrame, tf: str) -> tuple[pd.DataFrame, pd.Series]:
    """(one row per session with high/low/close, each bar's session key)."""
    if not is_intraday(tf):
        key = pd.Series(np.arange(len(df)), index=df.index)
        return df[["high", "low", "close"]].set_axis(key.to_numpy()), key
    key = session_day(df["ts"])
    sessions = df.groupby(key).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    return sessions, key


def level_frame(df: pd.DataFrame, tf: str, exchange: str, config: dict | None = None) -> pd.DataFrame:
    """Levels in force during each bar, built only from data up to that bar.

    Day levels come from the previous session (previous bar on 1D); swings are the latest confirmed
    ones; VWAP is the running session VWAP (intraday only).
    """
    ctx = (config or load_pattern_config())["context"]
    sessions, key = _session_hlc(df, tf)
    prev = sessions.shift(1)
    day = {
        "pdh": prev["high"].reindex(key.to_numpy()).to_numpy(),
        "pdl": prev["low"].reindex(key.to_numpy()).to_numpy(),
        "pdc": prev["close"].reindex(key.to_numpy()).to_numpy(),
    }
    out = pd.DataFrame(day, index=df.index)
    for name, series in pivots(out["pdh"], out["pdl"], out["pdc"]).items():
        out[name] = series
    swing_h, swing_l = swing_points(df, int(ctx["swing_bars"]))
    out["swing_high"] = swing_h.ffill()
    out["swing_low"] = swing_l.ffill()
    out["vwap"] = vwap(df) if is_intraday(tf) else np.nan
    return out[LEVEL_KINDS].astype("float64")


def nearest_level(close: pd.Series, levels: pd.DataFrame, atr: pd.Series, max_atr: float) -> pd.Series:
    dist = (levels.sub(close, axis=0)).abs().to_numpy()
    dist = np.where(np.isnan(dist), np.inf, dist)
    best = dist.argmin(axis=1) if len(dist) else np.array([], dtype=int)
    best_dist = dist[np.arange(len(dist)), best] if len(dist) else np.array([])
    names = np.array(levels.columns)[best] if len(dist) else np.array([], dtype=object)
    ok = best_dist <= max_atr * atr.to_numpy()
    return pd.Series(np.where(ok, names, None), index=close.index, dtype=object)


def _day_source(df: pd.DataFrame, tf: str, exchange: str) -> tuple[pd.Series | None, pd.Timestamp]:
    """(high/low/close of the session the day levels come from, that session's first bar time).

    On 1D that is the last bar. Intraday it is the last session once it has closed, else the one before
    it; with no earlier session there is no source and the time is the last bar's."""
    sessions, key = _session_hlc(df, tf)
    last_ts = df["ts"].iloc[-1]
    if not is_intraday(tf):
        return sessions.iloc[-1], last_ts
    cal = get_calendar()
    _, close = cal.session_times(exchange, cal.local_date(last_ts))
    pos = len(sessions) - 1 if cal.bar_close_time(exchange, last_ts, tf) >= close else len(sessions) - 2
    if pos < 0:
        return None, last_ts
    day = sessions.index[pos]
    return sessions.iloc[pos], df["ts"][(key == day).to_numpy()].iloc[0]


def levels_as_of(df: pd.DataFrame, tf: str, exchange: str) -> pd.Timestamp | None:
    """Open time of the bar (1D) or session (intraday) that key_levels' day levels come from."""
    return None if df.empty else _day_source(df, tf, exchange)[1]


def previous_trading_day(exchange: str, d: date) -> date:
    cal = get_calendar()
    d -= timedelta(days=1)
    while not cal.is_trading_day(exchange, d):
        d -= timedelta(days=1)
    return d


def key_levels(df: pd.DataFrame, tf: str, exchange: str, config: dict | None = None) -> list[Level]:
    """Levels relevant for the bar after the last closed bar of `df`."""
    if df.empty:
        return []
    ctx = (config or load_pattern_config())["context"]
    source, _ = _day_source(df, tf, exchange)
    values: list[tuple[str, float]] = []
    if source is not None:
        h, lo, c = (pd.Series([source[x]]) for x in ("high", "low", "close"))
        values += [("pdh", h.iloc[0]), ("pdl", lo.iloc[0]), ("pdc", c.iloc[0])]
        values += [(name, float(s.iloc[0])) for name, s in pivots(h, lo, c).items()]
    swing_h, swing_l = swing_points(df, int(ctx["swing_bars"]))
    count = int(ctx["swing_count"])
    values += [("swing_high", v) for v in swing_h.dropna().iloc[-count:][::-1]]
    values += [("swing_low", v) for v in swing_l.dropna().iloc[-count:][::-1]]
    if is_intraday(tf):
        values.append(("vwap", vwap(df).iloc[-1]))
    return [
        Level(price=float(v), label=LEVEL_LABELS[kind], kind=kind)
        for kind, v in values
        if v is not None and np.isfinite(v)
    ]
