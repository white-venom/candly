"""xs_v1 / pv2_xs research code: causal features, labels, schedule, walk-forward, portfolio, statistics."""

import dataclasses
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from candly.patterns import detect_patterns
from candly.research import xs_backtest as bt
from candly.research import xs_eval as ev
from candly.research import xs_model as xm
from candly.research import xs_panel as xp
from candly.research.causality import alter_future
from candly.research.edge_config import load_edge_config
from candly.research.edge_v2_config import edge_v2_section

MARKET = "NSE:NIFTY50"
IDS = [f"NSE:S{i:02d}" for i in range(24)]
INDUSTRY = {iid: ("banks" if i % 3 else "metals") for i, iid in enumerate(IDS)}
HORIZONS = (5, 20)


def business_bars(start: str, end: str, seed: int, price: float = 100.0, drop: float = 0.0) -> pd.DataFrame:
    """Random daily candles on weekdays at 09:15 IST; `drop` removes that share of bars (data gaps)."""
    ts = pd.bdate_range(start, end, tz="UTC") + pd.Timedelta(hours=3, minutes=45)
    rng = np.random.default_rng(seed)
    n = len(ts)
    r = rng.normal(0.0003, 0.015, n)
    close = price * np.exp(np.cumsum(r))
    open_ = close * np.exp(rng.normal(0.0, 0.006, n))
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.0, 0.015, n))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.0, 0.015, n))
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.round(1e5 * rng.lognormal(0.0, 0.5, n)),
            "oi": np.nan,
        }
    )
    if drop:
        keep = rng.uniform(size=n) >= drop
        keep[:5] = True
        df = df[keep].reset_index(drop=True)
    return df


def make_series(start="2018-01-01", end="2019-12-31") -> dict[str, pd.DataFrame]:
    series = {MARKET: business_bars(start, end, seed=999, price=10000.0)}
    for i, iid in enumerate(IDS):
        series[iid] = business_bars(
            start, end, seed=i, price=15.0 if i == 0 else 40.0 + 10 * i, drop=0.02 * (i % 3)
        )
    return series


def loader_for(series: dict[str, pd.DataFrame]):
    def load(instrument_id, tf, start=None, end=None):
        df = series[instrument_id]
        if end is not None:
            df = df[df["ts"] <= end]
        return df.reset_index(drop=True)

    return load


CUTOFF = pd.Timestamp("2025-10-01", tz="Asia/Kolkata").tz_convert("UTC")


def frame_of(series, min_price=20.0) -> pd.DataFrame:
    panel = xp.build_panel(IDS, INDUSTRY, CUTOFF, load=loader_for(series))
    return xp.panel_frame(panel, min_price, HORIZONS, with_patterns=True)


# --- causality: every feature, truncated and with an altered future ---------------------------------------

FEATURE_NAMES = [*xp.BASE_FEATURES, *xp.PATTERN_FEATURES]


@pytest.fixture(scope="module")
def truncation_runs():
    series = make_series()
    full = frame_of(series)
    market_ts = series[MARKET]["ts"]
    runs = []
    for cut in (300, 430):
        cut_ts = market_ts.iloc[cut]
        truncated = {k: v[v["ts"] <= cut_ts].reset_index(drop=True) for k, v in series.items()}
        altered = {}
        for k, v in series.items():
            pos = int((v["ts"] <= cut_ts).sum()) - 1
            altered[k] = alter_future(v, pos, seed=pos + 7)
        runs.append((cut, frame_of(truncated), frame_of(altered)))
    return full, runs


