"""Truncation (lookahead) checks: a causal feature must not change when future bars are cut or altered."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import numpy as np
import pandas as pd


def alter_future(df: pd.DataFrame, cut: int, seed: int = 0) -> pd.DataFrame:
    """Copy of `df` where every row after position `cut` gets different, still-valid OHLCV values."""
    rng = np.random.default_rng(seed)
    out = df.copy()
    future = slice(cut + 1, len(df))
    n = len(df) - cut - 1
    if n <= 0:
        return out
    o = df["open"].to_numpy()[future] * rng.uniform(0.9, 1.1, n)
    c = df["close"].to_numpy()[future] * rng.uniform(0.9, 1.1, n)
    h = np.maximum(df["high"].to_numpy()[future] * rng.uniform(0.95, 1.15, n), np.maximum(o, c))
    lo = np.minimum(df["low"].to_numpy()[future] * rng.uniform(0.85, 1.05, n), np.minimum(o, c))
    idx = df.index[future]
    out.loc[idx, "open"] = o
    out.loc[idx, "close"] = c
    out.loc[idx, "high"] = h
    out.loc[idx, "low"] = lo
    out.loc[idx, "volume"] = df["volume"].to_numpy()[future] * rng.uniform(0.2, 5.0, n)
    return out


def _prefix(result: pd.DataFrame | pd.Series, ts_cut: pd.Timestamp, df: pd.DataFrame, cut: int):
    if isinstance(result, pd.DataFrame) and "ts" in result.columns and len(result) != len(df):
        return result[result["ts"] <= ts_cut].reset_index(drop=True)
    return result.iloc[: cut + 1]


def check_causal(
    fn: Callable[[pd.DataFrame], pd.DataFrame | pd.Series],
    df: pd.DataFrame,
    cuts: Iterable[int],
    seed: int = 0,
) -> None:
    """Assert fn(df) up to each cut equals fn(df[:cut+1]) and fn(df with altered future) up to the cut.

    `fn` returns either a frame/series aligned with `df` rows, or an event table with a `ts` column.
    """
    full = fn(df)
    for cut in cuts:
        ts_cut = df["ts"].iloc[cut]
        expected = _prefix(full, ts_cut, df, cut)
        truncated = _prefix(fn(df.iloc[: cut + 1].copy()), ts_cut, df, cut)
        altered = _prefix(fn(alter_future(df, cut, seed)), ts_cut, df, cut)
        for label, got in (("truncated", truncated), ("altered-future", altered)):
            try:
                if isinstance(expected, pd.DataFrame):
                    pd.testing.assert_frame_equal(got, expected, check_exact=False, rtol=1e-10, atol=1e-12)
                else:
                    pd.testing.assert_series_equal(got, expected, check_exact=False, rtol=1e-10, atol=1e-12)
            except AssertionError as exc:
                raise AssertionError(f"lookahead at cut={cut} ({label}): {exc}") from None
