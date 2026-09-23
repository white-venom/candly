import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

from candly.core.calendar import IST
from candly.research.config import load_research_config
from candly.research.costs import cost_breakdown, round_trip_cost
from candly.research.data import load_research_candles
from candly.research.labels import forward_end_ts, forward_labels, forward_paths
from candly.research.splits import fold_boundaries, gap_bars, walk_forward_folds
from candly.research.stats import (
    benjamini_hochberg,
    beta_interval,
    beta_posterior,
    binomial_pvalue,
    wilson_interval,
)
from candly.research.synthetic import synthetic_candles


def tiny() -> pd.DataFrame:
    closes = [100.0, 102.0, 101.0, 105.0, 104.0]
    opens = [99.0, 100.5, 102.0, 101.0, 105.0]
    return pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01 03:45", periods=5, freq="D", tz="UTC"),
            "open": opens,
            "high": [101.0, 103.0, 102.5, 106.0, 105.5],
            "low": [98.0, 100.0, 100.0, 100.5, 103.0],
            "close": closes,
            "volume": 1.0,
            "oi": np.nan,
        }
    )


def test_forward_labels_known_values():
    df = tiny()
    atr = pd.Series(2.0, index=df.index)
    lab = forward_labels(df, [1, 2], atr)
    assert lab["up_1"].tolist()[:4] == [1.0, 0.0, 1.0, 0.0]
    assert np.isnan(lab["up_1"].iloc[4]) and lab["up_2"].iloc[3:].isna().all()
    assert lab["down_1"].iloc[1] == 1.0
    assert lab["ret_2"].iloc[0] == pytest.approx(101 / 100 - 1)
    assert lab["trade_ret_2"].iloc[0] == pytest.approx(101 / 100.5 - 1)
    assert lab["mfe_2_atr"].iloc[0] == pytest.approx((103.0 - 100.0) / 2)
    assert lab["mae_2_atr"].iloc[0] == pytest.approx((100.0 - 100.0) / 2)
    assert lab["mfe_2_atr"].iloc[2] == pytest.approx((106.0 - 101.0) / 2)
    assert np.isnan(lab["mfe_2_atr"].iloc[3])


def test_forward_paths_and_end_ts():
    df = tiny()
    paths = forward_paths(df, pd.Series(2.0, index=df.index), 2)
    assert paths.shape == (5, 2, 4)
    assert paths[0, 0].tolist() == pytest.approx([(100.5 - 100) / 2, 1.5, 0.0, 1.0])
    assert paths[0, 1, 3] == pytest.approx(0.5)
    assert np.isnan(paths[4]).all() and np.isnan(paths[3, 1]).all()
    assert forward_end_ts(df["ts"], 2).iloc[0] == df["ts"].iloc[2]


def test_holdout_lock():
    cfg = load_research_config()
    full = synthetic_candles("1D", "2025-08-01", "2025-11-30", seed=1)
    seen = {}

    def loader(instrument_id, tf, start=None, end=None):
        seen["end"] = end
        return full

    locked = load_research_candles("NSE:RELIANCE", "1D", load=loader)
    assert locked["ts"].max() < cfg.holdout_start_utc
    assert locked["ts"].max().tz_convert(IST).date() < cfg.holdout_start
    assert seen["end"] == cfg.holdout_start_utc
    assert len(locked) < len(full)

    unlocked = load_research_candles("NSE:RELIANCE", "1D", allow_holdout=True, load=loader)
    assert len(unlocked) == len(full) and seen["end"] is None


def test_walk_forward_folds_respect_protocol():
    cfg = load_research_config()
    df = synthetic_candles("1D", "2015-01-01", "2026-06-30", seed=2)
    df = df[df["ts"] < cfg.holdout_start_utc].reset_index(drop=True)
    bounds = fold_boundaries(df["ts"].iloc[0], cfg)
    assert bounds[0][0].tz_convert(IST).date().isoformat() == "2018-01-01"
    assert bounds[-1][1] == cfg.holdout_start_utc
    assert all(a[1] == b[0] for a, b in zip(bounds, bounds[1:], strict=False))
    folds = walk_forward_folds(df["ts"], cfg)
    assert len(folds) == len(bounds)
    for fold in folds:
        ts = df["ts"]
        assert ts.iloc[fold.test].min() >= fold.test_start
        assert ts.iloc[fold.test].max() < min(fold.test_end, cfg.holdout_start_utc)
        first_test = fold.test[0]
        assert fold.train[-1] == first_test - 1 - gap_bars(cfg)
        assert fold.train[0] == 0
    assert len(folds[1].train) > len(folds[0].train)
    assert gap_bars(cfg) == cfg.purge_bars + cfg.embargo_bars >= max(cfg.horizons)


