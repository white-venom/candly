import dataclasses
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest
import yaml

from candly.core.calendar import get_calendar, reload_calendar
from candly.core.settings import get_settings
from candly.research import regime
from candly.research.causality import alter_future
from candly.research.config import load_research_config
from candly.research.regime import (
    FEATURE_COLUMNS,
    RegimeCall,
    baseline_probs,
    build_panel,
    fit_regime,
    fold_windows,
    gap_bars,
    hyperparameters,
    load_pivot_config,
    load_production_model,
    predict_regime,
    predict_regime_all,
    regime_labels,
    train_production_model,
    training_rows,
)
from candly.research.regime_eval import evaluate_regime
from candly.research.regime_features import regime_features
from candly.research.synthetic import bar_times, synthetic_candles

IDS = ["NSE:NIFTY50", "NSE:RELIANCE", "NSE:INFY"]
START, END = "2004-01-01", "2012-06-29"
TRAIN_END, HOLDOUT = date(2011, 1, 1), date(2012, 1, 1)


def momentum_candles(seed: int, price: float = 100.0, strength: float = 0.3) -> pd.DataFrame:
    """Known answer: persistent drift regimes (a flip every ~80 bars), so past returns predict future ones."""
    n = len(bar_times("1D", START, END))
    flips = np.random.default_rng(seed + 1000).uniform(size=n) < 1 / 80
    sign = np.where(np.cumsum(flips) % 2 == 0, 1.0, -1.0)
    return synthetic_candles("1D", START, END, seed=seed, price=price, drift=strength * sign)


def make_loader(frames: dict, seen: list | None = None):
    def load(instrument_id, tf, start=None, end=None):
        if seen is not None:
            seen.append((instrument_id, end))
        df = frames[instrument_id]
        return df if end is None else df[df["ts"] <= end].reset_index(drop=True)

    return load


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(tmp_path_factory.mktemp("data")))
        get_settings.cache_clear()
        reload_calendar()
        frames = {
            "NSE:NIFTY50": momentum_candles(1, price=5000.0),
            "NSE:RELIANCE": momentum_candles(2),
            "NSE:INFY": momentum_candles(3),
            "NSE:INDIAVIX": synthetic_candles("1D", START, END, seed=9, price=15.0, vol=0.05),
        }
    get_settings.cache_clear()
    reload_calendar()
    return frames


@pytest.fixture(scope="module")
def pivot():
    return dataclasses.replace(
        load_pivot_config(), train_end={"1D": TRAIN_END, "intraday": TRAIN_END}, holdout_start=HOLDOUT
    )


@pytest.fixture(scope="module")
def panel(world, pivot):
    return build_panel(IDS, pivot.regime_model.horizons_days, load=make_loader(world), pivot=pivot)


@pytest.fixture(scope="module")
def evaluation(pivot, panel):
    return evaluate_regime(5, instruments=IDS, pivot=pivot, panel=panel)


@pytest.fixture(scope="module")
def production(world, pivot, tmp_path_factory):
    out = tmp_path_factory.mktemp("models")
    meta = train_production_model(IDS, load=make_loader(world), pivot=pivot, out_dir=out)
    return out, meta


# --- pivot.yaml ----------------------------------------------------------------------------------------


