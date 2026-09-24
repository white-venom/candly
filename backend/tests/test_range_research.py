import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST, get_calendar, reload_calendar
from candly.core.schema import empty_candles
from candly.core.settings import get_settings
from candly.forecast import make_forecast
from candly.indicators.functions import atr as atr_fn
from candly.research import range_eval, range_model
from candly.research.causality import alter_future
from candly.research.config import load_research_config
from candly.research.lgbm import lgb  # noqa: F401  (loads LightGBM the safe way before anything else)
from candly.research.pivot_config import load_pivot_config
from candly.research.range_eval import (
    MethodOutput,
    analog_bands_for_series,
    evaluate_range,
    go_no_go_2,
    interval_iou,
    pinball,
    row_scores,
    summarise,
)
from candly.research.range_features import (
    FEATURE_GROUPS,
    RangeContext,
    feature_columns,
    range_features,
)
from candly.research.range_model import (
    build_panel,
    fit_range_model,
    fold_windows,
    ghost_ohlc,
    load_model,
    range_targets,
    save_model,
    train_rows,
)
from candly.research.synthetic import synthetic_candles

PIVOT = Path(get_settings().config_dir) / "pivot.yaml"


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


# ---------------------------------------------------------------- pivot.yaml


def test_pivot_config_reads_the_registered_protocol():
    p = load_pivot_config()
    spec = p.range_model
    assert spec.name == "range_v1" and spec.algo == "lightgbm_quantile"
    assert spec.quantiles == (0.1, 0.5, 0.9) and spec.steps == 3 and spec.interval_alpha == pytest.approx(0.2)
    assert spec.timeframes == ("5m", "15m", "1h", "1D") and spec.exchanges == ("NSE", "BSE", "MCX")
    assert set(spec.feature_groups) == set(FEATURE_GROUPS)
    assert spec.gate.coverage_within == (0.76, 0.84)
    assert spec.gate.min_improvement == 0.05 and spec.gate.ci_lower_above == 0.0
    assert spec.gate.min_passing == 2 and spec.gate.required_any == ("1D", "1h")
    assert p.candle_accuracy.same_close_atr == 0.25
    assert p.walk_forward.gap_bars == 20 and p.walk_forward.retrain_every_months == 6
    assert p.holdout_start == load_research_config().holdout_start
    assert len(p.sha256) == 64


@pytest.mark.parametrize(
    "old, new",
    [
        ("  steps: 3\n", "  steps: 3\n  extra_knob: 1\n"),  # unknown key
        ("within 0.25 ATR of the predicted median", "within 0.25 ATR of the predicted mean"),  # wording
        ("at least 2 of 4 timeframes pass", "at least 2 of 5 timeframes pass"),  # rule vs timeframes
        ("algo: lightgbm_quantile", "algo: xgboost_quantile"),  # not implemented
        ("holdout_start: 2025-10-01", "holdout_start: 2025-11-01"),  # differs from research.yaml
        ("expanding: true", "expanding: false"),
    ],
)
def test_pivot_config_is_strict(tmp_path, old, new):
    text = PIVOT.read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / "pivot.yaml"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(ValueError):
        load_pivot_config(path)


# ---------------------------------------------------------------- features


def _alter_after(df: pd.DataFrame | None, ts_cut: pd.Timestamp, seed: int) -> pd.DataFrame | None:
    """`df` with every bar opened after `ts_cut` altered."""
    if df is None:
        return None
    later = np.flatnonzero((df["ts"] > ts_cut).to_numpy())
    return df if not len(later) else alter_future(df, int(later[0]) - 1, seed=seed)


def _upto(df: pd.DataFrame | None, ts_cut: pd.Timestamp) -> pd.DataFrame | None:
    return None if df is None else df[df["ts"] <= ts_cut].reset_index(drop=True)


def _daily_cut(tf: str, ts_cut: pd.Timestamp) -> pd.Timestamp:
    """Intraday, the daily bar of the cut's own session has not closed yet: it counts as future."""
    if tf == "1D":
        return ts_cut
    return pd.Timestamp(ts_cut.tz_convert(IST).normalize()).tz_convert("UTC") - pd.Timedelta(seconds=1)