def test_wilson_interval_known_value():
    lo, hi = wilson_interval(8, 10, 0.95)
    assert (float(lo), float(hi)) == pytest.approx((0.4902, 0.9433), abs=1e-4)
    lo0, hi0 = wilson_interval(0, 20, 0.95)
    assert float(lo0) == 0.0 and float(hi0) == pytest.approx(0.1611, abs=1e-4)


def test_binomial_test_against_base_rate():
    assert binomial_pvalue(60, 100, 0.5) == pytest.approx(sps.binomtest(60, 100, 0.5).pvalue)
    assert binomial_pvalue(52, 100, 0.52) == pytest.approx(1.0)
    assert binomial_pvalue(0, 0, 0.5) == 1.0


def test_benjamini_hochberg_hand_computed():
    p = np.array([0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216])
    expected = np.array([0.01, 0.04, 0.084, 0.084, 0.084, 0.1, 0.074 * 10 / 7, 0.216, 0.216, 0.216])
    perm = np.random.default_rng(0).permutation(10)
    q = benjamini_hochberg(p[perm])
    assert q == pytest.approx(expected[perm])
    assert q == pytest.approx(sps.false_discovery_control(p[perm], method="bh"))
    assert benjamini_hochberg([]).size == 0


def test_beta_binomial_shrinks_to_base_rate():
    strength = load_research_config().prior_strength
    assert beta_posterior(30, 50, 0.5, 20) == pytest.approx(40 / 70)
    assert beta_posterior(0, 0, 0.53, strength) == pytest.approx(0.53)
    small = beta_posterior(4, 5, 0.5, strength)
    big = beta_posterior(400, 500, 0.5, strength)
    assert 0.5 < small < big < 0.8
    lo, hi = beta_interval(30, 50, 0.5, 20, 0.95)
    assert lo < 40 / 70 < hi


def test_costs_follow_the_table():
    table = {
        "brokerage": {"fyers": {"per_order_inr": 20.0, "pct_cap": 0.0003, "delivery_free": True}},
        "segments": {
            "equity_intraday": {
                "stt": {"sell": 0.00025},
                "exchange_txn": {"both": 0.00003},
                "stamp_duty": {"buy": 0.00003},
            },
            "equity_delivery": {
                "stt": {"buy": 0.001, "sell": 0.001},
                "exchange_txn": {"both": 0.00003},
                "stamp_duty": {"buy": 0.00015},
            },
            "mcx_futures": {
                "ctt": {"sell": 0.0001},
                "exchange_txn": {"both": 0.00002},
                "stamp_duty": {"buy": 0.00002},
            },
        },
        "sebi_fee": 0.000001,
        "gst_rate": 0.18,
        "slippage": {"equity": 0.0003, "future": 0.0003, "default": 0.0005},
        "mapping": {
            "equity": {"intraday": "equity_intraday", "multi_day": "equity_delivery"},
            "future": {"intraday": "mcx_futures", "multi_day": "mcx_futures"},
        },
    }
    delivery = 0.002 + 0.00015 + 0.00006 + 0.000002 + 0.18 * (0.00006 + 0.000002) + 0.0006
    assert round_trip_cost("equity", "multi_day", costs=table) == pytest.approx(delivery)
    intraday = 0.0006 + 0.00025 + 0.00003 + 0.00006 + 0.000002 + 0.18 * (0.0006 + 0.00006 + 0.000002) + 0.0006
    assert round_trip_cost("equity", "intraday", costs=table) == pytest.approx(intraday)
    big = cost_breakdown("future", "intraday", notional_inr=1_000_000, costs=table)
    assert big["brokerage"] == pytest.approx(2 * 20 / 1_000_000)
    assert big["transaction_tax"] == pytest.approx(0.0001)
    assert big["gst"] == pytest.approx(0.18 * (big["brokerage"] + big["exchange_txn"] + big["sebi_fee"]))


def test_real_cost_table_is_sane():
    delivery = round_trip_cost("equity", "multi_day")
    assert 0.002 < delivery < 0.005
    assert round_trip_cost("equity", "intraday") < delivery
    assert round_trip_cost("index", "multi_day") > 0
    assert round_trip_cost("future", "intraday") > 0