def test_pivot_config_is_read_from_the_yaml():
    cfg = load_pivot_config()
    raw = yaml.safe_load((get_settings().config_dir / "pivot.yaml").read_text(encoding="utf-8"))
    rm, wf = raw["regime_model"], raw["data"]["walk_forward"]
    assert cfg.holdout_start == raw["data"]["holdout_start"] == load_research_config().holdout_start
    assert cfg.train_end["1D"] == raw["data"]["train_end"]["1D"]
    assert cfg.regime_model.horizons_days == tuple(rm["horizons_days"])
    assert cfg.walk_forward.retrain_every_months == wf["retrain_every_months"]
    assert cfg.walk_forward.purge_bars == wf["purge_bars"]
    assert cfg.walk_forward.embargo_bars == wf["embargo_bars"]
    assert cfg.regime_model.go_no_go_2.min_scored == rm["go_no_go_2"]["min_scored"]
    assert cfg.regime_model.go_no_go_2.calibration_min_p == rm["go_no_go_2"]["calibration_min_p"]
    assert len(cfg.sha256) == 64


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["regime_model"].update(extra_setting=1),
        lambda r: r["regime_model"]["go_no_go_2"].pop("min_scored"),
        lambda r: r["regime_model"].update(algo="xgboost"),
        lambda r: r["regime_model"].update(feature_groups=["momentum"]),
        lambda r: r["data"]["walk_forward"].update(expanding=False),
        lambda r: r["data"].update(holdout_start=date(2026, 1, 1)),
        lambda r: r["regime_model"]["go_no_go_2"].update(pass_rule="both horizons pass"),
    ],
)
def test_pivot_config_is_strict(tmp_path, mutate):
    raw = yaml.safe_load((get_settings().config_dir / "pivot.yaml").read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / "pivot.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load_pivot_config(path)


# --- labels and splits ---------------------------------------------------------------------------------


def test_labels_drop_flat_closes():
    close = np.array([100.0, 101.0, 101.0, 99.0, 102.0, 102.0])
    df = pd.DataFrame(
        {
            "ts": pd.date_range("2020-01-06 03:45", periods=6, freq="B", tz="UTC"),
            "open": close - 0.5,
            "high": close + 1,
            "low": close - 1,
            "close": close,
        }
    )
    lab = regime_labels(df, [1, 2])
    np.testing.assert_array_equal(lab["y_1"].to_numpy(), [1.0, np.nan, 0.0, 1.0, np.nan, np.nan])
    assert lab["flat_1"].tolist() == [False, True, False, False, True, False]
    np.testing.assert_array_equal(lab["y_2"].to_numpy(), [1.0, 0.0, 1.0, 1.0, np.nan, np.nan])
    assert lab["trade_ret_1"].iloc[0] == pytest.approx(101.0 / 100.5 - 1)
    assert lab["end_ts_2"].iloc[1] == df["ts"].iloc[3] and pd.isna(lab["end_ts_2"].iloc[4])


def test_gap_covers_the_label_horizon_plus_the_embargo():
    cfg = load_pivot_config()
    wf = cfg.walk_forward
    for h in cfg.regime_model.horizons_days:
        assert gap_bars(h, cfg) == max(wf.purge_bars, h) + wf.embargo_bars >= h + wf.embargo_bars


def test_validation_windows_tile_train_end_to_holdout():
    cfg = load_pivot_config()
    windows = fold_windows(cfg.train_end_utc(), cfg.holdout_start_utc, cfg.walk_forward.retrain_every_months)
    assert windows[0][0] == cfg.train_end_utc() and windows[-1][1] == cfg.holdout_start_utc
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:], strict=False))
    assert all(hi - lo <= pd.Timedelta(days=185) for lo, hi in windows)


def test_training_rows_are_purged_before_the_cut(panel, pivot):
    cut = pivot.train_end_utc()
    for h in pivot.regime_model.horizons_days:
        train = training_rows(panel, cut, h, pivot)
        rows = panel.loc[train]
        assert (rows[f"end_ts_{h}"] < cut).all()
        for instrument, group in panel.groupby("instrument"):
            n_before = int((group["ts"] < cut).sum())
            last_pos = rows.loc[rows["instrument"] == instrument, "pos"].max()
            assert last_pos == n_before - gap_bars(h, pivot) - 1


