"""Vectorised candlestick pattern detection (PLAN.md §6); thresholds live in config/patterns.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import yaml

from candly.core.schema import CANDLE_COLUMNS
from candly.core.settings import get_settings
from candly.indicators.functions import atr as _atr

Direction = Literal["bullish", "bearish", "neutral"]
PATTERN_COLUMNS = ["ts", "pattern", "label", "direction", "state", "bars", "invalidation"]


@dataclass(frozen=True)
class PatternInfo:
    name: str
    label: str
    direction: Direction
    bars: int


PATTERN_INFO: dict[str, PatternInfo] = {
    p.name: p
    for p in [
        PatternInfo("doji", "Doji", "neutral", 1),
        PatternInfo("dragonfly_doji", "Dragonfly doji", "bullish", 1),
        PatternInfo("gravestone_doji", "Gravestone doji", "bearish", 1),
        PatternInfo("hammer", "Hammer", "bullish", 1),
        PatternInfo("hanging_man", "Hanging man", "bearish", 1),
        PatternInfo("inverted_hammer", "Inverted hammer", "bullish", 1),
        PatternInfo("shooting_star", "Shooting star", "bearish", 1),
        PatternInfo("bullish_marubozu", "Bullish marubozu", "bullish", 1),
        PatternInfo("bearish_marubozu", "Bearish marubozu", "bearish", 1),
        PatternInfo("bullish_engulfing", "Bullish engulfing", "bullish", 2),
        PatternInfo("bearish_engulfing", "Bearish engulfing", "bearish", 2),
        PatternInfo("piercing_line", "Piercing line", "bullish", 2),
        PatternInfo("dark_cloud_cover", "Dark cloud cover", "bearish", 2),
        PatternInfo("bullish_harami", "Bullish harami", "bullish", 2),
        PatternInfo("bearish_harami", "Bearish harami", "bearish", 2),
        PatternInfo("tweezer_bottom", "Tweezer bottom", "bullish", 2),
        PatternInfo("tweezer_top", "Tweezer top", "bearish", 2),
        PatternInfo("inside_bar", "Inside bar", "neutral", 2),
        PatternInfo("outside_bar", "Outside bar", "neutral", 2),
        PatternInfo("morning_star", "Morning star", "bullish", 3),
        PatternInfo("evening_star", "Evening star", "bearish", 3),
        PatternInfo("three_white_soldiers", "Three white soldiers", "bullish", 3),
        PatternInfo("three_black_crows", "Three black crows", "bearish", 3),
    ]
}


@lru_cache(maxsize=4)
def _config_cached(path: str, mtime: float) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_pattern_config(path: Path | None = None) -> dict:
    path = path or get_settings().config_dir / "patterns.yaml"
    return _config_cached(str(path), path.stat().st_mtime)


def _shift(a: np.ndarray, k: int) -> np.ndarray:
    if k == 0:
        return a
    out = np.full_like(a, np.nan)
    out[k:] = a[:-k]
    return out


class _Bars:
    """Arrays for the bar `k` positions before the current one."""

    def __init__(self, o, h, lo, c, k: int):
        self.o, self.h, self.l, self.c = (_shift(x, k) for x in (o, h, lo, c))
        self.body = np.abs(self.c - self.o)
        self.range = self.h - self.l
        self.upper = self.h - np.maximum(self.o, self.c)
        self.lower = np.minimum(self.o, self.c) - self.l
        self.bull = self.c > self.o
        self.bear = self.c < self.o
        self.top = np.maximum(self.o, self.c)
        self.bottom = np.minimum(self.o, self.c)


def _masks(df: pd.DataFrame, cfg: dict) -> tuple[dict[str, np.ndarray], np.ndarray]:
    o, h, lo, c = (df[x].to_numpy(dtype=float) for x in ("open", "high", "low", "close"))
    atr = _atr(df, int(cfg["atr_period"])).to_numpy()
    lookback = int(cfg["prior_trend"]["lookback_bars"])
    min_move = float(cfg["prior_trend"]["min_move_atr"])
    p = cfg["patterns"]

    def atr_before(k: int) -> np.ndarray:
        return _shift(atr, k)

    def prior(k: int, want: str | None) -> np.ndarray:
        if want is None:
            return np.ones(len(c), dtype=bool)
        move = (_shift(c, k) - _shift(c, k + lookback)) / atr_before(k)
        return move <= -min_move if want == "down" else move >= min_move

    b0, b1, b2 = _Bars(o, h, lo, c, 0), _Bars(o, h, lo, c, 1), _Bars(o, h, lo, c, 2)
    a1, a2, a3 = atr_before(1), atr_before(2), atr_before(3)
    m: dict[str, np.ndarray] = {}

    def doji_base(q: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        base = (b0.range >= q["min_range_atr"] * a1) & (b0.body <= q["max_body_range"] * b0.range)
        no_upper = b0.upper <= q["max_wick_range"] * b0.range
        no_lower = b0.lower <= q["max_wick_range"] * b0.range
        return base, no_upper, no_lower

    base, no_upper, no_lower = doji_base(p["doji"])
    m["doji"] = base & ~no_upper & ~no_lower
    base, no_upper, _ = doji_base(p["dragonfly_doji"])
    m["dragonfly_doji"] = base & no_upper
    base, _, no_lower = doji_base(p["gravestone_doji"])
    m["gravestone_doji"] = base & no_lower

    def long_wick(q: dict, wick: np.ndarray, other: np.ndarray) -> np.ndarray:
        return (
            (b0.range >= q["min_range_atr"] * a1)
            & (b0.body <= q["max_body_range"] * b0.range)
            & (wick >= q["min_wick_body"] * b0.body)
            & (other <= q["max_other_wick_range"] * b0.range)
            & prior(1, q.get("prior_trend"))
        )

    for name in ("hammer", "hanging_man"):
        m[name] = long_wick(p[name], b0.lower, b0.upper)
    for name in ("inverted_hammer", "shooting_star"):
        m[name] = long_wick(p[name], b0.upper, b0.lower)

    for name, colour in (("bullish_marubozu", b0.bull), ("bearish_marubozu", b0.bear)):
        q = p[name]
        m[name] = colour & (b0.body >= q["min_body_atr"] * a1) & (b0.body >= q["min_body_range"] * b0.range)

    q = p["bullish_engulfing"]
    m["bullish_engulfing"] = (
        b1.bear
        & b0.bull
        & (b0.o <= b1.c)
        & (b0.c >= b1.o)
        & (b0.body > b1.body)
        & (b1.body >= q["min_first_body_atr"] * a2)
        & (b0.body >= q["min_body_atr"] * a2)
        & prior(2, q.get("prior_trend"))
    )
    q = p["bearish_engulfing"]
    m["bearish_engulfing"] = (
        b1.bull
        & b0.bear
        & (b0.o >= b1.c)
        & (b0.c <= b1.o)
        & (b0.body > b1.body)
        & (b1.body >= q["min_first_body_atr"] * a2)
        & (b0.body >= q["min_body_atr"] * a2)
        & prior(2, q.get("prior_trend"))
    )
    q = p["piercing_line"]
    m["piercing_line"] = (
        b1.bear
        & (b1.body >= q["min_first_body_atr"] * a2)
        & b0.bull
        & (b0.o < b1.c)
        & (b0.c > b1.c + q["min_penetration"] * b1.body)
        & (b0.c < b1.o)
        & prior(2, q.get("prior_trend"))
    )
    q = p["dark_cloud_cover"]
    m["dark_cloud_cover"] = (
        b1.bull
        & (b1.body >= q["min_first_body_atr"] * a2)
        & b0.bear
        & (b0.o > b1.c)
        & (b0.c < b1.c - q["min_penetration"] * b1.body)
        & (b0.c > b1.o)
        & prior(2, q.get("prior_trend"))
    )
    q = p["bullish_harami"]
    m["bullish_harami"] = (
        b1.bear
        & (b1.body >= q["min_first_body_atr"] * a2)
        & (b0.top <= b1.o)
        & (b0.bottom >= b1.c)
        & (b0.body <= q["max_body_ratio"] * b1.body)
        & prior(2, q.get("prior_trend"))
    )
    q = p["bearish_harami"]
    m["bearish_harami"] = (
        b1.bull
        & (b1.body >= q["min_first_body_atr"] * a2)
        & (b0.top <= b1.c)
        & (b0.bottom >= b1.o)
        & (b0.body <= q["max_body_ratio"] * b1.body)
        & prior(2, q.get("prior_trend"))
    )
    q = p["tweezer_bottom"]
    m["tweezer_bottom"] = (
        b1.bear
        & b0.bull
        & (np.abs(b0.l - b1.l) <= q["max_diff_atr"] * a2)
        & (b1.range >= q["min_range_atr"] * a2)
        & (b0.range >= q["min_range_atr"] * a2)
        & prior(2, q.get("prior_trend"))
    )
    q = p["tweezer_top"]
    m["tweezer_top"] = (
        b1.bull
        & b0.bear
        & (np.abs(b0.h - b1.h) <= q["max_diff_atr"] * a2)
        & (b1.range >= q["min_range_atr"] * a2)
        & (b0.range >= q["min_range_atr"] * a2)
        & prior(2, q.get("prior_trend"))
    )
    q = p["inside_bar"]
    m["inside_bar"] = (b0.h < b1.h) & (b0.l > b1.l) & (b1.range >= q["min_first_range_atr"] * a2)
    q = p["outside_bar"]
    m["outside_bar"] = (b0.h > b1.h) & (b0.l < b1.l) & (b0.range >= q["min_range_atr"] * a2)

    q = p["morning_star"]
    m["morning_star"] = (
        b2.bear
        & (b2.body >= q["min_first_body_atr"] * a3)
        & (b1.body <= q["max_star_body_atr"] * a3)
        & (b1.top <= b2.c)
        & b0.bull
        & (b0.body >= q["min_last_body_atr"] * a3)
        & (b0.c > b2.c + q["min_penetration"] * b2.body)
        & prior(3, q.get("prior_trend"))
    )
    q = p["evening_star"]
    m["evening_star"] = (
        b2.bull
        & (b2.body >= q["min_first_body_atr"] * a3)
        & (b1.body <= q["max_star_body_atr"] * a3)
        & (b1.bottom >= b2.c)
        & b0.bear
        & (b0.body >= q["min_last_body_atr"] * a3)
        & (b0.c < b2.c - q["min_penetration"] * b2.body)
        & prior(3, q.get("prior_trend"))
    )
    q = p["three_white_soldiers"]
    m["three_white_soldiers"] = (
        b2.bull
        & b1.bull
        & b0.bull
        & (np.minimum.reduce([b2.body, b1.body, b0.body]) >= q["min_body_atr"] * a3)
        & (b1.c > b2.c)
        & (b0.c > b1.c)
        & (b1.o >= b2.o)
        & (b1.o <= b2.c)
        & (b0.o >= b1.o)
        & (b0.o <= b1.c)
        & (b2.upper <= q["max_close_wick_range"] * b2.range)
        & (b1.upper <= q["max_close_wick_range"] * b1.range)
        & (b0.upper <= q["max_close_wick_range"] * b0.range)
    )
    q = p["three_black_crows"]
    m["three_black_crows"] = (
        b2.bear
        & b1.bear
        & b0.bear
        & (np.minimum.reduce([b2.body, b1.body, b0.body]) >= q["min_body_atr"] * a3)
        & (b1.c < b2.c)
        & (b0.c < b1.c)
        & (b1.o <= b2.o)
        & (b1.o >= b2.c)
        & (b0.o <= b1.o)
        & (b0.o >= b1.c)
        & (b2.lower <= q["max_close_wick_range"] * b2.range)
        & (b1.lower <= q["max_close_wick_range"] * b1.range)
        & (b0.lower <= q["max_close_wick_range"] * b0.range)
    )
    return m, atr


def empty_patterns() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "pattern": pd.Series(dtype="str"),
            "label": pd.Series(dtype="str"),
            "direction": pd.Series(dtype="str"),
            "state": pd.Series(dtype="str"),
            "bars": pd.Series(dtype="int64"),
            "invalidation": pd.Series(dtype="float64"),
        }
    )


def _forming_row(bar: pd.Series) -> pd.DataFrame:
    row = {col: [bar.get(col, np.nan)] for col in CANDLE_COLUMNS}
    row["ts"] = [pd.Timestamp(bar["ts"]).tz_convert("UTC")]
    out = pd.DataFrame(row)
    out[CANDLE_COLUMNS[1:]] = out[CANDLE_COLUMNS[1:]].astype("float64")
    return out


def detect_patterns(
    df: pd.DataFrame, tf: str, forming_bar: pd.Series | None = None, config: dict | None = None
) -> pd.DataFrame:
    """Every pattern whose last bar is a closed bar of `df` (state "confirmed"), plus, when
    `forming_bar` is given, the patterns that would complete if that partial bar closed now
    (state "forming"). One row per (last bar, pattern)."""
    cfg = config or load_pattern_config()
    frame = df[CANDLE_COLUMNS].reset_index(drop=True)
    has_forming = forming_bar is not None and (
        frame.empty or pd.Timestamp(forming_bar["ts"]) > frame["ts"].iloc[-1]
    )
    if has_forming:
        frame = pd.concat([frame, _forming_row(forming_bar)], ignore_index=True)
    if frame.empty:
        return empty_patterns()
    with np.errstate(invalid="ignore", divide="ignore"):
        masks, atr = _masks(frame, cfg)
    buffer = float(cfg["buffer_atr"]) * atr
    lows = {k: frame["low"].rolling(k).min().to_numpy() for k in (1, 2, 3)}
    highs = {k: frame["high"].rolling(k).max().to_numpy() for k in (1, 2, 3)}
    order = {name: i for i, name in enumerate(PATTERN_INFO)}
    positions, names, invalidations = [], [], []
    for name, mask in masks.items():
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            continue
        info = PATTERN_INFO[name]
        if info.direction == "bullish":
            inv = lows[info.bars][idx] - buffer[idx]
        elif info.direction == "bearish":
            inv = highs[info.bars][idx] + buffer[idx]
        else:
            inv = np.full(idx.size, np.nan)
        positions.append(idx)
        names.append(np.full(idx.size, order[name]))
        invalidations.append(inv)
    if not positions:
        return empty_patterns()
    pos, code, inv = np.concatenate(positions), np.concatenate(names), np.concatenate(invalidations)
    sort = np.lexsort((code, pos))
    pos, code, inv = pos[sort], code[sort], inv[sort]
    infos = list(PATTERN_INFO.values())
    out = pd.DataFrame(
        {
            "ts": frame["ts"].array[pos],
            "pattern": [infos[c].name for c in code],
            "label": [infos[c].label for c in code],
            "direction": [infos[c].direction for c in code],
            "state": np.where(has_forming & (pos == len(frame) - 1), "forming", "confirmed"),
            "bars": np.array([infos[c].bars for c in code], dtype="int64"),
            "invalidation": inv,
        }
    )
    return out[PATTERN_COLUMNS]


__all__ = ["PATTERN_COLUMNS", "PATTERN_INFO", "PatternInfo", "detect_patterns", "load_pattern_config"]
