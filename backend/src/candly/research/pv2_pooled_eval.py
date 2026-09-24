"""pv2_pooled: the v1 candlestick scorecard on the Nifty 200 universe (edge_search_v2.yaml patterns_pooled).

The statistics are research.scorecard's, unchanged: research.yaml horizons, context buckets, bucket base
rates, cluster-robust test, Benjamini-Hochberg within the scorecard over rows with at least min_samples
clusters, and the certification rule, with train bars before train_end[1D] less the purge + embargo gap
and validation from train_end to the holdout (never read).

Only the instrument list changes. build_scorecard resolves ids through the dashboard watchlist
(get_instrument), which does not hold the research universe, so this module runs the same steps
(_prepare_instrument, _event_outcomes, _aggregate, _finalise) on config/universe_nifty200.yaml's
instrument records, selected by the rule the v1 NSE build uses (research.yaml go_no_go_1.slice: NSE,
equities and indices, minus the exclusions, tradable, with the timeframe).

Memory: outcomes are expanded and aggregated one pattern at a time. A scorecard row's group (pattern,
direction, context, instrument or ALL, horizon, split) never spans two patterns and clusters form within a
group, so every count, sum and cluster variance equals build_scorecard's; _finalise then runs the
Benjamini-Hochberg step once over the whole family.

Primary: the Simes global p-value = the smallest BH-adjusted p-value (q) in the family. pass_if: at least
one certified bucket.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from candly.core.calendar import IST
from candly.core.instruments import Instrument
from candly.core.settings import REPO_ROOT, get_settings
from candly.data.ingest import load_universe
from candly.research.config import ResearchConfig, config_hashes, load_research_config
from candly.research.data import CandleLoader, load_research_candles
from candly.research.edge_v2_config import edge_v2_section, edge_v2_sha256
from candly.research.scorecard import (
    ROW_COLUMNS,
    ROW_KEYS,
    _aggregate,
    _event_outcomes,
    _finalise,
    _prepare_instrument,
    load_scorecard,
)
from candly.research.splits import split_labels
from candly.research.stats import z_pvalue

TEST_NAME = "pv2_pooled"
SECTION = "patterns_pooled"
REPORT_STEM = "2026-09-24-pv2-pooled-validation"
TOP_ROWS = 15
EXTREME_TRADE = 0.15  # |trade return| above this is flagged in event_summary (information only)
LARGE_GAP = 0.25  # overnight gaps beyond this are counted as a data-quality flag (information only)
VALIDATION_INFO = ["validation_n_clusters", "validation_p_one_sided", "validation_expectancy_after_cost_pct"]
ROW_STAT_COLUMNS = [c for c in ROW_COLUMNS if c != "label"] + VALIDATION_INFO


@dataclass(frozen=True)
class Registration:
    name: str
    universe: str  # the universe name load_universe takes ("nifty200")
    universe_path: str
    tf: str
    certified_min: int
    raw: dict


def load_registration(path: Path | None = None) -> Registration:
    raw = edge_v2_section(SECTION, path)
    if raw.get("name") != TEST_NAME:
        raise ValueError(f"{SECTION}.name is {raw.get('name')!r}; this module implements {TEST_NAME!r}")
    universe_path = str(raw["universe"])
    stem = Path(universe_path).stem
    if not stem.startswith("universe_"):
        raise ValueError(f"{SECTION}.universe {universe_path!r} is not a config/universe_<name>.yaml file")
    return Registration(
        name=raw["name"],
        universe=stem.removeprefix("universe_"),
        universe_path=universe_path,
        tf=str(raw["tf"]),
        certified_min=int(raw["pass_if"]["certified_buckets_min"]),
        raw=raw,
    )


def universe_instruments(universe: str, tf: str, cfg: ResearchConfig | None = None) -> list[Instrument]:
    """The universe's instruments the v1 NSE build would pool (scorecard.default_instruments' rule)."""
    s = (cfg or load_research_config()).go_no_go_1.slice
    return [
        i
        for i in load_universe(universe)
        if i.exchange == s.exchange and i.kind in s.kinds and i.id not in s.exclude and i.tradable
        and tf in i.timeframes
    ]


@dataclass
class PooledBuild:
    rows: pd.DataFrame
    n_tests: int
    instruments: list[str]
    skipped: list[str]  # no pre-holdout bars
    bars: dict[str, int]  # train / validation / gap bars over every instrument
    events: dict[str, int]  # pattern events per split
    first_bar: dict[str, str]
    validation: pd.DataFrame | None = None  # validation-split sums per row (validation_stats)
    certified_events: list[dict] | None = None  # event_summary of every certified row
    large_gaps: dict[str, int] | None = None  # overnight gaps beyond LARGE_GAP per instrument


def build_pooled_rows(
    instruments: list[Instrument],
    tf: str,
    *,
    load: CandleLoader | None = None,
    log: Callable[[str], None] | None = None,
) -> PooledBuild:
    """build_scorecard's rows and BH family size for these instrument records (never persisted)."""
    cfg = load_research_config()
    events, base, used, skipped, first = [], [], [], [], {}
    bars = {"train": 0, "validation": 0, "gap": 0}
    large_gaps: dict[str, int] = {}
    for k, inst in enumerate(instruments):
        df = load_research_candles(inst.id, tf, load=load)
        if df.empty:
            skipped.append(inst.id)
            continue
        prep = _prepare_instrument(inst, tf, df, cfg)
        events.append(prep.events)
        base.append(prep.base)
        used.append(inst.id)
        first[inst.id] = df["ts"].iloc[0].isoformat()
        labels = pd.Series(split_labels(df["ts"], tf, cfg)).value_counts()
        bars["train"] += int(labels.get("train", 0))
        bars["validation"] += int(labels.get("validation", 0))
        bars["gap"] += int(labels.get("gap", 0))
        jumps = int((np.abs(np.log(df["open"] / df["close"].shift(1))) > np.log(1 + LARGE_GAP)).sum())
        if jumps:
            large_gaps[inst.id] = jumps
        if log is not None and (k + 1) % 25 == 0:
            log(f"prepared {k + 1}/{len(instruments)} instruments")
    if not events:
        return PooledBuild(pd.DataFrame(columns=ROW_COLUMNS), 0, used, skipped, bars, {}, first)
    all_events = pd.concat(events, ignore_index=True)
    all_base = pd.concat(base, ignore_index=True)
    events.clear()
    counts = all_events["split"].value_counts()
    event_counts = {s: int(counts.get(s, 0)) for s in ("train", "validation")}
    parts = []
    for name, sub in all_events.groupby("pattern", sort=False):
        outcomes = _event_outcomes(sub, all_base, cfg)
        if not outcomes.empty:
            parts.append(_aggregate(outcomes))
        if log is not None:
            log(f"aggregated {name}: {len(sub)} events")
    if not parts:
        return PooledBuild(pd.DataFrame(columns=ROW_COLUMNS), 0, used, skipped, bars, event_counts, first)
    agg = pd.concat(parts, ignore_index=True)
    rows, n_tests = _finalise(agg, cfg)
    sums = [*ROW_KEYS, "n", "n_clusters", "net_sum", "resid_sum", "cluster_var"]
    validation = agg.loc[agg["split"] == "validation", sums].reset_index(drop=True)
    summaries = [event_summary(all_events, row) for _, row in rows[rows["certified"]].iterrows()]
    return PooledBuild(
        rows, n_tests, used, skipped, bars, event_counts, first, validation, summaries, large_gaps
    )


