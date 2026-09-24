import dataclasses
import json

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import reload_calendar
from candly.core.schema import validate_candles
from candly.core.settings import get_settings
from candly.research import vrp_eval as ev
from candly.research import vrp_model as vm
from candly.research.causality import alter_future, check_causal
from candly.research.config import ist_midnight_utc
from candly.research.edge_config import Bootstrap
from candly.research.edge_v2_config import edge_v2_section
from candly.research.synthetic import bar_times

H = 21
BOOT = Bootstrap(kind="date_block", resamples=300, ci_level=0.95, seed=13)
CUTS = (60, 300, 700)


def _date(s: str) -> pd.Timestamp:
    return ist_midnight_utc(pd.Timestamp(s).date())


def small_spec(**kw) -> vm.VrpSpec:
    base = dict(
        name="vrp_v1",
        underlying="NSE:NIFTY50",
        implied="NSE:INDIAVIX",
        horizons_days=(H,),
        train_start=_date("2009-01-01"),
        validation_start=_date("2013-01-01"),
        holdout_start=_date("2016-01-01"),
        clark_west_p_below=0.05,
        timing_rule="long when the forecast exceeds round-trip cost",
        raw={},
    )
    return vm.VrpSpec(**{**base, **kw})


def market(seed: int, beta: float, start: str = "2008-06-01", end: str = "2015-12-31"):
    """Daily NIFTY and VIX where VIX^2 carries a slow-moving premium p_t and the next H days' drift is
    beta * p_t per day (beta = 0: no predictability)."""
    ts = bar_times("1D", start, end)
    n = len(ts)
    rng = np.random.default_rng(seed)
    sigma = 0.012 * np.exp(0.2 * np.sin(np.arange(n) / 40.0))
    premium = np.zeros(n)
    for i in range(1, n):
        premium[i] = 0.99 * premium[i - 1] + rng.normal(0.0, 0.0003)
    drift = beta * np.r_[0.0, premium[:-1]]
    r = drift + sigma * rng.normal(size=n)
    close = 10_000.0 * np.exp(np.cumsum(r))
    open_ = close * np.exp(-0.3 * sigma * rng.normal(size=n))
    daily = validate_candles(
        pd.DataFrame(
            {
                "ts": ts,
                "open": open_,
                "high": np.maximum(open_, close) * 1.002,
                "low": np.minimum(open_, close) * 0.998,
                "close": close,
                "volume": 1.0,
                "oi": np.nan,
            }
        )
    )
    iv2_monthly = np.maximum(22 * sigma**2 + premium + 0.002, 1e-5)
    level = 100 * np.sqrt(12 * iv2_monthly)
    vix = validate_candles(
        pd.DataFrame(
            {
                "ts": ts,
                "open": level,
                "high": level,
                "low": level,
                "close": level,
                "volume": 0.0,
                "oi": np.nan,
            }
        )
    )
    return daily, vix


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    out = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(tmp_path_factory.mktemp("data")))
        get_settings.cache_clear()
        reload_calendar()
        spec = small_spec()
        for name, beta in (("predictable", 0.5), ("null", 0.0)):
            daily, vix = market(7, beta)
            frame = vm.build_frame(daily, vix, (H,))
            out[name] = (daily, vix, frame, ev.evaluate_horizon(frame, spec, H, BOOT, 0.001))
    get_settings.cache_clear()
    reload_calendar()
    return spec, out


# --- inputs ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("column", ["iv2", "rv22", "vrp"])
def test_each_input_ignores_the_future(worlds, column):
    _, out = worlds
    daily, vix = out["null"][:2]
    assert vm.vrp_inputs(daily, vix)[column].iloc[list(CUTS)].notna().all()
    check_causal(lambda d: vm.vrp_inputs(d, vix)[[column]], daily, CUTS)
    full = vm.vrp_inputs(daily, vix)[column]
    for cut in CUTS:
        for v in (vix.iloc[: cut + 1], alter_future(vix, cut, seed=2)):
            pd.testing.assert_series_equal(
                vm.vrp_inputs(daily, v)[column].iloc[: cut + 1], full.iloc[: cut + 1]
            )


