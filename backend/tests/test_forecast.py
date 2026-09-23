import dataclasses
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from scorecard_helpers import bucket_base, bucket_events, validation_card

from candly.core.calendar import IST, get_calendar, reload_calendar
from candly.features.context import compute_context
from candly.forecast import analog, baseline_forecasts, make_forecast
from candly.forecast.analog import confidence_label, ghost_path
from candly.forecast.timing import future_bar_times, last_expected_closed_bar
from candly.patterns import detect_patterns
from candly.research.config import load_research_config
from candly.research.labels import forward_paths, path_columns
from candly.research.scorecard import Scorecard, ScorecardMeta
from candly.research.stats import beta_interval, beta_posterior
from candly.research.synthetic import bar_times, plant_marubozu_edge, synthetic_candles

FORECAST_KEYS = {
    "trade",
    "instrument",
    "tf",
    "method",
    "made_at",
    "ref_time",
    "ref_close",
    "horizon_bars",
    "p_up",
    "p_up_ci",
    "base_rate",
    "abstain",
    "abstain_reason",
    "confidence",
    "expected_move_pct",
    "ghost_candles",
    "bands",
    "invalidation",
    "drivers",
    "n_analogs",
    "explanation",
}
cal = get_calendar()


@pytest.fixture(autouse=True)
def calendar_of_this_data_dir(tmp_data_dir):
    """A calendar cached while DATA_DIR pointed at the real data would carry its observed holidays and
    change the synthetic bar grids."""
    reload_calendar()
    yield
    reload_calendar()


def just_after_close(df: pd.DataFrame, exchange: str = "NSE", tf: str = "1D") -> pd.Timestamp:
    return cal.bar_close_time(exchange, df["ts"].iloc[-1], tf) + pd.Timedelta(minutes=1)


def ist(y, m, d, hh=0, mm=0) -> pd.Timestamp:
    return pd.Timestamp(datetime(y, m, d, hh, mm), tz=IST).tz_convert("UTC")


@pytest.fixture(scope="module")
def random_daily():
    """Random candles cut so that the last bar has no pattern (analogs = its whole trend bucket)."""
    df = synthetic_candles("1D", "2016-01-01", "2024-06-28", seed=31)
    with_pattern = set(detect_patterns(df, "1D")["ts"])
    last = max(i for i, ts in enumerate(df["ts"]) if ts not in with_pattern)
    return df.iloc[: last + 1].reset_index(drop=True)


def test_forecast_shape_matches_contract(random_daily):
    fc = make_forecast("NSE:RELIANCE", "1D", random_daily, None, now=just_after_close(random_daily))
    data = fc.model_dump(mode="json")
    assert set(data) == FORECAST_KEYS
    assert fc.method == "analog_v1" and fc.explanation is None
    assert fc.ref_time == int(random_daily["ts"].iloc[-1].timestamp())
    assert fc.horizon_bars == load_research_config().forecast_steps
    assert 0 < fc.p_up < 1 and 0 < fc.base_rate < 1
    lo, hi = fc.p_up_ci
    assert lo < fc.p_up < hi
    assert fc.n_analogs >= load_research_config().min_analogs
    assert fc.abstain == (fc.abstain_reason is not None)
    assert fc.abstain and fc.trade is None and fc.invalidation is None
    assert fc.drivers and fc.drivers[0].name == "Analogs"
    assert any(d.name == "No pattern" for d in fc.drivers)
    assert fc.context is not None and fc.context.atr > 0 and fc.context.patterns == []


def test_analogs_only_use_outcomes_closed_by_the_reference_bar(random_daily):
    steps = 3
    ctx = compute_context(random_daily, "1D", "NSE")
    trend = ctx["trend"].to_numpy()
    # A reference bar whose previous steps-1 bars share its trend, so the exclusion is non-vacuous.
    ref = next(
        i for i in range(len(random_daily) - 1, 300, -1) if (trend[i - steps + 1 : i + 1] == trend[i]).all()
    )
    candles = random_daily.iloc[: ref + 1]
    fc = make_forecast("NSE:RELIANCE", "1D", candles, None, steps=steps, now=just_after_close(candles))
    upto = ctx.iloc[: ref + 1]
    same = (upto["trend"] == fc.context.trend).to_numpy() & (upto["atr14"] > 0).to_numpy()
    assert fc.n_analogs == int(same[: ref - steps + 1].sum())
    assert fc.n_analogs < int(same[:ref].sum())


