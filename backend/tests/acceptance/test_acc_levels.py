"""Acceptance: levels and indicators on stored data match hand formulas (TradingView conventions).

The reference implementations in acceptance_helpers seed Wilder/EMA smoothing with an SMA (TradingView's
ta.rma); candly seeds with the first value. On multi-year series the two agree after warm-up, which is
what a trader comparing against a broker chart sees.
"""

from __future__ import annotations

import numpy as np
import pytest
from acceptance_helpers import (
    local,
    ref_atr,
    ref_floor_levels,
    ref_macd,
    ref_rsi,
    ref_supertrend,
)

from candly.features.levels import key_levels
from candly.indicators import compute_indicators

CASES = [
    ("NSE:NIFTY50", "1D"),
    ("NSE:RELIANCE", "1D"),
    ("BSE:SENSEX", "1D"),
    ("NSE:RELIANCE", "1h"),
    ("NSE:NIFTY50", "1h"),
]
TAIL = 50  # compare the last 50 bars, well past warm-up


@pytest.mark.parametrize(("instrument", "tf"), CASES)
def test_indicators_match_hand_formulas(real_candles, instrument, tf):
    df = real_candles(instrument, tf)
    if len(df) < 500:
        pytest.skip("need at least 500 bars for warm-up")
    got = compute_indicators(df, tf, ["rsi14", "atr14", "macd", "supertrend", "supertrend_dir"])
    macd, signal = ref_macd(df["close"].to_numpy())
    st_line, st_dir = ref_supertrend(df)
    expected = {
        "rsi14": ref_rsi(df["close"].to_numpy()),
        "atr14": ref_atr(df),
        "macd": macd,
        "macd_signal": signal,
        "macd_hist": macd - signal,
        "supertrend": st_line,
        "supertrend_dir": st_dir,
    }
    for name, want in expected.items():
        np.testing.assert_allclose(
            got[name].to_numpy()[-TAIL:],
            want[-TAIL:],
            rtol=1e-6,
            atol=1e-6,
            err_msg=f"{instrument} {tf} {name}",
        )


@pytest.mark.parametrize(("instrument", "tf"), CASES)
def test_floor_pivots_and_cpr_match_formulas(real_candles, instrument, tf):
    df = real_candles(instrument, tf)
    exchange = instrument.split(":")[0]
    levels = {lv.kind: lv.price for lv in key_levels(df, tf, exchange) if not lv.kind.startswith("swing")}
    if tf == "1D":
        src = df.iloc[-1]
        h, lo, c = src["high"], src["low"], src["close"]
    else:
        loc = local(df["ts"])
        day = loc.dt.date
        sessions = df.groupby(day).agg(h=("high", "max"), lo=("low", "min"), c=("close", "last"))
        # the session is finished only once its 15:15 bar has closed
        src = sessions.iloc[-1] if loc.iloc[-1].strftime("%H:%M") == "15:15" else sessions.iloc[-2]
        h, lo, c = src["h"], src["lo"], src["c"]
    for kind, want in ref_floor_levels(h, lo, c).items():
        assert levels[kind] == pytest.approx(want, rel=1e-9), kind


def test_session_vwap_matches_typical_price_formula(real_candles):
    df = real_candles("NSE:RELIANCE", "1h")
    loc = local(df["ts"])
    today = loc.dt.date == loc.dt.date.iloc[-1]
    vol = df.loc[today, "volume"]
    if vol.sum() <= 0:
        pytest.skip("no volume in the latest session")
    typical = (df["high"] + df["low"] + df["close"]) / 3
    want = float((typical[today] * vol).sum() / vol.sum())
    got = compute_indicators(df, "1h", ["vwap"])["vwap"].iloc[-1]
    assert got == pytest.approx(want, rel=1e-9)