def test_the_check_catches_the_target(worlds):
    _, out = worlds
    daily = out["null"][0]
    with pytest.raises(AssertionError, match="lookahead"):
        check_causal(lambda d: vm.forward_log_return(d, H).to_frame("y"), daily, CUTS)


def test_known_values(worlds):
    _, out = worlds
    daily, vix, frame = out["null"][:3]
    i = 500
    r = np.diff(np.log(daily["close"].to_numpy()))
    rv22 = (r[i - 22 : i] ** 2).sum()
    iv2 = (vix["close"].iloc[i] / 100) ** 2 / 12
    assert frame["rv22"].iloc[i] == pytest.approx(rv22)
    assert frame["vrp"].iloc[i] == pytest.approx(iv2 - rv22)
    assert frame[f"y_{H}"].iloc[i] == pytest.approx(
        np.log(daily["close"].iloc[i + H] / daily["close"].iloc[i])
    )


# --- walk-forward ---------------------------------------------------------------------------------


def test_every_fold_trains_on_labels_that_end_before_it(worlds):
    spec, out = worlds
    frame, result = out["null"][2], out["null"][3]
    assert len(result["folds"]) == 3
    for fold in result["folds"]:
        assert pd.Timestamp(fold["last_label_end_ts"]) < pd.Timestamp(fold["test_start"])
        assert pd.Timestamp(fold["first_train_ts"]) >= spec.train_start
        cut = pd.Timestamp(fold["test_start"])
        train = frame[vm.training_mask(frame, spec, cut, H)]
        assert fold["benchmark_mean"] == pytest.approx(train[f"y_{H}"].mean())
    preds = result["predictions"]
    assert preds["ts"].min() >= spec.validation_start and preds["ts"].max() < spec.holdout_start


def test_a_predictive_premium_is_detected(worlds):
    _, out = worlds
    result = out["predictable"][3]
    assert result["clark_west"]["p_one_sided"] < 0.01
    assert result["oos_r2"] > 0
    assert all(f["slope"] > 0 for f in result["folds"])


def test_no_premium_no_detection(worlds):
    _, out = worlds
    assert out["null"][3]["clark_west"]["p_one_sided"] > 0.05


def test_holdout_bars_are_dropped_and_refused(worlds):
    spec, out = worlds
    daily, vix, frame = out["null"][:3]
    late = small_spec(holdout_start=daily["ts"].iloc[-100])
    series = {"NSE:NIFTY50": daily, "NSE:INDIAVIX": vix}
    got = vm.load_inputs(late, load=lambda inst, tf, start=None, end=None: series[inst])
    assert all((df["ts"] < late.holdout_start).all() for df in got)
    with pytest.raises(PermissionError):
        ev.evaluate_horizon(frame, late, H, BOOT, 0.001)


# --- statistics -----------------------------------------------------------------------------------


def test_clark_west_known_values():
    y = np.array([0.02, -0.01, 0.03, 0.00, 0.01])
    b = np.full(5, 0.005)
    m = np.array([0.015, -0.005, 0.02, 0.004, 0.006])
    cw = ev.clark_west(y, b, m, lags=0)
    f = (y - b) ** 2 - ((y - m) ** 2 - (b - m) ** 2)
    se = np.sqrt(((f - f.mean()) ** 2).sum()) / 5
    assert cw["mean_adjusted_diff"] == pytest.approx(f.mean())
    assert cw["hac_se"] == pytest.approx(se)
    assert cw["t"] == pytest.approx(f.mean() / se)
    assert ev.oos_r2(y, b, m) == pytest.approx(1 - ((y - m) ** 2).sum() / ((y - b) ** 2).sum())


