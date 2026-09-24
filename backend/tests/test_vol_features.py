from datetime import date

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST, reload_calendar
from candly.core.settings import get_settings
from candly.research.causality import alter_future, check_causal
from candly.research.synthetic import synthetic_candles
from candly.research.vol_model import (
    BASELINE_INPUTS,
    FEATURE_COLUMNS,
    build_frame,
    forecast_inputs,
    forward_realized_variance,
    training_mask,
    vol_features,
)

START, END = "2014-01-01", "2016-12-31"
NIFTY = "NSE:NIFTY50"
CUTS = (300, 420, 600)
INPUT_COLUMNS = (*FEATURE_COLUMNS, *BASELINE_INPUTS)


@pytest.fixture(scope="module")
def frames(tmp_path_factory):
    """Built once, on a calendar without the real data dir's observed holidays."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(tmp_path_factory.mktemp("data")))
        get_settings.cache_clear()
        reload_calendar()
        nifty = synthetic_candles("1D", START, END, seed=21, price=8_000.0, vol=0.012)
        vix = synthetic_candles("1D", START, END, seed=22, price=15.0, vol=0.05)
    get_settings.cache_clear()
    reload_calendar()
    return nifty, vix


@pytest.mark.parametrize("column", INPUT_COLUMNS)
def test_each_input_ignores_the_underlyings_future(frames, column):
    nifty, vix = frames
    values = forecast_inputs(nifty, vix, NIFTY)[column]
    assert values.iloc[list(CUTS)].notna().all()
    check_causal(lambda d: forecast_inputs(d, vix, NIFTY)[[column]], nifty, CUTS)


@pytest.mark.parametrize("cut", CUTS)
def test_future_vix_bars_never_reach_the_past(frames, cut):
    nifty, vix = frames
    full = forecast_inputs(nifty, vix, NIFTY)
    v_cut = int(np.flatnonzero(vix["ts"] <= nifty["ts"].iloc[cut])[-1])
    for label, v in (("truncated", vix.iloc[: v_cut + 1]), ("altered", alter_future(vix, v_cut, seed=5))):
        got = forecast_inputs(nifty, v, NIFTY).iloc[: cut + 1]
        pd.testing.assert_frame_equal(got, full.iloc[: cut + 1], obj=label)


def test_the_truncation_check_catches_the_forward_target(frames):
    nifty, _ = frames
    with pytest.raises(AssertionError, match="lookahead"):
        check_causal(lambda d: forward_realized_variance(d, 5).to_frame("y"), nifty, CUTS)


def test_known_values(frames):
    nifty, vix = frames
    x = forecast_inputs(nifty, vix, NIFTY)
    o, h, lo, c = (nifty[k].to_numpy() for k in ("open", "high", "low", "close"))
    r = np.r_[np.nan, np.diff(np.log(c))]
    i = 500
    assert x["rv1"].iloc[i] == pytest.approx(100 * np.sqrt(252) * abs(r[i]))
    assert x["rv20"].iloc[i] == pytest.approx(100 * np.sqrt(252 * np.mean(r[i - 19 : i + 1] ** 2)))
    park = np.log(h / lo) ** 2 / (4 * np.log(2))
    assert x["park5"].iloc[i] == pytest.approx(100 * np.sqrt(252 * park[i - 4 : i + 1].mean()))
    assert x["gap1"].iloc[i] == pytest.approx(100 * np.log(o[i] / c[i - 1]))
    assert x["ret5"].iloc[i] == pytest.approx(100 * np.log(c[i] / c[i - 5]))
    assert x["har_w"].iloc[i] == pytest.approx(252 * np.mean(r[i - 4 : i + 1] ** 2))
    assert x["har_m"].iloc[i] == pytest.approx(252 * np.mean(r[i - 21 : i + 1] ** 2))
    assert x["offset_var"].iloc[i] == pytest.approx((x["rv20"].iloc[i] / 100) ** 2)
    assert (nifty["ts"] == vix["ts"]).all()
    v = vix["close"].to_numpy()
    assert x["vix"].iloc[i] == v[i]
    assert x["vix_chg5"].iloc[i] == pytest.approx(100 * np.log(v[i] / v[i - 5]))
    assert x["vix_var"].iloc[i] == pytest.approx((v[i] / 100) ** 2)
    assert x["vix_rv20_spread"].iloc[i] == pytest.approx(v[i] - x["rv20"].iloc[i])
    assert x["day_of_week"].iloc[i] == nifty["ts"].iloc[i].tz_convert(IST).weekday()


def test_ewma_follows_the_riskmetrics_recursion(frames):
    nifty, vix = frames
    ewma = forecast_inputs(nifty, vix, NIFTY)["ewma_var"].to_numpy()
    r2 = np.diff(np.log(nifty["close"].to_numpy())) ** 2
    s = r2[0]
    assert np.isnan(ewma[0]) and ewma[1] == pytest.approx(252 * s)
    for t in range(1, 50):
        s = 0.94 * s + 0.06 * r2[t]
    assert ewma[50] == pytest.approx(252 * s)


def test_forward_target_is_the_annualised_sum_of_the_next_h_squared_returns(frames):
    nifty, _ = frames
    r2 = np.r_[np.nan, np.diff(np.log(nifty["close"].to_numpy()))] ** 2
    for h in (5, 20):
        y = forward_realized_variance(nifty, h).to_numpy()
        assert y[100] == pytest.approx(252 / h * r2[101 : 101 + h].sum())
        assert np.isnan(y[-h:]).all() and np.isfinite(y[-h - 1])


def test_a_date_without_a_vix_print_has_no_vix_features_and_is_not_usable(frames):
    nifty, vix = frames
    frame = build_frame(nifty, vix.drop(index=400), (5,), NIFTY)
    assert frame.loc[400, ["vix", "vix_var", "vix_chg1", "vix_rv20_spread"]].isna().all()
    assert not frame.loc[400, "features_ok"]
    assert frame.loc[[399, 401], "features_ok"].all()
    assert frame.loc[401, "vix_chg1"] == pytest.approx(100 * np.log(vix["close"][401] / vix["close"][399]))


def test_expiry_flags_follow_the_published_schedule():
    ts = pd.Series(
        pd.to_datetime(["2015-01-20", "2015-01-27", "2015-01-29", "2015-01-30", "2020-02-24"])
        .tz_localize(IST)
        .tz_convert("UTC")
        + pd.Timedelta(hours=3, minutes=45)
    )
    df = pd.DataFrame(
        {"ts": ts, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0, "oi": np.nan}
    )
    x = vol_features(df, None, NIFTY)
    # 2015-01-29: last-Thursday monthly expiry; 2020-02-27 was both a weekly and the monthly expiry.
    assert x["monthly_expiry_week"].tolist() == [0.0, 1.0, 1.0, 0.0, 1.0]
    assert x["expiry_day"].tolist() == [0.0, 0.0, 1.0, 0.0, 0.0]
    assert x["days_to_expiry"].iloc[2] == 0.0 and x["days_to_expiry"].iloc[1] == 2.0


def test_training_rows_are_purged_so_no_label_crosses_the_cut(frames):
    nifty, vix = frames
    frame = build_frame(nifty, vix, (5, 20), NIFTY)
    cut = pd.Timestamp(date(2016, 1, 1)).tz_localize(IST).tz_convert("UTC")
    n_before = int((frame["ts"] < cut).sum())
    for h in (5, 20):
        mask = training_mask(frame, cut, h)
        assert frame.loc[mask, "day_idx"].max() == n_before - h - 1
        assert (frame.loc[mask, f"end_ts_{h}"] < cut).all()
        assert frame.loc[mask, "features_ok"].all()
