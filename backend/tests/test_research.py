import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

from candly.core.calendar import IST
from candly.core.settings import get_settings
from candly.research.config import load_costs_config, load_research_config
from candly.research.costs import cost_breakdown, round_trip_cost, segment_for
from candly.research.data import load_research_candles
from candly.research.labels import forward_end_ts, forward_labels, forward_paths
from candly.research.splits import fold_boundaries, gap_bars, split_labels, walk_forward_folds
from candly.research.stats import (
    benjamini_hochberg,
    beta_interval,
    beta_posterior,
    calibration_self_consistency,
    cluster_robust_z,
    clustered_bootstrap_skill_ci,
    expected_calibration_error,
    overlap_cluster_ids,
    wilson_interval,
    z_pvalue,
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


def test_config_parses_every_section():
    cfg = load_research_config()
    assert cfg.train_end_for("1D") == cfg.train_end["1D"] and cfg.train_end["1D"] < cfg.holdout_start
    assert {cfg.train_end_for(tf) for tf in ("5m", "15m", "1h")} == {cfg.train_end["intraday"]}
    assert cfg.train_end_utc("1D").tz_convert(IST).hour == 0
    assert cfg.test == "cluster_robust" and cfg.bh_family == "min_samples"
    assert cfg.bucket_null == "bucket_base_rate" and cfg.min_clusters > 0
    assert (cfg.analog_bucket, cfg.analog_no_pattern, cfg.analog_effective_n) == (
        "trend",
        "bucket_only",
        "n_over_steps",
    )
    assert list(cfg.confidence) == ["high", "medium"]
    assert cfg.require_validated_bucket and cfg.intraday_within_session and cfg.require_edge_over_costs
    assert cfg.fallback_stop_atr > 0
    assert cfg.context_buckets == ("trend", "vol_regime", "expiry")
    assert cfg.confidence["high"].min_edge_multiple > cfg.confidence["medium"].min_edge_multiple
    gng = cfg.go_no_go_1
    assert gng.slice.tf == "1D" and gng.slice.exchange == "NSE" and "NSE:INDIAVIX" in gng.slice.exclude
    assert gng.ece_bins > 1 and gng.ece_binning == "quantile"
    assert gng.population == "pattern_bars" and gng.brier_skill_ci == "date_clustered_bootstrap"
    assert gng.calibration_test == "self_consistency" and 0 < gng.calibration_min_p < 1
    assert gng.min_scored > 0
    assert cfg.validation_min_clusters >= cfg.min_samples and 0 < cfg.validation_max_p < 1
    assert cfg.same_exchange_validation and cfg.min_reward_risk > 0


def _variant(tmp_path, old: str, new: str):
    raw = (get_settings().config_dir / "research.yaml").read_text(encoding="utf-8")
    assert old in raw
    path = tmp_path / "research.yaml"
    path.write_text(raw.replace(old, new), encoding="utf-8")
    return path


def test_config_rejects_unknown_context_buckets(tmp_path):
    with pytest.raises(ValueError, match="weekday"):
        load_research_config(_variant(tmp_path, "[trend, vol_regime, expiry]", "[trend, weekday]"))


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("  min_scored: 500", "  min_scored: 500\n  require_ece_below: 0.03", "require_ece_below"),
        ("  min_reward_risk: 1.0", "  min_reward_risk: 1.0\n  max_drawdown: 0.2", "max_drawdown"),
        ("population: pattern_bars", "population: every_other_bar", "every_other_bar"),
        ("calibration_test: self_consistency", "calibration_test: hosmer_lemeshow", "hosmer_lemeshow"),
    ],
)
def test_config_rejects_unknown_names(tmp_path, old, new, match):
    with pytest.raises(ValueError, match=match):
        load_research_config(_variant(tmp_path, old, new))


