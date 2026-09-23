"""Forward outcomes. These look into the future by design, so they live only in research/ (and ledger/)."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd
from pandas.api.indexers import FixedForwardWindowIndexer

PATH_FIELDS = ("open", "high", "low", "close")


def _future(series: pd.Series, h: int, how: str) -> pd.Series:
    """Max/min of series[t+1 .. t+h], NaN when fewer than h future bars exist."""
    window = series.shift(-1).rolling(FixedForwardWindowIndexer(window_size=h), min_periods=h)
    out = window.max() if how == "max" else window.min()
    return out.where(series.shift(-h).notna())


def forward_labels(df: pd.DataFrame, horizons: Iterable[int], atr: pd.Series | None = None) -> pd.DataFrame:
    """Per horizon h (in bars), relative to the close of bar t:

    up_h / down_h   1.0 if close[t+h] is above / below close[t], else 0.0; NaN without h future bars
    ret_h           close[t+h] / close[t] - 1
    trade_ret_h     close[t+h] / open[t+1] - 1: a long entered at the next bar's open, exited at t+h's close
    mfe_h_atr       (max high[t+1..t+h] - close[t]) / ATR[t]   upside excursion (favourable for a long)
    mae_h_atr       (close[t] - min low[t+1..t+h]) / ATR[t]    downside excursion (adverse for a long)
    """
    close = df["close"]
    next_open = df["open"].shift(-1)
    out: dict[str, pd.Series] = {}
    for h in horizons:
        fut = close.shift(-h)
        valid = fut.notna()
        out[f"up_{h}"] = (fut > close).astype("float64").where(valid)
        out[f"down_{h}"] = (fut < close).astype("float64").where(valid)
        out[f"ret_{h}"] = fut / close - 1.0
        out[f"trade_ret_{h}"] = fut / next_open - 1.0
        if atr is not None:
            out[f"mfe_{h}_atr"] = (_future(df["high"], h, "max") - close) / atr
            out[f"mae_{h}_atr"] = (close - _future(df["low"], h, "min")) / atr
    return pd.DataFrame(out, index=df.index)


def forward_paths(df: pd.DataFrame, atr: pd.Series, steps: int) -> np.ndarray:
    """Array (n_bars, steps, 4): OHLC of bar t+s (s = 1..steps) minus close[t], divided by ATR[t].

    NaN where the future bar does not exist.
    """
    close = df["close"].to_numpy(dtype=float)
    scale = atr.to_numpy(dtype=float)
    n = len(df)
    out = np.full((n, steps, 4), np.nan)
    for j, field in enumerate(PATH_FIELDS):
        values = df[field].to_numpy(dtype=float)
        for s in range(1, steps + 1):
            if s < n:
                out[: n - s, s - 1, j] = (values[s:] - close[: n - s]) / scale[: n - s]
    return out


def path_columns(steps: int) -> list[str]:
    return [f"{f[0]}{s}" for s in range(1, steps + 1) for f in PATH_FIELDS]


def forward_end_ts(ts: pd.Series, steps: int) -> pd.Series:
    """Open time of bar t+steps (NaT when it does not exist)."""
    return ts.shift(-steps)
