import dataclasses
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from candly.core.settings import get_settings
from candly.patterns import detect_patterns
from candly.research import scorecard as scorecard_module
from candly.research.config import HASHED_CONFIGS, load_research_config
from candly.research.scorecard import ROW_COLUMNS, build_scorecard, default_instruments, load_scorecard
from candly.research.synthetic import bar_times, plant_marubozu_edge, synthetic_candles

IDS = ["NSE:RELIANCE", "NSE:TCS"]
KEY = ["pattern", "context", "horizon_bars"]


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
    assert (derived / "scorecard_1D.parquet").exists() and (derived / "analogs_1D.parquet").exists()
    meta = json.loads((derived / "scorecard_1D.json").read_text())
    assert set(meta) == {
        "tf",
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
    stats = loaded.stats_for("inside_bar", "NSE:TCS", "up", 1, cfg.min_samples)
    assert stats is not None and stats.horizon_bars == 1


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
    # both instruments carry the signal on the same days, so each day is one cluster
    assert row["n_clusters"] == row["n"] // 2
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