def test_walk_forward_folds_respect_protocol():
    cfg = load_research_config()
    df = synthetic_candles("1D", "2015-01-01", "2026-06-30", seed=2)
    df = df[df["ts"] < cfg.holdout_start_utc].reset_index(drop=True)
    bounds = fold_boundaries("1D", cfg)
    assert bounds[0][0] == cfg.train_end_utc("1D")
    assert bounds[-1][1] == cfg.holdout_start_utc
    assert all(a[1] == b[0] for a, b in zip(bounds, bounds[1:], strict=False))
    folds = walk_forward_folds(df["ts"], "1D", cfg)
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

    deeper = synthetic_candles("1D", "2008-01-01", "2025-09-30", seed=3)
    assert [(f.test_start, f.test_end) for f in walk_forward_folds(deeper["ts"], "1D", cfg)] == bounds


def test_split_labels_use_the_fixed_train_end():
    cfg = load_research_config()
    df = synthetic_candles("1D", "2017-01-01", "2025-09-30", seed=4)
    split = split_labels(df["ts"], "1D", cfg)
    train_end = cfg.train_end_utc("1D")
    before = (df["ts"] < train_end).to_numpy()
    assert (split[before][: before.sum() - gap_bars(cfg)] == "train").all()
    assert (split[before][-gap_bars(cfg) :] == "gap").all()
    assert (split[~before] == "validation").all()
    late = synthetic_candles("15m", "2022-12-20", "2023-01-10", seed=5)
    late_split = split_labels(late["ts"], "15m", cfg)
    assert (late_split[(late["ts"] >= cfg.train_end_utc("15m")).to_numpy()] == "validation").all()


def test_overlap_clusters_known_answer():
    start = np.array([0, 1, 2, 10, 11, 20, 0, 4])
    end = np.array([3, 4, 5, 11, 12, 21, 5, 6])
    group = np.array([0, 0, 0, 0, 0, 0, 1, 1])
    ids = overlap_cluster_ids(group, start, end)
    # (0,3], (1,4], (2,5] chain; (10,11] and (11,12] only touch; group 1 is separate
    assert ids[0] == ids[1] == ids[2]
    assert len({ids[0], ids[3], ids[4], ids[5], ids[6]}) == 5
    assert ids[6] == ids[7]
    assert len(set(ids)) == 5


def test_cluster_robust_z_is_not_fooled_by_duplicates():
    rng = np.random.default_rng(0)
    resid = (rng.uniform(size=200) < 0.6).astype(float) - 0.5
    cluster = np.arange(200)
    z, n = cluster_robust_z(resid, cluster)
    assert n == 200 and z == pytest.approx(resid.sum() / np.sqrt((resid**2).sum()))
    z2, n2 = cluster_robust_z(np.r_[resid, resid], np.r_[cluster, cluster])
    assert n2 == 200 and z2 == pytest.approx(z)
    naive, _ = cluster_robust_z(np.r_[resid, resid], np.arange(400))
    assert naive == pytest.approx(z * np.sqrt(2))
    assert z_pvalue([z], [False])[0] == pytest.approx(sps.norm.sf(z))
    assert z_pvalue([-z], [True])[0] == pytest.approx(2 * sps.norm.sf(abs(z)))
    assert z_pvalue([np.nan], [False])[0] == 1.0


def test_quantile_ece_known_value():
    p = np.array([0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9])
    y = np.array([0, 0, 1, 0, 1, 1, 0, 1])
    # four equal-count bins: (0.1, 0.2), (0.3, 0.4), (0.6, 0.7), (0.8, 0.9)
    expected = 0.25 * (abs(0.15 - 0) + abs(0.35 - 0.5) + abs(0.65 - 1) + abs(0.85 - 0.5))
    assert expected_calibration_error(p, y, 4, "quantile") == pytest.approx(expected)
    assert expected_calibration_error(p, y, 2, "uniform") == pytest.approx(
        0.5 * abs(0.25 - 0.25) + 0.5 * abs(0.75 - 0.75)
    )
    assert expected_calibration_error([], [], 10) is None


def test_calibration_self_consistency_reports_the_observed_ece():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.3, 0.7, 400)
    y = (rng.uniform(size=400) < p).astype(float)
    ece, pvalue = calibration_self_consistency(p, y, 10, "quantile", sims=2000, seed=3)
    assert ece == pytest.approx(expected_calibration_error(p, y, 10, "quantile"))
    assert 0.0 <= pvalue <= 1.0
    assert calibration_self_consistency(p, y, 10, "quantile", sims=2000, seed=3) == (ece, pvalue)
    assert calibration_self_consistency([], [], 10) == (None, None)