def event_summary(events: pd.DataFrame, row: pd.Series) -> dict:
    """Information only: a scorecard row's events by split and IST year, and its hit rate without the
    events whose trade moved more than EXTREME_TRADE (unadjusted corporate actions or bad prints)."""
    h = int(row["horizon_bars"])
    bearish = row["direction"] == "bearish"
    hit, net = (f"down_{h}", f"net_short_{h}") if bearish else (f"up_{h}", f"net_long_{h}")
    sel = (events["pattern"] == row["pattern"]) & events[hit].notna()
    if row["instrument"] != "ALL":
        sel &= events["instrument"] == row["instrument"]
    if row["context"] != "all":
        dim, _, value = str(row["context"]).partition("=")
        sel &= events[dim].astype(str) == value
    out: dict = {k: row[k] for k in ("pattern", "direction", "instrument", "context")} | {"horizon_bars": h}
    for split in ("train", "validation"):
        ev = events[sel & (events["split"] == split)]
        year = pd.to_datetime(ev["start"], utc=True).dt.tz_convert(IST).dt.year
        extreme = ev[net].abs() > EXTREME_TRADE
        by_year = ev.groupby(year.to_numpy())[hit].agg(["size", "mean"])
        out[split] = {
            "n": int(len(ev)),
            "hit_rate": float(ev[hit].mean()) if len(ev) else None,
            "net_after_cost_mean_pct": float(100 * ev[net].mean()) if len(ev) else None,
            "net_after_cost_median_pct": float(100 * ev[net].median()) if len(ev) else None,
            "extreme_trades": int(extreme.sum()),
            "hit_rate_without_extreme": float(ev.loc[~extreme, hit].mean()) if (~extreme).any() else None,
            "by_year": {int(y): [int(r["size"]), round(float(r["mean"]), 4)] for y, r in by_year.iterrows()},
        }
    return out


