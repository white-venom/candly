import dataclasses
import hashlib
import json

import numpy as np
import pandas as pd
import pytest
from scorecard_helpers import bucket_base, bucket_events, gate_kwargs, validation_card

from candly.core.calendar import IST, reload_calendar
from candly.core.instruments import get_instrument
from candly.core.settings import get_settings
from candly.patterns import detect_patterns
from candly.research import scorecard as scorecard_module
from candly.research.config import HASHED_CONFIGS, config_hashes, load_research_config
from candly.research.costs import round_trip_cost
from candly.research.labels import forward_labels
from candly.research.scorecard import (
    ROW_COLUMNS,
    Scorecard,
    build_scorecard,
    default_instruments,
    exchange_universe,
    load_scorecard,
    scorecard_paths,
    validation_columns,
)
from candly.research.synthetic import bar_times, plant_marubozu_edge, synthetic_candles

IDS = ["NSE:RELIANCE", "NSE:TCS"]
KEY = ["pattern", "context", "horizon_bars"]


@pytest.fixture(autouse=True)
def calendar_of_this_data_dir(tmp_data_dir):
    """A calendar cached while DATA_DIR pointed at the real data would carry its observed holidays."""
    reload_calendar()
    yield
    reload_calendar()


def make_loader(frames: dict[str, pd.DataFrame]):
    def loader(instrument_id, tf, start=None, end=None):
        return frames[instrument_id]

    return loader


def pooled(rows: pd.DataFrame) -> pd.DataFrame:
    return rows[rows["instrument"] == "ALL"].set_index(KEY).sort_index()


@pytest.fixture(scope="module")
def random_frames():
    return {i: synthetic_candles("1D", "2012-01-01", "2026-03-31", seed=10 + k) for k, i in enumerate(IDS)}


def test_scorecard_on_random_data(tmp_data_dir, no_keys, random_frames):
    cfg = load_research_config()
    card = build_scorecard("1D", IDS, load=make_loader(random_frames))
    rows = card.rows
    assert list(rows.columns) == ROW_COLUMNS
    assert len(rows) > 100 and card.meta.n_rows == len(rows)
    family = rows["n_clusters"] >= cfg.min_samples
    assert card.meta.n_tests == int(family.sum()) < len(rows)
    assert rows.loc[family, "q_value"].notna().all() and rows.loc[~family, "q_value"].isna().all()
    assert (rows.loc[family, "q_value"] >= rows.loc[family, "p_value"] - 1e-12).all()
    assert card.meta.train_end == cfg.train_end_for("1D").isoformat()
    assert card.meta.holdout_start == cfg.holdout_start.isoformat()
    assert card.meta.horizons == list(cfg.horizons) and card.meta.fdr_alpha == cfg.fdr_alpha
    assert card.meta.instruments == IDS
    config_dir = get_settings().config_dir
    assert card.meta.config_sha256 == {
        name: hashlib.sha256((config_dir / name).read_bytes()).hexdigest() for name in HASHED_CONFIGS
    }
    assert set(rows["horizon_bars"]) == set(cfg.horizons)
    assert set(rows["instrument"]) == {*IDS, "ALL"}
    assert {"all", "trend=up", "trend=down", "vol_regime=high", "expiry=yes", "expiry=no"} <= set(
        rows["context"]
    )
    assert ((rows["ci_low"] <= rows["hit_rate"]) & (rows["hit_rate"] <= rows["ci_high"])).all()
    assert ((rows["p_value"] >= 0) & (rows["p_value"] <= 1)).all()
    assert (rows["n_clusters"] <= rows["n"]).all() and (rows["n_clusters"] >= 1).all()
    assert not rows["certified"].any()
    assert rows.loc[rows["direction"] == "neutral", "expectancy_after_cost_pct"].isna().all()

    per = rows[rows["instrument"] != "ALL"].groupby(KEY)[["n", "hits"]].sum()
    assert pooled(rows)[["n", "hits"]].equals(per.sort_index())

    one = rows[
        (rows["instrument"] != "ALL")
        & (rows["pattern"] == "inside_bar")
        & (rows["context"] == "all")
        & (rows["horizon_bars"] == 1)
    ]
    both = pooled(rows).loc[("inside_bar", "all", 1)]
    assert both["base_rate"] == pytest.approx((one["n"] * one["base_rate"]).sum() / one["n"].sum())

    derived = tmp_data_dir / "derived"
    for name in ("scorecard", "analogs", "validation", "validation_base"):
        assert (derived / f"{name}_1D.parquet").exists(), name
    meta = json.loads((derived / "scorecard_1D.json").read_text())
    assert meta["exchange"] == "NSE"
    assert set(meta) == {
        "tf",
        "exchange",
        "built_at",
        "train_end",
        "holdout_start",
        "n_tests",
        "fdr_alpha",
        "horizons",
        "n_rows",
        "instruments",
        "config_sha256",
    }
    assert card.analogs["end_ts"].max() < cfg.holdout_start_utc

    loaded = load_scorecard("1D")
    assert loaded is not None and loaded.meta == card.meta
    pd.testing.assert_frame_equal(loaded.rows, rows)
    pd.testing.assert_frame_equal(loaded.validation, card.validation)
    pd.testing.assert_frame_equal(loaded.validation_base, card.validation_base)
    stats = loaded.stats_for("inside_bar", "NSE:TCS", "up", 1, cfg.min_samples)
    assert stats is not None and stats.horizon_bars == 1

    v = card.validation
    assert list(v.columns) == validation_columns(cfg) and len(v)
    assert (v["start"] >= cfg.train_end_utc("1D").value).all()
    assert ((v["up_1"] + v["down_1"])[v["up_1"].notna()] <= 1).all()  # a flat close is neither
    assert set(card.validation_base["context"]) >= {"all", "trend=up", "expiry=no"}


