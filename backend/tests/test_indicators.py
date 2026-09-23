import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST
from candly.indicators import INDICATOR_CATALOG, compute_indicators
from candly.indicators import functions as f
from candly.research.causality import check_causal
from candly.research.synthetic import bar_times, synthetic_candles


def frame(opens, highs, lows, closes, volumes=None, ts=None) -> pd.DataFrame:
    n = len(opens)
    ts = ts if ts is not None else pd.date_range("2024-01-01 03:45", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": np.asarray(opens, float),
            "high": np.asarray(highs, float),
            "low": np.asarray(lows, float),
            "close": np.asarray(closes, float),
            "volume": np.asarray(volumes if volumes is not None else [100.0] * n, float),
            "oi": np.nan,
        }
    )


@pytest.fixture(scope="module")
def daily():
    return synthetic_candles("1D", "2021-01-01", "2022-06-30", seed=3)


@pytest.fixture(scope="module")
def intraday():
    return synthetic_candles("5m", "2024-01-01", "2024-02-29", seed=4, vol=0.002)


def test_catalog_covers_every_series(daily):
    out = compute_indicators(daily, "1D")
    assert list(out.columns) == [i.name for i in INDICATOR_CATALOG]
    assert out.index.equals(daily.index)
    assert {i.pane for i in INDICATOR_CATALOG} == {"price", "oscillator", "volume"}
    assert {i.group for i in INDICATOR_CATALOG} == {"trend", "momentum", "volatility", "volume"}


def test_family_names_expand_and_unknown_names_fail(daily):
    out = compute_indicators(daily, "1D", ["macd", "rsi14"])
    assert list(out.columns) == ["macd", "macd_signal", "macd_hist", "rsi14"]
    with pytest.raises(ValueError):
        compute_indicators(daily, "1D", ["nope"])


def test_moving_averages_known_values():
    closes = np.arange(1.0, 31.0)
    df = frame(closes, closes + 1, closes - 1, closes)
    out = compute_indicators(df, "1D", ["sma20", "ema9"])
    assert np.isnan(out["sma20"].iloc[18])
    assert out["sma20"].iloc[19] == pytest.approx(np.mean(closes[:20]))
    alpha = 2 / 10
    ema = closes[0]
    for c in closes[1:9]:
        ema = alpha * c + (1 - alpha) * ema
    assert np.isnan(out["ema9"].iloc[7])
    assert out["ema9"].iloc[8] == pytest.approx(ema)


def test_atr_rsi_bollinger_known_values():
    n = 40
    closes = np.full(n, 100.0)
    df = frame(closes, closes + 1, closes - 1, closes)
    out = compute_indicators(df, "1D", ["atr14", "rsi14", "bb", "bb_width"])
    assert out["atr14"].iloc[13] == pytest.approx(2.0)
    assert np.isnan(out["atr14"].iloc[12])
    assert out["rsi14"].iloc[-1] == 50.0
    assert out["bb_upper"].iloc[-1] == pytest.approx(100.0)
    assert out["bb_width"].iloc[-1] == pytest.approx(0.0)

    rising = np.arange(100.0, 140.0)
    up = compute_indicators(frame(rising, rising + 1, rising - 1, rising), "1D", ["rsi14"])
    assert up["rsi14"].iloc[-1] == 100.0


def test_wilder_atr_matches_manual_recursion():
    rng = np.random.default_rng(0)
    c = 100 + np.cumsum(rng.normal(0, 1, 60))
    o = c + rng.normal(0, 0.5, 60)
    h = np.maximum(o, c) + rng.uniform(0, 1, 60)
    lo = np.minimum(o, c) - rng.uniform(0, 1, 60)
    df = frame(o, h, lo, c)
    tr = np.maximum.reduce([h - lo, np.abs(h - np.roll(c, 1)), np.abs(lo - np.roll(c, 1))])
    tr[0] = h[0] - lo[0]
    atr = tr[0]
    for x in tr[1:]:
        atr = atr + (x - atr) / 14
    assert f.atr(df, 14).iloc[-1] == pytest.approx(atr)