def _upto(frame: pd.DataFrame, cut: int) -> pd.DataFrame:
    return frame[frame["t"] <= cut].reset_index(drop=True)


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_feature_is_causal(truncation_runs, name):
    full, runs = truncation_runs
    for cut, truncated, altered in runs:
        expected = _upto(full, cut)
        for label, got in (("truncated", _upto(truncated, cut)), ("altered", _upto(altered, cut))):
            assert len(got) == len(expected), f"eligible rows differ at cut {cut} ({label})"
            np.testing.assert_array_equal(got["id"].to_numpy(), expected["id"].to_numpy())
            for col in (name, xp.rank_column(name)):
                np.testing.assert_allclose(
                    got[col].to_numpy(),
                    expected[col].to_numpy(),
                    rtol=1e-10,
                    atol=1e-12,
                    equal_nan=True,
                    err_msg=f"{col} changed at cut {cut} ({label})",
                )


def test_truncation_harness_catches_labels(truncation_runs):
    full, runs = truncation_runs
    cut, _, altered = runs[0]
    near = (full["t"] <= cut) & (full["t"] >= cut - 3)
    a = full.loc[near, xp.label_column(5)].to_numpy()
    b = altered.loc[(altered["t"] <= cut) & (altered["t"] >= cut - 3), xp.label_column(5)].to_numpy()
    assert not np.allclose(a, b, equal_nan=True)


def test_eligible_rows_have_the_baseline_signals_and_the_price_floor(truncation_runs):
    full, _ = truncation_runs
    assert full[list(xp.ELIGIBILITY_SIGNALS)].notna().all().all()
    assert full["t"].min() >= xp.YEAR_SESSIONS
    assert (full.groupby("t")["stock"].apply(lambda s: s.is_monotonic_increasing)).all()


# --- feature values -----------------------------------------------------------------------------------------


def _panel(series=None):
    series = series or make_series()
    return xp.build_panel(IDS, INDUSTRY, CUTOFF, load=loader_for(series)), series


def test_return_features_match_their_definitions():
    panel, _ = _panel()
    feats = xp.base_feature_matrices(panel)
    cf = panel.close.ffill()
    t, s = 400, IDS[0]
    assert feats["reversal_1w"][s].iloc[t] == pytest.approx(math.log(cf[s].iloc[t] / cf[s].iloc[t - 5]))
    assert feats["momentum_12_1"][s].iloc[t] == pytest.approx(
        math.log(cf[s].iloc[t - 21] / cf[s].iloc[t - 252])
    )
    daily = np.log(panel.close[s]).diff().iloc[t - 59 : t + 1]
    assert feats["volatility_60d"][s].iloc[t] == pytest.approx(daily.std() * math.sqrt(252))
    high = panel.high[s].iloc[t - 251 : t + 1].max()
    assert feats["distance_52w_high"][s].iloc[t] == pytest.approx(math.log(cf[s].iloc[t] / high))


def test_beta_recovers_a_known_slope():
    rng = np.random.default_rng(3)
    m = pd.Series(rng.normal(0, 0.01, 400))
    r = pd.DataFrame({"a": 2.0 * m + rng.normal(0, 1e-4, 400), "b": -0.5 * m})
    r.iloc[100:130, 0] = np.nan
    beta = xp.rolling_beta(r, m, 252, 200)
    assert beta["a"].iloc[-1] == pytest.approx(2.0, abs=0.02)
    assert beta["b"].iloc[-1] == pytest.approx(-0.5)
    assert beta["a"].iloc[:199].isna().all()


def test_sector_relative_return_is_demeaned_within_each_industry():
    ret = pd.DataFrame([[0.1, 0.3, -0.2, 0.0]], columns=list("wxyz"))
    eligible = pd.DataFrame([[True, True, True, False]], columns=list("wxyz"))
    industry = pd.Series({"w": "A", "x": "A", "y": "B", "z": "B"})
    out = xp.sector_relative(ret, eligible, industry).iloc[0]
    assert out["w"] == pytest.approx(-0.1) and out["x"] == pytest.approx(0.1)
    assert out["y"] == pytest.approx(0.0)
    assert out["z"] == pytest.approx(0.2)  # ineligible peers are not in the sector mean


