"""pv2_range: do candlestick patterns sharpen range_v1? (config/edge_search_v2.yaml patterns_range, §20c)

range_v1 is retrained exactly as research.range_model trains it (same panel of bars, fold windows, purge
gap, seeded training sample, hyperparameters, seeds and number of rounds, through the same
`fit_range_model`) twice per fold: once as it is (the reproduced baseline) and once with the
`candlestick_patterns` group of research.pv2_patterns added before the instrument column. The only other
change is LightGBM's thread count (NUM_THREADS; the laptop is shared), recorded as a deviation.

Scoring reuses range_eval's per-row scores on the validation rows both models forecast (range_v1's
validation span, train_end[intraday] up to the holdout; the holdout is never loaded).

Primary, per timeframe: the Winkler improvement 1 - W(pv2) / W(range_v1 reproduced), summed over the
rows' 3 steps, one-sided > 0, with a date-block bootstrap (edge_search.yaml common.bootstrap): whole IST
dates are resampled with every instrument's forecasts of a date together, the CI is the percentile
interval and p = (1 + #{bootstrap improvement <= 0}) / (1 + resamples). The same draws score every
secondary comparison of the timeframe (coverage, pinball, and pv2 against the cached range_v1
predictions from data/derived/range_v1).

The reproduction check scores the cached range_v1 predictions through this harness (it must give the
Winkler range_eval reported) and compares the reproduced baseline with them.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from candly.core.settings import REPO_ROOT, get_settings
from candly.research import range_model
from candly.research.config import config_hashes
from candly.research.data import CandleLoader
from candly.research.edge_config import load_edge_config
from candly.research.edge_v2_config import edge_v2_section, edge_v2_sha256
from candly.research.lgbm import lgb
from candly.research.pivot_config import PivotConfig, load_pivot_config, pivot_config_hashes
from candly.research.pv2_patterns import (
    FORMING,
    LAGS,
    PATTERN_NAMES,
    PREFIX,
    confirmed_bits,
    expand,
    feature_names,
    lagged_bits,
)
from candly.research.range_eval import MethodOutput, _ist_dates, row_scores
from candly.research.range_model import (
    SEED,
    Panel,
    _sample,
    build_panel,
    fit_range_model,
    fold_windows,
    ghost_ohlc,
    panel_fingerprint,
    research_loader,
    train_rows,
)

MODEL_NAME = "pv2_range"
BASE_NAME = range_model.MODEL_NAME
SECTION = "patterns_range"
FEATURE_GROUP = "candlestick_patterns"
NUM_THREADS = 2
REPRODUCED_WITHIN = 0.001  # |W(reproduced) / W(reported) - 1| below this counts as reproduced
REPORT_STEM = "2026-09-24-pv2-range-validation"
V1_REPORT = "2026-09-24-range-v1-validation.json"
TARGETS = ("high", "low", "close")


# ---------------------------------------------------------------- registration


@dataclass(frozen=True)
class Registration:
    name: str
    base: str
    feature_group: str
    timeframes: tuple[str, ...]
    exchange: str
    min_improvement: float
    ci_lower_above: float
    min_passing: int
    rule: str
    raw: dict


def load_registration(path: Path | None = None) -> Registration:
    """edge_search_v2.yaml patterns_range, checked against what this module implements."""
    raw = edge_v2_section(SECTION, path)
    expected = {"name": MODEL_NAME, "base": BASE_NAME, "added_feature_group": FEATURE_GROUP}
    for key, value in expected.items():
        if raw.get(key) != value:
            raise ValueError(f"{SECTION}.{key} is {raw.get(key)!r}; this module implements {value!r}")
    gate = raw["pass_if"]
    timeframes = tuple(raw["timeframes"])
    words = str(gate["rule"]).split()
    if words[:2] != ["at", "least"] or words[3:] != ["of", str(len(timeframes)), "timeframes"]:
        raise ValueError(f"{SECTION}.pass_if.rule {gate['rule']!r} is not 'at least k of n timeframes'")
    return Registration(
        name=raw["name"],
        base=raw["base"],
        feature_group=raw["added_feature_group"],
        timeframes=timeframes,
        exchange=raw["exchange"],
        min_improvement=float(gate["improvement_point_min"]),
        ci_lower_above=float(gate["ci_lower_above"]),
        min_passing=int(words[2]),
        rule=str(gate["rule"]),
        raw=raw,
    )


# ---------------------------------------------------------------- features and fits


def panel_pattern_bits(
    panel: Panel, get: Callable[[str, str], pd.DataFrame], pivot: PivotConfig
) -> np.ndarray:
    """(len(panel), LAGS) pattern masks, from the same candles build_panel read for each instrument."""
    out = np.zeros((len(panel), LAGS), dtype=np.uint32)
    for code, iid in enumerate(panel.instruments):
        block = panel.block(code)
        df = get(iid, panel.tf)
        if panel.tf == "1D":
            df = df[df["ts"] >= pivot.daily_start_utc].reset_index(drop=True)
        same = len(df) == block.stop - block.start and bool(
            (pd.DatetimeIndex(df["ts"]) == panel.ts[block]).all()
        )
        if not same:
            raise ValueError(f"{iid} {panel.tf}: candles changed since the panel was built")
        out[block] = lagged_bits(confirmed_bits(df, panel.tf))
    return out


def augmented(panel: Panel, bits: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """range_v1's features of `rows`, then the pattern group, then the instrument code (still last, so
    fit_range_model keeps it as the categorical column)."""
    X = panel.X[rows]
    return np.concatenate([X[:, :-1], expand(bits[rows]), X[:, -1:]], axis=1)


@contextmanager
def lgbm_threads(n: int) -> Iterator[None]:
    """range_model's fits with `num_threads` = n; every other hyperparameter unchanged."""
    params = range_model.HYPERPARAMETERS
    old = params["num_threads"]
    params["num_threads"] = n
    try:
        yield
    finally:
        params["num_threads"] = old


