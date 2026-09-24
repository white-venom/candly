import dataclasses
import json

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import reload_calendar
from candly.core.schema import validate_candles
from candly.core.settings import get_settings
from candly.research import vol2_eval as ev
from candly.research import vol2_model as vm
from candly.research.causality import alter_future
from candly.research.config import ist_midnight_utc
from candly.research.edge_config import Bootstrap
from candly.research.synthetic import bar_times

H = 5
BOOT = Bootstrap(kind="date_block", resamples=300, ci_level=0.95, seed=11)
CUTS = (40, 130, 260)


def market(seed: int, start: str, end: str, vix_kind: str, premium: float = 1.2):
    """Daily and 5m NIFTY bars from a persistent daily volatility, and a VIX that is either an oracle of the
    next-H realised variance (`oracle`) or a stale, noisy reading of past volatility (`noisy`)."""
    days = bar_times("1D", start, end)
    bars_ts = bar_times("5m", start, end)
    n = len(days)
    rng = np.random.default_rng(seed)
    x, eps = np.zeros(n), rng.normal(0.0, 0.15, n)
    for i in range(1, n):
        x[i] = 0.97 * x[i - 1] + eps[i]
    sigma = 0.01 * np.exp(x)
    per_day = pd.Series(bars_ts).groupby(pd.Series(bars_ts).dt.tz_convert("Asia/Kolkata").dt.date).size()
    k = per_day.to_numpy()
    day_of_bar = np.repeat(np.arange(n), k)
    first_bar = np.r_[0, np.cumsum(k)[:-1]]
    r = rng.normal(size=len(bars_ts)) * sigma[day_of_bar] * np.sqrt(0.8 / k[day_of_bar])
    gap = rng.normal(size=n) * sigma * np.sqrt(0.2)
    gap[0] = 0.0
    steps = r.copy()
    steps[first_bar] += gap
    log_close = np.log(10_000.0) + np.cumsum(steps)
    log_open = log_close - r
    wick = np.abs(rng.normal(size=(2, len(r)))) * 0.2 * sigma[day_of_bar] / np.sqrt(k[day_of_bar])
    o, c = np.exp(log_open), np.exp(log_close)
    bars = validate_candles(
        pd.DataFrame(
            {
                "ts": bars_ts,
                "open": o,
                "high": np.maximum(o, c) * np.exp(wick[0]),
                "low": np.minimum(o, c) * np.exp(-wick[1]),
                "close": c,
                "volume": 1.0,
                "oi": np.nan,
            }
        )
    )
    last_bar = np.cumsum(k) - 1
    daily = validate_candles(
        pd.DataFrame(
            {
                "ts": days,
                "open": o[first_bar],
                "high": pd.Series(bars["high"].to_numpy()).groupby(day_of_bar).max().to_numpy(),
                "low": pd.Series(bars["low"].to_numpy()).groupby(day_of_bar).min().to_numpy(),
                "close": c[last_bar],
                "volume": 1.0,
                "oi": np.nan,
            }
        )
    )
    if vix_kind == "oracle":
        level = premium * 100 * np.sqrt(252 * pd.Series(sigma**2).rolling(H).mean().shift(-H).to_numpy())
    else:
        stale = pd.Series(sigma**2).shift(15).to_numpy()
        level = premium * 100 * np.sqrt(252 * stale) * np.exp(rng.normal(0.0, 0.35, n))
    keep = np.isfinite(level)
    vix = validate_candles(
        pd.DataFrame(
            {
                "ts": days[keep],
                "open": level[keep],
                "high": level[keep],
                "low": level[keep],
                "close": level[keep],
                "volume": 0.0,
                "oi": np.nan,
            }
        )
    )
    return daily, bars, vix


