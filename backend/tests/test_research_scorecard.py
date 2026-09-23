import json

import pandas as pd
import pytest

from candly.research.config import load_research_config
from candly.research.scorecard import ROW_COLUMNS, build_scorecard, load_scorecard
from candly.research.synthetic import plant_marubozu_edge, synthetic_candles

IDS = ["NSE:RELIANCE", "NSE:TCS"]


def make_loader(frames: dict[str, pd.DataFrame]):
    def loader(instrument_id, tf, start=None, end=None):
        return frames[instrument_id]

    return loader


@pytest.fixture
def random_frames():
    return {i: synthetic_candles("1D", "2012-01-01", "2026-03-31", seed=10 + k) for k, i in enumerate(IDS)}


def test_scorecard_on_random_data(tmp_data_dir, no_keys, random_frames):
    cfg = load_research_config()
    card = build_scorecard("1D", IDS, load=make_loader(random_frames))
    rows = card.rows
    assert list(rows.columns) == ROW_COLUMNS
    assert len(rows) > 100 and card.meta.n_tests == len(rows)
    assert card.meta.train_end == "2015-01-02"  # first bar is Mon 2012-01-02, plus min_train_years
    assert card.meta.holdout_start == cfg.holdout_start.isoformat()
    assert card.meta.horizons == list(cfg.horizons) and card.meta.fdr_alpha == cfg.fdr_alpha
    assert set(rows["horizon_bars"]) == set(cfg.horizons)
    assert set(rows["instrument"]) == {*IDS, "ALL"}
    assert {"all", "trend=up", "trend=down", "vol_regime=high"} <= set(rows["context"])
    assert (rows["q_value"] >= rows["p_value"] - 1e-12).all()
    assert ((rows["ci_low"] <= rows["hit_rate"]) & (rows["hit_rate"] <= rows["ci_high"])).all()
    assert not rows["certified"].any()
    assert rows.loc[rows["direction"] == "neutral", "expectancy_after_cost_pct"].isna().all()

    key = ["pattern", "context", "horizon_bars"]
    per = rows[rows["instrument"] != "ALL"].groupby(key)[["n", "hits"]].sum()
    pooled = rows[rows["instrument"] == "ALL"].set_index(key)[["n", "hits"]]
    assert pooled.sort_index().equals(per.sort_index())

    one = rows[
        (rows["instrument"] != "ALL")
        & (rows["pattern"] == "inside_bar")
        & (rows["context"] == "all")
        & (rows["horizon_bars"] == 1)
    ]
    both = rows[
        (rows["instrument"] == "ALL")
        & (rows["pattern"] == "inside_bar")
        & (rows["context"] == "all")
        & (rows["horizon_bars"] == 1)
    ].iloc[0]
    assert both["base_rate"] == pytest.approx((one["n"] * one["base_rate"]).sum() / one["n"].sum())

    derived = tmp_data_dir / "derived"
    assert (derived / "scorecard_1D.parquet").exists() and (derived / "analogs_1D.parquet").exists()
    meta = json.loads((derived / "scorecard_1D.json").read_text())
    assert set(meta) == {"tf", "built_at", "train_end", "holdout_start", "n_tests", "fdr_alpha", "horizons"}
    assert card.analogs["end_ts"].max() < cfg.holdout_start_utc

    loaded = load_scorecard("1D")
    assert loaded is not None and loaded.meta == card.meta
    pd.testing.assert_frame_equal(loaded.rows, rows)
    stats = loaded.stats_for("inside_bar", "NSE:TCS", "up", 1, cfg.min_samples)
    assert stats is not None and stats.horizon_bars == 1


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
    row = rows[
        (rows["pattern"] == "bullish_marubozu")
        & (rows["instrument"] == "ALL")
        & (rows["context"] == "all")
        & (rows["horizon_bars"] == 1)
    ].iloc[0]
    assert row["n"] >= 30 and row["hit_rate"] > 0.75
    assert row["q_value"] < 0.01 and row["expectancy_after_cost_pct"] > 0
    assert row["validation_n"] >= 30 and row["validation_hit_rate"] > 0.75
    assert row["certified"]
    assert row["posterior"] < row["hit_rate"]
    others = rows[rows["certified"] & ~rows["pattern"].isin(["bullish_marubozu"])]
    assert others.empty, others[["pattern", "context", "instrument", "horizon_bars"]]


def test_load_scorecard_missing(tmp_data_dir, no_keys):
    assert load_scorecard("15m") is None


def test_intraday_scorecard_without_enough_history_for_validation(tmp_data_dir, no_keys):
    cfg = load_research_config()
    frames = {
        i: synthetic_candles("15m", "2024-01-01", "2024-04-30", seed=90 + k, vol=0.003)
        for k, i in enumerate(IDS)
    }
    card = build_scorecard("15m", IDS, load=make_loader(frames), persist=False)
    rows = card.rows
    assert len(rows) > 0 and card.meta.train_end == cfg.holdout_start.isoformat()
    assert (rows["validation_n"] == 0).all() and not rows["certified"].any()
    directional = rows[rows["direction"] != "neutral"]
    assert directional["expectancy_after_cost_pct"].notna().all()