def test_posterior_uses_the_effective_number_of_analogs(random_daily):
    cfg = load_research_config()
    steps = 3
    now = just_after_close(random_daily)
    fc = make_forecast("NSE:RELIANCE", "1D", random_daily, None, steps=steps, now=now)
    ctx = compute_context(random_daily, "1D", "NSE")
    ref = len(random_daily) - 1
    same = (ctx["trend"] == fc.context.trend).to_numpy() & (ctx["atr14"] > 0).to_numpy()
    idx = np.flatnonzero(same[: ref - steps + 1])
    closes = random_daily["close"].to_numpy()
    hits, n = int((closes[idx + steps] > closes[idx]).sum()), len(idx)
    assert fc.n_analogs == n
    k = cfg.prior_strength
    assert fc.p_up == pytest.approx(float(beta_posterior(hits / steps, n / steps, fc.base_rate, k)))
    lo, hi = beta_interval(hits / steps, n / steps, fc.base_rate, k, cfg.ci_level)
    assert fc.p_up_ci == pytest.approx((float(lo), float(hi)))
    naive_lo, naive_hi = beta_interval(hits, n, fc.base_rate, k, cfg.ci_level)
    assert hi - lo > naive_hi - naive_lo


def test_confidence_labels_follow_the_config():
    cfg = load_research_config()
    edge = cfg.confidence["high"].min_edge_multiple * cfg.min_edge
    assert confidence_label(0.56, 0.70, 0.5, edge + 0.01, cfg) == "high"
    assert confidence_label(0.51, 0.60, 0.5, edge - 0.01, cfg) == "medium"
    assert confidence_label(0.45, 0.70, 0.5, edge + 0.01, cfg) == "low"
    assert confidence_label(0.30, 0.44, 0.5, -(edge + 0.01), cfg) == "high"


def test_abstains_with_too_few_analogs():
    df = synthetic_candles("1D", "2016-01-01", "2024-06-28", seed=52, plain=True)
    df = plant_marubozu_edge(df, every=400, first=len(df) - 1 - 400 * 5)
    fc = make_forecast("NSE:RELIANCE", "1D", df, None, steps=1, now=just_after_close(df))
    assert "bullish_marubozu" in fc.context.patterns
    assert fc.abstain and fc.n_analogs < load_research_config().min_analogs
    assert fc.abstain_reason.startswith(("too few analogs", "no analogs"))
    assert fc.ghost_candles == [] and fc.bands == []


def test_ghost_candles_and_bands(random_daily):
    fc = make_forecast("NSE:RELIANCE", "1D", random_daily, None, steps=3, now=just_after_close(random_daily))
    assert len(fc.ghost_candles) == 3 and len(fc.bands) == 3
    last = random_daily["ts"].iloc[-1]
    expected = [int(t.timestamp()) for t in future_bar_times("NSE", "1D", last, 3)]
    assert [g.time for g in fc.ghost_candles] == expected == [b.time for b in fc.bands]
    assert expected[0] > int(last.timestamp())
    for g, b in zip(fc.ghost_candles, fc.bands, strict=True):
        assert g.low <= min(g.open, g.close) <= max(g.open, g.close) <= g.high
        assert g.volume == 0
        assert b.p10 <= b.p50 <= b.p90
        assert b.p50 == pytest.approx(g.close)
    assert fc.expected_move_pct == pytest.approx(100 * (fc.ghost_candles[-1].close / fc.ref_close - 1))


def test_future_bar_times_skip_weekends_and_holidays():
    friday = ist(2026, 9, 11, 9, 15)
    days = [t.tz_convert(IST).date().isoformat() for t in future_bar_times("NSE", "1D", friday, 3)]
    assert days == ["2026-09-15", "2026-09-16", "2026-09-17"]  # Mon 14 Sep is Ganesh Chaturthi
    last_hour = ist(2026, 9, 23, 15, 15)
    nxt = future_bar_times("NSE", "1h", last_hour, 2)
    assert nxt == [ist(2026, 9, 24, 9, 15), ist(2026, 9, 24, 10, 15)]
    assert last_expected_closed_bar("NSE", "1D", ist(2026, 9, 26, 12)) == ist(2026, 9, 25, 9, 15)


