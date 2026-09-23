"""Pattern scorecard (PLAN.md §6).

Protocol (all data before the holdout):
- Walk-forward boundaries are anchored on the earliest bar across the instruments. The first test
  window opens `min_train_years` later (= train_end); test windows tile [train_end, holdout).
- Headline statistics (n, hits, base_rate, CI, p, q, posterior, expectancy) use only train rows:
  bars before train_end, minus the purge + embargo gap. Base rates come from the same train rows.
- Validation statistics pool every walk-forward test window, so they are out-of-sample for the headline.
- Hits are counted in the pattern's direction: up at h for bullish (and neutral) patterns, down at h for
  bearish ones. base_rate is the matching unconditional rate on the same rows.
- Pooled rows ("ALL") use an event-weighted base rate: sum(n_i * base_i) / sum(n_i).
- Every row is one hypothesis test; Benjamini-Hochberg runs across all of them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property, lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import BaseModel

from candly.core.calendar import IST
from candly.core.instruments import Instrument, get_instrument, load_watchlist
from candly.core.settings import get_settings
from candly.core.timeframes import is_intraday, validate_tf
from candly.features.context import compute_context
from candly.patterns import PATTERN_INFO, detect_patterns
from candly.research.config import ResearchConfig, load_research_config
from candly.research.costs import round_trip_cost
from candly.research.data import CandleLoader, load_research_candles
from candly.research.labels import forward_end_ts, forward_labels, forward_paths, path_columns
from candly.research.splits import fold_boundaries, train_positions
from candly.research.stats import benjamini_hochberg, beta_posterior, binomial_pvalue, wilson_interval

ROW_COLUMNS = [
    "pattern",
    "label",
    "direction",
    "instrument",
    "context",
    "horizon_bars",
    "n",
    "hits",
    "hit_rate",
    "base_rate",
    "ci_low",
    "ci_high",
    "p_value",
    "q_value",
    "posterior",
    "expectancy_after_cost_pct",
    "validation_n",
    "validation_hit_rate",
    "validation_base_rate",
    "certified",
]
CONTEXT_DIMENSIONS = ("trend", "vol_regime")
ANALOG_COLUMNS = ["instrument", "ts", "end_ts", "pattern", "direction", "trend", "vol_regime", "atr", "close"]


class ScorecardMeta(BaseModel):
    tf: str
    built_at: int | None
    train_end: str
    holdout_start: str
    n_tests: int
    fdr_alpha: float
    horizons: list[int]


class ScoreStats(BaseModel):
    horizon_bars: int
    n: int
    hit_rate: float
    base_rate: float
    ci_low: float
    ci_high: float
    posterior: float
    q_value: float | None
    expectancy_after_cost_pct: float | None
    certified: bool


@dataclass
class Scorecard:
    meta: ScorecardMeta
    rows: pd.DataFrame
    analogs: pd.DataFrame | None = None

    @cached_property
    def _index(self) -> dict[tuple, int]:
        keys = zip(
            self.rows["pattern"],
            self.rows["instrument"],
            self.rows["context"],
            self.rows["horizon_bars"],
            strict=True,
        )
        return {(p, i, c, int(h)): pos for pos, (p, i, c, h) in enumerate(keys)}

    def lookup(self, pattern: str, instrument: str, context: str, horizon: int) -> pd.Series | None:
        pos = self._index.get((pattern, instrument, context, int(horizon)))
        return None if pos is None else self.rows.iloc[pos]

    def stats_for(
        self, pattern: str, instrument: str, trend: str | None, horizon: int, min_samples: int
    ) -> ScoreStats | None:
        """Most specific row with at least `min_samples`: instrument before ALL, trend bucket before
        "all". Falls back to the largest-n candidate when none is big enough."""
        candidates = []
        for inst in (instrument, "ALL"):
            for ctx in ([f"trend={trend}"] if trend else []) + ["all"]:
                row = self.lookup(pattern, inst, ctx, horizon)
                if row is not None:
                    if row["n"] >= min_samples:
                        return to_stats(row)
                    candidates.append(row)
        return to_stats(max(candidates, key=lambda r: r["n"])) if candidates else None


def _num(x) -> float | None:
    return None if x is None or pd.isna(x) else float(x)


def to_stats(row: pd.Series) -> ScoreStats:
    return ScoreStats(
        horizon_bars=int(row["horizon_bars"]),
        n=int(row["n"]),
        hit_rate=float(row["hit_rate"]),
        base_rate=float(row["base_rate"]),
        ci_low=float(row["ci_low"]),
        ci_high=float(row["ci_high"]),
        posterior=float(row["posterior"]),
        q_value=_num(row["q_value"]),
        expectancy_after_cost_pct=_num(row["expectancy_after_cost_pct"]),
        certified=bool(row["certified"]),
    )


def default_instruments(tf: str) -> list[str]:
    return [i.id for i in load_watchlist() if tf in i.timeframes and i.tradable]


@dataclass
class _Prepared:
    events: pd.DataFrame
    base: pd.DataFrame
    analogs: pd.DataFrame


def _prepare_instrument(
    inst: Instrument, tf: str, df: pd.DataFrame, cfg: ResearchConfig, validation_start: pd.Timestamp | None
) -> _Prepared:
    ts = df["ts"]
    ctx = compute_context(df, tf, inst.exchange)
    atr = ctx["atr14"]
    labels = forward_labels(df, cfg.horizons)
    split = np.full(len(df), "gap", dtype=object)
    train_before = validation_start if validation_start is not None else cfg.holdout_start_utc
    split[train_positions(ts, train_before, cfg)] = "train"
    if validation_start is not None:
        split[(ts >= validation_start).to_numpy()] = "validation"

    cost_multi = round_trip_cost(inst.kind, "multi_day")
    cost_intra = round_trip_cost(inst.kind, "intraday") if is_intraday(tf) else cost_multi
    day = ts.dt.tz_convert(IST).dt.date
    bar_cols: dict[str, pd.Series | np.ndarray] = {"split": split}
    base_rows = []
    for h in cfg.horizons:
        same_day = (day.shift(-h) == day.shift(-1)).to_numpy() if is_intraday(tf) else np.zeros(len(df), bool)
        cost = np.where(same_day, cost_intra, cost_multi)
        bar_cols[f"up_{h}"] = labels[f"up_{h}"].to_numpy()
        bar_cols[f"down_{h}"] = labels[f"down_{h}"].to_numpy()
        bar_cols[f"net_long_{h}"] = labels[f"trade_ret_{h}"].to_numpy() - cost
        bar_cols[f"net_short_{h}"] = -labels[f"trade_ret_{h}"].to_numpy() - cost
        for s in ("train", "validation"):
            mask = (split == s) & labels[f"up_{h}"].notna().to_numpy()
            if mask.any():
                base_rows.append(
                    {
                        "instrument": inst.id,
                        "split": s,
                        "horizon_bars": h,
                        "base_up": float(labels[f"up_{h}"].to_numpy()[mask].mean()),
                        "base_down": float(labels[f"down_{h}"].to_numpy()[mask].mean()),
                    }
                )
    bars = pd.DataFrame(bar_cols, index=df.index)

    pats = detect_patterns(df, tf)
    p_idx = pd.Index(ts).get_indexer(pats["ts"])
    events = pd.DataFrame(
        {
            "instrument": inst.id,
            "pattern": pats["pattern"].to_numpy(),
            "direction": pats["direction"].to_numpy(),
            "trend": ctx["trend"].to_numpy()[p_idx],
            "vol_regime": ctx["vol_regime"].to_numpy()[p_idx],
        }
    )
    for col in bars.columns:
        events[col] = bars[col].to_numpy()[p_idx]

    steps = cfg.max_path_bars
    paths = forward_paths(df, atr, steps)[p_idx].reshape(len(p_idx), steps * 4)
    analogs = pd.DataFrame(
        {
            "instrument": inst.id,
            "ts": pats["ts"].array,
            "end_ts": forward_end_ts(ts, steps).array[p_idx],
            "pattern": pats["pattern"].to_numpy(),
            "direction": pats["direction"].to_numpy(),
            "trend": ctx["trend"].to_numpy()[p_idx],
            "vol_regime": ctx["vol_regime"].to_numpy()[p_idx],
            "atr": atr.to_numpy()[p_idx],
            "close": df["close"].to_numpy()[p_idx],
        }
    )
    analogs = pd.concat([analogs, pd.DataFrame(paths, columns=path_columns(steps))], axis=1)
    analogs = analogs[analogs["end_ts"].notna() & analogs["atr"].gt(0)]
    return _Prepared(events, pd.DataFrame(base_rows), analogs)


def _bucketed(events: pd.DataFrame) -> pd.DataFrame:
    parts = [events.assign(context="all")]
    for dim in CONTEXT_DIMENSIONS:
        sub = events[events[dim].notna()]
        parts.append(sub.assign(context=dim + "=" + sub[dim].astype(str)))
    return pd.concat(parts, ignore_index=True)


def _aggregate(bucketed: pd.DataFrame, base: pd.DataFrame, horizons: Iterable[int]) -> pd.DataFrame:
    keys = ["pattern", "direction", "context", "instrument", "split"]
    out = []
    for h in horizons:
        bearish = bucketed["direction"] == "bearish"
        hit = np.where(bearish, bucketed[f"down_{h}"], bucketed[f"up_{h}"])
        net = np.where(
            bucketed["direction"] == "bullish",
            bucketed[f"net_long_{h}"],
            np.where(bearish, bucketed[f"net_short_{h}"], np.nan),
        )
        frame = bucketed[keys].assign(hit=hit, net=net)
        frame = frame[frame["hit"].notna() & frame["split"].isin(["train", "validation"])]
        agg = (
            frame.groupby(keys, observed=True)
            .agg(n=("hit", "size"), hits=("hit", "sum"), net_sum=("net", "sum"))
            .reset_index()
        )
        b = base[base["horizon_bars"] == h]
        agg = agg.merge(
            b[["instrument", "split", "base_up", "base_down"]], on=["instrument", "split"], how="left"
        )
        agg["base_x_n"] = np.where(agg["direction"] == "bearish", agg["base_down"], agg["base_up"]) * agg["n"]
        pooled = (
            agg.groupby(["pattern", "direction", "context", "split"], observed=True)[
                ["n", "hits", "net_sum", "base_x_n"]
            ]
            .sum()
            .reset_index()
            .assign(instrument="ALL")
        )
        both = pd.concat([agg.drop(columns=["base_up", "base_down"]), pooled], ignore_index=True)
        both["horizon_bars"] = h
        out.append(both)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _finalise(agg: pd.DataFrame, cfg: ResearchConfig) -> pd.DataFrame:
    keys = ["pattern", "direction", "context", "instrument", "horizon_bars"]
    train = agg[agg["split"] == "train"].drop(columns="split")
    val = agg[agg["split"] == "validation"][[*keys, "n", "hits", "base_x_n"]].rename(
        columns={"n": "validation_n", "hits": "validation_hits", "base_x_n": "validation_base_x_n"}
    )
    rows = train.merge(val, on=keys, how="left")
    rows = rows[rows["n"] > 0].reset_index(drop=True)
    if rows.empty:
        return pd.DataFrame(columns=ROW_COLUMNS)
    rows["validation_n"] = rows["validation_n"].fillna(0).astype("int64")
    rows["n"] = rows["n"].astype("int64")
    rows["hits"] = rows["hits"].astype("int64")
    rows["hit_rate"] = rows["hits"] / rows["n"]
    rows["base_rate"] = rows["base_x_n"] / rows["n"]
    rows["ci_low"], rows["ci_high"] = wilson_interval(rows["hits"], rows["n"], cfg.ci_level)
    rows["p_value"] = [
        binomial_pvalue(k, n, b) for k, n, b in zip(rows["hits"], rows["n"], rows["base_rate"], strict=True)
    ]
    rows["q_value"] = benjamini_hochberg(rows["p_value"].to_numpy())
    rows["posterior"] = beta_posterior(rows["hits"], rows["n"], rows["base_rate"], cfg.prior_strength)
    directional = rows["direction"] != "neutral"
    rows["expectancy_after_cost_pct"] = (100.0 * rows["net_sum"] / rows["n"]).where(directional)
    has_val = rows["validation_n"] > 0
    rows["validation_hit_rate"] = (rows["validation_hits"] / rows["validation_n"]).where(has_val)
    rows["validation_base_rate"] = (rows["validation_base_x_n"] / rows["validation_n"]).where(has_val)
    rows["certified"] = (
        (
            directional
            & (rows["n"] >= cfg.min_samples)
            & (rows["q_value"] < cfg.fdr_alpha)
            & (rows["expectancy_after_cost_pct"] > 0)
            & (rows["hit_rate"] > rows["base_rate"])
            & (rows["validation_n"] >= cfg.min_samples)
            & (rows["validation_hit_rate"] > rows["validation_base_rate"])
        )
        .fillna(False)
        .astype(bool)
    )
    rows["label"] = rows["pattern"].map(lambda p: PATTERN_INFO[p].label)
    order = {name: i for i, name in enumerate(PATTERN_INFO)}
    rows = rows.assign(_o=rows["pattern"].map(order), _all=rows["instrument"] != "ALL")
    rows = rows.sort_values(["_o", "_all", "instrument", "context", "horizon_bars"]).reset_index(drop=True)
    return rows[ROW_COLUMNS]


def _paths(tf: str) -> tuple[Path, Path, Path]:
    d = get_settings().derived_dir
    return d / f"scorecard_{tf}.parquet", d / f"scorecard_{tf}.json", d / f"analogs_{tf}.parquet"


def build_scorecard(
    tf: str,
    instruments: Iterable[str] | None = None,
    load: CandleLoader | None = None,
    *,
    persist: bool = True,
) -> Scorecard:
    """Build (and by default persist) the scorecard for one timeframe from pre-holdout data only."""
    validate_tf(tf)
    cfg = load_research_config()
    ids = list(instruments) if instruments is not None else default_instruments(tf)
    frames = {}
    for instrument_id in ids:
        df = load_research_candles(instrument_id, tf, load=load)
        if not df.empty:
            frames[instrument_id] = df
    start = min((df["ts"].iloc[0] for df in frames.values()), default=None)
    bounds = fold_boundaries(start, cfg) if start is not None else []
    validation_start = bounds[0][0] if bounds else None

    prepared = [
        _prepare_instrument(get_instrument(i), tf, df, cfg, validation_start) for i, df in frames.items()
    ]
    events = pd.concat([p.events for p in prepared], ignore_index=True) if prepared else pd.DataFrame()
    base = pd.concat([p.base for p in prepared], ignore_index=True) if prepared else pd.DataFrame()
    analogs = (
        pd.concat([p.analogs for p in prepared], ignore_index=True)
        if prepared
        else pd.DataFrame(columns=ANALOG_COLUMNS)
    )
    if events.empty or base.empty:
        rows = pd.DataFrame(columns=ROW_COLUMNS)
    else:
        rows = _finalise(_aggregate(_bucketed(events), base, cfg.horizons), cfg)

    train_end = validation_start.tz_convert(IST).date() if validation_start is not None else cfg.holdout_start
    meta = ScorecardMeta(
        tf=tf,
        built_at=int(datetime.now(UTC).timestamp()),
        train_end=train_end.isoformat(),
        holdout_start=cfg.holdout_start.isoformat(),
        n_tests=len(rows),
        fdr_alpha=cfg.fdr_alpha,
        horizons=list(cfg.horizons),
    )
    card = Scorecard(meta, rows, analogs)
    if persist:
        save_scorecard(card)
    return card


def save_scorecard(card: Scorecard) -> None:
    rows_path, meta_path, analogs_path = _paths(card.meta.tf)
    rows_path.parent.mkdir(parents=True, exist_ok=True)
    for path, frame in ((rows_path, card.rows), (analogs_path, card.analogs)):
        if frame is None:
            continue
        tmp = path.with_suffix(".tmp")
        frame.to_parquet(tmp, index=False)
        tmp.replace(path)
    tmp = meta_path.with_suffix(".tmp")
    tmp.write_text(card.meta.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(meta_path)


@lru_cache(maxsize=8)
def _load_cached(
    rows_path: str, rows_mtime: float, meta_mtime: float, analogs_mtime: float | None
) -> Scorecard:
    rows_file = Path(rows_path)
    meta = ScorecardMeta(**json.loads(rows_file.with_suffix(".json").read_text(encoding="utf-8")))
    rows = pd.read_parquet(rows_file)
    analogs_file = rows_file.with_name(rows_file.name.replace("scorecard_", "analogs_"))
    analogs = pd.read_parquet(analogs_file) if analogs_mtime is not None else None
    return Scorecard(meta, rows, analogs)


def load_scorecard(tf: str) -> Scorecard | None:
    rows_path, meta_path, analogs_path = _paths(validate_tf(tf))
    if not (rows_path.exists() and meta_path.exists()):
        return None
    analogs_mtime = analogs_path.stat().st_mtime if analogs_path.exists() else None
    return _load_cached(str(rows_path), rows_path.stat().st_mtime, meta_path.stat().st_mtime, analogs_mtime)