def small_spec(**kw) -> vm.Vol2Spec:
    base = dict(
        name="vol_v2",
        underlying="NSE:NIFTY50",
        implied="NSE:INDIAVIX",
        horizons_days=(H,),
        train_start=ist_midnight_utc(pd.Timestamp("2012-01-01").date()),
        validation_start=ist_midnight_utc(pd.Timestamp("2014-01-01").date()),
        holdout_start=ist_midnight_utc(pd.Timestamp("2016-01-01").date()),
        qlike_ci_lower_above=0.0,
        sharpe_ci_lower_above=0.0,
        caveat="variance-swap proxy only",
        raw={},
    )
    return vm.Vol2Spec(**{**base, **kw})


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    """Built once, on a calendar without the real data dir's observed holidays."""
    out = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(tmp_path_factory.mktemp("data")))
        get_settings.cache_clear()
        reload_calendar()
        spec = small_spec()
        for kind in ("noisy", "oracle"):
            daily, bars, vix = market(41, "2012-01-01", "2015-12-31", kind)
            frame = vm.build_frame(daily, bars, vix, spec.horizons_days)
            out[kind] = (daily, bars, vix, frame, ev.evaluate_horizon(frame, spec, H, None, BOOT, 0.004))
        out["short"] = market(42, "2014-01-01", "2014-12-31", "noisy")
    get_settings.cache_clear()
    reload_calendar()
    return spec, out


# --- inputs ------------------------------------------------------------------------------------------------


def _truncate(daily, bars, vix, cut):
    ts_cut = daily["ts"].iloc[cut]
    session_end = ts_cut + pd.Timedelta(hours=7)
    return (
        daily.iloc[: cut + 1].copy(),
        bars[bars["ts"] < session_end].reset_index(drop=True),
        vix[vix["ts"] <= ts_cut].reset_index(drop=True),
    )


def _alter(daily, bars, vix, cut):
    ts_cut = daily["ts"].iloc[cut]
    b_cut = int(np.flatnonzero(bars["ts"] < ts_cut + pd.Timedelta(hours=7))[-1])
    v_cut = int(np.flatnonzero(vix["ts"] <= ts_cut)[-1])
    return (
        alter_future(daily, cut, seed=3),
        alter_future(bars, b_cut, seed=4),
        alter_future(vix, v_cut, seed=5),
    )


@pytest.mark.parametrize("column", [*vm.INPUT_COLUMNS, *vm.LOG_COLUMNS])
def test_each_input_is_causal(worlds, column):
    _, out = worlds
    daily, bars, vix = out["short"]
    full = vm.vol2_inputs(daily, bars, vix)[column]
    assert full.iloc[list(CUTS)].notna().all()
    for cut in CUTS:
        for label, parts in (
            ("truncated", _truncate(daily, bars, vix, cut)),
            ("altered", _alter(daily, bars, vix, cut)),
        ):
            got = vm.vol2_inputs(*parts)[column].iloc[: cut + 1]
            pd.testing.assert_series_equal(got, full.iloc[: cut + 1], obj=f"{column} {label} cut={cut}")


def test_the_check_catches_a_leaky_input(worlds):
    _, out = worlds
    daily, bars, vix = out["short"]
    leaky = vm.vol2_inputs(daily, bars, vix)["rv5m"].shift(-1)
    cut = CUTS[0]
    parts = _alter(daily, bars, vix, cut)
    got = vm.vol2_inputs(*parts)["rv5m"].shift(-1).iloc[: cut + 1]
    assert not np.allclose(got, leaky.iloc[: cut + 1], equal_nan=True)


def test_known_values(worlds):
    _, out = worlds
    daily, bars, vix = out["short"]
    x = vm.vol2_inputs(daily, bars, vix)
    i = 100
    day = daily["ts"].iloc[i].tz_convert("Asia/Kolkata").date()
    s = bars[bars["ts"].dt.tz_convert("Asia/Kolkata").dt.date == day]
    closes = s["close"].to_numpy()
    returns = np.r_[np.log(closes[0] / s["open"].iloc[0]), np.diff(np.log(closes))]
    assert len(s) == 75
    assert x["rv5m"].iloc[i] == pytest.approx(252 * (returns**2).sum())
    assert x["rv5m_w"].iloc[i] == pytest.approx(x["rv5m"].iloc[i - 4 : i + 1].mean())
    assert x["rv5m_m"].iloc[i] == pytest.approx(x["rv5m"].iloc[i - 21 : i + 1].mean())
    g = np.log(daily["open"].iloc[i] / daily["close"].iloc[i - 1])
    assert x["on2"].iloc[i] == pytest.approx(252 * g**2)
    assert x["log_on2"].iloc[i] == pytest.approx(np.log(252 * max(g**2, vm.OVERNIGHT_FLOOR**2)))
    vix_close = vix.set_index("ts")["close"].loc[daily["ts"].iloc[i]]
    assert x["vix_var"].iloc[i] == pytest.approx((vix_close / 100) ** 2)