def _importance(model: range_model.RangeModel) -> dict[str, np.ndarray]:
    """Gain and split counts per feature, summed over the model's boosters, per target and in total."""
    out: dict[str, np.ndarray] = {}
    for (_, target, _), booster in model.boosters.items():
        for kind in ("gain", "split"):
            value = booster.feature_importance(importance_type=kind).astype(np.float64)
            for key in (f"{kind}_{target}", kind):
                out[key] = out.get(key, 0.0) + value
    return out


@dataclass
class PairResult:
    """Walk-forward predictions of both models on the same rows, (n, steps, 3 targets, 3 quantiles)."""

    rows: np.ndarray
    base: np.ndarray
    pv2: np.ndarray
    fold: np.ndarray
    folds: list[dict]
    importance: dict[str, np.ndarray] = field(default_factory=dict)  # pv2 model, summed over folds
    base_importance: dict[str, np.ndarray] = field(default_factory=dict)


def _add(total: dict[str, np.ndarray], part: dict[str, np.ndarray]) -> None:
    for key, value in part.items():
        total[key] = total.get(key, 0.0) + value


def walk_forward_pair(
    panel: Panel,
    bits: np.ndarray,
    pivot: PivotConfig | None = None,
    *,
    threads: int = NUM_THREADS,
    log: Callable[[str], None] | None = None,
) -> PairResult:
    """range_model.walk_forward's folds, sample and seeds, fitting range_v1 and pv2_range on each."""
    pivot = pivot or load_pivot_config()
    spec = pivot.range_model
    gap = pivot.walk_forward.gap_bars
    names = [*panel.feature_names, *feature_names()]
    rows, base_preds, pv2_preds, folds_of, folds = [], [], [], [], []
    importance: dict[str, np.ndarray] = {}
    base_importance: dict[str, np.ndarray] = {}
    for k, (lo, hi) in enumerate(fold_windows(panel.tf, pivot)):
        test = np.flatnonzero(np.asarray((panel.ts >= lo) & (panel.ts < hi)))
        train = train_rows(panel, lo, gap)
        if not len(test) or not len(train):
            continue
        used = _sample(train, SEED + k)
        seconds = {}
        with lgbm_threads(threads):
            t0 = time.perf_counter()
            base = fit_range_model(
                panel.X[used], panel.Y[used], spec, panel.feature_names, panel.instruments, SEED + k
            )
            base_preds.append(base.predict(panel.X[test]).astype(np.float32))
            _add(base_importance, _importance(base))
            seconds["base"] = time.perf_counter() - t0
            del base
            t0 = time.perf_counter()
            model = fit_range_model(
                augmented(panel, bits, used), panel.Y[used], spec, names, panel.instruments, SEED + k
            )
            pv2_preds.append(model.predict(augmented(panel, bits, test)).astype(np.float32))
            _add(importance, _importance(model))
            seconds["pv2"] = time.perf_counter() - t0
            del model
        rows.append(test)
        folds_of.append(np.full(len(test), k))
        folds.append(
            {
                "fold": k,
                "test_start": lo.isoformat(),
                "test_end": hi.isoformat(),
                "train_rows": int(len(train)),
                "train_rows_used": int(len(used)),
                "train_last_ts": panel.ts[train].max().isoformat(),
                "test_rows": int(len(test)),
                "fit_seconds": {key: round(v, 1) for key, v in seconds.items()},
            }
        )
        if log is not None:
            log(
                f"{panel.exchange} {panel.tf} fold {k} {lo.date()}..{hi.date()}: {len(used)}/{len(train)} "
                f"train rows, {len(test)} test rows, base {seconds['base']:.0f}s, pv2 {seconds['pv2']:.0f}s"
            )
    if not rows:
        raise ValueError(f"no walk-forward folds for {panel.exchange} {panel.tf}")
    return PairResult(
        np.concatenate(rows),
        np.concatenate(base_preds),
        np.concatenate(pv2_preds),
        np.concatenate(folds_of),
        folds,
        importance,
        base_importance,
    )


# ---------------------------------------------------------------- caches


def derived_dir() -> Path:
    return get_settings().derived_dir / MODEL_NAME


def code_sha256() -> str:
    parts = (
        panel_pattern_bits, augmented, lgbm_threads, walk_forward_pair, confirmed_bits, lagged_bits, expand
    )
    digest = hashlib.sha256()
    for part in parts:
        digest.update(inspect.getsource(part).encode("utf-8"))
    return digest.hexdigest()[:16]