def test_obv_known_values():
    df = frame([10, 11, 10, 10], [11, 12, 11, 11], [9, 10, 9, 9], [10, 11, 10, 10], [5, 7, 3, 9])
    assert f.obv(df).tolist() == [0.0, 7.0, 4.0, 4.0]


def test_supertrend_follows_a_clear_trend():
    up = np.linspace(100, 160, 60)
    down = np.linspace(160, 100, 60)
    closes = np.concatenate([up, down])
    df = frame(closes, closes + 0.5, closes - 0.5, closes)
    out = compute_indicators(df, "1D", ["supertrend", "supertrend_dir"])
    assert out["supertrend_dir"].iloc[50] == 1.0
    assert out["supertrend"].iloc[50] < closes[50]
    assert out["supertrend_dir"].iloc[-1] == -1.0
    assert out["supertrend"].iloc[-1] > closes[-1]


def test_vwap_resets_each_ist_session():
    ts = bar_times("1h", "2024-01-01", "2024-01-02")
    n = len(ts)
    closes = np.arange(n, dtype=float) + 100
    df = frame(closes, closes + 1, closes - 1, closes, np.arange(1.0, n + 1), ts=ts)
    v = f.vwap(df)
    day = ts.tz_convert(IST).date
    first_of_day = np.r_[True, day[1:] != day[:-1]]
    assert np.allclose(v[first_of_day], closes[first_of_day])
    second = np.flatnonzero(first_of_day)[1]
    assert v.iloc[second + 1] == pytest.approx(
        (closes[second] * (second + 1) + closes[second + 1] * (second + 2)) / (2 * second + 3)
    )
    assert compute_indicators(df, "1D", ["vwap"])["vwap"].isna().all()


def test_rel_volume_uses_same_slot_over_previous_20_sessions():
    ts = bar_times("1h", "2024-01-01", "2024-02-15")
    local = ts.tz_convert(IST)
    volume = np.where(local.hour == 9, 1000.0, 100.0)
    df = frame(
        np.full(len(ts), 100.0),
        np.full(len(ts), 101.0),
        np.full(len(ts), 99.0),
        np.full(len(ts), 100.0),
        volume,
        ts=ts,
    )
    df.loc[df.index[-1], "volume"] = 300.0
    rv = f.rel_volume(df, intraday=True)
    per_day = 7
    assert rv.iloc[: 20 * per_day].isna().all()
    assert rv.iloc[20 * per_day] == pytest.approx(1.0)
    assert rv.iloc[-1] == pytest.approx(3.0)

    daily = frame([1.0] * 25, [1.0] * 25, [1.0] * 25, [1.0] * 25, [10.0] * 20 + [10, 20, 10, 10, 10])
    rv_d = f.rel_volume(daily, intraday=False)
    assert np.isnan(rv_d.iloc[19])
    assert rv_d.iloc[21] == pytest.approx(2.0)
    assert rv_d.iloc[22] == pytest.approx(10 / 10.5)


def test_zero_volume_gives_nan_not_inf():
    df = synthetic_candles("15m", "2024-03-01", "2024-03-31", seed=1).assign(volume=0.0)
    out = compute_indicators(df, "15m", ["vwap", "rel_volume", "obv"])
    assert out["vwap"].isna().all() and out["rel_volume"].isna().all()
    assert (out["obv"] == 0).all()


@pytest.mark.parametrize("tf_fixture,tf", [("daily", "1D"), ("intraday", "5m")])
def test_all_indicators_are_causal(request, tf_fixture, tf):
    df = request.getfixturevalue(tf_fixture)
    n = len(df)
    cuts = [n // 3, n // 2, n - 40]
    check_causal(lambda d: compute_indicators(d, tf), df, cuts)