def test_short_sessions_and_zero_gaps():
    ts = pd.date_range("2024-01-02 03:45", periods=3, freq="5min", tz="UTC")
    bars = pd.DataFrame(
        {
            "ts": ts,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": [100.5, 100.0, 100.2],
            "volume": 1.0,
            "oi": np.nan,
        }
    )
    sessions = vm.session_realized_variance(bars)
    assert sessions["n_5m_bars"].iloc[0] == 3 and np.isnan(sessions["rv5m"].iloc[0])
    daily = pd.DataFrame(
        {
            "ts": pd.to_datetime(["2024-01-01 03:45", "2024-01-02 03:45"], utc=True),
            "open": [100.0, 100.0],
            "high": 101.0,
            "low": 99.0,
            "close": [100.0, 100.2],
            "volume": 1.0,
            "oi": np.nan,
        }
    )
    x = vm.vol2_inputs(daily, bars, None)
    assert x["on2"].iloc[1] == 0.0
    assert x["log_on2"].iloc[1] == pytest.approx(np.log(252 * vm.OVERNIGHT_FLOOR**2))


# --- fits ---------------------------------------------------------------------------------------------------


def test_log_ols_recovers_coefficients_and_smears():
    rng = np.random.default_rng(0)
    n = 4000
    a, b = rng.normal(size=n), rng.normal(size=n)
    resid = rng.normal(0.0, 0.5, n)
    rows = pd.DataFrame({"a": a, "b": b, "y_1": np.exp(0.3 + 0.8 * a - 0.4 * b + resid)})
    fit = vm.fit_log_ols(rows, ("a", "b"), 1)
    assert fit.coef == pytest.approx([0.3, 0.8, -0.4], abs=0.03)
    fitted_resid = np.log(rows["y_1"]) - (fit.coef[0] + rows[["a", "b"]].to_numpy() @ fit.coef[1:])
    assert fit.smear == pytest.approx(np.mean(np.exp(fitted_resid)))
    assert fit.smear == pytest.approx(np.exp(0.5**2 / 2), rel=0.05)
    assert fit.predict(rows.iloc[:1])[0] == pytest.approx(
        np.exp(fit.coef[0] + fit.coef[1] * a[0] + fit.coef[2] * b[0]) * fit.smear
    )
    assert fit.hac_se[1] == pytest.approx(0.5 / np.sqrt(n), rel=0.15)


def test_newey_west_with_no_lags_is_the_white_covariance():
    rng = np.random.default_rng(1)
    X = np.column_stack([np.ones(50), rng.normal(size=50)])
    e = rng.normal(size=50)
    bread = np.linalg.inv(X.T @ X)
    white = bread @ (X.T * e**2) @ X @ bread
    assert vm.newey_west_cov(X, e, 0) == pytest.approx(white)


def test_training_targets_end_before_validation_and_holdout_is_never_scored(worlds):
    spec, out = worlds
    frame, result = out["noisy"][3], out["noisy"][4]
    train = frame[vm.train_mask(frame, spec, H)]
    assert (train[f"end_ts_{H}"] < spec.validation_start).all()
    assert train["ts"].min() >= spec.train_start
    preds = result["predictions"]
    assert preds["ts"].min() >= spec.validation_start
    assert preds["ts"].max() < spec.holdout_start
    assert pd.Timestamp(result["fits"]["last_label_end_ts"]) < spec.validation_start


