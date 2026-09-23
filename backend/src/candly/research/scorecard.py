"""Pattern scorecard (PLAN.md §6 and §12).

Protocol (all data before the holdout; every setting from research.yaml):
- Headline statistics use "train" bars: before the fixed train_end[tf], minus the purge + embargo gap.
  Validation statistics pool the walk-forward test windows [train_end, holdout).
- Hits are counted in the pattern's direction: up at h for bullish (and neutral) patterns, down at h for
  bearish ones.
- Null (stats.bucket_null): each event is compared with the base rate of its own instrument x split x
  context bucket, so a "trend=down" row is tested against how often any down-trend bar moved that way.
  base_rate is the mean of the events' bases, which for pooled ("ALL") rows is the event-weighted mean.
- Test (stats.test = cluster_robust): events whose outcome windows (t, t+h] overlap in time form one
  cluster; for ALL rows, clusters span instruments. With r = hit - base,
      z = sum_i r_i / sqrt(sum_c (sum_{i in c} r_i)^2)
  p is one-sided (H1: hit rate above base) for bullish and bearish rows, whose direction is fixed by the
  pattern definition before any data is seen and is the only direction that can be certified, and
  two-sided for neutral rows, which have no direction.
- Benjamini-Hochberg runs over the rows with n_clusters >= min_samples (stats.bh_family). Rows outside
  that family get no q-value and can't be certified. The Wilson CI is for display only.
- A build pools one exchange only, so ALL rows never mix NSE with BSE or MCX.
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
from pydantic import BaseModel, Field

from candly.core.calendar import IST
from candly.core.instruments import Instrument, exchange_of, get_instrument, load_watchlist
from candly.core.settings import get_settings
from candly.core.timeframes import is_intraday, validate_tf
from candly.features.context import compute_context
from candly.patterns import PATTERN_INFO, detect_patterns
from candly.research.config import ResearchConfig, config_hashes, load_research_config
from candly.research.costs import round_trip_cost
from candly.research.data import CandleLoader, load_research_candles
from candly.research.labels import forward_end_ts, forward_labels, forward_paths, path_columns
from candly.research.splits import split_labels
from candly.research.stats import (
    benjamini_hochberg,
    beta_posterior,
    overlap_cluster_ids,
    wilson_interval,
    z_pvalue,
)

ROW_COLUMNS = [
    "pattern",
    "label",
    "direction",
    "instrument",
    "context",
    "horizon_bars",
    "n",
    "hits",
    "n_clusters",
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
ANALOG_COLUMNS = ["instrument", "ts", "end_ts", "pattern", "direction", "trend", "vol_regime", "atr", "close"]
SPLITS = ("train", "validation")
ROW_KEYS = ["pattern", "direction", "context", "instrument", "horizon_bars"]
GROUP_KEYS = [*ROW_KEYS, "split"]


class ScorecardMeta(BaseModel):
    tf: str
    built_at: int | None
    train_end: str
    holdout_start: str
    n_tests: int  # rows in the Benjamini-Hochberg family (hypotheses tested)
    fdr_alpha: float
    horizons: list[int]
    n_rows: int | None = None
    instruments: list[str] = Field(default_factory=list)
    config_sha256: dict[str, str] = Field(default_factory=dict)


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


@dataclass(frozen=True)
class BucketValidation:
    validated: bool
    instrument: str | None  # whose rows decided (the instrument's own or "ALL"); None: too few events
    n: int
    hit_rate: float | None  # validation rate of moves in the call's direction
    base_rate: float | None


@dataclass
class Scorecard:
    meta: ScorecardMeta
    rows: pd.DataFrame
    analogs: pd.DataFrame | None = None

    def validate_bucket(
        self,
        patterns: Iterable[str],
        instrument: str,
        context: str,
        horizon: int,
        bullish: bool,
        min_samples: int,
    ) -> BucketValidation:
        """Validation-period evidence for an analog bucket (the union of `patterns` in `context`) in the
        call's direction. Each row's validation hit and base rates become rates of moves the call's way
        (one minus them when the row counts hits the other way; flat closes are ignored), pooled over
        the patterns by validation_n. The instrument's own rows decide when they hold at least
        `min_samples` validation events, else the pooled ALL rows."""
        n_seen = 0
        for inst in (instrument, "ALL"):
            n = hits = base = 0.0
            for pattern in patterns:
                row = self.lookup(pattern, inst, context, horizon)
                if row is None:
                    continue
                vn = int(row.get("validation_n", 0) or 0)
                vh, vb = _num(row.get("validation_hit_rate")), _num(row.get("validation_base_rate"))
                if vn == 0 or vh is None or vb is None:
                    continue
                if (row["direction"] != "bearish") != bullish:
                    vh, vb = 1.0 - vh, 1.0 - vb
                n, hits, base = n + vn, hits + vn * vh, base + vn * vb
            n_seen = max(n_seen, int(n))
            if n >= min_samples:
                return BucketValidation(hits > base, inst, int(n), hits / n, base / n)
        return BucketValidation(False, None, n_seen, None, None)

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
    """The go/no-go slice's universe (research.yaml go_no_go_1.slice: one exchange, its kinds, minus
    the exclusions) for any timeframe."""
    s = load_research_config().go_no_go_1.slice
    return [
        i.id
        for i in load_watchlist()
        if i.exchange == s.exchange
        and i.kind in s.kinds
        and i.id not in s.exclude
        and i.tradable
        and tf in i.timeframes
    ]


@dataclass
class _Prepared:
    events: pd.DataFrame
    base: pd.DataFrame
    analogs: pd.DataFrame


def _ns(ts: pd.Series) -> np.ndarray:
    return ts.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().astype("datetime64[ns]").view("int64")


def _with_context(frame: pd.DataFrame, dims: Iterable[str]) -> pd.DataFrame:
    """One copy of each row per context bucket it belongs to: "all" and "<dim>=<value>" for each of
    research.yaml scorecard.context_buckets (rows with no value for a dimension skip its buckets)."""
    parts = [frame.assign(context="all")]
    for dim in dims:
        sub = frame[frame[dim].notna()]
        parts.append(sub.assign(context=dim + "=" + sub[dim].astype(str)))
    return pd.concat(parts, ignore_index=True)


def _context_values(ctx: pd.DataFrame, dim: str) -> np.ndarray:
    """Bucket values per bar; expiry is "yes"/"no" on instruments with expiries and None elsewhere."""
    if dim == "expiry":
        flag = ctx["expiry_day"]
        return np.where(flag.eq(True), "yes", np.where(flag.eq(False), "no", None)).astype(object)
    return ctx[dim].to_numpy()


def _base_rates(
    bars: pd.DataFrame, instrument: str, horizons: Iterable[int], dims: tuple[str, ...]
) -> pd.DataFrame:
    """P(up at h) and P(down at h) over every bar of an instrument, per split x context bucket."""
    cols = [c for h in horizons for c in (f"up_{h}", f"down_{h}")]
    frame = _with_context(bars.loc[bars["split"].isin(SPLITS), ["split", *dims, *cols]], dims)
    means = frame.groupby(["split", "context"], observed=True)[cols].mean()
    parts = [
        means[[f"up_{h}", f"down_{h}"]]
        .set_axis(["base_up", "base_down"], axis=1)
        .reset_index()
        .assign(horizon_bars=h)
        for h in horizons
    ]
    return pd.concat(parts, ignore_index=True).assign(instrument=instrument)


def _prepare_instrument(inst: Instrument, tf: str, df: pd.DataFrame, cfg: ResearchConfig) -> _Prepared:
    ts = df["ts"]
    n = len(df)
    ctx = compute_context(df, tf, inst.exchange, instrument_id=inst.id)
    atr = ctx["atr14"]
    labels = forward_labels(df, cfg.horizons)
    start = _ns(ts)

    long_multi = round_trip_cost(inst.kind, "multi_day")
    short_multi = round_trip_cost(inst.kind, "multi_day", side="short")
    long_intra = round_trip_cost(inst.kind, "intraday") if is_intraday(tf) else long_multi
    short_intra = round_trip_cost(inst.kind, "intraday", side="short") if is_intraday(tf) else short_multi
    day = ts.dt.tz_convert(IST).dt.date
    bar_cols: dict[str, np.ndarray] = {
        "split": split_labels(ts, tf, cfg),
        **{dim: _context_values(ctx, dim) for dim in cfg.context_buckets},
        "start": start,
    }
    for h in cfg.horizons:
        # Daily bars are never "same day", so a daily h=1 trade (next open to that close, an intraday
        # round trip) keeps the higher multi-day cost on purpose: the conservative choice.
        same_day = (day.shift(-h) == day.shift(-1)).to_numpy() if is_intraday(tf) else np.zeros(n, bool)
        trade = labels[f"trade_ret_{h}"].to_numpy()
        end = np.full(n, np.iinfo(np.int64).max)
        end[: max(0, n - h)] = start[h:]
        bar_cols[f"up_{h}"] = labels[f"up_{h}"].to_numpy()
        bar_cols[f"down_{h}"] = labels[f"down_{h}"].to_numpy()
        bar_cols[f"end_{h}"] = end
        bar_cols[f"net_long_{h}"] = trade - np.where(same_day, long_intra, long_multi)
        bar_cols[f"net_short_{h}"] = -trade - np.where(same_day, short_intra, short_multi)
    bars = pd.DataFrame(bar_cols, index=df.index)
    base = _base_rates(bars, inst.id, cfg.horizons, cfg.context_buckets)

    pats = detect_patterns(df, tf)
    p_idx = pd.Index(ts).get_indexer(pats["ts"])
    events = bars.iloc[p_idx].reset_index(drop=True)
    events.insert(0, "direction", pats["direction"].to_numpy())
    events.insert(0, "pattern", pats["pattern"].to_numpy())
    events.insert(0, "instrument", inst.id)

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
    return _Prepared(events, base, analogs)


def _event_outcomes(events: pd.DataFrame, base: pd.DataFrame, cfg: ResearchConfig) -> pd.DataFrame:
    """One row per event x context bucket x horizon with its hit, net return, null base rate and window."""
    bucketed = _with_context(events, cfg.context_buckets)
    bullish = bucketed["direction"].eq("bullish").to_numpy()
    bearish = bucketed["direction"].eq("bearish").to_numpy()
    parts = []
    for h in cfg.horizons:
        parts.append(
            pd.DataFrame(
                {
                    "pattern": bucketed["pattern"],
                    "direction": bucketed["direction"],
                    "context": bucketed["context"],
                    "instrument": bucketed["instrument"],
                    "split": bucketed["split"],
                    "horizon_bars": h,
                    "start": bucketed["start"],
                    "end": bucketed[f"end_{h}"],
                    "hit": np.where(bearish, bucketed[f"down_{h}"], bucketed[f"up_{h}"]),
                    "net": np.where(
                        bullish,
                        bucketed[f"net_long_{h}"],
                        np.where(bearish, bucketed[f"net_short_{h}"], np.nan),
                    ),
                }
            )
        )
    out = pd.concat(parts, ignore_index=True)
    out = out[out["hit"].notna() & out["split"].isin(SPLITS)]
    null_context = out["context"] if cfg.bucket_null == "bucket_base_rate" else "all"
    out = out.assign(null_context=null_context).merge(
        base.rename(columns={"context": "null_context"}),
        on=["instrument", "split", "null_context", "horizon_bars"],
        how="left",
    )
    out["base"] = np.where(out["direction"] == "bearish", out["base_down"], out["base_up"])
    out["resid"] = out["hit"] - out["base"]
    return out.drop(columns=["null_context", "base_up", "base_down"])


def _group_stats(frame: pd.DataFrame) -> pd.DataFrame:
    """Counts, sums and the cluster-robust variance per row x split."""
    groups = frame.groupby(GROUP_KEYS, sort=False, observed=True)
    gid = groups.ngroup().to_numpy()
    cid = overlap_cluster_ids(gid, frame["start"].to_numpy(), frame["end"].to_numpy())
    cluster_sum = np.bincount(cid, weights=frame["resid"].to_numpy())
    cluster_gid = np.empty(cluster_sum.size, dtype=np.int64)
    cluster_gid[cid] = gid
    n_groups = int(gid.max()) + 1
    out = groups.agg(
        n=("hit", "size"),
        hits=("hit", "sum"),
        net_sum=("net", "sum"),
        base_sum=("base", "sum"),
        resid_sum=("resid", "sum"),
    ).reset_index()
    out["n_clusters"] = np.bincount(cluster_gid, minlength=n_groups)
    out["cluster_var"] = np.bincount(cluster_gid, weights=cluster_sum**2, minlength=n_groups)
    return out


def _aggregate(outcomes: pd.DataFrame) -> pd.DataFrame:
    return pd.concat(
        [_group_stats(outcomes), _group_stats(outcomes.assign(instrument="ALL"))], ignore_index=True
    )


def _finalise(agg: pd.DataFrame, cfg: ResearchConfig) -> tuple[pd.DataFrame, int]:
    """Rows in ROW_COLUMNS order, plus the size of the Benjamini-Hochberg family."""
    train = agg[agg["split"] == "train"].drop(columns="split")
    val = agg[agg["split"] == "validation"][[*ROW_KEYS, "n", "hits", "base_sum"]].rename(
        columns={"n": "validation_n", "hits": "validation_hits", "base_sum": "validation_base_sum"}
    )
    rows = train.merge(val, on=ROW_KEYS, how="left")
    rows = rows[rows["n"] > 0].reset_index(drop=True)
    if rows.empty:
        return pd.DataFrame(columns=ROW_COLUMNS), 0
    rows["validation_n"] = rows["validation_n"].fillna(0).astype("int64")
    rows["n"] = rows["n"].astype("int64")
    rows["hits"] = rows["hits"].astype("int64")
    rows["n_clusters"] = rows["n_clusters"].astype("int64")
    rows["hit_rate"] = rows["hits"] / rows["n"]
    rows["base_rate"] = rows["base_sum"] / rows["n"]
    rows["ci_low"], rows["ci_high"] = wilson_interval(rows["hits"], rows["n"], cfg.ci_level)

    var = rows["cluster_var"].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(var > 0, rows["resid_sum"].to_numpy() / np.sqrt(var), np.nan)
    directional = rows["direction"] != "neutral"
    rows["p_value"] = z_pvalue(z, two_sided=~directional.to_numpy())
    family = (
        (rows["n_clusters"] >= cfg.min_samples).to_numpy()
        if cfg.bh_family == "min_samples"
        else np.ones(len(rows), bool)
    )
    q = np.full(len(rows), np.nan)
    q[family] = benjamini_hochberg(rows["p_value"].to_numpy()[family])
    rows["q_value"] = q

    rows["posterior"] = beta_posterior(rows["hits"], rows["n"], rows["base_rate"], cfg.prior_strength)
    rows["expectancy_after_cost_pct"] = (100.0 * rows["net_sum"] / rows["n"]).where(directional)
    has_val = rows["validation_n"] > 0
    rows["validation_hit_rate"] = (rows["validation_hits"] / rows["validation_n"]).where(has_val)
    rows["validation_base_rate"] = (rows["validation_base_sum"] / rows["validation_n"]).where(has_val)
    rows["certified"] = (
        (
            directional
            & (rows["n"] >= cfg.min_samples)
            & (rows["n_clusters"] >= cfg.min_clusters)
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
    return rows[ROW_COLUMNS], int(family.sum())


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
    """Build (and by default persist) the scorecard for one timeframe from pre-holdout data only.

    `instruments` defaults to the go/no-go slice's universe. All instruments must share one exchange.
    """
    validate_tf(tf)
    cfg = load_research_config()
    hashes = config_hashes()
    ids = list(instruments) if instruments is not None else default_instruments(tf)
    exchanges = sorted({exchange_of(i) for i in ids})
    if len(exchanges) > 1:
        raise ValueError(f"ALL rows pool one exchange; build {exchanges} separately")
    frames = {}
    for instrument_id in ids:
        df = load_research_candles(instrument_id, tf, load=load)
        if not df.empty:
            frames[instrument_id] = df

    prepared = [_prepare_instrument(get_instrument(i), tf, df, cfg) for i, df in frames.items()]
    events = pd.concat([p.events for p in prepared], ignore_index=True) if prepared else pd.DataFrame()
    base = pd.concat([p.base for p in prepared], ignore_index=True) if prepared else pd.DataFrame()
    analogs = (
        pd.concat([p.analogs for p in prepared], ignore_index=True)
        if prepared
        else pd.DataFrame(columns=ANALOG_COLUMNS)
    )
    rows, n_tests = pd.DataFrame(columns=ROW_COLUMNS), 0
    if not (events.empty or base.empty):
        outcomes = _event_outcomes(events, base, cfg)
        if not outcomes.empty:
            rows, n_tests = _finalise(_aggregate(outcomes), cfg)

    meta = ScorecardMeta(
        tf=tf,
        built_at=int(datetime.now(UTC).timestamp()),
        train_end=cfg.train_end_for(tf).isoformat(),
        holdout_start=cfg.holdout_start.isoformat(),
        n_tests=n_tests,
        fdr_alpha=cfg.fdr_alpha,
        horizons=list(cfg.horizons),
        n_rows=len(rows),
        instruments=list(frames),
        config_sha256=hashes,
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
