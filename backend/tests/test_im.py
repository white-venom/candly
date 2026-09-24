import json
import math
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from candly.core.calendar import IST
from candly.research import im_eval as ie
from candly.research.causality import check_causal
from candly.research.edge_v2_config import edge_v2_section
from candly.research.synthetic import synthetic_candles

HOLDOUT = date(2025, 10, 1)


@pytest.fixture
def spec():
    return ie.parse_spec(edge_v2_section("intraday_momentum"), HOLDOUT)


def _session(day: date, closes: dict[str, float] | None = None, base: float = 100.0, drop=()) -> pd.DataFrame:
    """A full 09:15-15:25 session of 5m bars at `base`, with chosen closes at some HH:MM bars."""
    closes = closes or {}
    rows = []
    t = datetime.combine(day, time(9, 15), tzinfo=IST)
    while t.time() < time(15, 30):
        hm = t.strftime("%H:%M")
        if hm not in drop:
            c = closes.get(hm, base)
            rows.append((pd.Timestamp(t).tz_convert("UTC"), c, c, c, c, 0.0, np.nan))
        t += timedelta(minutes=5)
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "oi"])


def _frame(*sessions) -> pd.DataFrame:
    return pd.concat(sessions, ignore_index=True)


# --- the registration ------------------------------------------------------------------------------------


def test_registered_times_map_to_bar_opens(spec):
    assert (spec.r1_end_bar, spec.r12_start_bar, spec.r12_end_bar, spec.target_end_bar) == (
        "09:40",
        "14:25",
        "14:55",
        "15:25",
    )
    assert spec.last_bar == "15:25"
    assert spec.train == (date(2017, 7, 17), date(2023, 1, 1))
    assert spec.validation == (date(2023, 1, 1), HOLDOUT)
    for hm in ("09:15", "09:40", "14:25", "14:55", "15:00", "15:25"):
        assert hm in spec.window_bars
    assert "09:45" not in spec.window_bars and "14:20" not in spec.window_bars


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("times_ist", {"first_end": "09:45", "second_last": ["14:30", "15:00"], "last": ["15:05", "15:30"]}),
        ("instruments", ["NSE:NIFTY50"]),
        ("extra", 1),
    ],
)
def test_a_changed_registration_fails_loudly(key, value):
    raw = {**edge_v2_section("intraday_momentum"), key: value}
    with pytest.raises(ValueError):
        ie.parse_spec(raw, HOLDOUT)


# --- features, labels, skip rules ------------------------------------------------------------------------


def test_known_answer_r1_r12_and_target(spec):
    d1, d2 = date(2024, 1, 2), date(2024, 1, 3)
    df = _frame(
        _session(d1, {"15:25": 200.0}),
        _session(d2, {"09:40": 202.0, "14:25": 204.0, "14:55": 203.0, "15:25": 206.0}, base=205.0),
    )
    t = ie.daily_table(df, spec).set_index("day")
    row = t.loc[d2]
    assert row["r1"] == pytest.approx(202.0 / 200.0 - 1)
    assert row["r12"] == pytest.approx(203.0 / 204.0 - 1)
    assert row["target"] == pytest.approx(206.0 / 203.0 - 1)
    assert bool(row["usable"]) and not bool(t.loc[d1, "usable"])  # the first day has no previous close
    assert row["ts"] == pd.Timestamp(datetime.combine(d2, time(14, 55), tzinfo=IST)).tz_convert("UTC")


@pytest.mark.parametrize("missing", ["09:15", "09:40", "14:25", "14:40", "15:00", "15:25"])
def test_a_missing_window_bar_skips_the_day(spec, missing):
    d1, d2 = date(2024, 1, 2), date(2024, 1, 3)
    df = _frame(_session(d1), _session(d2, drop=(missing,)))
    t = ie.daily_table(df, spec).set_index("day")
    assert d2 not in t.index or not bool(t.loc[d2, "usable"])


def test_a_bar_outside_the_windows_does_not_skip_the_day(spec):
    df = _frame(_session(date(2024, 1, 2)), _session(date(2024, 1, 3), drop=("12:00",)))
    assert bool(ie.daily_table(df, spec).set_index("day").loc[date(2024, 1, 3), "usable"])


def test_special_sessions_and_the_day_after_are_skipped(spec):
    # 2024-01-20 is a listed special Saturday session; 2024-01-22 was a holiday
    days = [date(2024, 1, 18), date(2024, 1, 19), date(2024, 1, 20), date(2024, 1, 23), date(2024, 1, 24)]
    t = ie.daily_table(_frame(*[_session(d) for d in days]), spec).set_index("day")
    assert t["usable"].to_dict() == {
        days[0]: False,
        days[1]: True,
        days[2]: False,
        days[3]: False,
        days[4]: True,
    }
    short = _frame(_session(days[0]), _session(days[1], drop=("15:20", "15:25")), _session(days[3]))
    t = ie.daily_table(short, spec).set_index("day")
    assert bool(t.loc[days[1], "special"]) and bool(t.loc[days[3], "prev_special"])


def test_directions_follow_the_registered_rules():
    d = ie.directions([0.01, 0.01, -0.02, 0.0, -0.01], [0.02, -0.01, -0.01, 0.01, 0.0])
    assert d["sign_r1"].tolist() == [1.0, 1.0, -1.0, -1.0, -1.0]
    assert d["agree"].tolist() == [1.0, 0.0, -1.0, 0.0, 0.0]