def test_pattern_counts_match_the_detector():
    panel, series = _panel()
    feats = xp.pattern_feature_matrices(panel)
    iid = IDS[4]
    pats = detect_patterns(series[iid], "1D")
    bull_ts = pats.loc[(pats["state"] == "confirmed") & (pats["direction"] == "bullish"), "ts"]
    t = 350
    window = panel.dates[t - 4 : t + 1]
    assert feats["bullish_patterns_5"][iid].iloc[t] == bull_ts.isin(window).sum()
    window20 = panel.dates[t - 19 : t + 1]
    assert feats["bullish_patterns_20"][iid].iloc[t] == bull_ts.isin(window20).sum()


def test_panel_never_holds_holdout_bars():
    series = {k: business_bars("2025-01-01", "2025-12-31", seed=i) for i, k in enumerate([MARKET, *IDS])}
    panel = xp.build_panel(IDS, INDUSTRY, CUTOFF, load=loader_for(series))
    assert panel.dates.max() < CUTOFF
    assert (panel.close.index < CUTOFF).all()


# --- labels and schedule ------------------------------------------------------------------------------------


def test_forward_returns_are_next_open_to_open_and_mark_missing_exits_at_the_last_close():
    dates = pd.bdate_range("2020-01-06", periods=10, tz="UTC")
    opens = pd.DataFrame({"a": np.arange(10, 20, dtype=float)}, index=dates)
    close = opens + 0.5
    close.iloc[7] = np.nan
    opens.iloc[7] = np.nan
    panel = xp.Panel(
        dates=dates,
        ids=["a"],
        industry=pd.Series({"a": "x"}),
        open=opens,
        high=close + 1,
        low=opens - 1,
        close=close,
        volume=opens * 0 + 1,
        market_close=pd.Series(1.0, index=dates),
        bullish=None,
        bearish=None,
        missing=[],
        n_off_calendar=0,
    )
    fwd = xp.forward_returns(panel, 2)["a"]
    assert fwd.iloc[0] == pytest.approx(13 / 11 - 1)
    assert fwd.iloc[4] == pytest.approx(16.5 / 15 - 1)  # exit day 7 has no bar: last close (day 6)
    assert fwd.iloc[7:].isna().all()


def test_rebalance_periods_enter_on_first_trading_days_and_drop_an_open_end():
    dates = pd.DatetimeIndex(
        [d for d in pd.bdate_range("2021-12-27", "2022-03-04", tz="UTC") if d.day != 3 or d.month != 1]
    ) + pd.Timedelta(hours=3, minutes=45)
    start = pd.Timestamp("2022-01-01", tz="UTC")
    weekly = xp.rebalance_periods(dates, "weekly_first_trading_day", start, dates[-1] + pd.Timedelta(days=1))
    first = weekly.iloc[0]
    assert dates[first["entry"]].date() == date(2022, 1, 4)  # Monday 3 Jan is missing: Tuesday opens the week
    assert first["signal"] == first["entry"] - 1
    assert (weekly["exit"].to_numpy()[:-1] == weekly["entry"].to_numpy()[1:]).all()
    assert dates[weekly["exit"].iloc[-1]].date() == date(2022, 2, 28)  # the last week has no exit bar
    monthly = xp.rebalance_periods(dates, "monthly_first_trading_day", start, dates[-1])
    assert [dates[e].date() for e in monthly["entry"]] == [date(2022, 1, 4), date(2022, 2, 1)]
    assert dates[monthly["exit"].iloc[-1]].date() == date(2022, 3, 1)


# --- walk-forward model -------------------------------------------------------------------------------------


def test_relevance_grades_are_within_day_quintiles():
    y = np.r_[np.arange(10.0), np.arange(5.0) * -1, np.nan]
    day = np.r_[np.zeros(10), np.ones(5), 1]
    g = xm.relevance_grades(y, day)
    assert g[:10].tolist() == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]
    assert g[10:15].tolist() == [4, 3, 2, 1, 0]
    assert np.isnan(g[15])