def test_abstains_on_stale_data(random_daily):
    later = just_after_close(random_daily) + timedelta(days=10)
    fc = make_forecast("NSE:RELIANCE", "1D", random_daily, None, now=later)
    assert fc.abstain and fc.abstain_reason.startswith("stale data")
    assert fc.p_up is None and fc.ghost_candles == []


def test_abstains_without_enough_history():
    short = synthetic_candles("1D", "2024-01-01", "2024-02-15", seed=1)
    fc = make_forecast("NSE:RELIANCE", "1D", short, None, now=just_after_close(short))
    assert fc.abstain and fc.abstain_reason.startswith("not enough history")


def test_unclosed_bar_is_ignored(random_daily):
    now = cal.bar_close_time("NSE", random_daily["ts"].iloc[-1], "1D") - pd.Timedelta(minutes=5)
    fc = make_forecast("NSE:RELIANCE", "1D", random_daily, None, now=now)
    assert fc.ref_time == int(random_daily["ts"].iloc[-2].timestamp())


def planted(end: str = "2024-06-28", every: int = 15, follow_bars: int = 3) -> pd.DataFrame:
    """Ends on a planted bullish marubozu, the only pattern on that bar."""
    df = synthetic_candles("1D", "2012-01-01", end, seed=42, plain=True)
    last_signal = max(range(60, len(df), every))
    edge = plant_marubozu_edge(df, every=every, p_follow=0.9, seed=3, follow_bars=follow_bars)
    return edge.iloc[: last_signal + 1]


def _meta() -> ScorecardMeta:
    cfg = load_research_config()
    return ScorecardMeta(
        tf="1D",
        built_at=None,
        train_end="2015-01-01",
        holdout_start=cfg.holdout_start.isoformat(),
        n_tests=0,
        fdr_alpha=cfg.fdr_alpha,
        horizons=list(cfg.horizons),
    )


PEERS = ["NSE:RELIANCE", "NSE:TCS", "BSE:SENSEX"]


def held_up(trend: str, hit: float = 0.8, n: int = 200, exchange: str | None = "NSE", **events) -> Scorecard:
    """A scorecard whose validation events of the bullish marubozu in `trend` closed up a share `hit` of
    the time, against a 50% base rate."""
    ups = (np.arange(n) % 100 < round(100 * hit)).astype(float)
    frame = bucket_events(ups, pattern="bullish_marubozu", trend=trend, **events)
    return validation_card(frame, bucket_base(PEERS, 0.5, trend), exchange=exchange)


def with_config(monkeypatch, **changes):
    cfg = dataclasses.replace(load_research_config(), **changes)
    monkeypatch.setattr(analog, "load_research_config", lambda: cfg)
    return cfg


def test_planted_edge_gives_a_confident_bullish_call():
    df = planted()
    now = just_after_close(df)
    unvalidated = make_forecast("NSE:RELIANCE", "1D", df, None, now=now)
    assert unvalidated.abstain and unvalidated.abstain_reason == "unvalidated bucket: no scorecard"
    assert unvalidated.trade is None and unvalidated.invalidation is None and unvalidated.ghost_candles

    fc = make_forecast("NSE:RELIANCE", "1D", df, held_up(unvalidated.context.trend), now=now)
    assert fc.context.patterns and "bullish_marubozu" in fc.context.patterns
    assert not fc.abstain, fc.abstain_reason
    assert fc.p_up > fc.base_rate + 0.2
    assert fc.confidence == "high"
    assert fc.ghost_candles[0].close > fc.ref_close
    assert fc.invalidation == pytest.approx(df["low"].iloc[-1] - 0.1 * fc.context.atr, rel=1e-3)
    assert any(d.name == "Bullish marubozu" and d.effect == "bullish" for d in fc.drivers)
    trade = fc.trade
    assert trade.entry == fc.ref_close and trade.stop == fc.invalidation
    assert trade.target == fc.bands[-1].p50 > trade.entry
    assert trade.reward_risk == pytest.approx((trade.target - trade.entry) / (trade.entry - trade.stop))
    assert trade.reward_risk >= load_research_config().min_reward_risk