def validation_stats(rows: pd.DataFrame, validation: pd.DataFrame | None) -> pd.DataFrame:
    """Per row, information only: the validation span's cluster count, one-sided cluster-robust p (the
    live gate's test, H1: hit rate above the bucket base rate) and expectancy after cost. The
    certification rule itself only asks that the validation hit rate beat its base rate."""
    if validation is None or validation.empty or rows.empty:
        return pd.DataFrame(np.nan, index=rows.index, columns=VALIDATION_INFO)
    merged = rows[ROW_KEYS].merge(validation, on=ROW_KEYS, how="left")
    var = merged["cluster_var"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(var > 0, merged["resid_sum"].to_numpy(dtype=float) / np.sqrt(var), np.nan)
    directional = (merged["direction"] != "neutral").to_numpy()
    out = pd.DataFrame(
        {
            "validation_n_clusters": merged["n_clusters"].to_numpy(dtype=float),
            "validation_p_one_sided": np.where(np.isnan(z), np.nan, z_pvalue(z, two_sided=~directional)),
            "validation_expectancy_after_cost_pct": np.where(
                directional, 100.0 * merged["net_sum"] / merged["n"], np.nan
            ),
        }
    )
    out.index = rows.index
    return out


def _row_record(row: pd.Series) -> dict:
    out = {}
    for key in ROW_STAT_COLUMNS:
        value = row.get(key)
        if isinstance(value, (np.integer, np.floating, np.bool_)):
            value = value.item()
        out[key] = None if isinstance(value, float) and np.isnan(value) else value
    return out


def criteria_counts(rows: pd.DataFrame, cfg: ResearchConfig) -> dict:
    """How many BH-family rows clear each part of the certification rule (information only)."""
    fam = rows[rows["q_value"].notna()]
    directional = fam["direction"] != "neutral"
    checks = {
        "directional": directional,
        "q_below_fdr": fam["q_value"] < cfg.fdr_alpha,
        "hit_rate_above_base": fam["hit_rate"] > fam["base_rate"],
        "expectancy_after_cost_positive": fam["expectancy_after_cost_pct"] > 0,
        "n_clusters_min": fam["n_clusters"] >= cfg.min_clusters,
        "validation_n_min": fam["validation_n"] >= cfg.min_samples,
        "validation_hit_above_base": fam["validation_hit_rate"] > fam["validation_base_rate"],
    }
    out = {name: int(mask.fillna(False).sum()) for name, mask in checks.items()}
    out["q_below_fdr_and_directional"] = int((checks["q_below_fdr"] & directional).sum())
    return out


def v1_reference(tf: str) -> dict | None:
    """The persisted v1 NSE build of `tf` (the 12-instrument watchlist), for scale only."""
    card = load_scorecard(tf, "NSE")
    if card is None:
        return None
    q = card.rows["q_value"]
    events = None
    if card.analogs is not None:
        events = int(len(card.analogs))
    return {
        "instruments": card.meta.instruments,
        "n_tests": card.meta.n_tests,
        "certified": int(card.rows["certified"].sum()),
        "simes_global_p": float(q.min()) if q.notna().any() else None,
        "pattern_events_with_paths": events,
    }


def evaluate(
    *,
    load: CandleLoader | None = None,
    instruments: list[Instrument] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict:
    reg = load_registration()
    cfg = load_research_config()
    started = time.perf_counter()
    chosen = instruments if instruments is not None else universe_instruments(reg.universe, reg.tf, cfg)
    build = build_pooled_rows(chosen, reg.tf, load=load, log=log)
    rows = pd.concat([build.rows, validation_stats(build.rows, build.validation)], axis=1)
    family = rows[rows["q_value"].notna()]
    simes = float(family["q_value"].min()) if len(family) else None
    best = family.loc[family["q_value"].idxmin()] if len(family) else None
    certified = rows[rows["certified"]]
    top = family.sort_values(["q_value", "p_value"]).head(TOP_ROWS)
    pooled = family[family["instrument"] == "ALL"]
    n_certified = int(len(certified))
    return {
        "tf": reg.tf,
        "exchange": cfg.go_no_go_1.slice.exchange,
        "instruments_requested": len(chosen),
        "instruments_used": build.instruments,
        "instruments_skipped_no_pre_holdout_bars": build.skipped,
        "first_bar_by_instrument": build.first_bar,
        "bars": build.bars,
        "pattern_events": build.events,
        "n_rows": int(len(rows)),
        "n_tests": build.n_tests,
        "n_tests_pooled_all_rows": int(len(pooled)),
        "certified_buckets": n_certified,
        "certified": [_row_record(r) for _, r in certified.iterrows()],
        "certified_event_summary": build.certified_events or [],
        "data_quality": {
            "overnight_gaps_beyond": LARGE_GAP,
            "count": sum((build.large_gaps or {}).values()),
            "by_instrument": build.large_gaps or {},
        },
        "simes_global_p": simes,
        "simes_row": _row_record(best) if best is not None else None,
        "top_rows_by_q": [_row_record(r) for _, r in top.iterrows()],
        "criteria_counts": criteria_counts(rows, cfg),
        "pass_if": {"certified_buckets_min": reg.certified_min},
        "pass_if_met": n_certified >= reg.certified_min,
        "runtime_seconds": round(time.perf_counter() - started, 1),
        "rows_frame": rows,
    }


# ---------------------------------------------------------------- report


FAMILY_BH = (
    "pending: the test passes only if its Simes p also survives Benjamini-Hochberg at q = 0.10 across "
    "every edge_search_v2.yaml primary (applied by the lead)"
)
SURVIVORSHIP = (
    "Survivorship bias: the universe is today's Nifty 200 (constituents file downloaded 2026-09-24). Stocks "
    "that left the index, were delisted, merged or failed before today are missing, and each stock is in the "
    "sample only from its first candle, so the train years hold the firms that later did well enough to be "
    "large today. The registration treats a pass as an upper bound. Each bucket is tested against the base "
    "rate of the same survivor instruments and bucket, so the selection moves hit rates and base rates "
    "together and the direction of its effect on a hit-vs-base edge is not known; a bearish edge cannot be "
    "assumed understated. Only a point-in-time universe would settle it."
)
UNREGISTERED_CHOICES = [
    "Instruments: config/universe_nifty200.yaml filtered by the v1 NSE build's own rule (research.yaml "
    "go_no_go_1.slice: NSE, kinds equity and index, INDIAVIX excluded, tradable, has 1D). This keeps the "
    "file's two benchmark indices (NIFTY200, NIFTY50) alongside the 200 stocks.",
    "Instruments with no bar before the holdout (listed after 2025-10-01) contribute nothing and are listed.",
    "Primary p = the smallest q-value of the scorecard's Benjamini-Hochberg family (rows with n_clusters >= "
    "min_samples), which is the Simes global p-value of that family; neutral patterns keep the scorecard's "
    "two-sided p.",
]
DEVIATIONS = [
    "build_scorecard itself cannot take the universe (it resolves ids through the dashboard watchlist), so "
    "its internal steps are run on the universe's instrument records; outcomes are aggregated per pattern "
    "to fit in memory. A unit test checks the rows equal build_scorecard's on the same instruments.",
    "Expiry context: candly's expiry schedule resolves only watchlist instruments, so the 'expiry=yes/no' "
    "buckets exist for the watchlist names in the universe (the 12 v1 instruments that are in it) and for "
    "NIFTY50; the other stocks enter the 'all', trend and vol_regime buckets only. Giving every stock the "
    "stock-F&O monthly rule would assert F&O eligibility many of them did not have for their whole history.",
]
CAVEATS = [
    SURVIVORSHIP,
    "Validation span only for certification (train before 2019-01-01 less the gap, validation 2019-01-01 "
    "to 2025-10-01); the holdout was never loaded.",
    "Hit = close[t+h] beyond close[t] in the pattern's direction, but the expectancy trade enters at the "
    "next open (labels.trade_ret): for a bearish row at h=1 that is a short from the next open to the next "
    "close, an intraday short a cash-equity trader can take, charged the higher multi-day cost. Bearish rows "
    "at h > 1 need an overnight short (futures, available only for F&O names).",
    "Nifty 200 stocks move together: the cluster-robust test counts overlapping outcome windows across "
    "instruments as one cluster in the pooled ALL rows, so 200 stocks do not give 200x the independent "
    "evidence.",
    "This p-value is one of the primaries of edge_search_v2.yaml; the family Benjamini-Hochberg at q = 0.10 "
    "across every v2 primary is applied by the lead once all v2 tests have reported.",
]


def report_paths() -> tuple[Path, Path]:
    folder = REPO_ROOT / "docs" / "test-reports"
    return folder / f"{REPORT_STEM}.json", folder / f"{REPORT_STEM}.md"


def rows_path() -> Path:
    return get_settings().derived_dir / TEST_NAME / "scorecard_1D_nifty200.parquet"


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _row_line(r: dict) -> str:
    q = "n/a" if r["q_value"] is None else f"{r['q_value']:.3f}"
    exp = r["expectancy_after_cost_pct"]
    exp_s = "n/a" if exp is None else f"{exp:+.3f}%"
    val = (
        f"{r['validation_n']} / {_pct(r['validation_hit_rate'])} vs {_pct(r['validation_base_rate'])}"
        if r["validation_n"]
        else "0"
    )
    vp = r.get("validation_p_one_sided")
    ve = r.get("validation_expectancy_after_cost_pct")
    val_info = f"{'n/a' if vp is None else f'{vp:.3f}'} / {'n/a' if ve is None else f'{ve:+.3f}%'}"
    return (
        f"| {r['pattern']} | {r['direction']} | {r['instrument']} | {r['context']} | {r['horizon_bars']} | "
        f"{r['n']} ({r['n_clusters']}) | {_pct(r['hit_rate'])} vs {_pct(r['base_rate'])} | "
        f"{r['p_value']:.2g} | {q} | {exp_s} | {val} | {val_info} | {r['certified']} |"
    )


def render_markdown(report: dict) -> str:
    r = report["result"]
    verdict = "pass_if MET" if r["pass_if_met"] else "FAIL (pass_if not met)"
    simes = r["simes_global_p"]
    ref = report.get("v1_reference") or {}
    lines = [
        "# pv2_pooled validation: the v1 candlestick scorecard on the Nifty 200",
        "",
        f"Generated {report['generated_at']} by `python -m candly.research.pv2_pooled_eval`. "
        "Pre-registered in `config/edge_search_v2.yaml` "
        f"(patterns_pooled, sha256 `{report['edge_search_v2_sha256'][:12]}`). "
        "Train bars before 2019-01-01 (less the purge gap), validation 2019-01-01 to 2025-10-01; the holdout "
        "was not read.",
        "",
        f"**Result: {verdict}.** {r['certified_buckets']} certified buckets out of {r['n_tests']:,} tests "
        f"(BH family, q < {report['fdr_alpha']}); Simes global p = "
        f"{'n/a' if simes is None else f'{simes:.3f}'}. "
        f"Rule: at least {r['pass_if']['certified_buckets_min']} certified bucket. "
        f"Family Benjamini-Hochberg: {report['family_bh']}.",
        "",
        f"- Instruments: {len(r['instruments_used'])} used of {r['instruments_requested']} "
        f"({len(r['instruments_skipped_no_pre_holdout_bars'])} have no bar before the holdout).",
        f"- Bars: {r['bars']['train']:,} train, {r['bars']['validation']:,} validation. Pattern events: "
        f"{r['pattern_events'].get('train', 0):,} train, {r['pattern_events'].get('validation', 0):,} "
        "validation.",
        f"- Rows: {r['n_rows']:,}, of which {r['n_tests']:,} are in the BH family "
        f"({r['n_tests_pooled_all_rows']:,} pooled ALL rows).",
        f"- Data quality: {r['data_quality']['count']} overnight gaps beyond "
        f"{r['data_quality']['overnight_gaps_beyond']:.0%} in {len(r['data_quality']['by_instrument'])} "
        "instruments (unadjusted corporate actions or bad prints; counted, not removed).",
    ]
    if ref:
        lines.append(
            f"- For scale, the v1 NSE 1D build (12 watchlist instruments): {ref['n_tests']:,} tests, "
            f"{ref['certified']} certified, Simes p {ref['simes_global_p']:.3f}."
        )
    head = (
        "| pattern | dir | instrument | context | h | n (clusters) | hit vs base | p | q | expectancy "
        "after cost | validation n / hit vs base | validation p (info) / expectancy | certified |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    lines += ["", "## Certified buckets", ""]
    lines += [head, sep] + [_row_line(x) for x in r["certified"]] if r["certified"] else ["None."]
    for e in r.get("certified_event_summary", []):
        name = f"{e['pattern']} / {e['instrument']} / {e['context']} / h={e['horizon_bars']}"
        lines += ["", f"Events of {name} (information only):", ""]
        for split in ("train", "validation"):
            x = e[split]
            years = ", ".join(f"{y}: {v[0]} at {100 * v[1]:.0f}%" for y, v in x["by_year"].items())
            lines.append(
                f"- {split}: n {x['n']}, hit {_pct(x['hit_rate'])}, net after cost mean "
                f"{x['net_after_cost_mean_pct']:+.3f}% / median {x['net_after_cost_median_pct']:+.3f}%, "
                f"{x['extreme_trades']} trades beyond {EXTREME_TRADE:.0%} (hit without them "
                f"{_pct(x['hit_rate_without_extreme'])}); by year: {years}."
            )
    lines += ["", f"## Smallest q-values (top {TOP_ROWS})", "", head, sep]
    lines += [_row_line(x) for x in r["top_rows_by_q"]]
    cc = r["criteria_counts"]
    lines += [
        "",
        "## Where buckets fall short (BH-family rows passing each part of the rule)",
        "",
        *[f"- {k}: {v:,}" for k, v in cc.items()],
        "",
        "## Unregistered choices (made before any result)",
        "",
        *[f"- {x}" for x in report["unregistered_choices"]],
        "",
        "## Deviations",
        "",
        *[f"- {x}" for x in report["deviations"]],
        "",
        "## Caveats",
        "",
        *[f"- {x}" for x in report["caveats"]],
    ]
    return "\n".join(lines) + "\n"


def run_validation(*, write: bool = True, log: Callable[[str], None] | None = None) -> dict:
    reg = load_registration()
    cfg = load_research_config()
    result = evaluate(log=log)
    rows = result.pop("rows_frame")
    report = {
        "test": TEST_NAME,
        "section": SECTION,
        "registration": reg.raw,
        "edge_search_v2_sha256": edge_v2_sha256(),
        "config_sha256": config_hashes(),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "period": "train + validation (scorecard protocol)",
        "splits": {
            "train": f"first bar .. {cfg.train_end_for(reg.tf).isoformat()} less the purge + embargo gap",
            "validation": f"{cfg.train_end_for(reg.tf).isoformat()} .. {cfg.holdout_start.isoformat()}",
            "holdout_start": cfg.holdout_start.isoformat(),
        },
        "stats": {
            "horizons_bars": list(cfg.horizons),
            "context_buckets": list(cfg.context_buckets),
            "min_samples": cfg.min_samples,
            "min_clusters": cfg.min_clusters,
            "bh_family": cfg.bh_family,
            "test": "cluster_robust",
            "bucket_null": cfg.bucket_null,
        },
        "fdr_alpha": cfg.fdr_alpha,
        "primary": "Simes global p = smallest BH-adjusted bucket p-value",
        "primary_p": result["simes_global_p"],
        "n_hypotheses_in_family": result["n_tests"],
        "pass_if_met": result["pass_if_met"],
        "family_bh": FAMILY_BH,
        "unregistered_choices": UNREGISTERED_CHOICES,
        "deviations": DEVIATIONS,
        "caveats": CAVEATS,
        "survivorship_bias": SURVIVORSHIP,
        "v1_reference": v1_reference(reg.tf),
        "result": result,
    }
    if write:
        path = rows_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        rows.to_parquet(path, index=False)
        json_path, md_path = report_paths()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_bytes((json.dumps(report, indent=2, default=str) + "\n").encode("utf-8"))
        md_path.write_bytes(render_markdown(report).encode("utf-8"))
    return report


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="pv2_pooled: v1 scorecard on the Nifty 200 (validation)")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    logging.getLogger("candly.features.expiry").setLevel(logging.ERROR)

    def log(line: str) -> None:
        print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {line}", flush=True)

    report = run_validation(write=not args.no_write, log=log)
    log(
        f"certified {report['result']['certified_buckets']} of {report['result']['n_tests']} tests, "
        f"Simes p {report['primary_p']}"
    )


if __name__ == "__main__":
    main()