def training_settings(threads: int = NUM_THREADS) -> dict:
    """range_v1's settings (hyperparameters as range_model registers them), the thread count actually
    used, the pattern group and the code that adds it."""
    return {
        "range_v1": range_model.training_settings(),
        "num_threads_used": threads,
        "pattern_features": feature_names(),
        "patterns_yaml_sha256": config_hashes().get("patterns.yaml"),
        "code_sha256": code_sha256(),
    }


def _bits_sha(bits: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(bits).tobytes()).hexdigest()[:16]


def pair_path(exchange: str, tf: str) -> Path:
    return derived_dir() / f"walk_forward_{exchange}_{tf}.npz"


def save_pair(result: PairResult, panel: Panel, bits: np.ndarray, settings: dict) -> Path:
    path = pair_path(panel.exchange, panel.tf)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "fingerprint": panel_fingerprint(panel),
        "bits_sha256": _bits_sha(bits),
        "settings": settings,
        "folds": result.folds,
        "importance_keys": sorted(result.importance),
    }
    tmp = path.with_suffix(".tmp.npz")
    np.savez_compressed(
        tmp,
        rows=result.rows,
        base=result.base,
        pv2=result.pv2,
        fold=result.fold,
        meta=np.array(json.dumps(meta)),
        **{f"imp_{k}": v for k, v in result.importance.items()},
        **{f"baseimp_{k}": v for k, v in result.base_importance.items()},
    )
    tmp.replace(path)
    return path


def load_pair(panel: Panel, bits: np.ndarray, settings: dict) -> PairResult | None:
    """Cached predictions for exactly these bars, patterns, settings and code, else None."""
    path = pair_path(panel.exchange, panel.tf)
    if not path.exists():
        return None
    with np.load(path) as data:
        meta = json.loads(str(data["meta"]))
        if (
            meta.get("fingerprint") != panel_fingerprint(panel)
            or meta.get("bits_sha256") != _bits_sha(bits)
            or meta.get("settings") != settings
        ):
            return None
        importance = {k[4:]: data[k] for k in data.files if k.startswith("imp_")}
        base_importance = {k[8:]: data[k] for k in data.files if k.startswith("baseimp_")}
        return PairResult(
            data["rows"], data["base"], data["pv2"], data["fold"], meta["folds"], importance, base_importance
        )


def cached_range_v1(panel: Panel) -> tuple[np.ndarray, np.ndarray, dict] | None:
    """range_v1's own walk-forward predictions (rows, pred, meta) from data/derived/range_v1."""
    path = range_model.walk_forward_path(panel.exchange, panel.tf)
    if not path.exists():
        return None
    with np.load(path) as data:
        return data["rows"], data["pred"], json.loads(str(data["meta"]))


def reported_range_v1(exchange: str, tf: str) -> dict | None:
    path = REPO_ROOT / "docs" / "test-reports" / V1_REPORT
    if not path.exists():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    for r in report["results"]:
        if r["exchange"] == exchange and r["tf"] == tf and r["period"] == "validation":
            return r
    return None


# ---------------------------------------------------------------- statistics


@dataclass
class DateBootstrap:
    """One draw of whole-date resamples shared by every comparison of a timeframe. Per resample, each
    date's rows enter as many times as the date was drawn (the draw of stats.clustered_bootstrap_skill_ci
    with the same seed, so its CIs are the ones range_eval reports)."""

    inverse: np.ndarray
    counts: np.ndarray  # (resamples, dates)
    level: float
    n_dates: int

    @classmethod
    def draw(cls, dates: np.ndarray, resamples: int, seed: int, level: float) -> DateBootstrap:
        _, inverse = np.unique(np.asarray(dates), return_inverse=True)
        k = int(inverse.max()) + 1 if len(inverse) else 0
        counts = np.random.default_rng(seed).multinomial(k, np.full(k, 1.0 / k), size=resamples)
        return cls(inverse, counts, level, k)

    def sums(self, x: np.ndarray) -> np.ndarray:
        return self.counts @ np.bincount(self.inverse, weights=np.asarray(x, float), minlength=self.n_dates)

    def _interval(self, boot: np.ndarray) -> list[float]:
        tail = (1.0 - self.level) / 2.0
        return [float(v) for v in np.quantile(boot, [tail, 1.0 - tail])]

    def skill(self, model: np.ndarray, base: np.ndarray) -> dict:
        """1 - sum(model) / sum(base) (a loss improvement), CI, and the one-sided p of improvement > 0."""
        point = 1.0 - float(np.sum(model)) / float(np.sum(base))
        boot = 1.0 - self.sums(model) / self.sums(base)
        p = (1 + int(np.sum(boot <= 0.0))) / (1 + len(boot))
        return {
            "point": point,
            "ci": self._interval(boot),
            "p_one_sided": p,
            "model_mean": float(np.mean(model)),
            "base_mean": float(np.mean(base)),
        }

    def mean_difference(self, a: np.ndarray, b: np.ndarray) -> dict:
        """mean(a) - mean(b) over the same rows, and its CI."""
        ones = self.sums(np.ones(len(a)))
        boot = (self.sums(a) - self.sums(b)) / ones
        return {
            "point": float(np.mean(a) - np.mean(b)),
            "ci": self._interval(boot),
            "a": float(np.mean(a)),
            "b": float(np.mean(b)),
        }

    def mean(self, x: np.ndarray) -> list[float]:
        """CI of mean(x)."""
        return self._interval(self.sums(x) / self.sums(np.ones(len(x))))


