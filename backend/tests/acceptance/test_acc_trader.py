"""Acceptance: would a trader act on it? Stops, ghost candles, signal rate, costs (first run 2026-09-23).

Uses stored data where it can; the stop-distance and cost checks use fixed inputs so they are deterministic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from acceptance_helpers import local, read_real, stored_ids

from candly.core.calendar import get_calendar
from candly.forecast import make_forecast
from candly.indicators.functions import atr as atr_fn
from candly.indicators.functions import rel_volume
from candly.patterns import detect_patterns
from candly.research.config import load_costs_config
from candly.research.costs import round_trip_cost

MIN_STOP_ATR = 0.3  # closer than this is inside a normal bar's noise
MAX_STOP_ATR = 3.0


def _signals_with_stop_distance(ids: list[str], tf: str) -> pd.DataFrame:
    parts = []
    for inst in ids:
        df = read_real(inst, tf)
        atr = atr_fn(df, 14).to_numpy()
        found = detect_patterns(df, tf)
        found = found[found["direction"] != "neutral"].copy()
        pos = pd.Index(df["ts"]).get_indexer(found["ts"])
        sign = np.where(found["direction"] == "bullish", 1.0, -1.0)
        found["stop_atr"] = sign * (df["close"].to_numpy()[pos] - found["invalidation"].to_numpy()) / atr[pos]
        found["instrument"] = inst
        parts.append(found)
    return pd.concat(parts, ignore_index=True)


@pytest.mark.parametrize("tf", ["1D", "1h"])
def test_pattern_stops_are_on_the_right_side_and_not_absurdly_wide(tf):
    ids = stored_ids(tf, kinds=("equity", "index"))
    if not ids:
        pytest.skip(f"no stored {tf} data")
    sig = _signals_with_stop_distance(ids, tf)
    assert (sig["stop_atr"] > 0).all(), sig.loc[sig["stop_atr"] <= 0, ["instrument", "ts", "pattern"]].head()
    assert (sig["stop_atr"] > MAX_STOP_ATR).mean() <= 0.05


@pytest.mark.xfail(
    reason="report F3: inverted hammer / hanging man put ~2/3 of stops within 0.3 ATR of the close",
    strict=False,
)
@pytest.mark.parametrize("tf", ["1D", "1h"])
def test_pattern_stops_sit_outside_normal_noise(tf):
    ids = stored_ids(tf, kinds=("equity", "index"))
    if not ids:
        pytest.skip(f"no stored {tf} data")
    sig = _signals_with_stop_distance(ids, tf)
    assert (sig["stop_atr"] < MIN_STOP_ATR).mean() <= 0.05


def _synthetic(prefix: str, last: tuple[float, float, float, float]) -> pd.DataFrame:
    """25 bars trending `prefix` (true range exactly 2.0), then `last` as offsets from the last close."""
    bars, p = [], 100.0
    for _ in range(25):
        if prefix == "down":
            bars.append((p, p + 0.5, p - 1.5, p - 1.0))
            p -= 1.0
        else:
            bars.append((p, p + 1.5, p - 0.5, p + 1.0))
            p += 1.0
    bars.append(tuple(p + x for x in last))
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


@pytest.mark.xfail(
    reason="report F3: stop = pattern extreme ± 0.1 ATR, which is 0.1 ATR from this close", strict=True
)
@pytest.mark.parametrize(
    ("pattern", "prefix", "bar"),
    [
        # shaped like RELIANCE 2026-09-16 (O 1243 H 1255 L 1240 C 1240): long upper wick, closes on its low
        ("inverted_hammer", "down", (0.3, 2.0, 0.0, 0.0)),
        # the mirror: long lower wick after a rise, closes on its high
        ("hanging_man", "up", (-0.3, 0.0, -2.0, 0.0)),
    ],
)
def test_textbook_long_wick_signal_gets_a_stop_outside_noise(pattern, prefix, bar):
    df = _synthetic(prefix, bar)
    found = detect_patterns(df, "1D")
    row = found[(found["pattern"] == pattern) & (found["ts"] == df["ts"].iloc[-1])]
    assert len(row) == 1, f"{pattern} not detected on the synthetic bar"
    atr = atr_fn(df, 14).iloc[-1]
    distance = abs(df["close"].iloc[-1] - row["invalidation"].iloc[0]) / atr
    assert distance >= MIN_STOP_ATR


def _forecasts(tf: str = "1D", limit: int = 6):
    """Forecasts on stored stock data as of just after each series' last bar closed (no staleness)."""
    cal = get_calendar()
    out = []
    for inst in stored_ids(tf, kinds=("equity",))[:limit]:
        df = read_real(inst, tf)
        now = cal.bar_close_time(inst.split(":")[0], df["ts"].iloc[-1], tf) + pd.Timedelta(minutes=1)
        out.append((inst, df, make_forecast(inst, tf, df, None, now=now)))
    return out