def test_a_calibrated_forecaster_passes_the_self_consistency_test_about_95_percent_of_the_time():
    min_p = load_research_config().go_no_go_1.calibration_min_p
    rng = np.random.default_rng(7)
    passed = []
    for trial in range(200):
        p = rng.uniform(0.35, 0.65, 500)
        y = (rng.uniform(size=p.size) < p).astype(float)
        _, pvalue = calibration_self_consistency(p, y, 10, "quantile", sims=2000, seed=trial)
        passed.append(pvalue >= min_p)
    assert 0.90 <= np.mean(passed) <= 0.99


def test_an_overconfident_forecaster_fails_the_self_consistency_test():
    min_p = load_research_config().go_no_go_1.calibration_min_p
    rng = np.random.default_rng(8)
    for trial in range(20):
        truth = rng.uniform(0.4, 0.6, 1000)
        p = 0.5 + 3.0 * (truth - 0.5)  # stretches a 40-60% truth to 20-80% claims
        y = (rng.uniform(size=truth.size) < truth).astype(float)
        ece, pvalue = calibration_self_consistency(p, y, 10, "quantile", sims=2000, seed=trial)
        assert pvalue < min_p, (trial, ece, pvalue)


def test_date_clustered_bootstrap_ci_for_brier_skill():
    rng = np.random.default_rng(4)
    n = 600
    y = (rng.uniform(size=n) < 0.5).astype(float)
    leans_right = rng.uniform(size=n) < 0.7  # a skilled forecaster: leans the right way 70% of the time
    p = np.where(leans_right == (y == 1), 0.6, 0.4)
    brier, baseline = (p - y) ** 2, (0.5 - y) ** 2
    dates = np.arange(n)
    skill = 1 - brier.sum() / baseline.sum()
    lo, hi = clustered_bootstrap_skill_ci(brier, baseline, dates, 0.95, resamples=2000, seed=0)
    assert lo < skill < hi and lo > 0
    assert clustered_bootstrap_skill_ci(brier, baseline, dates, 0.95, seed=0) == (lo, hi)
    # the same forecasts for a second instrument on the same dates add no independent evidence
    twins = clustered_bootstrap_skill_ci(
        np.r_[brier, brier], np.r_[baseline, baseline], np.r_[dates, dates], 0.95, seed=0
    )
    assert twins == pytest.approx((lo, hi))
    naive = clustered_bootstrap_skill_ci(
        np.r_[brier, brier], np.r_[baseline, baseline], np.arange(2 * n), 0.95, seed=0
    )
    assert naive[1] - naive[0] < 0.8 * (hi - lo)
    assert clustered_bootstrap_skill_ci([], [], [], 0.95) is None


def test_wilson_interval_known_value():
    lo, hi = wilson_interval(8, 10, 0.95)
    assert (float(lo), float(hi)) == pytest.approx((0.4902, 0.9433), abs=1e-4)
    lo0, hi0 = wilson_interval(0, 20, 0.95)
    assert float(lo0) == 0.0 and float(hi0) == pytest.approx(0.1611, abs=1e-4)


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
        "reference_notional_inr": 50_000,
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
    # ₹20 per order is 0.04% of the ₹50,000 reference trade, above the 0.03% cap, so the cap applies
    intraday = 0.0006 + 0.00025 + 0.00003 + 0.00006 + 0.000002 + 0.18 * (0.0006 + 0.00006 + 0.000002) + 0.0006
    assert round_trip_cost("equity", "intraday", costs=table) == pytest.approx(intraday)
    big = cost_breakdown("future", "intraday", notional_inr=1_000_000, costs=table)
    assert big["brokerage"] == pytest.approx(2 * 20 / 1_000_000)
    assert big["transaction_tax"] == pytest.approx(0.0001)
    assert big["gst"] == pytest.approx(0.18 * (big["brokerage"] + big["exchange_txn"] + big["sebi_fee"]))
    assert big["dp_charge"] == 0.0
    without = {k: v for k, v in table.items() if k != "reference_notional_inr"}
    assert cost_breakdown("equity", "intraday", costs=without)["brokerage"] == pytest.approx(2 * 20 / 100_000)