def test_directional_calls_need_a_bucket_that_held_up_in_validation():
    df = planted()
    now = just_after_close(df)
    trend = make_forecast("NSE:RELIANCE", "1D", df, None, now=now).context.trend

    def reason(card):
        return make_forecast("NSE:RELIANCE", "1D", df, card, now=now).abstain_reason

    assert reason(held_up(trend)) is None
    failed = reason(held_up(trend, hit=0.45))
    assert failed.startswith("unvalidated bucket:") and "(NSE:RELIANCE)" in failed
    assert "n=200 in 200 clusters, one-sided p=" in failed
    assert reason(held_up("elsewhere")).startswith("unvalidated bucket:")
    too_few = reason(held_up(trend, n=20))
    assert too_few.startswith("unvalidated bucket:")
    assert "20 independent validation clusters (20 events)" in too_few
    # overlapping outcome windows are one cluster, however many events they hold
    chained = reason(held_up(trend, window_days=30))
    assert chained.startswith("unvalidated bucket:") and "(200 events), fewer than" in chained

    # the instrument's own events are too thin, so the pooled events of its exchange decide
    own = bucket_events(np.r_[np.ones(3), np.zeros(7)], pattern="bullish_marubozu", trend=trend)
    peers = bucket_events(np.ones(100), "NSE:TCS", "bullish_marubozu", trend, first="2021-01-01")
    base = bucket_base(PEERS, 0.5, trend)
    assert reason(validation_card(pd.concat([own, peers]), base)) is None
    elsewhere = pd.concat([own, peers.assign(instrument="BSE:SENSEX")])
    assert reason(validation_card(elsewhere, base, exchange=None)).startswith("unvalidated bucket:")
    assert reason(held_up(trend, exchange="MCX")) == "unvalidated bucket: the scorecard is MCX's, not NSE's"
    # events that closed down never back a bullish call, whatever their pattern's direction
    fell = bucket_events(np.zeros(200), pattern="bullish_marubozu", trend=trend, direction="bearish")
    assert reason(validation_card(fell, base)).startswith("unvalidated bucket:")


def test_the_structural_bucket_gate_holds_in_validation_replays():
    df = planted()
    now = just_after_close(df)
    replay = make_forecast("NSE:RELIANCE", "1D", df, None, now=now, bucket_gate=False)
    assert not replay.abstain, replay.abstain_reason  # no validation statistics are read
    odd = make_forecast("NSE:RELIANCE", "1D", df, None, steps=2, now=now, bucket_gate=False)
    assert odd.abstain_reason == "unvalidated bucket: 2 bars is not a scorecard horizon [1, 3, 5]"


def test_edge_below_costs_abstains(monkeypatch):
    df = planted()
    now = just_after_close(df)
    trend = make_forecast("NSE:RELIANCE", "1D", df, None, now=now).context.trend
    seen = []

    def huge(kind, holding, side="long", **_):
        seen.append((kind, holding, side))
        return 0.5

    monkeypatch.setattr(analog, "round_trip_cost", huge)
    fc = make_forecast("NSE:RELIANCE", "1D", df, held_up(trend), now=now)
    assert fc.abstain and fc.abstain_reason.startswith("edge below costs:")
    assert "50.00% round trip (equity, multi-day)" in fc.abstain_reason
    assert fc.trade is None and fc.invalidation is None
    assert seen == [("equity", "multi_day", "long")]