def test_training_mask_purges_and_ends_labels_before_the_cut():
    t = np.arange(100)
    mask = xm.training_mask(t, np.ones(100, bool), cut_pos=80, h=5, purge=20)
    assert t[mask].max() == 59
    mask = xm.training_mask(t, np.ones(100, bool), cut_pos=80, h=20, purge=20)
    assert t[mask].max() == 58  # label t+1+h must end at 79 at the latest
    labelled = np.ones(100, bool)
    labelled[10] = False
    assert not xm.training_mask(t, labelled, 80, 5, 20)[10]


def test_walk_forward_learns_a_planted_signal_out_of_sample():
    dates = pd.bdate_range("2014-01-01", "2018-12-31", tz="UTC")
    n_days, n_stocks = len(dates), 30
    rng = np.random.default_rng(0)
    t = np.repeat(np.arange(n_days), n_stocks)
    x = rng.uniform(size=t.size)
    frame = pd.DataFrame(
        {"t": t, "stock": np.tile(np.arange(n_stocks), n_days), "x": x, "noise": rng.uniform(size=t.size)}
    )
    frame[xp.label_column(5)] = 0.02 * x + rng.normal(0, 0.01, t.size)
    frame.loc[frame["t"] >= n_days - 6, xp.label_column(5)] = np.nan
    start = pd.Timestamp("2017-01-01", tz="UTC")
    periods = xp.rebalance_periods(dates, "weekly_first_trading_day", start, dates[-1])
    windows = xm.yearly_windows(start, dates[-1])
    params = {**xm.LGBM_PARAMS, "min_data_in_leaf": 50, "num_threads": 1}
    scores, folds = xm.walk_forward(frame, ["x", "noise"], 5, dates, windows, periods, 20, params, 20)
    scored = np.isfinite(scores)
    assert scored.sum() == np.isin(t, periods["signal"]).sum()
    assert np.corrcoef(scores[scored], x[scored])[0, 1] > 0.9
    assert all(f["fitted"] for f in folds)
    assert all(pd.Timestamp(f["last_label_end"]) < pd.Timestamp(f["test_start"]) for f in folds)


# --- portfolio ----------------------------------------------------------------------------------------------


def _toy_panel(n_stocks=20, n_days=15):
    dates = pd.bdate_range("2024-01-01", periods=n_days, tz="UTC") + pd.Timedelta(hours=3, minutes=45)
    ids = [f"S{i}" for i in range(n_stocks)]
    growth = 1.0 + 0.01 * np.arange(n_stocks)
    days = np.arange(n_days)[:, None]
    price = pd.DataFrame(100.0 * growth**days, index=dates, columns=ids)
    return xp.Panel(
        dates=dates,
        ids=ids,
        industry=pd.Series("x", index=ids),
        open=price,
        high=price * 1.01,
        low=price * 0.99,
        close=price,
        volume=price * 0 + 1e5,
        market_close=price.iloc[:, 0],
        bullish=None,
        bearish=None,
        missing=[],
        n_off_calendar=0,
    )


def test_top_decile_portfolio_returns_turnover_and_costs():
    panel = _toy_panel()
    frame = pd.DataFrame({"t": np.repeat(np.arange(15), 20), "stock": np.tile(np.arange(20), 15)})
    periods = xp.rebalance_periods(
        panel.dates, "weekly_first_trading_day", panel.dates[0], panel.dates[-1] + pd.Timedelta(days=1)
    )
    books = bt.build_books(panel, frame, periods)
    assert [(b.entry, b.exit) for b in books] == [(5, 10)]  # 1 Jan is a Monday, so the first entry needs t=0
    scores = frame["stock"].to_numpy(dtype=float)
    sim = bt.simulate(books, scores, 20, cost_rt=0.004)
    growth = 1.0 + 0.01 * np.arange(20)
    assert sim.n_top[0] == 2
    assert sim.gross[0] == pytest.approx(np.mean(growth[18:] ** 5) - 1)
    assert sim.bench[0] == pytest.approx(np.mean(growth**5) - 1)
    assert sim.turnover[0] == pytest.approx(0.5)
    assert sim.cost[0] == pytest.approx(0.004 * (0.5 + 0.5))  # entry from cash + final liquidation
    assert sim.net[0] == pytest.approx((1 - sim.cost[0]) * (1 + sim.gross[0]) - 1)
    assert sim.ic[0] == pytest.approx(1.0)
    port, bench, block = sim.weekly()
    assert port[0] == pytest.approx(sim.net[0]) and bench[0] == pytest.approx(sim.bench[0])