def test_ghost_candle_ranges_and_bands_look_like_real_candles():
    fcs = [(i, df, fc) for i, df, fc in _forecasts() if fc.ghost_candles]
    if not fcs:
        pytest.skip("no forecast with ghost candles on stored data")
    for inst, df, fc in fcs:
        atr = fc.context.atr
        real = ((df["high"] - df["low"]) / atr_fn(df, 14).shift(1)).iloc[-250:].median()
        for g, b in zip(fc.ghost_candles, fc.bands, strict=True):
            assert 0.4 * real <= (g.high - g.low) / atr <= 1.6 * real, inst
            assert b.p10 <= b.p50 <= b.p90, inst
        widths = [b.p90 - b.p10 for b in fc.bands]
        assert widths == sorted(widths), f"{inst}: band should widen with the horizon"


@pytest.mark.xfail(
    reason="report F9: ghost candles are medians of O/H/L/C taken separately, so all are dojis", strict=False
)
def test_ghost_candle_bodies_look_like_real_candles():
    fcs = [(i, df, fc) for i, df, fc in _forecasts() if fc.ghost_candles]
    if not fcs:
        pytest.skip("no forecast with ghost candles on stored data")
    for inst, _, fc in fcs:
        bodies = [abs(g.close - g.open) / fc.context.atr for g in fc.ghost_candles]
        assert np.mean(bodies) >= 0.15, f"{inst}: mean ghost body {np.mean(bodies):.2f} ATR"


@pytest.mark.xfail(
    reason="report F4: a directional call can come with invalidation=None (no stop)", strict=False
)
def test_every_directional_call_has_a_stop():
    calls = [(i, fc) for i, _, fc in _forecasts(limit=13) if not fc.abstain]
    if not calls:
        pytest.skip("no non-abstaining forecast on stored data")
    assert all(fc.invalidation is not None for _, fc in calls), [
        i for i, fc in calls if fc.invalidation is None
    ]


def test_daily_signal_rate_is_not_noise():
    ids = [i for i in stored_ids("1D", kinds=("equity", "index")) if i != "NSE:INDIAVIX"]  # context only
    if not ids:
        pytest.skip("no stored 1D data")
    for inst in ids:
        df = read_real(inst, "1D").iloc[-500:].reset_index(drop=True)
        found = detect_patterns(df, "1D")
        found = found[found["ts"] >= df["ts"].iloc[30]]
        months = (df["ts"].iloc[-1] - df["ts"].iloc[30]).days / 30.44
        directional = (found["direction"] != "neutral").sum() / months
        share_bars = found["ts"].nunique() / (len(df) - 30)
        assert 0.5 <= directional <= 6.0, f"{inst}: {directional:.1f} directional signals/month"
        assert share_bars < 0.5, f"{inst}: {share_bars:.0%} of daily bars carry a signal"


@pytest.mark.xfail(
    reason="report D4/F6: zero-volume 09:15 history makes today's opening bar a fake volume spike",
    strict=False,
)
def test_opening_bar_relative_volume_is_not_an_artifact():
    ids = stored_ids("1h", kinds=("equity",))
    if not ids:
        pytest.skip("no stored 1h equity data")
    for inst in ids:
        df = read_real(inst, "1h").iloc[-7 * 60 :].reset_index(drop=True)
        rv = rel_volume(df, intraday=True)
        opening = local(df["ts"]).dt.strftime("%H:%M") == "09:15"
        assert not (rv[opening] > 10).any(), f"{inst}: opening-bar rel_volume up to {rv[opening].max():.1f}x"


def test_intraday_round_trip_cost_is_realistic():
    # ₹1 lakh equity intraday at Fyers: ₹40 brokerage, 0.025% STT, 0.003% stamp, txn + GST, 2 × 0.03% slippage
    cost = round_trip_cost("equity", "intraday", notional_inr=100_000)
    assert 0.0010 <= cost <= 0.0020


def test_fyers_delivery_brokerage_is_not_free():
    assert not load_costs_config()["brokerage"]["fyers"].get("delivery_free", False)
