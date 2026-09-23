import copy

import numpy as np
import pandas as pd
import pytest

from candly.indicators.functions import atr as atr_fn
from candly.patterns import PATTERN_COLUMNS, PATTERN_INFO, detect_patterns, load_pattern_config
from candly.research.causality import check_causal
from candly.research.synthetic import synthetic_candles

# Every prefix bar has a true range of exactly 2.0, so ATR(14) before each pattern is 2.0.


def prefix(kind: str, n: int = 25, start: float = 100.0) -> tuple[list[tuple], float]:
    bars, p = [], start
    for i in range(n):
        if kind == "down" or (kind == "flat" and i % 2 == 1):
            bars.append((p, p + 0.5, p - 1.5, p - 1.0))
            p -= 1.0
        else:
            bars.append((p, p + 1.5, p - 0.5, p + 1.0))
            p += 1.0
    return bars, p


def build(kind: str, pattern_bars: list[tuple]) -> pd.DataFrame:
    bars, p = prefix(kind)
    bars += [(p + o, p + h, p + lo, p + c) for o, h, lo, c in pattern_bars]
    o, h, lo, c = map(np.array, zip(*bars, strict=True))
    return pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01 03:45", periods=len(bars), freq="D", tz="UTC"),
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "volume": 1000.0,
            "oi": np.nan,
        }
    )


# name: (prefix, bars, near-miss prefix, near-miss bars).
# Bars are (open, high, low, close) offsets from the prefix's last close.
CASES = {
    "hammer": ("down", [(0, 0.5, -1.6, 0.4)], "flat", [(0, 0.5, -1.6, 0.4)]),
    "hanging_man": ("up", [(0, 0.5, -1.6, 0.4)], "down", [(0, 0.5, -1.6, 0.4)]),
    "inverted_hammer": ("down", [(0, 2.0, -0.1, 0.4)], "flat", [(0, 2.0, -0.1, 0.4)]),
    "shooting_star": ("up", [(0, 1.6, -0.5, -0.4)], "down", [(0, 1.6, -0.5, -0.4)]),
    "doji": ("flat", [(0, 1.0, -1.0, 0.05)], "flat", [(0, 1.0, -1.0, 0.5)]),
    "dragonfly_doji": ("flat", [(0, 0.1, -2.0, 0.05)], "flat", [(0, 0.55, -2.0, 0.05)]),
    "gravestone_doji": ("flat", [(0, 2.0, -0.1, -0.05)], "flat", [(0, 2.0, -0.55, -0.05)]),
    "bullish_marubozu": ("flat", [(0, 2.05, -0.05, 2.0)], "flat", [(0, 2.3, -0.05, 2.0)]),
    "bearish_marubozu": ("flat", [(0, 0.05, -2.05, -2.0)], "flat", [(0, 0.05, -2.3, -2.0)]),
    "bullish_engulfing": (
        "down",
        [(0, 0.3, -0.9, -0.6), (-0.7, 0.4, -0.9, 0.2)],
        "down",
        [(0, 0.3, -0.9, -0.6), (-0.7, 0.4, -0.9, -0.1)],
    ),
    "bearish_engulfing": (
        "up",
        [(0, 0.9, -0.3, 0.6), (0.7, 0.9, -0.4, -0.2)],
        "up",
        [(0, 0.9, -0.3, 0.6), (0.7, 0.9, -0.4, 0.1)],
    ),
    "piercing_line": (
        "down",
        [(0, 0.1, -1.7, -1.6), (-1.8, -0.4, -1.9, -0.5)],
        "down",
        [(0, 0.1, -1.7, -1.6), (-1.8, -0.4, -1.9, -1.0)],
    ),
    "dark_cloud_cover": (
        "up",
        [(0, 1.7, -0.1, 1.6), (1.8, 1.9, 0.4, 0.5)],
        "up",
        [(0, 1.7, -0.1, 1.6), (1.8, 1.9, 0.4, 1.0)],
    ),
    "bullish_harami": (
        "down",
        [(0, 0.1, -1.7, -1.6), (-1.2, -0.6, -1.4, -0.8)],
        "down",
        [(0, 0.1, -1.7, -1.6), (-1.5, -0.05, -1.6, -0.1)],
    ),
    "bearish_harami": (
        "up",
        [(0, 1.7, -0.1, 1.6), (1.2, 1.4, 0.6, 0.8)],
        "up",
        [(0, 1.7, -0.1, 1.6), (1.5, 1.6, 0.05, 0.1)],
    ),
    "tweezer_bottom": (
        "down",
        [(0, 0.2, -1.5, -1.0), (-0.9, 0.0, -1.52, -0.2)],
        "down",
        [(0, 0.2, -1.5, -1.0), (-0.9, 0.0, -1.8, -0.2)],
    ),
    "tweezer_top": (
        "up",
        [(0, 1.5, -0.2, 1.0), (0.9, 1.52, 0.0, 0.2)],
        "up",
        [(0, 1.5, -0.2, 1.0), (0.9, 1.8, 0.0, 0.2)],
    ),
    "inside_bar": (
        "flat",
        [(0, 1.5, -0.5, 1.0), (0.8, 1.2, -0.2, 0.3)],
        "flat",
        [(0, 1.5, -0.5, 1.0), (0.8, 1.6, -0.2, 0.3)],
    ),
    "outside_bar": (
        "flat",
        [(0, 0.8, -0.3, 0.5), (0.4, 1.2, -0.8, 0.2)],
        "flat",
        [(0, 0.8, -0.3, 0.5), (0.4, 1.2, -0.2, 0.2)],
    ),
    "morning_star": (
        "down",
        [(0, 0.1, -1.7, -1.6), (-1.8, -1.5, -2.2, -1.7), (-1.5, -0.4, -1.6, -0.5)],
        "down",
        [(0, 0.1, -1.7, -1.6), (-1.8, -1.5, -2.2, -1.7), (-1.5, -0.9, -1.6, -1.0)],
    ),
    "evening_star": (
        "up",
        [(0, 1.7, -0.1, 1.6), (1.8, 2.2, 1.5, 1.7), (1.5, 1.6, 0.4, 0.5)],
        "up",
        [(0, 1.7, -0.1, 1.6), (1.8, 2.2, 1.5, 1.7), (1.5, 1.6, 0.9, 1.0)],
    ),
    "three_white_soldiers": (
        "flat",
        [(0, 1.2, -0.3, 1.0), (0.5, 1.8, 0.4, 1.6), (1.2, 2.5, 1.1, 2.3)],
        "flat",
        [(0, 1.2, -0.3, 1.0), (0.5, 1.8, 0.4, 1.6), (1.7, 3.0, 1.6, 2.8)],
    ),
    "three_black_crows": (
        "flat",
        [(0, 0.3, -1.2, -1.0), (-0.5, -0.4, -1.8, -1.6), (-1.2, -1.1, -2.5, -2.3)],
        "flat",
        [(0, 0.3, -1.2, -1.0), (-0.5, -0.4, -1.8, -1.6), (-1.7, -1.6, -3.0, -2.8)],
    ),
}