def test_the_cost_hurdle_is_measured_from_the_next_open(monkeypatch):
    steps = load_research_config().forecast_steps
    df = planted().copy()
    marubozu = detect_patterns(df, "1D").query("pattern == 'bullish_marubozu'")
    nxt = pd.Index(df["ts"]).get_indexer(marubozu["ts"]) + 1
    nxt = nxt[nxt < len(df)]
    # the bar after each marubozu gaps up by half of its move, so a trade entered at its open catches less
    close = df["close"].to_numpy()
    gap_open = close[nxt - 1] + 0.5 * (close[nxt] - close[nxt - 1])
    df.loc[nxt, "open"] = gap_open
    df.loc[nxt, "high"] = np.maximum(df.loc[nxt, "high"], gap_open)
    df.loc[nxt, "low"] = np.minimum(df.loc[nxt, "low"], gap_open)
    now = just_after_close(df)
    trend = make_forecast("NSE:RELIANCE", "1D", df, None, now=now).context.trend
    monkeypatch.setattr(analog, "round_trip_cost", lambda *a, **k: 0.5)
    fc = make_forecast("NSE:RELIANCE", "1D", df, held_up(trend), now=now)

    ctx = compute_context(df, "1D", "NSE")
    ref = len(df) - 1
    atr = ctx["atr14"].to_numpy()
    found = detect_patterns(df, "1D")
    active = found.loc[found["pattern"].isin(fc.context.patterns), "ts"]
    analogs = np.zeros(len(df), bool)
    analogs[pd.Index(df["ts"]).get_indexer(active)] = True
    analogs &= (ctx["trend"] == trend).to_numpy() & (atr > 0) & (np.arange(len(df)) + steps <= ref)
    paths = forward_paths(df, ctx["atr14"], steps)[analogs]
    assert len(paths) == fc.n_analogs
    entry = fc.ref_close + paths[:, 0, 0] * atr[ref]
    from_open = float(np.median((paths[:, -1, 3] - paths[:, 0, 0]) * atr[ref] / entry))
    assert f"median move {100 * from_open:.2f}% the call's way from the next open" in fc.abstain_reason
    assert fc.expected_move_pct > 100 * from_open + 0.2  # measured from the reference close it looks bigger


def test_calls_whose_reward_is_below_the_risk_abstain(monkeypatch):
    df = planted(follow_bars=1)  # one bar of follow-through: the target sits closer than the stop
    now = just_after_close(df)
    trend = make_forecast("NSE:RELIANCE", "1D", df, None, steps=1, now=now).context.trend
    fc = make_forecast("NSE:RELIANCE", "1D", df, held_up(trend), steps=1, now=now)
    assert fc.abstain and fc.abstain_reason.startswith("reward below risk: R:R ")
    assert fc.trade is None and fc.invalidation is None
    rr = float(fc.abstain_reason.removeprefix("reward below risk: R:R "))
    assert 0 < rr < load_research_config().min_reward_risk
    with_config(monkeypatch, min_reward_risk=rr - 0.01)
    called = make_forecast("NSE:RELIANCE", "1D", df, held_up(trend), steps=1, now=now)
    assert not called.abstain and called.trade.reward_risk == pytest.approx(rr, abs=0.005)