def _context(tf: str, start: str, end: str) -> RangeContext:
    market = synthetic_candles(tf, start, end, seed=2, price=20000.0, vol=0.004)
    vix = synthetic_candles(tf, start, end, seed=3, price=15.0, vol=0.03)
    vix_daily = synthetic_candles("1D", "2019-06-01", end, seed=4, price=15.0, vol=0.05)
    return RangeContext(market=market, vix=vix, vix_daily=vix_daily if tf != "1D" else vix)


@pytest.mark.parametrize(
    "tf, start, end, instrument",
    [
        ("5m", "2024-03-01", "2024-04-10", "NSE:RELIANCE"),
        ("15m", "2024-01-01", "2024-04-30", "NSE:RELIANCE"),
        ("1h", "2023-09-01", "2024-03-31", "NSE:RELIANCE"),
        ("1D", "2019-09-01", "2021-06-30", "NSE:NIFTY50"),
        ("1D", "2019-09-01", "2021-06-30", "NSE:RELIANCE"),
        ("5m", "2024-03-01", "2024-04-12", "MCX:CRUDEOIL"),
    ],
)
def test_range_features_are_causal(tf, start, end, instrument):
    """Cut every input at bar t, or alter every input's later bars: no value up to t may change."""
    exchange = instrument.split(":")[0]
    df = synthetic_candles(tf, start, end, exchange=exchange, seed=1, vol=0.004 if tf != "1D" else 0.015)
    ctx = _context(tf, start, end) if exchange == "NSE" else RangeContext()
    full = range_features(df, tf, instrument, ctx)
    assert list(full.columns) == ["atr", *feature_columns()]
    n = len(df)
    for k, cut in enumerate([80, n // 3, n // 2, n - 2]):
        ts_cut = df["ts"].iloc[cut]
        expected = full.iloc[: cut + 1]
        day_cut = _daily_cut(tf, ts_cut)
        truncated = range_features(
            df.iloc[: cut + 1].copy(),
            tf,
            instrument,
            RangeContext(_upto(ctx.market, ts_cut), _upto(ctx.vix, ts_cut), _upto(ctx.vix_daily, day_cut)),
        )
        altered = range_features(
            alter_future(df, cut, seed=k),
            tf,
            instrument,
            RangeContext(
                _alter_after(ctx.market, ts_cut, 10 + k),
                _alter_after(ctx.vix, ts_cut, 20 + k),
                _alter_after(ctx.vix_daily, day_cut, 30 + k),
            ),
        ).iloc[: cut + 1]
        for label, got in (("truncated", truncated), ("altered", altered)):
            try:
                pd.testing.assert_frame_equal(got, expected, check_exact=False, rtol=1e-10, atol=1e-12)
            except AssertionError as exc:
                raise AssertionError(f"lookahead at cut={cut} ({label}): {exc}") from None
    usable = full.iloc[100:]
    context = ("mkt_atr_pct", "vix", "vix_chg_5d") if exchange == "NSE" else ()
    for column in ("atr_pct", "rv20_atr", "range1_atr", "adx14", *context):
        assert usable[column].notna().all(), column
    if instrument != "NSE:NIFTY50":  # an index has no meaningful volume
        assert full["rel_volume"].iloc[-(n // 5) :].notna().all()  # past its 20-session warm-up
    if exchange == "MCX":
        assert full[["mkt_atr_pct", "mkt_range_atr", "vix", "vix_chg_5d"]].isna().all(axis=None)


def test_range_features_known_values():
    tf = "5m"
    df = synthetic_candles(tf, "2024-03-04", "2024-03-15", seed=5, vol=0.004)
    market = synthetic_candles(tf, "2024-03-04", "2024-03-15", seed=6, price=20000.0, vol=0.004)
    missing_bar = market["ts"].iloc[200]
    market = market[market["ts"] != missing_bar].reset_index(drop=True)
    f = range_features(df, tf, "NSE:RELIANCE", RangeContext(market=market))
    atr = atr_fn(df, 14)
    t = 150
    assert f["atr"].iloc[t] == pytest.approx(atr.iloc[t])
    assert f["range1_atr"].iloc[t] == pytest.approx((df["high"] - df["low"]).iloc[t] / atr.iloc[t])
    assert f["body3_atr"].iloc[t] == pytest.approx((df["close"] - df["open"]).iloc[t - 2] / atr.iloc[t])
    local = df["ts"].dt.tz_convert(IST)
    first_of_day = np.flatnonzero((local.dt.date != local.dt.date.shift(1)).to_numpy())
    day2 = first_of_day[1]
    gap = df["open"].iloc[day2] - df["close"].iloc[day2 - 1]
    for i in (day2, day2 + 10):  # the opening gap is carried through the session
        assert f["gap_atr"].iloc[i] == pytest.approx(gap / atr.iloc[i])
    assert f["minutes_from_open"].iloc[day2] == 0 and f["minutes_from_open"].iloc[day2 + 1] == 5
    assert f["minutes_to_close"].iloc[day2 - 1] == 0  # 15:25-15:30 is the session's last bar
    assert f["day_of_week"].iloc[day2] == local.iloc[day2].weekday()
    # a missing market bar borrows the previous bar of the same session
    at_missing = int(np.flatnonzero((df["ts"] == missing_bar).to_numpy())[0])
    assert f["mkt_range_atr"].iloc[at_missing] == pytest.approx(f["mkt_range_atr"].iloc[at_missing - 1])
    assert f["vix"].isna().all()
    index = range_features(df, tf, "NSE:NIFTY50")
    assert index["rel_volume"].isna().all() and index["mkt_atr_pct"].isna().all()


def test_market_features_never_borrow_from_an_older_session():
    tf = "5m"
    df = synthetic_candles(tf, "2024-03-04", "2024-03-08", seed=5)
    market = synthetic_candles(tf, "2024-03-04", "2024-03-08", seed=6, price=20000.0)
    day = market["ts"].dt.tz_convert(IST).dt.date
    dropped = sorted(set(day))[2]
    f = range_features(df, tf, "NSE:RELIANCE", RangeContext(market=market[day != dropped]))
    on_dropped = (df["ts"].dt.tz_convert(IST).dt.date == dropped).to_numpy()
    assert f.loc[on_dropped, "mkt_atr_pct"].isna().all()
    assert f.loc[~on_dropped, "mkt_atr_pct"].iloc[50:].notna().all()


def test_vix_change_is_five_sessions_back():
    df = synthetic_candles("1D", "2020-01-01", "2020-12-31", seed=8)
    vix = synthetic_candles("1D", "2020-01-01", "2020-12-31", seed=9, price=15.0, vol=0.05)
    f = range_features(df, "1D", "NSE:RELIANCE", RangeContext(vix=vix, vix_daily=vix))
    t = 100
    assert f["vix"].iloc[t] == pytest.approx(vix["close"].iloc[t])
    assert f["vix_chg_5d"].iloc[t] == pytest.approx(math.log(vix["close"].iloc[t] / vix["close"].iloc[t - 5]))
    assert f["vix_chg_5d"].iloc[:5].isna().all()


# ---------------------------------------------------------------- targets, folds, training rows


def test_range_targets_known_values():
    df = pd.DataFrame(
        {
            "high": [11.0, 13.0, 12.0, 15.0],
            "low": [9.0, 10.0, 8.0, 11.0],
            "close": [10.0, 12.0, 9.0, 14.0],
        }
    )
    y = range_targets(df, pd.Series([2.0, 2.0, 1.0, 1.0]), steps=3)
    assert y.shape == (4, 3, 3)
    np.testing.assert_allclose(y[0, 0], [(13 - 10) / 2, (10 - 10) / 2, (12 - 10) / 2])
    np.testing.assert_allclose(y[0, 2], [(15 - 10) / 2, (11 - 10) / 2, (14 - 10) / 2])
    np.testing.assert_allclose(y[2, 0], [15 - 9, 11 - 9, 14 - 9])
    assert np.isnan(y[1, 2]).all() and np.isnan(y[3]).all()


def test_fold_windows_follow_pivot_yaml():
    p = load_pivot_config()
    daily = fold_windows("1D", p)
    assert daily[0][0] == pd.Timestamp("2018-12-31 18:30", tz="UTC")  # 2019-01-01 00:00 IST
    assert daily[0][1] == pd.Timestamp("2019-06-30 18:30", tz="UTC")
    assert daily[-1][1] == p.holdout_start_utc and len(daily) == 14
    intraday = fold_windows("5m", p)
    assert intraday[0][0] == p.train_end_utc("5m") and len(intraday) == 6
    assert all(a[1] == b[0] for a, b in zip(daily, daily[1:], strict=False))


def _frames(start="2012-01-01", end="2026-03-31"):
    return {
        ("NSE:RELIANCE", "1D"): synthetic_candles("1D", start, end, seed=21),
        ("NSE:TCS", "1D"): synthetic_candles("1D", start, end, seed=22),
        ("NSE:NIFTY50", "1D"): synthetic_candles("1D", start, end, seed=23, price=20000.0, vol=0.008),
        ("NSE:INDIAVIX", "1D"): synthetic_candles("1D", start, end, seed=24, price=15.0, vol=0.05),
    }


def locked_loader(frames, seen: list | None = None):
    """A candle loader that refuses to hand out holdout bars unless asked for them explicitly."""
    holdout = load_pivot_config().holdout_start_utc

    def load(instrument_id, tf, start=None, end=None):
        if seen is not None:
            seen.append((instrument_id, tf, end))
        df = frames.get((instrument_id, tf))
        if df is None:
            return empty_candles()
        if end is not None:
            df = df[df["ts"] < end]
        return df.reset_index(drop=True)

    load.holdout = holdout
    return load


def test_panel_never_reads_the_holdout_and_train_rows_keep_the_gap():
    seen = []
    load = locked_loader(_frames(), seen)
    p = load_pivot_config()
    panel = build_panel("NSE", "1D", load=load, instruments=["NSE:RELIANCE", "NSE:TCS"])
    assert seen and all(end == p.holdout_start_utc for _, _, end in seen)
    assert panel.ts.max() < p.holdout_start_utc
    assert panel.instruments == ["NSE:RELIANCE", "NSE:TCS"]
    assert panel.X.dtype == np.float32 and panel.X.shape[1] == len(feature_columns()) + 1
    assert set(np.unique(panel.X[:, -1])) == {0.0, 1.0}
    cutoff = p.train_end_utc("1D")
    rows = train_rows(panel, cutoff, p.walk_forward.gap_bars)
    for code in range(2):
        block = panel.block(code)
        mine = rows[(rows >= block.start) & (rows < block.stop)]
        before = np.flatnonzero(np.asarray(panel.ts[block] < cutoff)) + block.start
        assert mine.max() == before[-1 - p.walk_forward.gap_bars]
        # the last training target ends well before the test window opens
        assert panel.ts[mine.max() + load_pivot_config().range_model.steps] < cutoff
    assert panel.valid[rows].all()


# ---------------------------------------------------------------- model


def _toy_panel():
    return build_panel(
        "NSE", "1D", load=locked_loader(_frames("2012-01-01", "2019-12-31")), instruments=["NSE:RELIANCE"]
    )


def test_model_quantiles_are_sorted_and_ghosts_consistent(fast_fits):
    panel = _toy_panel()
    rows = np.flatnonzero(panel.valid)
    spec = load_pivot_config().range_model
    model = fit_range_model(panel.X[rows], panel.Y[rows], spec, panel.feature_names, panel.instruments, 1)
    assert len(model.boosters) == 27
    pred = model.predict(panel.X[rows[:500]])
    assert pred.shape == (500, 3, 3, 3)
    assert (np.diff(pred, axis=-1) >= 0).all()
    ghost = ghost_ohlc(pred)
    o, h, lo, c = (ghost[..., i] for i in range(4))
    assert (o[:, 0] == 0).all() and np.allclose(o[:, 1:], c[:, :-1]) and np.allclose(c, pred[:, :, 2, 1])
    assert (h >= np.maximum(o, c)).all() and (lo <= np.minimum(o, c)).all()


def test_ghost_ohlc_widens_a_crossed_median_box():
    pred = np.zeros((1, 2, 3, 3))
    pred[0, 0] = [[0.1, 0.2, 0.3], [-0.3, -0.2, -0.1], [0.4, 0.5, 0.6]]  # p50 close above the p50 high
    pred[0, 1] = [[0.5, 0.9, 1.2], [-0.5, 0.1, 0.2], [0.2, 0.3, 0.4]]
    ghost = ghost_ohlc(pred)[0]
    np.testing.assert_allclose(ghost[0], [0.0, 0.5, -0.2, 0.5])
    np.testing.assert_allclose(ghost[1], [0.5, 0.9, 0.1, 0.3])


def test_walk_forward_cache_is_tied_to_the_panel_config_and_code():
    panel = _toy_panel()
    result = range_model.WalkForwardResult(
        np.arange(3), np.zeros((3, 3, 3, 3)), np.zeros(3, dtype=int), [{"fold": 0}]
    )
    meta = {"fingerprint": range_model.panel_fingerprint(panel), "settings": range_model.training_settings()}
    range_model.save_walk_forward(result, panel, meta)
    assert range_model.load_walk_forward(panel).folds == [{"fold": 0}]
    other_code = {**meta, "settings": {**meta["settings"], "code_sha256": "0" * 16}}
    range_model.save_walk_forward(result, panel, other_code)
    assert range_model.load_walk_forward(panel) is None
    range_model.save_walk_forward(result, panel, {**meta, "fingerprint": "other bars"})
    assert range_model.load_walk_forward(panel) is None


def test_saved_model_round_trips(fast_fits):
    panel = _toy_panel()
    model = range_model.fit_production(panel)
    assert model.meta["train_end"] < load_pivot_config().holdout_start_utc.isoformat()
    assert "pivot.yaml" in model.meta["config_sha256"] and model.meta["features"] == feature_columns()
    assert model.meta["code_sha256"] == range_model.code_sha256()
    save_model(model, "NSE", "1D")
    loaded = load_model("NSE", "1D")
    assert loaded is not None and loaded.version == model.meta["version"]
    X = panel.X[np.flatnonzero(panel.valid)[:50]]
    np.testing.assert_allclose(loaded.predict(X), model.predict(X), rtol=1e-6)
    assert load_model("NSE", "5m") is None


# ---------------------------------------------------------------- baselines and metrics


def test_analog_replay_matches_make_forecast():
    """The vectorised analog_v1 replay draws exactly the bands make_forecast draws on the cut series."""
    df = synthetic_candles("1D", "2016-01-01", "2020-06-30", seed=31)
    atr = atr_fn(df, 14).to_numpy()
    positions = np.array([400, 650, 900, len(df) - 5])
    bands, ghosts, drew = analog_bands_for_series("NSE:RELIANCE", "1D", df, positions, 3)
    cal = get_calendar()
    for k, i in enumerate(positions):
        cut = df.iloc[: i + 1]
        now = cal.bar_close_time("NSE", cut["ts"].iloc[-1], "1D") + pd.Timedelta(seconds=1)
        fc = make_forecast("NSE:RELIANCE", "1D", cut, None, now=now, check_stale=False, bucket_gate=False)
        assert drew[k] == bool(fc.bands)
        if fc.bands:
            ref = fc.ref_close
            expected = np.array(
                [[(b.p10 - ref) / atr[i], (b.p50 - ref) / atr[i], (b.p90 - ref) / atr[i]] for b in fc.bands]
            )
            np.testing.assert_allclose(bands[k], expected, rtol=1e-9, atol=1e-9)
            g = np.array(
                [[(c.open - ref), (c.high - ref), (c.low - ref), (c.close - ref)] for c in fc.ghost_candles]
            )
            np.testing.assert_allclose(ghosts[k], g / atr[i], rtol=1e-9, atol=1e-9)
    assert drew.any()


def test_scores_known_values():
    pred = np.zeros((2, 1, 3, 3))
    pred[:, 0, 2] = [-1.0, 0.0, 1.0]  # close band
    pred[:, 0, 0] = [0.2, 0.6, 1.0]
    pred[:, 0, 1] = [-1.0, -0.6, -0.2]
    out = MethodOutput(np.arange(2), pred, ghost_ohlc(pred))
    Y = np.array([[[0.5, -0.5, 0.1]], [[2.0, -0.1, 1.5]]])  # a "same" bar, then a close above the band
    s = row_scores(out, Y, (0.1, 0.5, 0.9), 0.2, 0.25)
    np.testing.assert_allclose(s["winkler"][:, 0], [2.0, 2.0 + 10 * 0.5])
    assert s["same"][:, 0].tolist() == [True, False] and s["wrong"][:, 0].tolist() == [False, True]
    np.testing.assert_allclose(s["pinball_close"][0, 0], np.mean([0.1 * 1.1, 0.5 * 0.1, 0.1 * 0.9]))
    np.testing.assert_allclose(s["iou"][0, 0], 1.0 / 1.2)  # ghost [-0.6, 0.6] vs actual [-0.5, 0.5]
    summary = summarise(s, has_high_low=True)
    assert summary["coverage_80"] == 0.5
    assert sum(summary["categories"].values()) == pytest.approx(1.0)
    assert summary["categories"] == {"same": 0.5, "close": 0.0, "wrong": 0.5}


def test_interval_iou_and_pinball_edges():
    np.testing.assert_allclose(
        interval_iou(
            np.array([0.0, 0.0, 1.0]),
            np.array([1.0, 1.0, 1.0]),
            np.array([2.0, 0.0, 1.0]),
            np.array([3.0, 1.0, 1.0]),
        ),
        [0, 1, 1],
    )
    assert pinball(np.array([[0.0, 0.0, 0.0]]), np.array([1.0]), (0.1, 0.5, 0.9))[0] == pytest.approx(0.5)


def test_go_no_go_2_rule():
    def result(tf, ok):
        return {"exchange": "NSE", "tf": tf, "period": "validation", "gate": {"pass": ok}}

    both = go_no_go_2([result("5m", True), result("15m", True), result("1h", False), result("1D", False)])
    assert not both["pass"] and both["n_pass"] == 2 and not both["required_any_passes"]
    good = go_no_go_2([result("5m", True), result("15m", False), result("1h", False), result("1D", True)])
    assert good["pass"]
    lone = go_no_go_2([result("1D", True), {**result("1h", True), "exchange": "BSE"}])
    assert not lone["pass"] and lone["missing"] == ["5m", "15m", "1h"]


# ---------------------------------------------------------------- evaluation end to end


def test_evaluate_range_holdout_is_locked():
    with pytest.raises(PermissionError):
        evaluate_range("1D", "NSE", period="holdout")


def test_evaluate_range_end_to_end(fast_fits):
    seen = []
    load = locked_loader(_frames(), seen)
    result = evaluate_range("1D", "NSE", load=load, instruments=["NSE:RELIANCE", "NSE:TCS"])
    p = load_pivot_config()
    assert all(end == p.holdout_start_utc for _, _, end in seen)
    json.dumps(result)
    assert result["period"] == "validation" and result["in_go_no_go_slice"]
    assert set(result["methods"]) == {"range_v1", "atr_bands", "rolling_quantiles", "analog_v1"}
    assert set(result["comparisons"]) == {"atr_bands", "rolling_quantiles", "analog_v1"}
    assert result["n_forecasts"] > 3000 and len(result["by_fold"]) == 14
    for name, m in result["methods"].items():
        assert 0 <= m["coverage_80"] <= 1 and m["winkler"] > 0, name
        assert sum(m["categories"].values()) == pytest.approx(1.0)
        assert m["categories"]["wrong"] == pytest.approx(1 - m["coverage_80"])
    assert result["methods"]["analog_v1"]["pinball_all"] is None
    assert result["methods"]["range_v1"]["pinball_all"] is not None
    for c in result["comparisons"].values():
        assert c["ci"][0] < c["ci"][1] and c["n"] > 0 and c["n_dates"] > 100
        assert c["ci_month_blocks"][0] < c["ci_month_blocks"][1] and 50 < c["n_months"] < c["n_dates"]
    worst = min(result["comparisons"], key=lambda k: result["comparisons"][k]["improvement"])
    assert result["best_baseline"] == worst
    per = {e["instrument"]: e for e in result["by_instrument"]}
    assert set(per) == {"NSE:RELIANCE", "NSE:TCS"}
    assert sum(e["n"] for e in per.values()) == result["n_forecasts"]
    assert all(e["improvement_vs_best"] is not None for e in per.values())
    coverages = [e["coverage_80"] for e in per.values()]
    assert min(coverages) <= result["methods"]["range_v1"]["coverage_80"] <= max(coverages)
    gate = result["gate"]
    assert set(gate["checks"]) == {"coverage", "improvement", "ci_lower"}
    assert gate["pass"] == all(gate["checks"].values())
    # atr_bands is refitted per window: its close band is symmetric around the reference close
    ks = result["methods"]["atr_bands"]["details"]["k_by_fold"]
    assert len(ks) == 14 and all(k > 0 for fold in ks.values() for k in fold)
    report = {
        "generated_at": "2026-09-24T00:00:00+00:00",
        "go_no_go_2": go_no_go_2([result]),
        "multiple_testing": range_eval.multiple_testing([result]),
        "pass_rule": p.range_model.gate.rule,
        "bootstrap_resamples": 2000,
        "same_close_atr": 0.25,
        "notes": range_eval.NOTES,
        "failures": [{"exchange": "MCX", "tf": "5m", "error": "OSError()"}],
        "results": [result],
    }
    assert report["multiple_testing"]["n_comparisons_gated"] == 3
    text = range_eval.render_markdown(report)
    assert "| NSE | 1D |" in text and "**Go/no-go #2 (range, NSE): FAIL**" in text  # 1 of 4 at most
    assert "MCX 5m could not be evaluated" in text
    assert "| NSE | 1D | NSE:TCS |" in text and "3 range_v1-vs-baseline comparisons" in text


def test_official_holdout_run_scores_the_saved_model_on_holdout_bars_only(fast_fits):
    frames = _frames()
    ids = ["NSE:RELIANCE", "NSE:TCS"]
    p = load_pivot_config()
    panel = build_panel("NSE", "1D", load=locked_loader(frames), instruments=ids)
    save_model(range_model.fit_production(panel), "NSE", "1D")
    with pytest.raises(PermissionError):
        evaluate_range("1D", "NSE", "holdout", load=locked_loader(frames), instruments=ids)
    result = evaluate_range(
        "1D", "NSE", "holdout", allow_holdout=True, load=locked_loader(frames), instruments=ids
    )
    assert result["period"] == "holdout" and result["start"] == p.holdout_start_utc.isoformat()
    holdout_bars = sum(
        int(((f["ts"] >= p.holdout_start_utc)).sum()) - p.range_model.steps
        for (iid, _), f in frames.items()
        if iid in ids
    )
    assert result["n_forecasts"] == holdout_bars and result["training_folds"] == []
    assert set(result["comparisons"]) == {"atr_bands", "rolling_quantiles", "analog_v1"}
    assert 0 < result["methods"]["range_v1"]["coverage_80"] < 1
    json.dumps(result)


def test_rolling_quantiles_use_only_closed_outcomes():
    panel = _toy_panel()
    rows = np.flatnonzero(panel.valid)[-50:]
    out = range_eval.rolling_quantiles_output(panel, rows, (0.1, 0.5, 0.9))
    i = rows[-1]
    s = 2
    known = panel.Y[i - s - range_eval.ROLLING_WINDOW + 1 : i - s + 1, s - 1, 2].astype(float)
    np.testing.assert_allclose(out.pred[-1, s - 1, 2], np.quantile(known, [0.1, 0.5, 0.9]), rtol=1e-6)
