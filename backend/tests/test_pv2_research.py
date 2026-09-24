"""pv2 candlestick tests (edge_search_v2.yaml patterns_range and patterns_pooled): the pattern feature group,
the paired range harness, and the pooled scorecard on a research universe."""

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import reload_calendar
from candly.core.instruments import get_instrument
from candly.core.schema import empty_candles
from candly.patterns import detect_patterns
from candly.research import pv2_pooled_eval, pv2_range_eval, range_model
from candly.research.causality import check_causal
from candly.research.lgbm import lgb  # noqa: F401  (loads LightGBM the safe way before anything else)
from candly.research.pivot_config import load_pivot_config
from candly.research.pv2_patterns import (
    FORMING,
    LAGS,
    PATTERN_NAMES,
    confirmed_bits,
    expand,
    feature_names,
    lagged_bits,
    pattern_features,
)
from candly.research.pv2_range_eval import DateBootstrap
from candly.research.scorecard import build_scorecard
from candly.research.stats import clustered_bootstrap_skill_ci
from candly.research.synthetic import plant_marubozu_edge, synthetic_candles


@pytest.fixture(autouse=True)
def plain_calendar(tmp_data_dir):
    reload_calendar()
    yield
    reload_calendar()


@pytest.fixture
def fast_fits(monkeypatch):
    monkeypatch.setattr(range_model, "NUM_ROUNDS", 4)
    monkeypatch.setattr(
        range_model, "HYPERPARAMETERS", {**range_model.HYPERPARAMETERS, "min_data_in_leaf": 20}
    )


def locked_loader(frames):
    """Candles by (instrument, tf), cut at `end` like the store; missing series come back empty."""

    def load(instrument_id, tf, start=None, end=None):
        df = frames.get((instrument_id, tf))
        if df is None:
            return empty_candles()
        if end is not None:
            df = df[df["ts"] < end]
        return df.reset_index(drop=True)

    return load


# ---------------------------------------------------------------- the pattern feature group


