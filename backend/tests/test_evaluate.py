import pandas as pd
import pytest

from candly.research.config import load_research_config
from candly.research.evaluate import evaluate_forecasts
from candly.research.synthetic import plant_marubozu_edge, synthetic_candles

ID = "NSE:RELIANCE"
# ~7 years of discovery history, then the first ~4 months of the validation span
START, END = "2012-01-01", "2019-04-30"


def loader(frame: pd.DataFrame, seen: dict | None = None):
    def load(instrument_id, tf, start=None, end=None):
        if seen is not None:
            seen["end"] = end
        return frame if end is None else frame[frame["ts"] < end]

    return load


def test_planted_edge_has_positive_skill(tmp_data_dir, no_keys):
    df = synthetic_candles("1D", START, END, seed=200, plain=True)
    df = plant_marubozu_edge(df, every=6, p_follow=0.85, seed=20, follow_bars=3, bearish_share=0.5)
    ev = evaluate_forecasts("1D", instruments=[ID], load=loader(df))
    cfg = load_research_config()
    assert ev.period == "validation" and ev.start == cfg.train_end_utc("1D").isoformat()
    assert ev.n_forecasts > 60 and ev.n_graded + ev.n_void + ev.n_unresolved == ev.n_forecasts
    assert ev.n_void == 0 and ev.n_unresolved == cfg.forecast_steps
    assert ev.records["ref_time"].min() >= cfg.train_end_utc("1D")
    assert ev.n_scored >= 30 and ev.n_abstained < ev.n_forecasts
    assert ev.skill > 0.1 and ev.brier < ev.brier_baseline
    assert ev.hit_rate > 0.6
    assert ev.ece is not None and ev.ece_bins == cfg.go_no_go_1.ece_bins
    assert 0.5 < ev.band_coverage_80 <= 1.0
    assert ev.gates["brier_skill"]


def test_random_data_has_no_skill(tmp_data_dir, no_keys):
    df = synthetic_candles("1D", START, END, seed=410)
    ev = evaluate_forecasts("1D", instruments=[ID], load=loader(df))
    assert ev.n_scored > 10 and ev.n_abstained > ev.n_forecasts / 2
    assert abs(ev.skill) < 0.1
    assert not ev.gates["brier_skill"] or not ev.gates["ece"]


def test_base_rate_baseline_scores_exactly_zero_skill_and_voids_missing_bars(tmp_data_dir, no_keys):
    cfg = load_research_config()
    df = synthetic_candles("1D", "2016-01-01", END, seed=5)
    missing = int((df["ts"] >= cfg.train_end_utc("1D")).to_numpy().argmax()) + 20
    gap = df.drop(index=missing).reset_index(drop=True)
    ev = evaluate_forecasts("1D", "baseline_base_rate", instruments=[ID], load=loader(gap))
    assert ev.n_abstained == 0 and ev.n_scored == ev.n_graded
    assert ev.skill == pytest.approx(0.0, abs=1e-12)
    assert ev.n_void == cfg.forecast_steps
    assert ev.n_certified is None


def test_the_holdout_needs_an_explicit_go_no_go_run(tmp_data_dir, no_keys):
    cfg = load_research_config()
    df = synthetic_candles("1D", "2025-05-01", "2025-11-28", seed=6)
    with pytest.raises(PermissionError):
        evaluate_forecasts("1D", "baseline_base_rate", instruments=[ID], period="holdout", load=loader(df))

    seen: dict = {}
    val = evaluate_forecasts("1D", "baseline_base_rate", instruments=[ID], load=loader(df, seen))
    assert seen["end"] == cfg.holdout_start_utc
    assert val.records["ref_time"].max() < cfg.holdout_start_utc

    held = evaluate_forecasts(
        "1D", "baseline_base_rate", True, period="holdout", instruments=[ID], load=loader(df, seen)
    )
    assert seen["end"] is None and held.period == "holdout" and held.n_graded > 0
    assert held.records["ref_time"].min() >= cfg.holdout_start_utc
    with pytest.raises(ValueError):
        evaluate_forecasts("1D", "magic", instruments=[ID], load=loader(df))
