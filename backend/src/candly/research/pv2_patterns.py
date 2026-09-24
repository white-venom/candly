"""pv2_range's `candlestick_patterns` feature group (config/edge_search_v2.yaml patterns_range, PLAN.md §20c).

At reference bar t, for every pattern of candly.patterns and every lag k in 0..LAGS-1: 1.0 when that
pattern was confirmed with its last bar at t - k, else 0.0. A pattern exists only once its last bar has
closed, so every value uses bars up to and including t. Several patterns can end on the same bar (an
outside bar that is also an engulfing), so each lag's block is multi-hot.

`pat_forming` is the registered "forming flag". It is 0.0 at every reference bar: a range forecast is made
when bar t closes, and at that instant no bar is forming. The only partial bar after t is the target bar
t+1, and reading it would leak the target. The column is kept so the feature set matches the
registration; a constant column never enters a LightGBM split.

Bits are stored per bar as a uint32 mask (bit i = PATTERN_NAMES[i]) and expanded to float32 columns only
for the rows a fit or a prediction needs: the 5m panel has 1.8M bars.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from candly.patterns import PATTERN_INFO, detect_patterns

PATTERN_NAMES: tuple[str, ...] = tuple(PATTERN_INFO)
LAGS = 3
FORMING = "pat_forming"
PREFIX = "pat_"


def feature_names() -> list[str]:
    return [f"{PREFIX}{name}_lag{k}" for k in range(LAGS) for name in PATTERN_NAMES] + [FORMING]


def confirmed_bits(df: pd.DataFrame, tf: str) -> np.ndarray:
    """uint32 mask per bar of `df`: the patterns confirmed with their last bar on that bar."""
    found = detect_patterns(df, tf)
    found = found[found["state"] == "confirmed"]
    out = np.zeros(len(df), dtype=np.uint32)
    if found.empty:
        return out
    pos = pd.Index(df["ts"]).get_indexer(found["ts"])
    bit = found["pattern"].map({name: i for i, name in enumerate(PATTERN_NAMES)}).to_numpy(dtype=np.uint32)
    np.bitwise_or.at(out, pos, np.left_shift(np.uint32(1), bit))
    return out


def lagged_bits(bits: np.ndarray) -> np.ndarray:
    """(n, LAGS): the mask of bar t - k in column k; 0 before the series starts."""
    out = np.zeros((len(bits), LAGS), dtype=np.uint32)
    for k in range(LAGS):
        out[k:, k] = bits[: len(bits) - k]
    return out


def expand(lagged: np.ndarray) -> np.ndarray:
    """(n, LAGS) masks to (n, len(feature_names())) float32 columns, in feature_names() order."""
    shifts = np.arange(len(PATTERN_NAMES), dtype=np.uint32)
    onehot = (lagged[:, :, None] >> shifts) & np.uint32(1)
    out = np.zeros((len(lagged), LAGS * len(PATTERN_NAMES) + 1), dtype=np.float32)
    out[:, :-1] = onehot.reshape(len(lagged), -1)
    return out


def pattern_features(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """The whole feature group on `df`'s index (the frame form of what the panel stores as masks)."""
    values = expand(lagged_bits(confirmed_bits(df, tf)))
    return pd.DataFrame(values, columns=feature_names(), index=df.index)