def _level_ci(per_row: dict[str, np.ndarray], steps: int, boot: DateBootstrap) -> dict:
    """95% CIs of one model's levels: Winkler and pinball per step, coverage and 'same' shares."""
    return {
        "winkler_per_step": [v / steps for v in boot.mean(per_row["winkler"])],
        "pinball_close": [v / steps for v in boot.mean(per_row["pinball_close"])],
        "coverage_80": boot.mean(per_row["inside"]),
        "same": boot.mean(per_row["same"]),
    }


def _per_row(scores: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Row totals over the steps (what the improvements pool) and row means (coverage)."""
    return {
        "winkler": scores["winkler"].sum(axis=1),
        "pinball_close": scores["pinball_close"].sum(axis=1),
        "pinball_all": scores["pinball_all"].sum(axis=1),
        "inside": scores["inside"].mean(axis=1),
        "same": scores["same"].mean(axis=1),
    }


def compare_scores(model: dict[str, np.ndarray], base: dict[str, np.ndarray], boot: DateBootstrap) -> dict:
    return {
        "winkler_improvement": boot.skill(model["winkler"], base["winkler"]),
        "pinball_close_improvement": boot.skill(model["pinball_close"], base["pinball_close"]),
        "pinball_all_improvement": boot.skill(model["pinball_all"], base["pinball_all"]),
        "coverage_change": boot.mean_difference(model["inside"], base["inside"]),
        "same_change": boot.mean_difference(model["same"], base["same"]),
    }


def _month_block_ci(
    model: np.ndarray, base: np.ndarray, dates: np.ndarray, resamples: int, seed: int, level: float
) -> list[float]:
    months = np.array([d[:7] for d in dates])
    return DateBootstrap.draw(months, resamples, seed, level).skill(model, base)["ci"]


def importance_summary(importance: dict[str, np.ndarray], names: list[str]) -> dict:
    """Share of the pv2 model's gain and splits taken by the pattern group, and its top features."""
    full = [*names, range_model.INSTRUMENT_FEATURE]
    is_pattern = np.array([n.startswith(PREFIX) for n in full])
    out: dict = {}
    for kind in ("gain", "split"):
        total = importance.get(kind)
        if total is None or not np.sum(total):
            continue
        out[f"{kind}_share_patterns"] = float(total[is_pattern].sum() / total.sum())
        for target in TARGETS:
            part = importance.get(f"{kind}_{target}")
            if part is not None and np.sum(part):
                out[f"{kind}_share_patterns_{target}"] = float(part[is_pattern].sum() / part.sum())
    gain, split = importance.get("gain"), importance.get("split")
    if gain is not None and split is not None:
        order = np.argsort(-gain)
        rank = {full[i]: r + 1 for r, i in enumerate(order)}
        pattern_rows = [
            {
                "feature": full[i],
                "gain_share": float(gain[i] / gain.sum()),
                "splits": int(split[i]),
                "rank_among_all": rank[full[i]],
            }
            for i in order
            if is_pattern[i]
        ]
        out["top_pattern_features"] = pattern_rows[:10]
        out["pattern_features_used"] = int(sum(1 for r in pattern_rows if r["splits"] > 0))
        out["pattern_features_total"] = int(is_pattern.sum())
        out["forming_flag_splits"] = int(split[full.index(FORMING)])
        out["top_features_overall"] = [
            {"feature": full[i], "gain_share": float(gain[i] / gain.sum())} for i in order[:10]
        ]
    return out


# ---------------------------------------------------------------- evaluation


def _scores(pred: np.ndarray, rows: np.ndarray, Y: np.ndarray, pivot: PivotConfig) -> dict[str, np.ndarray]:
    spec = pivot.range_model
    out = MethodOutput(rows, pred, ghost_ohlc(pred))
    return row_scores(out, Y, spec.quantiles, spec.interval_alpha, pivot.candle_accuracy.same_close_atr)


def _level(scores: dict[str, np.ndarray]) -> dict:
    return {
        "winkler_per_step": float(scores["winkler"].mean()),
        "winkler_by_step": [float(x) for x in scores["winkler"].mean(axis=0)],
        "coverage_80": float(scores["inside"].mean()),
        "pinball_close": float(scores["pinball_close"].mean()),
        "pinball_all": float(scores["pinball_all"].mean()),
        "same": float(scores["same"].mean()),
    }


def _pattern_frequency(bits: np.ndarray) -> dict:
    lag0 = bits[:, 0] != 0
    counts = {
        name: int(((bits[:, 0] >> np.uint32(i)) & np.uint32(1)).sum()) for i, name in enumerate(PATTERN_NAMES)
    }
    return {
        "rows": int(len(bits)),
        "share_with_pattern_on_bar_t": float(lag0.mean()) if len(bits) else None,
        "share_with_pattern_on_last_3_bars": float((bits != 0).any(axis=1).mean()) if len(bits) else None,
        "confirmed_on_bar_t_by_pattern": counts,
    }


def _reproduction(
    panel: Panel,
    fingerprint: str,
    rows: np.ndarray,
    base_pred: np.ndarray,
    base_scores: dict[str, np.ndarray],
    pivot: PivotConfig,
    boot: DateBootstrap,
) -> tuple[dict, dict[str, np.ndarray] | None]:
    """The reproduction check, and the cached range_v1 scores when they cover exactly `rows` (else None)."""
    reported = reported_range_v1(panel.exchange, panel.tf)
    rep = reported["methods"][BASE_NAME] if reported else None
    out: dict = {
        "reproduced_within": REPRODUCED_WITHIN,
        "reported": (
            {k: rep[k] for k in ("n", "winkler", "coverage_80", "pinball_close", "pinball_all")}
            if rep
            else None
        ),
        "reproduced": {"n": int(len(rows)), **_level(base_scores)},
    }
    if rep:
        diff = out["reproduced"]["winkler_per_step"] / rep["winkler"] - 1.0
        out["relative_winkler_difference_vs_report"] = diff
        out["reproduced_ok"] = abs(diff) < REPRODUCED_WITHIN
    cached = cached_range_v1(panel)
    if cached is None:
        out["cache"] = "missing"
        return out, None
    c_rows, c_pred, c_meta = cached
    keep = panel.valid[c_rows]
    c_rows, c_pred = c_rows[keep], c_pred[keep].astype(np.float64)
    c_scores = _scores(c_pred, c_rows, panel.Y[c_rows].astype(np.float64), pivot)
    out["cache_fingerprint_matches_panel"] = c_meta.get("fingerprint") == fingerprint
    out["cache_settings_match_range_model"] = c_meta.get("settings") == range_model.training_settings()
    out["harness_on_cached"] = {"n": int(len(c_rows)), **_level(c_scores)}
    if rep:
        out["harness_matches_report"] = (
            len(c_rows) == rep["n"]
            and abs(out["harness_on_cached"]["winkler_per_step"] - rep["winkler"]) < 1e-9
        )
    out["cache_rows_match"] = bool(np.array_equal(c_rows, rows))
    if not out["cache_rows_match"]:
        return out, None
    diff = np.abs(c_pred - base_pred)
    out["prediction_max_abs_difference"] = float(diff.max())
    out["prediction_share_bit_identical"] = float(np.mean(diff == 0.0))
    out["paired_reproduced_vs_cached"] = compare_scores(_per_row(base_scores), _per_row(c_scores), boot)
    return out, c_scores


def evaluate_timeframe(
    tf: str,
    exchange: str = "NSE",
    *,
    load: CandleLoader | None = None,
    instruments: list[str] | None = None,
    threads: int = NUM_THREADS,
    use_cache: bool = True,
    log: Callable[[str], None] | None = None,
) -> dict:
    """Both models' walk-forward on one (exchange, tf), and every comparison of the report."""
    reg = load_registration()
    pivot = load_pivot_config()
    boot_cfg = load_edge_config().common.bootstrap
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    panel = build_panel(exchange, tf, load=load, instruments=instruments, pivot=pivot)
    bits = panel_pattern_bits(panel, research_loader(False, load), pivot)
    timings["panel"] = time.perf_counter() - t0
    settings = training_settings(threads)
    t0 = time.perf_counter()
    result = load_pair(panel, bits, settings) if use_cache else None
    cached = result is not None
    if result is None:
        result = walk_forward_pair(panel, bits, pivot, threads=threads, log=log)
        if instruments is None:
            save_pair(result, panel, bits, settings)
    timings["walk_forward"] = time.perf_counter() - t0
    names = [*panel.feature_names, *feature_names()]
    fingerprint = panel_fingerprint(panel)
    panel.X = np.empty((0, panel.X.shape[1]), dtype=np.float32)

    keep = panel.valid[result.rows]
    rows, fold = result.rows[keep], result.fold[keep]
    base_pred, pv2_pred = result.base[keep].astype(np.float64), result.pv2[keep].astype(np.float64)
    if not (np.isfinite(base_pred).all() and np.isfinite(pv2_pred).all()):
        raise ValueError(f"non-finite quantiles for {exchange} {tf}")
    Y = panel.Y[rows].astype(np.float64)
    dates = _ist_dates(panel.ts[rows])
    boot = DateBootstrap.draw(dates, boot_cfg.resamples, boot_cfg.seed, boot_cfg.ci_level)

    base_scores, pv2_scores = _scores(base_pred, rows, Y, pivot), _scores(pv2_pred, rows, Y, pivot)
    base_rows, pv2_rows = _per_row(base_scores), _per_row(pv2_scores)
    primary = compare_scores(pv2_rows, base_rows, boot)
    w = primary["winkler_improvement"]
    w["ci_month_blocks"] = _month_block_ci(
        pv2_rows["winkler"], base_rows["winkler"], dates, boot_cfg.resamples, boot_cfg.seed, boot_cfg.ci_level
    )
    checks = {
        "improvement_point_min": w["point"] >= reg.min_improvement,
        "ci_lower_above": w["ci"][0] > reg.ci_lower_above,
    }

    reproduction, cached_scores = _reproduction(panel, fingerprint, rows, base_pred, base_scores, pivot, boot)
    versus_cached = None
    if cached_scores is not None:
        versus_cached = compare_scores(pv2_rows, _per_row(cached_scores), boot)
        vc = versus_cached["winkler_improvement"]
        versus_cached["checks"] = {
            "improvement_point_min": vc["point"] >= reg.min_improvement,
            "ci_lower_above": vc["ci"][0] > reg.ci_lower_above,
        }

    by_fold = []
    for k in np.unique(fold):
        sel = fold == k
        by_fold.append(
            {
                "fold": int(k),
                "n": int(sel.sum()),
                "winkler_improvement": 1.0 - pv2_rows["winkler"][sel].sum() / base_rows["winkler"][sel].sum(),
            }
        )
    by_instrument = []
    codes = panel.inst[rows]
    for code, iid in enumerate(panel.instruments):
        sel = codes == code
        if sel.any():
            by_instrument.append(
                {
                    "instrument": iid,
                    "n": int(sel.sum()),
                    "winkler_improvement": 1.0
                    - pv2_rows["winkler"][sel].sum() / base_rows["winkler"][sel].sum(),
                }
            )
    return {
        "exchange": exchange,
        "tf": tf,
        "instruments": panel.instruments,
        "validation_start": pivot.train_end_utc(tf).isoformat(),
        "validation_end": pivot.holdout_start_utc.isoformat(),
        "panel_bars": int(len(panel)),
        "panel_last_ts": panel.ts.max().isoformat(),
        "n_rows": int(len(rows)),
        "n_forecasts_steps": int(len(rows) * Y.shape[1]),
        "n_dates": boot.n_dates,
        "folds": result.folds,
        "walk_forward_cached": cached,
        "range_v1_reproduced": _level(base_scores),
        "pv2_range": _level(pv2_scores),
        "level_ci": {
            "range_v1_reproduced": _level_ci(base_rows, Y.shape[1], boot),
            "pv2_range": _level_ci(pv2_rows, Y.shape[1], boot),
        },
        "primary": {
            "metric": "Winkler improvement 1 - W(pv2_range) / W(range_v1 reproduced), rows x 3 steps",
            **w,
            "checks": checks,
            "pass_if_met": all(checks.values()),
        },
        "secondary": {k: v for k, v in primary.items() if k != "winkler_improvement"},
        "versus_cached_range_v1": versus_cached,
        "reproduction": reproduction,
        "by_fold": by_fold,
        "by_instrument": by_instrument,
        "pattern_frequency_validation_rows": _pattern_frequency(bits[rows]),
        "feature_importance_pv2": importance_summary(result.importance, names),
        "runtime_seconds": {k: round(v, 1) for k, v in timings.items()},
    }


# ---------------------------------------------------------------- report


FAMILY_BH = (
    "pending: a timeframe passes only if its primary p also survives Benjamini-Hochberg at q = 0.10 "
    "across every edge_search_v2.yaml primary (applied by the lead)"
)
UNREGISTERED_CHOICES = [
    "Pattern group: for each of the 23 patterns of candly.patterns and each lag k = 0, 1, 2, a 0/1 column "
    "'confirmed with its last bar on bar t-k' (multi-hot: several patterns can end on one bar), 69 columns, "
    "plus pat_forming; appended after range_v1's features, before the instrument column.",
    "Lags are by bar position in each instrument's series (a lag can reach into the previous session, as "
    "the detector's multi-bar patterns do).",
    "Primary comparison: pv2_range against range_v1 retrained in the same run with the same settings and "
    "thread count (the reproduced baseline), so the feature group is the only difference. pv2_range "
    "against the cached range_v1 predictions is reported alongside.",
    f"range_v1 counts as reproduced when its Winkler per step is within {100 * REPRODUCED_WITHIN:.1f}% of "
    "the value range_eval reported (a tenth of the 1% pass bar).",
    "Bootstrap blocks are whole IST dates, as range_v1's evaluation used (edge_search.yaml common.bootstrap "
    "kind date_block, 2000 resamples, seed 20260924, 95%). Month blocks are shown for information only.",
    "Coverage, pinball and 'same' changes use the same bootstrap draws as the primary.",
]
DEVIATIONS = [
    "Forming flag: pat_forming is 0 at every reference bar. A range forecast is made when bar t closes; at "
    "that instant no bar is forming, and the next partial bar is the target bar t+1 itself, whose shape "
    "would leak the target. Any non-constant causal definition would have to be invented after the fact "
    "(for example 'the first bars of a 3-bar pattern are in place'), so the most conservative reading "
    "keeps the registered column and gives it no information. The test therefore measures confirmed "
    "patterns only.",
    f"LightGBM num_threads = {NUM_THREADS} instead of range_v1's 4 (the laptop was shared with three other "
    "jobs). Every other hyperparameter, the sample and the seeds are range_v1's; the reproduction check "
    "shows whether the thread count changed any prediction.",
]
CAVEATS = [
    "Validation span only (train_end 2023-01-01 to 2025-10-01); the holdout was never loaded.",
    "The 12 NSE watchlist instruments are today's large caps (survivorship bias), as in range_v1.",
    "Intraday fits use range_v1's seeded sample of at most 120,000 training bars per fold.",
    "Rare patterns could not enter the model: piercing line, dark cloud cover, morning and evening star, "
    "three white soldiers and three black crows fire on roughly 70-350 of the 120,000 sampled training "
    "bars, and with range_v1's min_data_in_leaf = 300 and 70% bagging no split can isolate them (they get "
    "no split in any fold). The test measures the common patterns; the rare ones were not testable under "
    "range_v1's hyperparameters, which the registration fixes.",
    "Whole-date blocks ignore serial dependence across days (volatility clustering); the month-block CI is "
    "shown for information.",
    "This is one of several primary p-values in edge_search_v2.yaml; the family Benjamini-Hochberg at "
    "q = 0.10 across every v2 primary is applied by the lead once all v2 tests have reported.",
]


def report_paths() -> tuple[Path, Path]:
    folder = REPO_ROOT / "docs" / "test-reports"
    return folder / f"{REPORT_STEM}.json", folder / f"{REPORT_STEM}.md"


def _pct(x, digits: int = 2) -> str:
    return "n/a" if x is None else f"{100 * x:.{digits}f}%"


def _ci(ci, digits: int = 2) -> str:
    return "[n/a]" if not ci else f"[{_pct(ci[0], digits)}, {_pct(ci[1], digits)}]"


def render_markdown(report: dict) -> str:
    verdict = "pass_if MET" if report["pass_if_met"] else "FAIL (pass_if not met)"
    lines = [
        "# pv2_range validation: do candlestick patterns sharpen range_v1?",
        "",
        f"Generated {report['generated_at']} by `python -m candly.research.pv2_range_eval`. "
        "Pre-registered in `config/edge_search_v2.yaml` "
        f"(patterns_range, sha256 `{report['edge_search_v2_sha256'][:12]}`). "
        "Validation span only; the holdout was not read.",
        "",
        f"**Result: {verdict}** ({report['n_passing']} of {len(report['timeframes'])} timeframes meet "
        f"pass_if; rule: {report['pass_rule']}; each needs improvement >= 1% and CI lower > 0). Family "
        f"Benjamini-Hochberg: {report['family_bh']}.",
        "",
        "| tf | rows | Winkler range_v1 / pv2 (per step) | improvement [95% CI] | one-sided p | coverage "
        "range_v1 / pv2 | pinball close change | pattern gain share | pass_if |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        p = r["primary"]
        pb = r["secondary"]["pinball_close_improvement"]
        imp = r["feature_importance_pv2"]
        lines.append(
            f"| {r['tf']} | {r['n_rows']:,} | {r['range_v1_reproduced']['winkler_per_step']:.4f} / "
            f"{r['pv2_range']['winkler_per_step']:.4f} | {_pct(p['point'])} {_ci(p['ci'])} | "
            f"{p['p_one_sided']:.4f} | {_pct(r['range_v1_reproduced']['coverage_80'], 1)} / "
            f"{_pct(r['pv2_range']['coverage_80'], 1)} | {_pct(pb['point'])} {_ci(pb['ci'])} | "
            f"{_pct(imp.get('gain_share_patterns'))} | {'met' if p['pass_if_met'] else 'fail'} |"
        )
    lines += [
        "",
        "Improvement = 1 - W(pv2_range) / W(range_v1) on the same validation forecasts (3 steps each); "
        "positive means patterns narrowed the intervals or cut the misses. CIs and p resample whole IST "
        "dates (2000 resamples); p = (1 + #{bootstrap improvement <= 0}) / 2001.",
        "",
        "## Reproduction of range_v1",
        "",
        "| tf | reported Winkler | harness on cached predictions | reproduced (retrained here) | "
        "difference | reproduced? | max abs prediction diff |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        rep = r["reproduction"]
        reported = rep.get("reported") or {}
        harness = rep.get("harness_on_cached") or {}
        lines.append(
            f"| {r['tf']} | {reported.get('winkler', float('nan')):.6f} | "
            f"{harness.get('winkler_per_step', float('nan')):.6f} | "
            f"{rep['reproduced']['winkler_per_step']:.6f} | "
            f"{_pct(rep.get('relative_winkler_difference_vs_report'), 3)} | {rep.get('reproduced_ok')} | "
            f"{rep.get('prediction_max_abs_difference', 'n/a')} |"
        )
    lines += [
        "",
        "## Pattern features in the pv2 model",
        "",
        "| tf | gain share (all / high / low / close) | pattern features used "
        "| top pattern feature (gain share, rank) | rows with a pattern on bar t |",
        "|---|---|---|---|---|",
    ]
    for r in report["results"]:
        imp = r["feature_importance_pv2"]
        top = (imp.get("top_pattern_features") or [{}])[0]
        shares = " / ".join(
            _pct(imp.get(k)) for k in ("gain_share_patterns", *(f"gain_share_patterns_{t}" for t in TARGETS))
        )
        lines.append(
            f"| {r['tf']} | {shares} "
            f"| {imp.get('pattern_features_used')} of {imp.get('pattern_features_total')} "
            f"| {top.get('feature')} ({_pct(top.get('gain_share'))}, #{top.get('rank_among_all')}) | "
            f"{_pct(r['pattern_frequency_validation_rows']['share_with_pattern_on_bar_t'], 1)} |"
        )
    lines += ["", "## Unregistered choices (made before any result)", ""]
    lines += [f"- {x}" for x in report["unregistered_choices"]]
    lines += ["", "## Deviations", ""] + [f"- {x}" for x in report["deviations"]]
    lines += ["", "## Caveats", ""] + [f"- {x}" for x in report["caveats"]]
    for f in report.get("failures", []):
        lines.append(f"- **{f['tf']} could not be evaluated** (counts as a fail): {f['error']}")
    return "\n".join(lines) + "\n"


def assemble_report(results: list[dict], failures: list[dict], threads: int = NUM_THREADS) -> dict:
    """The report over the registered timeframes; a timeframe that is missing or failed does not pass."""
    reg = load_registration()
    pivot = load_pivot_config()
    boot = load_edge_config().common.bootstrap
    by_tf = {r["tf"]: r for r in results}
    passing = {
        tf: bool(by_tf[tf]["primary"]["pass_if_met"]) if tf in by_tf else False for tf in reg.timeframes
    }
    n_passing = sum(passing.values())
    return {
        "test": MODEL_NAME,
        "section": SECTION,
        "registration": reg.raw,
        "edge_search_v2_sha256": edge_v2_sha256(),
        "config_sha256": {**config_hashes(), **pivot_config_hashes()},
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "period": "validation",
        "holdout_start": pivot.holdout_start_utc.isoformat(),
        "splits": {
            "train": f"first bar .. {pivot.train_end_for('5m').isoformat()} (expanding, refit every window)",
            "validation": f"{pivot.train_end_for('5m').isoformat()} .. {pivot.holdout_start.isoformat()}",
            "holdout_start": pivot.holdout_start.isoformat(),
            "walk_forward": {
                "retrain_every_months": pivot.walk_forward.retrain_every_months,
                "purge_plus_embargo_bars": pivot.walk_forward.gap_bars,
                "expanding": True,
            },
        },
        "bootstrap": {
            "kind": boot.kind,
            "resamples": boot.resamples,
            "ci_level": boot.ci_level,
            "seed": boot.seed,
        },
        "training_settings": training_settings(threads),
        "lightgbm": lgb.__version__,
        "pass_rule": reg.rule,
        "timeframes": passing,
        "missing": [tf for tf in reg.timeframes if tf not in by_tf],
        "n_passing": n_passing,
        "pass_if_met": n_passing >= reg.min_passing,
        "family_bh": FAMILY_BH,
        "primary_p": {tf: by_tf[tf]["primary"]["p_one_sided"] for tf in reg.timeframes if tf in by_tf},
        "n_hypotheses": len(reg.timeframes),
        "unregistered_choices": UNREGISTERED_CHOICES,
        "deviations": DEVIATIONS,
        "caveats": CAVEATS,
        "failures": failures,
        "results": [by_tf[tf] for tf in reg.timeframes if tf in by_tf],
    }


def run_validation(
    tfs: list[str] | None = None,
    *,
    threads: int = NUM_THREADS,
    write: bool = True,
    log: Callable[[str], None] | None = None,
) -> dict:
    reg = load_registration()
    results, failures = [], []
    for tf in tfs or list(reg.timeframes):
        started = time.perf_counter()
        try:
            result = evaluate_timeframe(tf, reg.exchange, threads=threads, log=log)
        except Exception as exc:  # a broken timeframe must not lose the others; it counts as a fail
            failures.append({"tf": tf, "error": repr(exc)})
            if log is not None:
                log(f"{reg.exchange} {tf}: FAILED {exc!r}")
            continue
        result["runtime_seconds"]["total"] = round(time.perf_counter() - started, 1)
        results.append(result)
        if log is not None:
            p = result["primary"]
            log(
                f"{reg.exchange} {tf}: improvement {_pct(p['point'])} CI {_ci(p['ci'])} "
                f"p={p['p_one_sided']:.4f} pass_if_met={p['pass_if_met']}"
            )
    report = assemble_report(results, failures, threads)
    if write:
        json_path, md_path = report_paths()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_bytes((json.dumps(report, indent=2) + "\n").encode("utf-8"))
        md_path.write_bytes(render_markdown(report).encode("utf-8"))
    return report


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="pv2_range: range_v1 with candlestick patterns (validation only)"
    )
    parser.add_argument("--tf", nargs="*", default=None)
    parser.add_argument("--threads", type=int, default=NUM_THREADS)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    log_path = derived_dir() / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(line: str) -> None:
        stamped = f"{datetime.now(UTC).isoformat(timespec='seconds')} {line}"
        print(stamped, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(stamped + "\n")

    run_validation(args.tf, threads=args.threads, write=not args.no_write, log=log)


if __name__ == "__main__":
    main()