def test_newey_west_lags_widen_the_error_of_an_overlapping_series():
    rng = np.random.default_rng(5)
    e = pd.Series(rng.normal(size=3000)).rolling(20).sum().dropna().to_numpy()
    naive = ev.clark_west(e, np.zeros_like(e), np.zeros_like(e) + 1e-9, lags=0)
    hac = ev.clark_west(e, np.zeros_like(e), np.zeros_like(e) + 1e-9, lags=20)
    assert hac["hac_se"] > 2 * naive["hac_se"]


# --- timing ---------------------------------------------------------------------------------------


def test_futures_proxy_subtracts_calendar_day_carry():
    frame = pd.DataFrame(
        {
            "ts": pd.to_datetime(["2024-01-05", "2024-01-08", "2024-01-09"], utc=True),
            "close": [100.0, 101.0, 100.0],
        }
    )
    ret = ev.futures_returns(frame).to_numpy()
    assert np.isnan(ret[0])
    assert ret[1] == pytest.approx(0.01 - 0.05 * 3 / 365)
    assert ret[2] == pytest.approx(100 / 101 - 1 - 0.05 / 365)


def test_carry_is_the_registered_tsmom_carry():
    assert "5% a year" in edge_v2_section("tsmom")["index_futures_return"]
    assert ev.FUTURES_CARRY == 0.05


def test_strategy_costs_and_decisions():
    out = ev.strategy_returns(np.array([0, 1, 1, 0]), np.array([0.01, 0.02, -0.01, 0.03]), rt=0.002)
    assert out["gross"].tolist() == pytest.approx([0.0, 0.02, -0.01, 0.0])
    assert out["switch_cost"].tolist() == pytest.approx([0.0, 0.001, 0.0, 0.001])
    assert out["roll_cost"].tolist() == pytest.approx([0.0, 0.002 / 21, 0.002 / 21, 0.0])
    d = ev.decisions(6, [1, 2, 4], [0.01, -0.01, 0.02], threshold=0.0)
    assert d.tolist() == [0.0, 1.0, 0.0, 0.0, 1.0, 1.0]


def test_timing_uses_decisions_two_days_back(worlds):
    spec, out = worlds
    frame, result = out["predictable"][2], out["predictable"][3]
    t = result["timing"]
    first_pred = result["predictions"]["ts"].min()
    first_idx = int(np.flatnonzero(frame["ts"] == first_pred)[0])
    assert pd.Timestamp(t["first_day"]) == frame["ts"].iloc[first_idx + 2]
    assert t["buy_and_hold"]["share_long"] == 1.0
    reg = t["registered"]
    assert reg["sharpe_diff_vs_buy_and_hold"] == pytest.approx(
        reg["sharpe_net"] - t["buy_and_hold"]["sharpe_net"]
    )
    assert result["checks"]["timing_sharpe_after_cost_beats_buy_and_hold_point"]["pass"] == (
        reg["sharpe_diff_vs_buy_and_hold"] > 0
    )


def test_report_json_and_markdown(worlds, tmp_path):
    spec, out = worlds
    spec = dataclasses.replace(spec, raw={"name": "vrp_v1"})
    report = ev.validation_report({H: out["null"][3]}, {"train": {}}, spec, 1.0)
    assert report["primary_p"][str(H)] == out["null"][3]["clark_west"]["p_one_sided"]
    assert report["split"]["holdout_used"] is False and report["deviations"]
    json_path, md_path = ev.write_report(report, tmp_path / "vrp.json")
    assert json.loads(json_path.read_text(encoding="utf-8"))["test"] == "vrp_v1"
    assert f"## {H}-day horizon" in md_path.read_text(encoding="utf-8")


def test_registered_spec_is_read_from_the_v2_file():
    spec = vm.load_spec()
    assert spec.name == "vrp_v1" and spec.horizons_days == (21, 63)
    assert spec.clark_west_p_below == 0.05
    assert spec.train_start < spec.validation_start < spec.holdout_start