def test_turnover_counts_drift_and_replacement():
    panel = _toy_panel(n_days=20)
    frame = pd.DataFrame({"t": np.repeat(np.arange(20), 20), "stock": np.tile(np.arange(20), 20)})
    periods = xp.rebalance_periods(
        panel.dates, "weekly_first_trading_day", panel.dates[1], panel.dates[-1] + pd.Timedelta(days=1)
    )
    books = bt.build_books(panel, frame, periods)
    assert len(books) == 2
    same = frame["stock"].to_numpy(dtype=float)
    sim = bt.simulate(books, same, 20, 0.0)
    drift = np.array([1.18, 1.19]) ** 5
    w = drift / drift.sum()
    assert sim.turnover[1] == pytest.approx(0.5 * np.abs(w - 0.5).sum())
    flip = np.where(frame["t"].to_numpy() < 8, same, -same)
    assert bt.simulate(books, flip, 20, 0.0).turnover[1] == pytest.approx(1.0)


def test_restrict_books_keeps_only_the_chosen_candidates():
    panel = _toy_panel()
    frame = pd.DataFrame({"t": np.repeat(np.arange(15), 20), "stock": np.tile(np.arange(20), 15)})
    periods = xp.rebalance_periods(
        panel.dates, "weekly_first_trading_day", panel.dates[0], panel.dates[-1] + pd.Timedelta(days=1)
    )
    books = bt.build_books(panel, frame, periods)
    keep = frame["stock"].to_numpy() < 12
    (small,) = bt.restrict_books(books, keep)
    assert small.stocks.tolist() == list(range(12))
    np.testing.assert_allclose(small.rel_exit, books[0].rel_exit[:12])
    with pytest.raises(ValueError):
        bt.restrict_books(books, frame["stock"].to_numpy() < 5)


def test_listing_age_counts_from_the_first_stored_bar():
    panel = _toy_panel(n_stocks=2, n_days=10)
    panel.close.iloc[:4, 1] = np.nan
    frame = pd.DataFrame({"t": [9, 9], "stock": [0, 1]})
    age = ev.listing_age_years(panel, frame)
    days = (panel.dates[9] - panel.dates[0]).days, (panel.dates[9] - panel.dates[4]).days
    np.testing.assert_allclose(age, np.array(days) / 365.25)