def test_load_inputs_drops_holdout_bars_before_anything(worlds):
    spec, out = worlds
    daily, bars, vix = out["short"]
    late = small_spec(holdout_start=daily["ts"].iloc[150])
    series = {("NSE:NIFTY50", "1D"): daily, ("NSE:NIFTY50", "5m"): bars, ("NSE:INDIAVIX", "1D"): vix}
    got = vm.load_inputs(late, load=lambda inst, tf, start=None, end=None: series[(inst, tf)])
    assert all((df["ts"] < late.holdout_start).all() for df in got)
    frame = vm.build_frame(daily, bars, vix, (H,))
    with pytest.raises(PermissionError):
        ev.evaluate_horizon(frame, late, H, None, BOOT, 0.004)


# --- evaluation --------------------------------------------------------------------------------------------


def test_a_stale_noisy_vix_is_beaten(worlds):
    _, out = worlds
    result = out["noisy"][4]
    gate = result["gates"]["qlike_improvement_vs_recalibrated_vix"]
    assert gate["value"] > 0 and gate["pass"]
    assert gate["p_one_sided"] == max(s["p_one_sided"] for s in gate["by_block_scheme"].values())
    assert result["primary_p"] == gate["p_one_sided"] < 0.05
    fits = result["fits"]
    coef = fits[vm.MODEL]["coef"]
    assert coef["log_rv5m_d"] + coef["log_rv5m_w"] + coef["log_rv5m_m"] > 0.5
    assert set(result["qlike"]) == set(ev.FORECASTERS)
    assert result["qlike"]["vol_v1_model"]["n"] == 0


def test_an_oracle_vix_is_not_beaten(worlds):
    _, out = worlds
    gate = out["oracle"][4]["gates"]["qlike_improvement_vs_recalibrated_vix"]
    assert not gate["pass"]
    assert gate["p_one_sided"] > 0.05


def test_p_value_formula_and_conservative_gate():
    assert ev.p_one_sided(np.array([-1.0, 0.0, 1.0, 2.0])) == pytest.approx(3 / 5)
    assert ev.p_one_sided(np.array([1.0, 2.0])) == pytest.approx(1 / 3)
    schemes = {"a": {"ci": [0.1, 0.3], "p_one_sided": 0.01}, "b": {"ci": [-0.01, 0.3], "p_one_sided": 0.04}}
    gate = ev._conservative(schemes, 0.0)
    assert gate["p_one_sided"] == 0.04 and not gate["pass"]


def test_conditional_equals_always_short_when_vix_always_exceeds_the_forecast():
    n = 60
    rng = np.random.default_rng(3)
    rows = pd.DataFrame(
        {
            "ts": pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC"),
            "day_idx": np.arange(n),
            "vix_var": 0.04 * rng.uniform(0.8, 1.2, n),
            "y": 0.03 * rng.lognormal(0, 0.5, n),
            vm.MODEL: np.full(n, 0.01),
        }
    )
    pnl = ev.variance_swap_section(rows, H, 0, 0.004, BOOT)
    assert pnl["n_periods"] == 12 and pnl["conditional"]["n_short"] == 12
    assert pnl["sharpe_diff"] == pytest.approx(0.0)
    assert pnl["1_period_blocks"]["ci"] == pytest.approx([0.0, 0.0])
    assert ev.conditional_short([0.04, 0.04], [0.03, 0.05]).tolist() == [1, 0]


def test_report_json_and_markdown(worlds, tmp_path):
    spec, out = worlds
    spec = dataclasses.replace(spec, raw={"name": "vol_v2"})
    report = ev.validation_report({H: out["noisy"][4]}, spec, 1.0)
    assert report["edge_search_v2_sha256"] and report["split"]["holdout_used"] is False
    assert report["primary_p"][str(H)] == out["noisy"][4]["primary_p"]
    assert report["deviations"] and report["caveats"][0] == spec.caveat
    json_path, md_path = ev.write_report(report, tmp_path / "vol2.json")
    assert json.loads(json_path.read_text(encoding="utf-8"))["test"] == "vol_v2"
    assert f"## {H}-day horizon" in md_path.read_text(encoding="utf-8")


def test_registered_spec_is_read_from_the_v2_file():
    spec = vm.load_spec()
    assert spec.name == "vol_v2" and spec.horizons_days == (5, 20)
    assert spec.train_start < spec.validation_start < spec.holdout_start
    assert spec.qlike_ci_lower_above == 0.0 and spec.sharpe_ci_lower_above == 0.0