def regime_daily(seed: int = 7) -> pd.DataFrame:
    """Up and down drift regimes in the opening gaps: the trend bucket carries the edge, patterns don't."""
    n = len(bar_times("1D", "2012-01-01", "2024-06-28"))
    sign = np.repeat(np.random.default_rng(seed).choice([-1.0, 1.0], size=n // 120 + 1), 120)[:n]
    return synthetic_candles("1D", "2012-01-01", "2024-06-28", seed=seed, drift=0.4 * sign, plain=True)


def test_a_bar_without_a_pattern_is_unvalidated_and_otherwise_gets_the_fallback_stop(monkeypatch):
    df = regime_daily()
    ctx = compute_context(df, "1D", "NSE")
    with_pattern = set(detect_patterns(df, "1D")["ts"])
    ref = max(
        i for i in range(len(df)) if df["ts"].iloc[i] not in with_pattern and ctx["trend"].iloc[i] == "up"
    )
    cut = df.iloc[: ref + 1]
    now = just_after_close(cut)
    gated = make_forecast("NSE:RELIANCE", "1D", cut, None, now=now)
    assert gated.abstain_reason == "unvalidated bucket: trend=up, any bar is not a scorecard bucket"
    replay = make_forecast("NSE:RELIANCE", "1D", cut, None, now=now, bucket_gate=False)
    assert replay.abstain_reason == gated.abstain_reason  # structural: on in validation replays too

    cfg = with_config(monkeypatch, require_validated_bucket=False, require_edge_over_costs=False)
    fc = make_forecast("NSE:RELIANCE", "1D", cut, None, now=now)
    assert fc.context.patterns == [] and not fc.abstain, fc.abstain_reason
    assert fc.p_up > fc.base_rate
    assert fc.invalidation == pytest.approx(fc.ref_close - cfg.fallback_stop_atr * fc.context.atr)
    assert fc.trade.stop == fc.invalidation and fc.trade.entry == fc.ref_close


def test_intraday_calls_must_end_inside_the_session(monkeypatch):
    with_config(monkeypatch, require_validated_bucket=False, require_edge_over_costs=False)
    base = synthetic_candles("1h", "2022-01-01", "2024-06-28", seed=45, plain=True)
    slot = base["ts"].dt.tz_convert(IST).dt.strftime("%H:%M")
    reasons = {}
    for start in ("09:15", "13:15"):
        first = int(np.flatnonzero((slot == start).to_numpy())[10])
        df = plant_marubozu_edge(base, every=14, first=first, p_follow=0.9, seed=4, follow_bars=3)
        signals = range(first, len(df), 14)
        last = [i for i in signals if i < len(df) - 3][-1]
        assert slot.iloc[last] == start
        cut = df.iloc[: last + 1]
        fc = make_forecast("NSE:RELIANCE", "1h", cut, None, now=just_after_close(cut, tf="1h"))
        assert "bullish_marubozu" in fc.context.patterns
        reasons[start] = fc.abstain_reason
    assert reasons["09:15"] is None
    assert reasons["13:15"].startswith("horizon crosses session close:")


def test_expiry_driver_on_and_before_expiry_days():
    df = synthetic_candles("1D", "2016-01-01", "2026-09-24", seed=33)

    def expiry_driver(end: str):
        cut = df[df["ts"].dt.tz_convert(IST).dt.date <= pd.Timestamp(end).date()]
        fc = make_forecast("NSE:NIFTY50", "1D", cut, None, now=just_after_close(cut))
        return next((d for d in fc.drivers if d.name == "Expiry"), None)

    on = expiry_driver("2026-09-22")
    assert on is not None and on.effect == "neutral" and "on the weekly expiry (2026-09-22)" in on.detail
    before = expiry_driver("2026-09-21")
    assert before is not None and "1 trading day before the weekly expiry (2026-09-22)" in before.detail
    assert expiry_driver("2026-09-24") is None  # 3 trading days to the monthly on 29 Sep


def test_ghost_candles_keep_real_bodies_and_wicks():
    df = synthetic_candles("1D", "2010-01-01", "2024-06-28", seed=9)
    atr = compute_context(df, "1D", "NSE")["atr14"]
    paths = forward_paths(df, atr, 3)
    paths = paths[~np.isnan(paths).any(axis=(1, 2))]
    g = ghost_path(paths)
    o, h, lo, c = g.T
    assert (lo <= np.minimum(o, c)).all() and (np.maximum(o, c) <= h).all()
    np.testing.assert_allclose(c, np.median(paths[:, :, 3], axis=0))
    body = np.abs(paths[:, :, 3] - paths[:, :, 0])
    np.testing.assert_allclose(np.abs(c - o), np.median(body, axis=0))
    assert (np.sign(c - o) == np.sign(np.diff(c, prepend=0.0))).all()
    ratio = np.abs(c - o) / (h - lo)
    q25, q75 = np.quantile(body / (paths[:, :, 1] - paths[:, :, 2]), [0.25, 0.75], axis=0)
    assert ((q25 <= ratio) & (ratio <= q75)).all()
    # medians of O and C taken separately almost cancel: the doji ghosts of report F9
    separate = np.median(paths, axis=0)
    assert (np.abs(separate[:, 3] - separate[:, 0]) < 0.25 * np.abs(c - o)).all()


def test_forecast_ghost_bodies_sit_inside_the_analogs_interquartile_range(random_daily):
    steps = 3
    now = just_after_close(random_daily)
    fc = make_forecast("NSE:RELIANCE", "1D", random_daily, None, steps=steps, now=now)
    ctx = compute_context(random_daily, "1D", "NSE")
    ref = len(random_daily) - 1
    same = (ctx["trend"] == fc.context.trend).to_numpy() & (ctx["atr14"] > 0).to_numpy()
    idx = np.flatnonzero(same[: ref - steps + 1])
    paths = forward_paths(random_daily, ctx["atr14"], steps)[idx]
    assert len(idx) == fc.n_analogs
    analog_ratio = np.abs(paths[:, :, 3] - paths[:, :, 0]) / (paths[:, :, 1] - paths[:, :, 2])
    q25, q75 = np.quantile(analog_ratio, [0.25, 0.75], axis=0)
    for s, g in enumerate(fc.ghost_candles):
        assert q25[s] <= abs(g.close - g.open) / (g.high - g.low) <= q75[s]
        assert abs(g.close - g.open) / fc.context.atr > 0.1


def library_card(rows: list[dict], steps: int) -> Scorecard:
    empty = pd.DataFrame(columns=["pattern", "instrument", "context", "horizon_bars"])
    return Scorecard(_meta(), empty, pd.DataFrame(rows))


def test_pooled_analogs_are_a_fallback_and_respect_time():
    df = synthetic_candles("1D", "2016-01-01", "2024-06-28", seed=51, plain=True)
    df = plant_marubozu_edge(df, every=10_000, first=len(df) - 1)
    alone = make_forecast("NSE:RELIANCE", "1D", df, None, steps=1, now=just_after_close(df))
    assert "bullish_marubozu" in alone.context.patterns
    assert alone.abstain and alone.n_analogs == 0 and alone.p_up is None

    steps = load_research_config().max_path_bars
    ref_ts = df["ts"].iloc[-1]
    trend = alone.context.trend
    base_row = {c: 0.5 for c in path_columns(steps)}
    rows = []
    for k in range(40):
        rows.append(
            {
                "instrument": "NSE:TCS",
                "ts": ref_ts - pd.Timedelta(days=400 + k),
                "end_ts": ref_ts - pd.Timedelta(days=390 + k),
                "pattern": "bullish_marubozu",
                "direction": "bullish",
                "trend": trend,
                "vol_regime": "normal",
                "atr": 1.0,
                "close": 100.0,
                **base_row,
            }
        )
    for k in range(25):
        rows.append(
            {**rows[0], "ts": ref_ts - pd.Timedelta(days=3), "end_ts": ref_ts + pd.Timedelta(days=5 + k)}
        )
    rows.append({**rows[0], "instrument": "NSE:RELIANCE"})
    for k in range(30):
        other_exchange = "BSE:SENSEX" if k % 2 else "MCX:GOLD"
        rows.append({**rows[0], "instrument": other_exchange, "ts": ref_ts - pd.Timedelta(days=500 + k)})
    card = library_card(rows, steps)
    pooled = make_forecast("NSE:RELIANCE", "1D", df, card, steps=1, now=just_after_close(df))
    assert pooled.n_analogs == 40
    assert "pooled" in pooled.drivers[0].detail
    assert pooled.p_up > pooled.base_rate + 0.2


def test_baselines():
    df = synthetic_candles("1D", "2020-01-01", "2024-06-28", seed=61)
    out = baseline_forecasts("NSE:RELIANCE", "1D", df, now=just_after_close(df))
    methods = {f.method: f for f in out}
    assert set(methods) == {"baseline_base_rate", "baseline_persistence", "baseline_random_walk"}
    assert not any(f.abstain for f in out)
    assert methods["baseline_base_rate"].p_up == methods["baseline_base_rate"].base_rate
    last = df.iloc[-1]
    assert methods["baseline_persistence"].p_up == (1.0 if last["close"] > last["open"] else 0.0)
    rw = methods["baseline_random_walk"]
    assert rw.p_up == 0.5 and len(rw.bands) == 3
    for b in rw.bands:
        assert b.p10 < b.p50 < b.p90
        assert b.p50 == pytest.approx(rw.ref_close)
    widths = [b.p90 - b.p10 for b in rw.bands]
    assert widths == sorted(widths)
    assert np.isfinite(methods["baseline_persistence"].ghost_candles[-1].close)