def test_every_pattern_has_a_case_and_config():
    assert set(CASES) == set(PATTERN_INFO)
    assert set(load_pattern_config()["patterns"]) == set(PATTERN_INFO)


def rows_at_last_bar(df: pd.DataFrame, name: str, **kwargs) -> pd.DataFrame:
    out = detect_patterns(df, "1D", **kwargs)
    return out[(out["ts"] == df["ts"].iloc[-1]) & (out["pattern"] == name)]


@pytest.mark.parametrize("name", list(CASES))
def test_known_answer(name):
    kind, bars, _, _ = CASES[name]
    df = build(kind, bars)
    hit = rows_at_last_bar(df, name)
    assert len(hit) == 1, f"{name} not detected"
    row = hit.iloc[0]
    info = PATTERN_INFO[name]
    assert (row["direction"], row["bars"], row["state"], row["label"]) == (
        info.direction,
        info.bars,
        "confirmed",
        info.label,
    )
    window = df.iloc[-info.bars :]
    buffer = 0.1 * atr_fn(df, 14).iloc[-1]
    if info.direction == "bullish":
        assert row["invalidation"] == pytest.approx(window["low"].min() - buffer)
    elif info.direction == "bearish":
        assert row["invalidation"] == pytest.approx(window["high"].max() + buffer)
    else:
        assert np.isnan(row["invalidation"])


@pytest.mark.parametrize("name", list(CASES))
def test_near_miss_is_rejected(name):
    _, _, kind, bars = CASES[name]
    assert rows_at_last_bar(build(kind, bars), name).empty


def test_doji_family_is_exclusive():
    df = build("flat", [(0, 0.55, -2.0, 0.05)])
    names = set(detect_patterns(df, "1D").query("ts == @df.ts.iloc[-1]")["pattern"])
    assert "doji" in names and "dragonfly_doji" not in names


def test_output_columns_and_order():
    df = synthetic_candles("1D", "2022-01-01", "2023-12-31", seed=2)
    out = detect_patterns(df, "1D")
    assert list(out.columns) == PATTERN_COLUMNS
    assert out["ts"].is_monotonic_increasing
    assert set(out["state"]) == {"confirmed"}
    assert out["pattern"].isin(list(PATTERN_INFO)).all()
    assert len(out) > 50


def test_forming_bar_produces_forming_rows():
    kind, bars, _, _ = CASES["bullish_engulfing"]
    df = build(kind, bars)
    closed, partial = df.iloc[:-1], df.iloc[-1]
    out = detect_patterns(closed, "1D", forming_bar=partial)
    forming = out[out["state"] == "forming"]
    assert "bullish_engulfing" in set(forming["pattern"])
    assert (forming["ts"] == partial["ts"]).all()
    assert (out.loc[out["state"] == "confirmed", "ts"] < partial["ts"]).all()
    stale = detect_patterns(closed, "1D", forming_bar=closed.iloc[-1])
    assert (stale["state"] == "confirmed").all()


def test_thresholds_come_from_config():
    kind, bars, _, _ = CASES["hammer"]
    df = build(kind, bars)
    cfg = copy.deepcopy(load_pattern_config())
    cfg["buffer_atr"] = 0.5
    wide = rows_at_last_bar(df, "hammer", config=cfg).iloc[0]["invalidation"]
    assert wide == pytest.approx(df["low"].iloc[-1] - 0.5 * atr_fn(df, 14).iloc[-1])
    cfg["patterns"]["hammer"]["min_wick_body"] = 10.0
    assert rows_at_last_bar(df, "hammer", config=cfg).empty


def test_detection_is_causal():
    df = synthetic_candles("1D", "2022-01-01", "2023-06-30", seed=5)
    n = len(df)
    check_causal(lambda d: detect_patterns(d, "1D"), df, [n // 3, n // 2, n - 20])
