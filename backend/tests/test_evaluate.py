import dataclasses

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import reload_calendar
from candly.forecast import analog
from candly.patterns import detect_patterns
from candly.research import evaluate
from candly.research.config import load_research_config
from candly.research.evaluate import evaluate_forecasts
from candly.research.synthetic import plant_marubozu_edge, synthetic_candles

ID = "NSE:RELIANCE"
# ~7 years of discovery history, then the first ~4 months of the validation span
START, END = "2012-01-01", "2019-04-30"
LONGER = "2019-08-31"  # ~8 months of validation, for enough scored pattern bars


@pytest.fixture(autouse=True)
def calendar_of_this_data_dir(tmp_data_dir):
    """A calendar cached while DATA_DIR pointed at the real data would carry its observed holidays and
    change the synthetic bar grids."""
    reload_calendar()
    yield
    reload_calendar()


def loader(frame: pd.DataFrame, seen: dict | None = None):
    def load(instrument_id, tf, start=None, end=None):
        if seen is not None:
            seen["end"] = end
        return frame if end is None else frame[frame["ts"] < end]

    return load


def planted_frame(end: str = LONGER) -> pd.DataFrame:
    df = synthetic_candles("1D", START, end, seed=200, plain=True)
    return plant_marubozu_edge(df, every=6, p_follow=0.85, seed=20, follow_bars=3, bearish_share=0.5)


def test_planted_edge_has_positive_skill(tmp_data_dir, no_keys):
    ev = evaluate_forecasts("1D", instruments=[ID], load=loader(planted_frame()))
    cfg = load_research_config()
    gng = cfg.go_no_go_1
    assert ev.period == "validation" and ev.start == cfg.train_end_utc("1D").isoformat()
    assert ev.n_forecasts > 100 and ev.n_graded + ev.n_void + ev.n_unresolved == ev.n_forecasts
    assert ev.n_void == 0 and ev.n_unresolved == cfg.forecast_steps
    assert ev.records["ref_time"].min() >= cfg.train_end_utc("1D")
    assert ev.population == "pattern_bars" and ev.n_scored <= ev.n_in_population < ev.n_forecasts
    assert ev.n_scored >= 15 and ev.n_abstained < ev.n_forecasts
    assert ev.skill > 0.1 and ev.brier < ev.brier_baseline
    lo, hi = ev.skill_ci
    assert 0 < lo <= ev.skill <= hi and ev.ci_level == cfg.ci_level
    assert ev.hit_rate > 0.6
    assert ev.ece is not None and ev.ece_bins == gng.ece_bins and 0 <= ev.calibration_p <= 1
    assert ev.calibration_sims >= 2000 and ev.bootstrap_resamples >= 2000
    assert 0.5 < ev.band_coverage_80 <= 1.0
    assert ev.gates["brier_skill"]
    assert not ev.gates["calibration"]  # far fewer than min_scored forecasts
    details = ev.gate_details
    assert set(details) == set(ev.gates) == {"certified_buckets", "brier_skill", "calibration"}
    assert details["brier_skill"]["ci"] == ev.skill_ci and details["brier_skill"]["value"] == ev.skill
    assert details["calibration"]["p_value"] == ev.calibration_p
    assert details["calibration"]["min_scored"] == gng.min_scored and details["calibration"]["pass"] is False


def test_only_pattern_bars_are_scored(tmp_data_dir, no_keys):
    df = planted_frame(END)
    found = detect_patterns(df, "1D").groupby("ts")["pattern"].agg(sorted)
    methods = ("analog_v1", "baseline_base_rate")
    evs = {m: evaluate_forecasts("1D", m, instruments=[ID], load=loader(df)) for m in methods}
    for ev in evs.values():
        rec = ev.records
        expected = rec["ref_time"].map(lambda t: found.get(t, []))
        assert rec["patterns"].map(sorted).tolist() == expected.tolist()
        assert rec["in_population"].tolist() == expected.map(bool).tolist()
        assert 0 < ev.n_in_population == int(rec["in_population"].sum()) < ev.n_forecasts
        scored = rec[rec["in_population"] & (rec["status"] == "graded") & ~rec["abstain"]]
        assert ev.n_scored == len(scored) > 0
        assert ev.brier == pytest.approx(scored["brier"].mean())
    # analog_v1 never calls a bar without a pattern, not even in a validation replay
    calls = evs["analog_v1"].records
    assert calls.loc[~calls["abstain"], "in_population"].all()


def test_random_data_has_no_skill(tmp_data_dir, no_keys, monkeypatch):
    df = synthetic_candles("1D", START, END, seed=410)
    gated = evaluate_forecasts("1D", instruments=[ID], load=loader(df))
    # the probabilities themselves are under test, so let calls through the cost and reward:risk hurdles
    free = dataclasses.replace(load_research_config(), require_edge_over_costs=False, min_reward_risk=-np.inf)
    monkeypatch.setattr(analog, "load_research_config", lambda: free)
    ev = evaluate_forecasts("1D", instruments=[ID], load=loader(df))
    assert ev.n_scored > 10 and ev.n_abstained > ev.n_forecasts / 2
    assert abs(ev.skill) < 0.1
    lo, hi = ev.skill_ci
    assert lo < ev.skill < hi and hi - lo > 0.02
    assert not ev.gates["brier_skill"] or not ev.gates["calibration"]
    assert gated.n_scored < ev.n_scored and gated.n_forecasts == ev.n_forecasts
    reasons = gated.records["abstain_reason"].fillna("")
    assert reasons.str.startswith(("edge below costs", "reward below risk")).any()


def test_validation_replays_do_not_gate_on_validation_statistics(tmp_data_dir, no_keys, monkeypatch):
    df = planted_frame(END)
    seen = []
    real = analog.make_forecast

    def spy(*args, **kwargs):
        seen.append(kwargs["bucket_gate"])
        return real(*args, **kwargs)

    monkeypatch.setattr(evaluate, "make_forecast", spy)
    ev = evaluate_forecasts("1D", instruments=[ID], load=loader(df))
    assert seen and not any(seen)
    reasons = ev.records["abstain_reason"].fillna("")
    unvalidated = reasons.str.startswith("unvalidated bucket")
    # only the structural part of the gate: bars without a pattern are not a scorecard bucket
    assert unvalidated.any() and reasons[unvalidated].str.endswith("is not a scorecard bucket").all()
    assert not ev.records.loc[unvalidated, "in_population"].any()

    seen.clear()
    late = synthetic_candles("1D", "2024-06-01", "2025-11-28", seed=7)  # synthetic: no real holdout data
    evaluate_forecasts("1D", allow_holdout=True, period="holdout", instruments=[ID], load=loader(late))
    assert seen and all(seen)


def test_base_rate_baseline_scores_exactly_zero_skill_and_voids_missing_bars(tmp_data_dir, no_keys):
    cfg = load_research_config()
    df = synthetic_candles("1D", "2016-01-01", END, seed=5)
    missing = int((df["ts"] >= cfg.train_end_utc("1D")).to_numpy().argmax()) + 20
    gap = df.drop(index=missing).reset_index(drop=True)
    ev = evaluate_forecasts("1D", "baseline_base_rate", instruments=[ID], load=loader(gap))
    graded = ev.records["status"] == "graded"
    assert ev.n_abstained == 0 and ev.n_scored == int((graded & ev.records["in_population"]).sum()) > 0
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