def test_config_hashes_cover_every_input_and_the_observed_holidays(tmp_data_dir, no_keys):
    config_dir = get_settings().config_dir
    assert {"expiry.yaml", "markets.yaml", "watchlist.yaml"} <= set(HASHED_CONFIGS)
    before = config_hashes()
    assert before == {n: hashlib.sha256((config_dir / n).read_bytes()).hexdigest() for n in HASHED_CONFIGS}
    holidays = tmp_data_dir / "derived" / "holidays_observed.json"
    holidays.parent.mkdir(parents=True)
    holidays.write_text('{"NSE": ["2024-01-22"]}', encoding="utf-8")
    after = config_hashes()
    assert after["holidays_observed.json"] == hashlib.sha256(holidays.read_bytes()).hexdigest()
    assert {k: v for k, v in after.items() if k != "holidays_observed.json"} == before


def test_expiry_buckets_only_cover_instruments_with_expiries(tmp_data_dir, no_keys, random_frames):
    ids = ["NSE:RELIANCE", "NSE:INDIAVIX"]
    frames = {"NSE:RELIANCE": random_frames[IDS[0]], "NSE:INDIAVIX": random_frames[IDS[1]]}
    rows = build_scorecard("1D", ids, load=make_loader(frames), persist=False).rows
    expiry = rows[rows["context"].str.startswith("expiry=")]
    assert set(expiry["instrument"]) == {"NSE:RELIANCE", "ALL"}
    own = expiry[expiry["instrument"] == "NSE:RELIANCE"].set_index(KEY)["n"].sort_index()
    assert pooled(expiry)["n"].equals(own)
    # a stock has one monthly expiry a month, so about 1 bar in 20 is an expiry day
    own_rows = rows[(rows["instrument"] == "NSE:RELIANCE") & (rows["horizon_bars"] == 1)]
    busiest = own_rows.loc[own_rows["context"] == "all"].sort_values("n")["pattern"].iloc[-1]
    by_ctx = own_rows[own_rows["pattern"] == busiest].set_index("context")["n"]
    assert by_ctx["expiry=yes"] + by_ctx["expiry=no"] == by_ctx["all"]
    assert 0 < by_ctx["expiry=yes"] < 0.1 * by_ctx["all"]


def test_overlapping_windows_form_fewer_clusters(tmp_data_dir, no_keys, random_frames):
    rows = build_scorecard("1D", IDS, load=make_loader(random_frames), persist=False).rows
    single = rows[(rows["instrument"] != "ALL") & (rows["horizon_bars"] == 1)]
    assert (single["n_clusters"] == single["n"]).all()
    busy = rows[(rows["horizon_bars"] == 5) & (rows["n"] >= 30)]
    assert len(busy) and (busy["n_clusters"] <= busy["n"]).all()
    assert busy["n_clusters"].sum() < busy["n"].sum()
    per_instrument = rows[rows["instrument"] != "ALL"].groupby(KEY)["n_clusters"].sum().sort_index()
    assert (pooled(rows)["n_clusters"] <= per_instrument).all()