def test_fit_ignores_everything_from_the_cut_on(world, pivot, panel):
    """Prices after the cut are altered, so labels near the cut and later features change; the fit may not."""
    cut = pivot.train_end_utc()
    altered = {}
    for instrument, df in world.items():
        last = int(np.flatnonzero(df["ts"] < cut)[-1])
        altered[instrument] = alter_future(df, last, seed=7)
    other = build_panel(IDS, pivot.regime_model.horizons_days, load=make_loader(altered), pivot=pivot)
    X = panel.loc[panel["features_ok"], list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    h = 5
    assert not other[f"y_{h}"].equals(panel[f"y_{h}"])
    a = fit_regime(panel, training_rows(panel, cut, h, pivot), cut, h, pivot)
    b = fit_regime(other, training_rows(other, cut, h, pivot), cut, h, pivot)
    assert a is not None and a.n_calibration > 0
    np.testing.assert_array_equal(a.iso_x, b.iso_x)
    np.testing.assert_array_equal(a.iso_y, b.iso_y)
    np.testing.assert_array_equal(a.predict(X), b.predict(X))


def test_baselines_are_per_instrument_conditional_rates():
    train = pd.DataFrame(
        {
            "instrument": ["A"] * 4 + ["B"] * 2,
            "close_ema200_atr": [1.0, 1.0, -1.0, -1.0, 1.0, 1.0],
            "ret_20": [0.1, -0.1, 0.1, -0.1, 0.1, 0.1],
            "y_5": [1.0, 1.0, 0.0, 1.0, 0.0, 1.0],
        }
    )
    test = pd.DataFrame(
        {"instrument": ["A", "A", "B"], "close_ema200_atr": [2.0, -2.0, -2.0], "ret_20": [0.2, -0.2, -0.2],
         "y_5": [np.nan] * 3}
    )
    panel = pd.concat([train, test], ignore_index=True)
    is_train = np.r_[np.ones(6, bool), np.zeros(3, bool)]
    out = baseline_probs(panel, is_train, ~is_train, 5)
    np.testing.assert_allclose(out["p_base"], [0.75, 0.75, 0.5])
    np.testing.assert_allclose(out["p_trend"], [1.0, 0.5, 0.5])  # B never traded below EMA200: base rate
    np.testing.assert_allclose(out["p_mom"], [0.5, 1.0, 0.5])


# --- walk-forward evaluation ---------------------------------------------------------------------------


def test_walk_forward_finds_planted_momentum(evaluation, pivot):
    ev = evaluation
    preds = ev.predictions
    assert ev.n_folds == 2 and ev.period == "validation"
    assert preds["ts"].min() >= pivot.train_end_utc() and preds["ts"].max() < pivot.holdout_start_utc
    assert ev.n_scored > 500 and ev.n_unresolved > 0
    assert ev.n_scored + ev.n_unresolved + ev.n_flat + ev.n_unfitted == ev.n_rows - ev.n_no_features
    model = ev.methods["regime_v1"]
    assert model["skill"] > 0.05 and model["skill_ci"][0] > 0
    assert model["skill"] > ev.methods["trend_rule_ema200"]["skill"]
    assert ev.methods["base_rate"]["skill"] == 0.0
    assert model["top_decile"]["n"] >= ev.n_scored // 10 and model["top_decile"]["hit_rate"] > 0.6
    assert set(ev.gates) == {
        "brier_skill_ci_lower", "calibration", "min_scored", "top_decile_expectancy_after_costs",
    }
    assert ev.passed == all(g["pass"] for g in ev.gates.values())
    assert 0 < preds["p_model"].min() and preds["p_model"].max() < 1


def test_validation_never_loads_holdout_bars(world, pivot):
    seen: list = []
    panel = build_panel(IDS, (5,), load=make_loader(world, seen), pivot=pivot)
    assert seen and all(end == load_research_config().holdout_start_utc for _, end in seen)
    assert panel["ts"].max() < pivot.holdout_start_utc


def test_holdout_is_locked(pivot, world):
    with pytest.raises(PermissionError):
        evaluate_regime(5, period="holdout", instruments=IDS, load=make_loader(world), pivot=pivot)
    with_holdout = build_panel(IDS, (5, 20), allow_holdout=True, load=make_loader(world), pivot=pivot)
    with pytest.raises(PermissionError):
        evaluate_regime(5, "validation", pivot=pivot, panel=with_holdout)
    with pytest.raises(ValueError):
        evaluate_regime(3, pivot=pivot, panel=with_holdout)


def test_official_holdout_run_scores_only_holdout_bars(pivot, world):
    load = make_loader(world)
    ev = evaluate_regime(5, "holdout", allow_holdout=True, instruments=IDS, load=load, pivot=pivot)
    assert ev.period == "holdout" and ev.n_folds == 1 and ev.n_scored > 0
    assert ev.predictions["ts"].min() >= pivot.holdout_start_utc


# --- production model and live calls -------------------------------------------------------------------


def test_production_model_is_saved_with_its_provenance(production, pivot, panel):
    out, meta = production
    assert (out / "meta.json").exists() and meta["pivot_sha256"] == pivot.sha256
    assert set(meta["config_hashes"]) == {"pivot.yaml", "research.yaml", "costs.yaml", "watchlist.yaml"}
    assert meta["hyperparameters"] == hyperparameters() and meta["instruments"] == IDS
    for h in pivot.regime_model.horizons_days:
        entry = meta["horizons"][str(h)]
        assert pd.Timestamp(entry["train_last_ts"]) < pivot.holdout_start_utc
        assert entry["n_train"] == int(training_rows(panel, pivot.holdout_start_utc, h, pivot).sum())
        assert set(entry["base_rates"]) == set(IDS)
    model = load_production_model(out)
    assert set(model.fitted) == set(pivot.regime_model.horizons_days)


def test_tampered_booster_is_rejected(production, tmp_path):
    out, _ = production
    copy = tmp_path / "model"
    copy.mkdir()
    for f in out.iterdir():
        (copy / f.name).write_bytes(f.read_bytes())
    booster = copy / "booster_h5.txt"
    booster.write_text(booster.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_production_model(copy)


def report_for(pivot, passed: dict[int, bool], threshold: float = 0.2) -> dict:
    return {
        "model": "regime_v1",
        "period": "validation",
        "pivot_sha256": pivot.sha256,
        "hyperparameters": hyperparameters(),
        "horizons": {
            str(h): {
                "pass": ok,
                "gates": {"brier_skill_ci_lower": {"pass": ok}, "calibration": {"pass": ok}},
                "methods": {"regime_v1": {"skill": 0.1, "top_decile": {"conviction_threshold": threshold}}},
            }
            for h, ok in passed.items()
        },
    }


def live_now(df: pd.DataFrame) -> pd.Timestamp:
    return get_calendar().bar_close_time("NSE", df["ts"].iloc[-1], "1D") + pd.Timedelta(minutes=1)


def test_predict_regime_reads_validation_from_the_report(production, world, pivot, tmp_path):
    model = load_production_model(production[0])
    df = world["NSE:RELIANCE"]
    kwargs = {"market": world["NSE:NIFTY50"], "vix": world["NSE:INDIAVIX"], "model": model, "pivot": pivot}
    report = report_for(pivot, {5: True, 20: False})
    call = predict_regime("NSE:RELIANCE", df, live_now(df), report=report, **kwargs)
    assert isinstance(call, RegimeCall) and call.horizon_days == 5 and call.validated
    assert call.ref_time == int(df["ts"].iloc[-1].timestamp())
    assert call.base_rate == model.base_rate("NSE:RELIANCE", 5)
    assert call.edge == pytest.approx(call.p_up - call.base_rate)
    features = regime_features(df, world["NSE:NIFTY50"], world["NSE:INDIAVIX"]).iloc[[-1]]
    assert call.p_up == pytest.approx(float(model.fitted[5].predict(features.to_numpy(dtype=float))[0]))

    long = predict_regime("NSE:RELIANCE", df, live_now(df), horizon=20, report=report, **kwargs)
    assert not long.validated and "failed" in long.reason and long.direction is None
    assert long.confidence == "low"

    saved = tmp_path / "report.json"
    saved.write_text(json.dumps(report), encoding="utf-8")
    from_file = predict_regime("NSE:RELIANCE", df, live_now(df), report_path=saved, **kwargs)
    assert from_file.validated and from_file.horizon_days == 5 and from_file.p_up == call.p_up

    missing = tmp_path / "no-report.json"
    unvalidated = predict_regime_all("NSE:RELIANCE", df, live_now(df), report_path=missing, **kwargs)
    assert unvalidated and all(not c.validated and c.reason == "no validation report" for c in unvalidated)
    other_sha = {**report, "pivot_sha256": "0" * 64}
    assert not predict_regime("NSE:RELIANCE", df, live_now(df), report=other_sha, **kwargs).validated
    stale_model = dataclasses.replace(model, meta={**model.meta, "pivot_sha256": "0" * 64})
    kwargs["model"] = stale_model
    stale = predict_regime("NSE:RELIANCE", df, live_now(df), report=report, **kwargs)
    assert not stale.validated and "predates" in stale.reason


def test_predict_regime_uses_only_closed_bars(production, world, pivot):
    model = load_production_model(production[0])
    df = world["NSE:INFY"]
    kwargs = {"market": world["NSE:NIFTY50"], "vix": world["NSE:INDIAVIX"], "model": model, "pivot": pivot}
    during_last_bar = df["ts"].iloc[-1] + pd.Timedelta(hours=1)
    call = predict_regime("NSE:INFY", df, during_last_bar, report={}, **kwargs)
    assert call.ref_time == int(df["ts"].iloc[-2].timestamp())
    closed = predict_regime("NSE:INFY", df.iloc[:-1], live_now(df.iloc[:-1]), report={}, **kwargs)
    assert closed.p_up == call.p_up


def test_predict_regime_declines_without_a_basis(production, world, pivot):
    model = load_production_model(production[0])
    df = world["NSE:RELIANCE"]
    kwargs = {"market": world["NSE:NIFTY50"], "vix": world["NSE:INDIAVIX"], "pivot": pivot, "report": {}}
    now = live_now(df)
    assert predict_regime("NSE:TCS", df, now, model=model, **kwargs) is None
    assert predict_regime("NSE:RELIANCE", df.iloc[-100:], now, model=model, **kwargs) is None
    assert predict_regime("NSE:RELIANCE", df, now + pd.Timedelta(days=30), model=model, **kwargs) is None
    no_market = {**kwargs, "market": world["NSE:NIFTY50"].iloc[:0]}
    assert predict_regime("NSE:RELIANCE", df, now, model=model, **no_market) is None


def test_no_saved_model_means_no_call(world, pivot):
    df = world["NSE:RELIANCE"]
    assert load_production_model() is None
    assert predict_regime("NSE:RELIANCE", df, live_now(df), pivot=pivot, report={}) is None


def test_confidence_levels():
    min_edge = load_research_config().min_edge
    entry = {"methods": {"regime_v1": {"top_decile": {"conviction_threshold": 0.15}}}}
    assert regime._confidence(0.70, 0.55, False, entry)[0] == "low"
    assert regime._confidence(0.56, 0.56 - min_edge / 2, True, entry)[0] == "low"
    assert regime._confidence(0.66, 0.55, True, entry)[0] == "high"
    assert regime._confidence(0.60, 0.55, True, entry)[0] == "medium"