def test_delivery_brokerage_and_dp_follow_the_table():
    table = {
        "brokerage": {
            "fyers": {
                "per_order_inr": 20.0,
                "pct_cap": 0.0003,
                "delivery_free": False,
                "delivery_pct_cap": 0.003,
                "dp_per_sell_inr": 12.5,
            }
        },
        "segments": {"equity_delivery": {"stt": {"buy": 0.001, "sell": 0.001}}},
        "gst_rate": 0.18,
        "mapping": {"equity": {"multi_day": "equity_delivery"}},
    }
    small = cost_breakdown("equity", "multi_day", notional_inr=5_000, costs=table)
    assert small["brokerage"] == pytest.approx(2 * 0.003)  # ₹15 = 0.3% of ₹5,000 is below ₹20
    assert small["dp_charge"] == pytest.approx(12.5 / 5_000)
    assert small["gst"] == pytest.approx(0.18 * (small["brokerage"] + small["dp_charge"]))
    table["brokerage"]["fyers"]["delivery_free"] = True
    free = cost_breakdown("equity", "multi_day", notional_inr=5_000, costs=table)
    assert free["brokerage"] == 0.0 and free["dp_charge"] == pytest.approx(12.5 / 5_000)


def test_one_lakh_delivery_round_trip_at_fyers():
    # ₹1 lakh delivery buy + sell: ₹40 brokerage, ₹12.5 DP, 0.1% + 0.1% STT, 0.00297% txn per side,
    # 0.015% stamp, ₹10/crore SEBI per side, 18% GST on brokerage + DP + txn + SEBI, 0.03% slippage per side
    c = cost_breakdown("equity", "multi_day", notional_inr=100_000)
    assert c["brokerage"] == pytest.approx(40 / 100_000)
    assert c["dp_charge"] == pytest.approx(12.5 / 100_000)
    assert c["transaction_tax"] == pytest.approx(0.002)
    assert c["exchange_txn"] == pytest.approx(2 * 0.0000297)
    assert c["stamp_duty"] == pytest.approx(0.00015)
    assert c["gst"] == pytest.approx(0.18 * (0.0004 + 0.000125 + 0.0000594 + 0.000002))
    total = round_trip_cost("equity", "multi_day", notional_inr=100_000)
    assert total == pytest.approx(0.003441952)
    assert total - c["slippage"] == pytest.approx(0.002841952)


def test_real_cost_table_is_sane():
    delivery = round_trip_cost("equity", "multi_day")
    assert delivery == pytest.approx(round_trip_cost("equity", "multi_day", notional_inr=100_000))
    assert 0.002 < delivery < 0.005
    assert round_trip_cost("equity", "intraday") < delivery
    assert round_trip_cost("index", "multi_day") > 0
    assert round_trip_cost("future", "intraday") > 0


def test_overnight_equity_shorts_are_costed_as_stock_futures():
    assert load_costs_config()["mapping"]["equity"]["multi_day_short"] == "futures"
    assert segment_for("equity", "multi_day", "short") == "futures"
    assert segment_for("equity", "multi_day", "long") == "equity_delivery"
    assert segment_for("equity", "intraday", "short") == "equity_intraday"
    assert segment_for("index", "multi_day", "short") == "futures"
    short = cost_breakdown("equity", "multi_day", side="short")
    futures_stt = load_costs_config()["segments"]["futures"]["stt"]["sell"]
    assert short["transaction_tax"] == pytest.approx(futures_stt)
    assert short["dp_charge"] == 0.0
    table = {"mapping": {"equity": {"multi_day": "equity_delivery", "multi_day_short": "options"}}}
    assert segment_for("equity", "multi_day", "short", costs=table) == "options"
    del table["mapping"]["equity"]["multi_day_short"]
    assert segment_for("equity", "multi_day", "short", costs=table) == "equity_delivery"