def test_features_are_causal(spec):
    df = synthetic_candles("5m", "2024-02-05", "2024-02-16", seed=11)
    ts_local = df["ts"].dt.tz_convert(IST).dt.strftime("%H:%M")
    decision_rows = np.flatnonzero((ts_local == "14:55").to_numpy())
    open_rows = np.flatnonzero((ts_local == "09:40").to_numpy())
    cuts = [int(decision_rows[3]), int(decision_rows[6]), int(open_rows[5]), int(decision_rows[6]) - 7]

    def features(frame: pd.DataFrame) -> pd.DataFrame:
        return ie.im_features(frame, spec)[
            ["ts", "day", "prev_close", "r1", "r12", "r1_complete", "r12_complete"]
        ]

    check_causal(features, df, cuts=cuts)


# --- statistics ------------------------------------------------------------------------------------------


class _Boot:
    resamples, ci_level, seed = 200, 0.95, 1


def test_strategy_stats_known_answer():
    rows = pd.DataFrame(
        {"day": [date(2023, 1, 2), date(2023, 1, 3), date(2023, 1, 4)], "target": [0.01, -0.02, 0.03]}
    )
    s = ie.strategy_stats(rows, np.array([1.0, -1.0, 0.0]), 0.001, _Boot)
    assert s["mean_net_per_day"] == pytest.approx((0.009 + 0.019 + 0.0) / 3)
    assert s["mean_gross_per_trade"] == pytest.approx(0.015)
    assert s["mean_net_per_trade"] == pytest.approx(0.014)
    assert (s["n_trades"], s["n_long"], s["n_short"], s["hit_rate"]) == (2, 1, 1, 1.0)
    assert s["base_rate_up"] == pytest.approx(2 / 3)
    assert s["hit_rate_if_independent"] == pytest.approx(0.5 * 2 / 3 + 0.5 * 1 / 3)


def test_ols_hc1_matches_a_direct_computation():
    rng = np.random.default_rng(3)
    x = rng.normal(size=500)
    y = 0.5 + 2.0 * x + rng.normal(scale=np.abs(x) + 0.1)
    out = ie.ols_hc1(y, x[:, None], ("x",))
    X = np.column_stack([np.ones(500), x])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    e = y - X @ beta
    inv = np.linalg.inv(X.T @ X)
    cov = inv @ (X.T * e**2) @ X @ inv * 500 / 498
    assert out["slope_x"] == pytest.approx(beta[1])
    assert out["t_x"] == pytest.approx(beta[1] / math.sqrt(cov[1, 1]))
    assert out["r2"] == pytest.approx(1 - e @ e / ((y - y.mean()) @ (y - y.mean())))


def test_p_value_counts_ties_and_adds_one():
    assert ie.p_one_sided(np.array([-0.1, 0.0, 0.2])) == pytest.approx(3 / 4)
    assert ie.p_one_sided(np.array([0.1, 0.2])) == pytest.approx(1 / 3)


# --- end to end ------------------------------------------------------------------------------------------


def _planted(seed: int, edge: float) -> pd.DataFrame:
    """Synthetic 5m bars where the last half hour moves `edge` in the direction of r1."""
    df = synthetic_candles("5m", "2022-06-01", "2023-06-30", seed=seed)
    local = df["ts"].dt.tz_convert(IST)
    day, hm = local.dt.date, local.dt.strftime("%H:%M")
    close = df.pivot_table(index=day, columns=hm, values="close")
    new_last, prev = {}, None
    for d in close.index:  # r1 must use the already-planted previous close
        if prev is None:
            new_last[d] = close.loc[d, "15:25"]
        else:
            direction = 1.0 if close.loc[d, "09:40"] / prev - 1 > 0 else -1.0
            new_last[d] = close.loc[d, "14:55"] * (1 + edge * direction)
        prev = new_last[d]
    last = (hm == "15:25").to_numpy()
    new = day[last].map(new_last).to_numpy()
    df.loc[last, "close"] = new
    df.loc[last, "high"] = np.maximum(df.loc[last, "high"], new)
    df.loc[last, "low"] = np.minimum(df.loc[last, "low"], new)
    return df


def test_evaluate_finds_a_planted_effect_and_writes_reports(tmp_path):
    frames = {"NSE:NIFTY50": _planted(1, 0.004), "NSE:BANKNIFTY": _planted(2, 0.004)}
    rep = ie.evaluate(frames=frames).report
    assert rep["n_hypotheses"] == 4
    by = {(h["instrument"], h["variant"]): h for h in rep["hypotheses"]}
    sign = by[("NSE:NIFTY50", "sign_r1")]
    assert sign["primary_mean_net_per_day"] == pytest.approx(
        0.004 - rep["costs"]["round_trip_pct"] / 100, rel=0.05
    )
    assert sign["pass_own_rule"] and sign["p_one_sided"] < 0.01
    assert rep["regressions"]["NSE:NIFTY50"]["validation"]["target_on_r1"]["n"] > 50
    json_path, md_path = ie.write_report(rep, tmp_path / "im.json")
    assert json.loads(json_path.read_text(encoding="utf-8"))["test"] == "im_v1"
    assert "sign_r1" in md_path.read_text(encoding="utf-8")


def test_no_effect_fails_after_costs():
    frames = {"NSE:NIFTY50": _planted(3, 0.0), "NSE:BANKNIFTY": _planted(4, 0.0)}
    rep = ie.evaluate(frames=frames).report
    assert not rep["pass_own_rule_any"]
    for h in rep["hypotheses"]:
        assert h["primary_mean_net_per_day"] < 0


def test_evaluate_refuses_holdout_bars():
    later = synthetic_candles("5m", "2025-10-01", "2025-10-03", seed=5)
    frames = {"NSE:NIFTY50": later, "NSE:BANKNIFTY": later}
    with pytest.raises(PermissionError):
        ie.evaluate(frames=frames)
