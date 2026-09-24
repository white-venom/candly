import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import reload_calendar
from candly.core.settings import get_settings
from candly.research.causality import alter_future, check_causal
from candly.research.regime import load_pivot_config
from candly.research.regime_features import (
    ASOF_TOLERANCE,
    FEATURE_COLUMNS,
    FEATURE_GROUPS,
    MARKET_COLUMNS,
    REQUIRED_COLUMNS,
    VIX_COLUMNS,
    regime_features,
)
from candly.research.synthetic import synthetic_candles

START, END = "2015-01-01", "2016-12-31"


@pytest.fixture(scope="module")
def frames(tmp_path_factory):
    """Built once, on a calendar without the real data dir's observed holidays."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(tmp_path_factory.mktemp("data")))
        get_settings.cache_clear()
        reload_calendar()
        stock = synthetic_candles("1D", START, END, seed=11, drift=0.05)
        market = synthetic_candles("1D", START, END, seed=12, price=10_000.0)
        vix = synthetic_candles("1D", START, END, seed=13, price=15.0, vol=0.05)
    get_settings.cache_clear()
    reload_calendar()
    return stock, market, vix


def cuts_for(df: pd.DataFrame) -> list[int]:
    return [300, 420, len(df) - 2]


@pytest.mark.parametrize("column", FEATURE_COLUMNS)
def test_each_feature_ignores_the_instruments_future(frames, column):
    stock, market, vix = frames
    values = regime_features(stock, market, vix)[column]
    assert values.iloc[cuts_for(stock)].notna().all()
    check_causal(lambda d: regime_features(d, market, vix)[[column]], stock, cuts_for(stock))


@pytest.mark.parametrize("cut", [300, 420, 515])
def test_market_and_vix_future_bars_never_reach_the_past(frames, cut):
    stock, market, vix = frames
    full = regime_features(stock, market, vix)
    t = stock["ts"].iloc[cut]
    m_cut = int(np.flatnonzero(market["ts"] <= t)[-1])
    v_cut = int(np.flatnonzero(vix["ts"] <= t)[-1])
    variants = {
        "truncated": (market.iloc[: m_cut + 1], vix.iloc[: v_cut + 1]),
        "altered": (alter_future(market, m_cut, seed=3), alter_future(vix, v_cut, seed=4)),
    }
    for label, (m, v) in variants.items():
        got = regime_features(stock, m, v).iloc[: cut + 1]
        pd.testing.assert_frame_equal(got, full.iloc[: cut + 1], obj=label)
    assert full.iloc[cut][list(MARKET_COLUMNS + VIX_COLUMNS)].notna().all()


def test_known_values(frames):
    stock, market, vix = frames
    f = regime_features(stock, market, vix)
    close = stock["close"]
    i = 450
    assert f["ret_20"].iloc[i] == pytest.approx(np.log(close.iloc[i] / close.iloc[i - 20]))
    assert f["ret_250_skip20"].iloc[i] == pytest.approx(np.log(close.iloc[i - 20] / close.iloc[i - 250]))
    assert f["mkt_ret_20"].iloc[i] == pytest.approx(
        np.log(market["close"].iloc[i] / market["close"].iloc[i - 20])
    )
    assert f["rel_ret_60"].iloc[i] == pytest.approx(f["ret_60"].iloc[i] - f["mkt_ret_60"].iloc[i])
    assert f["vix"].iloc[i] == vix["close"].iloc[i]
    assert f["vix_chg_20"].iloc[i] == pytest.approx(np.log(vix["close"].iloc[i] / vix["close"].iloc[i - 20]))
    valid = f.dropna()
    assert (valid["dist_high_250_atr"] <= 0).all() and (valid["dist_low_250_atr"] >= 0).all()
    assert valid["range_pos_250"].between(0, 1).all()
    assert valid["natr14_pctile_250"].between(0, 1).all() and (valid["adx14"] >= 0).all()
    assert f[list(REQUIRED_COLUMNS)].iloc[:249].isna().any(axis=1).all()


def test_stale_context_is_missing_not_carried_forward(frames):
    stock, market, vix = frames
    gap = (vix["ts"] > vix["ts"].iloc[400]) & (vix["ts"] <= vix["ts"].iloc[420])
    f = regime_features(stock, market, vix[~gap].reset_index(drop=True))
    age = stock["ts"] - vix["ts"].iloc[400]
    in_gap = gap.to_numpy()
    carried = in_gap & (age <= ASOF_TOLERANCE).to_numpy()
    assert (f["vix"][carried] == vix["close"].iloc[400]).all() and carried.any()
    assert f["vix"][in_gap & ~carried].isna().all() and (in_gap & ~carried).any()
    assert f["vix"].iloc[421] == vix["close"].iloc[421]


def test_missing_context_frames_give_nan_columns(frames):
    stock, _, _ = frames
    f = regime_features(stock)
    assert list(f.columns) == list(FEATURE_COLUMNS)
    assert f[list(MARKET_COLUMNS + VIX_COLUMNS)].isna().all().all()
    assert f["ret_20"].notna().sum() > 0


def test_feature_groups_are_the_preregistered_ones():
    assert sorted(FEATURE_GROUPS) == sorted(load_pivot_config().regime_model.feature_groups)
    assert len(set(FEATURE_COLUMNS)) == len(FEATURE_COLUMNS)
    assert not set(VIX_COLUMNS) & set(REQUIRED_COLUMNS)