def test_duplicated_instruments_are_not_more_significant(tmp_data_dir, no_keys, random_frames):
    df = random_frames[IDS[0]]
    alone = build_scorecard("1D", IDS[:1], load=make_loader({IDS[0]: df}), persist=False).rows
    twins = build_scorecard("1D", IDS, load=make_loader(dict.fromkeys(IDS, df)), persist=False).rows
    a, b = pooled(alone), pooled(twins)
    assert a.index.equals(b.index)
    assert (b["n"] == 2 * a["n"]).all()
    assert (b["n_clusters"] == a["n_clusters"]).all()
    np.testing.assert_allclose(b["p_value"], a["p_value"], rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(b["base_rate"], a["base_rate"], rtol=1e-12)


def test_holdout_data_cannot_change_the_scorecard(tmp_data_dir, no_keys, random_frames):
    cfg = load_research_config()
    first = build_scorecard("1D", IDS, load=make_loader(random_frames), persist=False)
    altered = {}
    for i, df in random_frames.items():
        late = df["ts"] >= cfg.holdout_start_utc
        altered[i] = df.assign(
            open=df["open"].where(~late, df["open"] * 3),
            high=df["high"].where(~late, df["high"] * 3),
            low=df["low"].where(~late, df["low"] * 3),
            close=df["close"].where(~late, df["close"] * 3),
        )
    second = build_scorecard("1D", IDS, load=make_loader(altered), persist=False)
    pd.testing.assert_frame_equal(first.rows, second.rows)


def test_planted_edge_is_found_and_certified(tmp_data_dir, no_keys):
    frames = {
        i: plant_marubozu_edge(
            synthetic_candles("1D", "2012-01-01", "2025-12-31", seed=20 + k, plain=True), seed=k
        )
        for k, i in enumerate(IDS)
    }
    card = build_scorecard("1D", IDS, load=make_loader(frames), persist=False)
    rows = card.rows
    row = pooled(rows).loc[("bullish_marubozu", "all", 1)]
    assert row["n"] >= 30 and row["hit_rate"] > 0.75
    # both instruments carry the signal on the same days (one may miss a day: a marubozu too small for
    # its ATR), so a day's events form one cluster
    assert row["n"] // 2 <= row["n_clusters"] <= row["n"] // 2 + 1
    assert row["q_value"] < 0.01 and row["expectancy_after_cost_pct"] > 0
    assert row["validation_n"] >= 30 and row["validation_hit_rate"] > 0.75
    assert row["certified"]
    assert row["posterior"] < row["hit_rate"]
    # a planted marubozu after a bearish bar in a decline is also a bullish engulfing, so patterns that
    # fire on planted bars share the edge; nothing else may be certified
    carriers = set()
    for df in frames.values():
        found = detect_patterns(df, "1D")
        planted = found.loc[found["pattern"] == "bullish_marubozu", "ts"]
        carriers |= set(found.loc[found["ts"].isin(planted), "pattern"])
    others = rows[rows["certified"] & ~rows["pattern"].isin(carriers)]
    assert others.empty, others[["pattern", "context", "instrument", "horizon_bars"]]

    bullish = card.validate_bucket(["bullish_marubozu"], IDS[0], "all", 1, True, **gate_kwargs())
    assert bullish.validated and bullish.instrument == IDS[0] and bullish.p_value < 0.001
    assert bullish.hit_rate > 0.7 > 0.6 > bullish.base_rate
    assert not card.validate_bucket(["bullish_marubozu"], IDS[0], "all", 1, False, **gate_kwargs()).validated


def check(card: Scorecard, patterns=("hammer",), context="trend=down", horizon=1, bullish=True, **extra):
    """card.validate_bucket for RELIANCE with the research.yaml gate settings."""
    return card.validate_bucket(list(patterns), IDS[0], context, horizon, bullish, **gate_kwargs(), **extra)


def test_a_null_bucket_rarely_passes_validation_and_a_real_edge_does():
    cfg = load_research_config()
    rng = np.random.default_rng(0)
    passed = {0.5: [], 0.7: []}
    for _ in range(300):
        for rate, out in passed.items():
            events = bucket_events((rng.uniform(size=120) < rate).astype(float))
            out.append(check(validation_card(events, bucket_base(IDS, 0.5)), horizon=3).validated)
    assert np.mean(passed[0.5]) < cfg.validation_max_p + 0.04  # a null bucket passes ~max_p of the time
    assert np.mean(passed[0.7]) > 0.95


def test_validation_counts_each_event_once_and_needs_independent_clusters():
    ups = (np.arange(200) % 10 < 7).astype(float)  # 70% up
    both = pd.concat([bucket_events(ups), bucket_events(ups, pattern="bullish_engulfing")], ignore_index=True)
    card = validation_card(both, bucket_base(IDS, 0.5))
    one = check(card)
    assert check(card, ["hammer", "bullish_engulfing"]) == one  # an event carrying both counts once
    assert one.n == one.n_clusters == 200 and one.validated and one.p_value < 0.001
    assert one.hit_rate == pytest.approx(0.7) and one.base_rate == pytest.approx(0.5)

    # the same 200 events with 10-day outcome windows overlap in chains: too few independent clusters
    v = check(validation_card(bucket_events(ups, window_days=10), bucket_base(IDS, 0.5)))
    assert not v.validated and v.instrument is None and v.n == 200 and v.n_clusters < 30

    # the bucket is compared with its own base rate, in the call's direction, with no 1 - rate flip
    assert not check(validation_card(both, bucket_base(IDS, 0.75))).validated
    flat = validation_card(bucket_events(ups).assign(down_1=0.0), bucket_base(IDS, 0.5))
    short = check(flat, bullish=False)
    assert short.hit_rate == 0.0 and not short.validated  # flat closes are not moves the call's way
    assert check(card, context="trend=up").n == 0


def test_thin_own_evidence_falls_back_to_the_same_exchange_pool_only():
    rng = np.random.default_rng(3)
    own = bucket_events((rng.uniform(size=10) < 0.2).astype(float))
    peer = bucket_events(np.ones(150), instrument="NSE:TCS", first="2021-01-01")
    v = check(validation_card(pd.concat([own, peer], ignore_index=True), bucket_base(IDS, 0.5)))
    assert v.validated and v.instrument == "ALL" and v.n == 160

    mixed = pd.concat([own, peer.assign(instrument="BSE:SENSEX")], ignore_index=True)
    card = validation_card(mixed, bucket_base([IDS[0], "BSE:SENSEX"], 0.5), exchange=None)
    v = check(card)
    assert not v.validated and v.instrument is None and v.n == 10
    loose = check(card, same_exchange=False)
    assert loose.validated and loose.instrument == "ALL"


def regime_candles(seed: int, drift: float = 0.3, block: int = 120) -> pd.DataFrame:
    """Alternating up/down drift regimes in the opening gaps; bar shapes, and so patterns, carry no edge."""
    n = len(bar_times("1D", "2012-01-01", "2025-09-30"))
    sign = np.repeat(np.random.default_rng(seed).choice([-1.0, 1.0], size=n // block + 1), block)[:n]
    return synthetic_candles("1D", "2012-01-01", "2025-09-30", seed=seed, drift=drift * sign)


def test_trend_buckets_are_tested_against_their_own_base_rate(tmp_data_dir, no_keys, monkeypatch):
    frames = {i: regime_candles(100 + k) for k, i in enumerate(IDS)}
    cfg = load_research_config()
    rows = build_scorecard("1D", IDS, load=make_loader(frames), persist=False).rows
    trend = rows[rows["context"].str.startswith("trend=")]
    up = pooled(rows).xs("trend=up", level="context")
    assert (up["base_rate"][up["direction"] == "bullish"] > 0.55).all()
    assert not trend["certified"].any()

    # the same data judged against the unconditional base rate "finds" trend-bucket edges
    unconditional = dataclasses.replace(cfg, bucket_null="unconditional")
    monkeypatch.setattr(scorecard_module, "load_research_config", lambda: unconditional)
    naive = build_scorecard("1D", IDS, load=make_loader(frames), persist=False).rows
    assert naive.loc[naive["context"].str.startswith("trend="), "certified"].any()


def test_default_universe_is_the_go_no_go_slice(tmp_data_dir, no_keys):
    s = load_research_config().go_no_go_1.slice
    ids = default_instruments("1D")
    assert ids and all(i.startswith(f"{s.exchange}:") for i in ids)
    assert not set(ids) & set(s.exclude)
    assert "BSE:SENSEX" not in ids and not any(i.startswith("MCX:") for i in ids)
    with pytest.raises(ValueError, match="one exchange"):
        build_scorecard("1D", ["NSE:RELIANCE", "BSE:SENSEX"], load=make_loader({}), persist=False)


def test_each_exchange_has_its_own_scorecard(tmp_data_dir, no_keys, random_frames):
    assert exchange_universe("1D", "NSE") == default_instruments("1D")
    assert exchange_universe("1D", "BSE") == ["BSE:SENSEX"]
    assert exchange_universe("1D", "MCX") == ["MCX:CRUDEOIL", "MCX:NATURALGAS", "MCX:GOLD", "MCX:SILVER"]
    mcx = {
        i: synthetic_candles("1D", "2012-01-01", "2025-09-30", exchange="MCX", seed=60 + k)
        for k, i in enumerate(("MCX:GOLD", "MCX:SILVER"))
    }
    frames = {**random_frames, **mcx}
    nse = build_scorecard("1D", IDS, load=make_loader(frames))
    gold = build_scorecard("1D", list(mcx), load=make_loader(frames))
    assert (nse.meta.exchange, gold.meta.exchange) == ("NSE", "MCX")
    assert set(gold.rows["instrument"]) == {*mcx, "ALL"} and set(gold.validation["instrument"]) == set(mcx)
    assert scorecard_paths("1D", "MCX")["rows"].name == "scorecard_1D_MCX.parquet"
    assert scorecard_paths("1D")["rows"].name == "scorecard_1D.parquet"
    assert load_scorecard("1D", "MCX").meta == gold.meta and load_scorecard("1D").meta == nse.meta
    assert load_scorecard("1D", "BSE") is None
    with pytest.raises(ValueError, match="not on MCX"):
        build_scorecard("1D", IDS, load=make_loader(frames), exchange="MCX", persist=False)

    # stats and validation never come from another exchange's rows or events
    cfg = load_research_config()
    assert gold.stats_for("inside_bar", "MCX:GOLD", "up", 1, cfg.min_samples) is not None
    assert gold.stats_for("inside_bar", "NSE:RELIANCE", "up", 1, cfg.min_samples) is None
    v = gold.validate_bucket(["inside_bar"], "NSE:RELIANCE", "all", 1, True, **gate_kwargs())
    assert v.n == 0 and not v.validated


def test_intraday_events_stay_inside_the_session(tmp_data_dir, no_keys, monkeypatch):
    cfg = load_research_config()
    df = synthetic_candles("15m", "2022-10-01", "2023-02-28", seed=95, vol=0.003)
    inst = get_instrument("NSE:RELIANCE")
    day = df["ts"].dt.tz_convert(IST).dt.date
    labels = forward_labels(df, cfg.horizons)
    pos = pd.Index(df["ts"])

    def same_session(events: pd.DataFrame, h: int) -> np.ndarray:
        i = pos.get_indexer(pd.to_datetime(events["start"], utc=True))
        return (day.shift(-h).to_numpy()[i] == day.to_numpy()[i])

    gated = scorecard_module._prepare_instrument(inst, "15m", df, cfg).events
    for h in cfg.horizons:
        crossing = ~same_session(gated, h)
        assert crossing.any() and gated.loc[crossing, f"up_{h}"].isna().all()
        assert gated.loc[~crossing, f"up_{h}"].notna().any()

    loose = dataclasses.replace(cfg, intraday_within_session=False)
    events = scorecard_module._prepare_instrument(inst, "15m", df, loose).events
    events = events[pos.get_indexer(pd.to_datetime(events["start"], utc=True)) < len(df) - 1]
    i = pos.get_indexer(pd.to_datetime(events["start"], utc=True))
    trade = labels["trade_ret_1"].to_numpy()[i]
    same = same_session(events, 1)
    intraday, multi_day = round_trip_cost("equity", "intraday"), round_trip_cost("equity", "multi_day")
    # the last bar of a session is not a same-day trade at h=1, although its entry and exit bar match
    assert (~same).any() and events.loc[~same, "up_1"].notna().all()
    np.testing.assert_allclose(events["net_long_1"].to_numpy()[same], trade[same] - intraday)
    np.testing.assert_allclose(events["net_long_1"].to_numpy()[~same], trade[~same] - multi_day)


def test_load_scorecard_missing(tmp_data_dir, no_keys):
    assert load_scorecard("15m") is None


def test_intraday_scorecard_uses_the_intraday_train_end(tmp_data_dir, no_keys):
    cfg = load_research_config()
    frames = {
        i: synthetic_candles("15m", "2022-10-01", "2023-02-28", seed=90 + k, vol=0.003)
        for k, i in enumerate(IDS)
    }
    card = build_scorecard("15m", IDS, load=make_loader(frames), persist=False)
    rows = card.rows
    assert card.meta.train_end == cfg.train_end_for("15m").isoformat()
    assert len(rows) > 0 and (rows["validation_n"] > 0).any() and not rows["certified"].any()
    directional = rows[rows["direction"] != "neutral"]
    assert directional["expectancy_after_cost_pct"].notna().all()

    before = {i: df[df["ts"] < cfg.train_end_utc("15m")] for i, df in frames.items()}
    early = build_scorecard("15m", IDS, load=make_loader(before), persist=False).rows
    assert (early["validation_n"] == 0).all()