def test_rank_ic_and_selection_ties():
    assert bt.rank_ic([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert bt.rank_ic([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert math.isnan(bt.rank_ic([1, 1, 1], [1, 2, 3]))
    assert bt.select_top(np.array([1.0, 3.0, 3.0, 2.0]), 2).tolist() == [1, 2]
    assert bt.top_count(95) == 10 and bt.top_count(3) == 1


# --- statistics ---------------------------------------------------------------------------------------------


def test_one_sided_p_uses_the_registered_formula():
    assert ev.p_one_sided(np.array([-1.0, 0.0, 1.0, 2.0])) == pytest.approx(3 / 5)
    assert ev.p_one_sided(np.ones(1999)) == pytest.approx(1 / 2000)


def test_block_means_resample_whole_blocks():
    values = np.array([1.0, 2.0, 3.0, 10.0, np.nan])
    blocks = np.array([0, 0, 1, 2, 2])
    draws = ev.draw_blocks(3, 50, seed=1)
    got = ev.block_means(values, blocks, draws)
    by_block = [[1.0, 2.0], [3.0], [10.0]]
    for j in range(50):
        drawn = np.concatenate([by_block[b] for b in draws.idx[j]])
        assert got[j] == pytest.approx(drawn.mean())


def test_max_drawdown_and_its_bootstrap_chain():
    log_rel = np.log([1.1, 0.5, 1.2, 0.5])
    assert ev.max_drawdown(log_rel) == pytest.approx(1 - 0.5 * 1.2 * 0.5)
    draws = ev.draw_blocks(4, 30, seed=2)
    got = ev.block_max_drawdowns(log_rel, draws)
    for j in range(30):
        assert got[j] == pytest.approx(ev.max_drawdown(log_rel[draws.idx[j]]))


# --- pre-registration ---------------------------------------------------------------------------------------


def test_registered_specs_are_the_ones_implemented():
    spec = load_edge_config().cross_section
    ev.check_xs_spec(spec)
    section = ev.check_pv2_section(edge_v2_section("cross_section_patterns"), spec)
    assert section["name"] == "pv2_xs"
    with pytest.raises(ValueError):
        ev.check_pv2_section({**section, "added_feature_group": "other"}, spec)
    with pytest.raises(ValueError):
        ev.check_xs_spec(dataclasses.replace(spec, baselines=("reversal_1w",)))


# --- end to end on synthetic data ---------------------------------------------------------------------------


def test_evaluate_horizon_and_reports_on_synthetic_data():
    series = make_series("2014-01-01", "2018-06-30")
    panel, _ = _panel(series)
    frame = xp.panel_frame(panel, 20.0, HORIZONS, with_patterns=True)
    edge = load_edge_config()
    spec = dataclasses.replace(edge.cross_section, train_end=date(2017, 1, 1))
    edge = dataclasses.replace(
        edge,
        cross_section=spec,
        common=dataclasses.replace(
            edge.common, bootstrap=dataclasses.replace(edge.common.bootstrap, resamples=50)
        ),
    )
    params = {**xm.LGBM_PARAMS, "min_data_in_leaf": 50, "num_threads": 1}
    cost = ev.equity_round_trip()
    results = {h: ev.evaluate_horizon(panel, frame, spec, edge, h, cost[0], params, 10) for h in HORIZONS}
    for r in results.values():
        assert set(r.sims) == {ev.XS_NAME, ev.PV2_NAME, *xp.BASELINES}
        assert all(f["fitted"] for f in r.folds[ev.XS_NAME])
    bh = ev.primary_bh(results)
    meta = ev._meta(panel, frame, spec, edge)
    xs = ev.xs_report(results, edge, meta, cost, bh, 1.0)
    pv2 = ev.pv2_report(results, edge, edge_v2_section("cross_section_patterns"), meta, cost, bh, 1.0)
    for report in (xs, pv2):
        assert set(report["primary_p"]) == {"5", "20"}
        assert all(0 < p <= 1 for p in report["primary_p"].values())
    assert "survivorship" in ev.xs_markdown(xs).lower()
    assert "Survivorship check" in ev.pv2_markdown(pv2)
    diag = xs["horizons"]["5"]["survivorship_diagnostics"]
    assert set(diag["survivor_proxies"]) == {
        "small_turnover",
        "young_listing",
        "momentum_plus_small_turnover",
    }
    assert set(diag["liquid_half"]["strategies"]) == set(results[5].sims)
    assert "pv2_xs" in ev.pv2_markdown(pv2)
    late = dataclasses.replace(panel, dates=panel.dates + pd.Timedelta(days=4000))
    with pytest.raises(PermissionError):
        ev.evaluate_horizon(late, frame, spec, edge, 5, cost[0], params, 10)
