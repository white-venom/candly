"""Causal technical indicators (PLAN.md §5)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from typing import Literal

import pandas as pd

from candly.core.timeframes import is_intraday
from candly.indicators import functions as f

Pane = Literal["price", "oscillator", "volume"]
Group = Literal["trend", "momentum", "volatility", "volume"]


@dataclass(frozen=True)
class IndicatorInfo:
    name: str
    label: str
    pane: Pane
    group: Group

    def as_dict(self) -> dict:
        return asdict(self)


INDICATOR_CATALOG: list[IndicatorInfo] = [
    IndicatorInfo("ema9", "EMA 9", "price", "trend"),
    IndicatorInfo("ema20", "EMA 20", "price", "trend"),
    IndicatorInfo("ema50", "EMA 50", "price", "trend"),
    IndicatorInfo("ema200", "EMA 200", "price", "trend"),
    IndicatorInfo("sma20", "SMA 20", "price", "trend"),
    IndicatorInfo("sma50", "SMA 50", "price", "trend"),
    IndicatorInfo("sma200", "SMA 200", "price", "trend"),
    IndicatorInfo("supertrend", "Supertrend (10, 3)", "price", "trend"),
    IndicatorInfo("supertrend_dir", "Supertrend direction", "oscillator", "trend"),
    IndicatorInfo("adx14", "ADX 14", "oscillator", "trend"),
    IndicatorInfo("plus_di14", "+DI 14", "oscillator", "trend"),
    IndicatorInfo("minus_di14", "-DI 14", "oscillator", "trend"),
    IndicatorInfo("rsi14", "RSI 14", "oscillator", "momentum"),
    IndicatorInfo("macd", "MACD (12, 26)", "oscillator", "momentum"),
    IndicatorInfo("macd_signal", "MACD signal (9)", "oscillator", "momentum"),
    IndicatorInfo("macd_hist", "MACD histogram", "oscillator", "momentum"),
    IndicatorInfo("stoch_k", "Stochastic %K (14, 3)", "oscillator", "momentum"),
    IndicatorInfo("stoch_d", "Stochastic %D (3)", "oscillator", "momentum"),
    IndicatorInfo("atr14", "ATR 14", "oscillator", "volatility"),
    IndicatorInfo("bb_upper", "Bollinger upper (20, 2)", "price", "volatility"),
    IndicatorInfo("bb_mid", "Bollinger mid (20)", "price", "volatility"),
    IndicatorInfo("bb_lower", "Bollinger lower (20, 2)", "price", "volatility"),
    IndicatorInfo("bb_width", "Bollinger width", "oscillator", "volatility"),
    IndicatorInfo("obv", "On-balance volume", "volume", "volume"),
    IndicatorInfo("vwap", "VWAP (session)", "price", "volume"),
    IndicatorInfo("rel_volume", "Relative volume (20)", "volume", "volume"),
]
CATALOG_BY_NAME: dict[str, IndicatorInfo] = {i.name: i for i in INDICATOR_CATALOG}

# Requesting a family name returns all of its lines.
FAMILIES: dict[str, list[str]] = {
    "macd": ["macd", "macd_signal", "macd_hist"],
    "bb": ["bb_upper", "bb_mid", "bb_lower"],
    "bollinger": ["bb_upper", "bb_mid", "bb_lower"],
    "stoch": ["stoch_k", "stoch_d"],
    "adx": ["adx14", "plus_di14", "minus_di14"],
    "dmi": ["adx14", "plus_di14", "minus_di14"],
}

_Compute = Callable[[pd.DataFrame, bool], dict[str, pd.Series]]

_COMPUTERS: dict[str, _Compute] = {
    "ema9": lambda df, _: {"ema9": f.ema(df["close"], 9)},
    "ema20": lambda df, _: {"ema20": f.ema(df["close"], 20)},
    "ema50": lambda df, _: {"ema50": f.ema(df["close"], 50)},
    "ema200": lambda df, _: {"ema200": f.ema(df["close"], 200)},
    "sma20": lambda df, _: {"sma20": f.sma(df["close"], 20)},
    "sma50": lambda df, _: {"sma50": f.sma(df["close"], 50)},
    "sma200": lambda df, _: {"sma200": f.sma(df["close"], 200)},
    "supertrend": lambda df, _: f.supertrend(df, 10, 3.0),
    "adx": lambda df, _: f.adx(df, 14),
    "rsi14": lambda df, _: {"rsi14": f.rsi(df["close"], 14)},
    "macd": lambda df, _: f.macd(df["close"], 12, 26, 9),
    "stoch": lambda df, _: f.stochastic(df, 14, 3, 3),
    "atr14": lambda df, _: {"atr14": f.atr(df, 14)},
    "bb": lambda df, _: f.bollinger(df["close"], 20, 2.0),
    "obv": lambda df, _: {"obv": f.obv(df)},
    "vwap": lambda df, intraday: {
        "vwap": f.vwap(df) if intraday else pd.Series(float("nan"), index=df.index)
    },
    "rel_volume": lambda df, intraday: {"rel_volume": f.rel_volume(df, intraday)},
}

_COMPUTER_OF: dict[str, str] = {
    "supertrend_dir": "supertrend",
    "adx14": "adx",
    "plus_di14": "adx",
    "minus_di14": "adx",
    "macd_signal": "macd",
    "macd_hist": "macd",
    "stoch_k": "stoch",
    "stoch_d": "stoch",
    "bb_upper": "bb",
    "bb_mid": "bb",
    "bb_lower": "bb",
    "bb_width": "bb",
}


def resolve_names(names: Iterable[str] | None) -> list[str]:
    """Expand family names and validate; raises ValueError on an unknown name."""
    if names is None:
        return [i.name for i in INDICATOR_CATALOG]
    out: list[str] = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        expanded = FAMILIES.get(name, [name])
        for n in expanded:
            if n not in CATALOG_BY_NAME:
                raise ValueError(f"unknown indicator {name!r}")
            if n not in out:
                out.append(n)
    return out


def compute_indicators(df: pd.DataFrame, tf: str, names: Iterable[str] | None = None) -> pd.DataFrame:
    """One column per indicator series, on the same index as `df` (a candle frame)."""
    wanted = resolve_names(names)
    intraday = is_intraday(tf)
    computed: dict[str, pd.Series] = {}
    for key in dict.fromkeys(_COMPUTER_OF.get(n, n) for n in wanted):
        computed.update(_COMPUTERS[key](df, intraday))
    return pd.DataFrame({n: computed[n].astype("float64") for n in wanted}, index=df.index)


__all__ = ["CATALOG_BY_NAME", "FAMILIES", "INDICATOR_CATALOG", "IndicatorInfo", "compute_indicators"]