@pytest.mark.parametrize(
    "tf, start, end, vol",
    [("1D", "2019-01-01", "2020-12-31", 0.015), ("5m", "2024-03-01", "2024-03-20", 0.004)],
)
def test_pattern_features_are_causal(tf, start, end, vol):
    """Cut the series at bar t, or alter every later bar: no feature value up to t may change."""
    df = synthetic_candles(tf, start, end, seed=3, vol=vol)
    n = len(df)
    check_causal(lambda d: pattern_features(d, tf), df, [20, 60, n // 2, n - 2], seed=4)
    assert pattern_features(df, tf).iloc[:, :-1].to_numpy().sum() > 0  # the check saw real patterns


def test_pattern_features_known_values():
    plain = synthetic_candles("1D", "2019-01-01", "2020-06-30", seed=5, plain=True)
    df = plant_marubozu_edge(plain, every=25)
    f = pattern_features(df, "1D")
    assert list(f.columns) == feature_names() and len(f.columns) == LAGS * len(PATTERN_NAMES) + 1
    marubozu = detect_patterns(df, "1D").query("pattern == 'bullish_marubozu'")
    found = pd.Index(df["ts"]).get_indexer(marubozu["ts"])
    planted = np.arange(60, len(df), 25)
    assert set(planted) <= set(found)
    for lag in range(LAGS):
        column = f[f"pat_bullish_marubozu_lag{lag}"].to_numpy()
        expected = np.zeros(len(df))
        expected[found[found + lag < len(df)] + lag] = 1.0
        np.testing.assert_array_equal(column, expected)
    assert (f[FORMING] == 0.0).all()


def test_lagged_bits_and_expand():
    bits = np.array([0b1, 0b10, 0, 0b101], dtype=np.uint32)
    lagged = lagged_bits(bits)
    np.testing.assert_array_equal(lagged, [[1, 0, 0], [2, 1, 0], [0, 2, 1], [5, 0, 2]])
    x = expand(lagged)
    names = feature_names()
    col = {n: i for i, n in enumerate(names)}
    first, second, third = (f"pat_{PATTERN_NAMES[i]}" for i in range(3))
    assert x[3, col[f"{first}_lag0"]] == 1 and x[3, col[f"{third}_lag0"]] == 1
    assert x[3, col[f"{second}_lag0"]] == 0 and x[3, col[f"{second}_lag1"]] == 0
    assert x[2, col[f"{second}_lag1"]] == 1 and x[3, col[f"{second}_lag2"]] == 1
    assert x[:, col[FORMING]].sum() == 0 and x.sum() == 1 + 2 + 2 + 3


# ---------------------------------------------------------------- pv2_range


def test_range_registration_matches_the_yaml():
    reg = pv2_range_eval.load_registration()
    assert reg.name == "pv2_range" and reg.base == "range_v1" and reg.feature_group == "candlestick_patterns"
    assert reg.timeframes == ("5m", "15m", "1h") and reg.exchange == "NSE"
    assert reg.min_improvement == 0.01 and reg.ci_lower_above == 0.0 and reg.min_passing == 2


def _daily_frames(end="2020-03-31"):
    return {
        ("NSE:RELIANCE", "1D"): synthetic_candles("1D", "2012-01-01", end, seed=21),
        ("NSE:TCS", "1D"): synthetic_candles("1D", "2012-01-01", end, seed=22),
        ("NSE:NIFTY50", "1D"): synthetic_candles("1D", "2012-01-01", end, seed=23, price=20000.0, vol=0.008),
        ("NSE:INDIAVIX", "1D"): synthetic_candles("1D", "2012-01-01", end, seed=24, price=15.0, vol=0.05),
    }


def test_panel_bits_follow_each_instrument_and_never_cross_it():
    frames = _daily_frames()
    load = locked_loader(frames)
    panel = range_model.build_panel("NSE", "1D", load=load, instruments=["NSE:RELIANCE", "NSE:TCS"])
    get = range_model.research_loader(False, load)
    bits = pv2_range_eval.panel_pattern_bits(panel, get, load_pivot_config())
    for code, iid in enumerate(panel.instruments):
        block = panel.block(code)
        np.testing.assert_array_equal(bits[block], lagged_bits(confirmed_bits(frames[(iid, "1D")], "1D")))
    second = panel.block(1).start
    assert bits[second, 1] == 0 and bits[second, 2] == 0 and bits[second + 1, 2] == 0
    X = pv2_range_eval.augmented(panel, bits, np.arange(10))
    assert X.shape == (10, panel.X.shape[1] + len(feature_names()))
    np.testing.assert_array_equal(X[:, -1], panel.X[:10, -1])  # the instrument code stays last


def test_walk_forward_pair_reproduces_range_v1_exactly(fast_fits):
    load = locked_loader(_daily_frames())
    panel = range_model.build_panel("NSE", "1D", load=load, instruments=["NSE:RELIANCE", "NSE:TCS"])
    get = range_model.research_loader(False, load)
    bits = pv2_range_eval.panel_pattern_bits(panel, get, load_pivot_config())
    pair = pv2_range_eval.walk_forward_pair(panel, bits, threads=1)
    reference = range_model.walk_forward(panel)
    np.testing.assert_array_equal(pair.rows, reference.rows)
    np.testing.assert_array_equal(pair.base, reference.pred.astype(np.float32))
    assert pair.pv2.shape == pair.base.shape and np.isfinite(pair.pv2).all()
    assert range_model.HYPERPARAMETERS["num_threads"] == 4  # restored after the fits
    names = [*panel.feature_names, *feature_names(), range_model.INSTRUMENT_FEATURE]
    assert pair.importance["gain"].shape == (len(names),)
    assert pair.importance["split"][names.index(FORMING)] == 0  # a constant column is never split on


def test_date_bootstrap_matches_range_eval_and_its_p_value():
    rng = np.random.default_rng(0)
    dates = np.repeat([f"2024-01-{d:02d}" for d in range(1, 29)], 20)
    base = rng.uniform(1.0, 2.0, len(dates))
    model = base * rng.uniform(0.9, 1.05, len(dates))
    boot = DateBootstrap.draw(dates, 500, 7, 0.95)
    got = boot.skill(model, base)
    expected = clustered_bootstrap_skill_ci(model, base, dates, 0.95, resamples=500, seed=7)
    np.testing.assert_allclose(got["ci"], expected, rtol=1e-12)
    assert got["point"] == pytest.approx(1.0 - model.sum() / base.sum())
    assert DateBootstrap.draw(dates, 500, 7, 0.95).skill(base * 0.9, base)["p_one_sided"] == 1 / 501
    assert DateBootstrap.draw(dates, 500, 7, 0.95).skill(base * 1.1, base)["p_one_sided"] == 1.0
    diff = boot.mean_difference(np.ones(len(dates)), np.zeros(len(dates)))
    assert diff["point"] == 1.0 and diff["ci"] == [1.0, 1.0]


def test_evaluate_timeframe_end_to_end(fast_fits):
    """The whole pv2_range path on toy daily data: paired scores, checks, report and markdown."""
    load = locked_loader(_daily_frames())
    result = pv2_range_eval.evaluate_timeframe(
        "1D", load=load, instruments=["NSE:RELIANCE", "NSE:TCS"], use_cache=False, threads=1
    )
    primary = result["primary"]
    assert 0 < primary["p_one_sided"] <= 1 and primary["ci"][0] <= primary["point"] <= primary["ci"][1]
    assert primary["pass_if_met"] == all(primary["checks"].values())
    assert result["n_rows"] > 0 and result["reproduction"]["cache"] == "missing"
    assert set(result["secondary"]) == {
        "pinball_close_improvement",
        "pinball_all_improvement",
        "coverage_change",
        "same_change",
    }
    report = pv2_range_eval.assemble_report([{**result, "tf": "1h"}], [], threads=1)
    assert report["timeframes"] == {"5m": False, "15m": False, "1h": primary["pass_if_met"]}
    assert report["missing"] == ["5m", "15m"] and report["pass_if_met"] is False
    assert "pv2_range validation" in pv2_range_eval.render_markdown(report)


def test_reproduction_check_against_a_cached_range_v1(fast_fits):
    """A range_v1 walk-forward cached by range_model is matched bit for bit by the reproduced baseline."""
    load = locked_loader(_daily_frames())
    ids = ["NSE:RELIANCE", "NSE:TCS"]
    panel = range_model.build_panel("NSE", "1D", load=load, instruments=ids)
    with pv2_range_eval.lgbm_threads(1):
        cached = range_model.walk_forward(panel)
    meta = {"fingerprint": range_model.panel_fingerprint(panel), "settings": range_model.training_settings()}
    range_model.save_walk_forward(cached, panel, meta)
    result = pv2_range_eval.evaluate_timeframe("1D", load=load, instruments=ids, use_cache=False, threads=1)
    rep = result["reproduction"]
    assert rep["cache_fingerprint_matches_panel"] and rep["cache_rows_match"]
    assert rep["cache_settings_match_range_model"]
    assert rep["prediction_max_abs_difference"] == 0.0 and rep["prediction_share_bit_identical"] == 1.0
    assert rep["paired_reproduced_vs_cached"]["winkler_improvement"]["point"] == 0.0
    versus = result["versus_cached_range_v1"]["winkler_improvement"]
    assert versus["point"] == result["primary"]["point"] and versus["ci"] == result["primary"]["ci"]


def test_holdout_is_never_loaded_by_the_range_harness(fast_fits):
    seen = []
    frames = _daily_frames(end="2026-03-31")
    inner = locked_loader(frames)

    def load(instrument_id, tf, start=None, end=None):
        seen.append(end)
        return inner(instrument_id, tf, start, end)

    panel = range_model.build_panel("NSE", "1D", load=load, instruments=["NSE:RELIANCE"])
    pv2_range_eval.panel_pattern_bits(panel, range_model.research_loader(False, load), load_pivot_config())
    holdout = load_pivot_config().holdout_start_utc
    assert seen and all(end == holdout for end in seen) and panel.ts.max() < holdout


# ---------------------------------------------------------------- pv2_pooled


def test_pooled_registration_and_universe():
    reg = pv2_pooled_eval.load_registration()
    assert reg.name == "pv2_pooled" and reg.universe == "nifty200"
    assert reg.tf == "1D" and reg.certified_min == 1
    ids = [i.id for i in pv2_pooled_eval.universe_instruments(reg.universe, reg.tf)]
    assert len(ids) == len(set(ids)) == 202
    assert "NSE:NIFTY200" in ids and "NSE:RELIANCE" in ids and "NSE:INDIAVIX" not in ids


def _pooled_frames():
    ids = ["NSE:RELIANCE", "NSE:TCS", "NSE:NIFTY50"]
    frames = {
        (i, "1D"): plant_marubozu_edge(
            synthetic_candles("1D", "2012-01-01", "2026-03-31", seed=40 + k, plain=True), seed=k
        )
        for k, i in enumerate(ids)
    }
    return ids, frames


def test_pooled_rows_equal_build_scorecard():
    """Aggregating one pattern at a time gives build_scorecard's rows and BH family exactly."""
    ids, frames = _pooled_frames()
    load = locked_loader(frames)
    card = build_scorecard("1D", ids, load=load, persist=False)
    pooled = pv2_pooled_eval.build_pooled_rows([get_instrument(i) for i in ids], "1D", load=load)
    assert pooled.n_tests == card.meta.n_tests > 0
    pd.testing.assert_frame_equal(pooled.rows, card.rows)
    assert pooled.instruments == ids and pooled.bars["validation"] > 0 and pooled.events["train"] > 0


def test_pooled_evaluate_reports_simes_and_certification():
    ids, frames = _pooled_frames()
    result = pv2_pooled_eval.evaluate(
        load=locked_loader(frames), instruments=[get_instrument(i) for i in ids]
    )
    rows = result.pop("rows_frame")
    family = rows[rows["q_value"].notna()]
    assert result["simes_global_p"] == pytest.approx(family["q_value"].min())
    assert result["n_tests"] == len(family)
    assert result["certified_buckets"] == int(rows["certified"].sum())
    assert result["pass_if_met"] == (result["certified_buckets"] >= 1)
    assert result["certified_buckets"] >= 1  # the planted marubozu edge is found
    assert "bullish_marubozu" in {r["pattern"] for r in result["certified"]}
    summaries = result["certified_event_summary"]
    assert len(summaries) == len(result["certified"])
    for row, summary in zip(result["certified"], summaries, strict=True):
        assert summary["train"]["n"] == row["n"] and summary["validation"]["n"] == row["validation_n"]
        assert summary["train"]["hit_rate"] == pytest.approx(row["hit_rate"])
        assert 0 < row["validation_n_clusters"] <= row["validation_n"]
        assert 0 < row["validation_p_one_sided"] <= 1
    report = {
        "family_bh": "pending",
        "generated_at": "now",
        "edge_search_v2_sha256": "0" * 64,
        "fdr_alpha": 0.1,
        "unregistered_choices": [],
        "deviations": [],
        "caveats": [],
        "result": result,
    }
    assert "Certified buckets" in pv2_pooled_eval.render_markdown(report)


def test_pooled_build_skips_instruments_without_pre_holdout_bars():
    ids, frames = _pooled_frames()
    late = synthetic_candles("1D", "2025-11-01", "2026-03-31", seed=9)
    frames[("NSE:INFY", "1D")] = late
    instruments = [get_instrument(i) for i in [*ids, "NSE:INFY"]]
    pooled = pv2_pooled_eval.build_pooled_rows(instruments, "1D", load=locked_loader(frames))
    assert pooled.skipped == ["NSE:INFY"] and "NSE:INFY" not in set(pooled.rows["instrument"])
