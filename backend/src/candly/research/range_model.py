"""range_v1, the "expected candle" (config/pivot.yaml range_model, PLAN.md §20a).

One LightGBM quantile model per (exchange, tf, target, quantile, step), pooled across the exchange's
tradable watchlist instruments with the instrument as a categorical feature. Targets are measured from
the reference close in ATR(14) units at the reference bar t:

    high_s  = (high[t+s]  - close[t]) / ATR[t]
    low_s   = (low[t+s]   - close[t]) / ATR[t]
    close_s = (close[t+s] - close[t]) / ATR[t]

Walk-forward (pivot.yaml data): expanding window, a refit at the start of every `retrain_every_months`
window from train_end[tf] to the holdout, each fit on the bars before that window minus the last
purge_bars + embargo_bars bars of every instrument. The production model is fit the same way on
everything before holdout_start. Bars on or after holdout_start are never loaded unless an official
holdout run passes allow_holdout=True.

Hyperparameters are fixed and conservative, chosen before any fit and never tuned. Intraday training
sets are a seeded uniform sample of at most MAX_TRAIN_ROWS bars: consecutive 5m bars overlap heavily,
and a full 1.5M-row fit of 27 models per fold would not run on the laptop.

Inference sorts each quantile triple (p10 <= p50 <= p90) and draws the ghost candle so that
high >= max(open, close) >= min(open, close) >= low.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from candly.core.calendar import IST
from candly.core.instruments import load_watchlist
from candly.core.settings import get_settings
from candly.research.data import CandleLoader, load_research_candles
from candly.research.lgbm import lgb
from candly.research.pivot_config import PivotConfig, RangeSpec, load_pivot_config, pivot_config_hashes
from candly.research.range_features import feature_columns, load_context, range_features

MODEL_NAME = "range_v1"
INSTRUMENT_FEATURE = "instrument"
SEED = 20260924
NUM_ROUNDS = 200
MAX_TRAIN_ROWS = 120_000
DATASET_PARAMS = {"max_bin": 127, "verbose": -1}
HYPERPARAMETERS = {
    "objective": "quantile",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 300,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.7,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "force_col_wise": True,
    "deterministic": True,
    "num_threads": 4,
    "verbose": -1,
}


def model_dir(exchange: str, tf: str) -> Path:
    return get_settings().data_dir / "models" / MODEL_NAME / f"{exchange}_{tf}"


def range_universe(exchange: str, tf: str) -> list[str]:
    """The exchange's tradable watchlist instruments that have `tf` (INDIAVIX is context, not a target)."""
    return [i.id for i in load_watchlist() if i.exchange == exchange and i.tradable and tf in i.timeframes]


def range_targets(df: pd.DataFrame, atr: pd.Series, steps: int) -> np.ndarray:
    """(n, steps, 3): high, low and close of bar t+s minus close[t], over ATR[t]. NaN past the last bar.
    Uses future bars by design, so it lives in research/ only."""
    close = df["close"].to_numpy(dtype=float)
    scale = atr.to_numpy(dtype=float)
    out = np.full((len(df), steps, 3), np.nan)
    for j, name in enumerate(("high", "low", "close")):
        values = df[name].to_numpy(dtype=float)
        for s in range(1, steps + 1):
            if s < len(df):
                out[: len(df) - s, s - 1, j] = (values[s:] - close[: len(df) - s]) / scale[: len(df) - s]
    return out


@dataclass
class Panel:
    """Every bar of every instrument of one (exchange, tf), instrument by instrument in time order."""

    exchange: str
    tf: str
    instruments: list[str]
    feature_names: list[str]
    ts: pd.DatetimeIndex
    inst: np.ndarray  # instrument code (position in `instruments`)
    ref_close: np.ndarray
    atr: np.ndarray
    X: np.ndarray  # float32 (n, features + 1); the last column is the instrument code
    Y: np.ndarray  # float32 (n, steps, 3): high, low, close targets
    valid: np.ndarray  # ATR > 0 and every target known
    offsets: np.ndarray = field(default_factory=lambda: np.zeros(1, dtype=np.int64))

    def __len__(self) -> int:
        return len(self.ts)

    def block(self, code: int) -> slice:
        return slice(int(self.offsets[code]), int(self.offsets[code + 1]))


def research_loader(allow_holdout: bool, load: CandleLoader | None) -> Callable[[str, str], pd.DataFrame]:
    def get(instrument_id: str, tf: str) -> pd.DataFrame:
        return load_research_candles(instrument_id, tf, allow_holdout=allow_holdout, load=load)

    return get


def build_panel(
    exchange: str,
    tf: str,
    *,
    load: CandleLoader | None = None,
    allow_holdout: bool = False,
    instruments: list[str] | None = None,
    pivot: PivotConfig | None = None,
) -> Panel:
    """Features and targets of every bar, built once per series. The holdout is cut unless allowed."""
    pivot = pivot or load_pivot_config()
    steps = pivot.range_model.steps
    ids = list(instruments) if instruments is not None else range_universe(exchange, tf)
    get = research_loader(allow_holdout, load)
    context = load_context(exchange, tf, get)
    names = feature_columns(pivot.range_model.feature_groups)
    parts: dict[str, list] = {k: [] for k in ("ts", "inst", "close", "atr", "X", "Y")}
    kept: list[str] = []
    for iid in ids:
        df = get(iid, tf)
        if tf == "1D":
            df = df[df["ts"] >= pivot.daily_start_utc].reset_index(drop=True)
        if df.empty:
            continue
        code = len(kept)
        kept.append(iid)
        feats = range_features(df, tf, iid, context)
        X = np.empty((len(df), len(names) + 1), dtype=np.float32)
        X[:, :-1] = feats[names].to_numpy(dtype=np.float32)
        X[:, -1] = code
        parts["ts"].append(df["ts"])
        parts["inst"].append(np.full(len(df), code, dtype=np.int16))
        parts["close"].append(df["close"].to_numpy(dtype=float))
        parts["atr"].append(feats["atr"].to_numpy(dtype=float))
        parts["X"].append(X)
        parts["Y"].append(range_targets(df, feats["atr"], steps).astype(np.float32))
    if not kept:
        raise ValueError(f"no {tf} candles for any {exchange} instrument")
    lengths = [len(t) for t in parts["ts"]]
    atr = np.concatenate(parts["atr"])
    Y = np.concatenate(parts["Y"])
    X = np.concatenate(parts["X"])
    parts["X"].clear()
    ts = pd.DatetimeIndex(pd.concat(parts["ts"], ignore_index=True)).tz_convert("UTC")
    return Panel(
        exchange=exchange,
        tf=tf,
        instruments=kept,
        feature_names=names,
        ts=ts,
        inst=np.concatenate(parts["inst"]),
        ref_close=np.concatenate(parts["close"]),
        atr=atr,
        X=X,
        Y=Y,
        valid=np.isfinite(atr) & (atr > 0) & np.isfinite(Y).all(axis=(1, 2)),
        offsets=np.concatenate([[0], np.cumsum(lengths)]).astype(np.int64),
    )


def fold_windows(tf: str, pivot: PivotConfig) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """[(test_start, test_end)] in UTC: `retrain_every_months` windows from train_end[tf] (midnight IST)
    up to the holdout; the last one stops at the holdout."""
    holdout = pivot.holdout_start_utc
    t = pivot.train_end_utc(tf).tz_convert(IST)
    out = []
    while t.tz_convert("UTC") < holdout:
        nxt = t + pd.DateOffset(months=pivot.walk_forward.retrain_every_months)
        out.append((t.tz_convert("UTC"), min(nxt.tz_convert("UTC"), holdout)))
        t = nxt
    return out


def train_rows(panel: Panel, before: pd.Timestamp, gap: int) -> np.ndarray:
    """Positions of valid bars before `before`, less each instrument's last `gap` bars before it."""
    out = []
    before_mask = np.asarray(panel.ts < before)
    for code in range(len(panel.instruments)):
        block = panel.block(code)
        idx = np.flatnonzero(before_mask[block]) + block.start
        out.append(idx[: max(0, len(idx) - gap)])
    idx = np.concatenate(out) if out else np.empty(0, dtype=np.int64)
    return idx[panel.valid[idx]]


def _sample(idx: np.ndarray, seed: int) -> np.ndarray:
    if len(idx) <= MAX_TRAIN_ROWS:
        return idx
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(idx, MAX_TRAIN_ROWS, replace=False))


@dataclass
class RangeModel:
    """27 boosters (for 3 steps): boosters[(step, target, quantile)]."""

    spec_quantiles: tuple[float, ...]
    steps: int
    feature_names: list[str]
    instruments: list[str]
    boosters: dict[tuple[int, str, float], lgb.Booster]
    meta: dict = field(default_factory=dict)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """(n, steps, 3 targets, 3 quantiles) in ATR units, each quantile triple sorted."""
        out = np.empty((len(X), self.steps, 3, len(self.spec_quantiles)), dtype=np.float64)
        for (step, target, q), booster in self.boosters.items():
            j = ("high", "low", "close").index(target)
            out[:, step - 1, j, self.spec_quantiles.index(q)] = booster.predict(X)
        return np.sort(out, axis=-1)

    @property
    def version(self) -> str:
        return str(self.meta.get("version", "unsaved"))


def fit_range_model(
    X: np.ndarray, Y: np.ndarray, spec: RangeSpec, feature_names: list[str], instruments: list[str], seed: int
) -> RangeModel:
    """Fit every (step, target, quantile) booster on the same rows; the binned Dataset is built once."""
    names = [*feature_names, INSTRUMENT_FEATURE]
    dataset = lgb.Dataset(
        np.ascontiguousarray(X),
        label=np.ascontiguousarray(Y[:, 0, 0]),
        feature_name=names,
        categorical_feature=[len(names) - 1],
        free_raw_data=False,
        params=DATASET_PARAMS,
    ).construct()
    boosters = {}
    for step in range(1, spec.steps + 1):
        for j, target in enumerate(("high", "low", "close")):
            dataset.set_label(np.ascontiguousarray(Y[:, step - 1, j]))
            for q in spec.quantiles:
                params = {**HYPERPARAMETERS, "alpha": q, "seed": seed}
                boosters[(step, target, q)] = lgb.train(params, dataset, num_boost_round=NUM_ROUNDS)
    return RangeModel(spec.quantiles, spec.steps, list(feature_names), list(instruments), boosters)


@dataclass
class WalkForwardResult:
    rows: np.ndarray  # panel positions of every bar in [train_end, holdout)
    pred: np.ndarray  # (len(rows), steps, 3, 3)
    fold: np.ndarray  # fold index per row
    folds: list[dict]  # per fold: window, training rows, fit seconds


def walk_forward(
    panel: Panel, pivot: PivotConfig | None = None, log: Callable[[str], None] | None = None
) -> WalkForwardResult:
    """Out-of-sample predictions for the validation span, one refit per window."""
    pivot = pivot or load_pivot_config()
    spec = pivot.range_model
    gap = pivot.walk_forward.gap_bars
    rows, preds, folds_of, folds = [], [], [], []
    for k, (lo, hi) in enumerate(fold_windows(panel.tf, pivot)):
        test = np.flatnonzero(np.asarray((panel.ts >= lo) & (panel.ts < hi)))
        train = train_rows(panel, lo, gap)
        if not len(test) or not len(train):
            continue
        used = _sample(train, SEED + k)
        started = datetime.now(UTC)
        model = fit_range_model(
            panel.X[used], panel.Y[used], spec, panel.feature_names, panel.instruments, SEED + k
        )
        seconds = (datetime.now(UTC) - started).total_seconds()
        rows.append(test)
        preds.append(model.predict(panel.X[test]))
        folds_of.append(np.full(len(test), k))
        folds.append(
            {
                "fold": k,
                "test_start": lo.isoformat(),
                "test_end": hi.isoformat(),
                "train_rows": int(len(train)),
                "train_rows_used": int(len(used)),
                "train_last_ts": panel.ts[train].max().isoformat(),
                "fit_seconds": round(seconds, 1),
            }
        )
        if log is not None:
            log(
                f"{panel.exchange} {panel.tf} fold {k} {lo.date()}..{hi.date()}: "
                f"{len(used)}/{len(train)} train rows, {len(test)} test rows, {seconds:.0f}s"
            )
    if not rows:
        empty = np.empty((0, spec.steps, 3, 3))
        return WalkForwardResult(np.empty(0, dtype=np.int64), empty, np.empty(0, dtype=int), [])
    return WalkForwardResult(np.concatenate(rows), np.concatenate(preds), np.concatenate(folds_of), folds)


def fit_production(panel: Panel, pivot: PivotConfig | None = None) -> RangeModel:
    """The live model: fit on every bar before holdout_start (less the purge/embargo gap)."""
    pivot = pivot or load_pivot_config()
    train = train_rows(panel, pivot.holdout_start_utc, pivot.walk_forward.gap_bars)
    if not len(train):
        raise ValueError(f"no training rows for {panel.exchange} {panel.tf}")
    used = _sample(train, SEED)
    model = fit_range_model(
        panel.X[used], panel.Y[used], pivot.range_model, panel.feature_names, panel.instruments, SEED
    )
    model.meta = {
        "model": MODEL_NAME,
        "exchange": panel.exchange,
        "tf": panel.tf,
        "trained_at": datetime.now(UTC).isoformat(),
        "train_start": panel.ts[train].min().isoformat(),
        "train_end": panel.ts[train].max().isoformat(),
        "cutoff": pivot.holdout_start_utc.isoformat(),
        "train_rows": int(len(train)),
        "train_rows_used": int(len(used)),
        "instruments": panel.instruments,
        "features": panel.feature_names,
        "categorical": INSTRUMENT_FEATURE,
        "feature_groups": list(pivot.range_model.feature_groups),
        "quantiles": list(pivot.range_model.quantiles),
        "steps": pivot.range_model.steps,
        "targets": ["high", "low", "close"],
        "units": "ATR(14) at the reference bar, from the reference close",
        "hyperparameters": HYPERPARAMETERS,
        "dataset_params": DATASET_PARAMS,
        "num_rounds": NUM_ROUNDS,
        "max_train_rows": MAX_TRAIN_ROWS,
        "seed": SEED,
        "config_sha256": pivot_config_hashes(),
        "code_sha256": code_sha256(),
        "lightgbm": lgb.__version__,
    }
    return model


def _booster_file(step: int, target: str, q: float) -> str:
    return f"s{step}_{target}_q{round(100 * q):02d}.txt"


def save_model(model: RangeModel, exchange: str, tf: str) -> Path:
    """Write the boosters, then meta.json last: a folder without meta.json is an unfinished save."""
    path = model_dir(exchange, tf)
    path.mkdir(parents=True, exist_ok=True)
    meta_path = path / "meta.json"
    meta_path.unlink(missing_ok=True)
    digest = hashlib.sha256()
    for (step, target, q), booster in sorted(model.boosters.items()):
        text = booster.model_to_string()
        digest.update(text.encode("utf-8"))
        # bytes, not write_text: Windows text mode writes CRLF, which LightGBM's model parser rejects
        (path / _booster_file(step, target, q)).write_bytes(text.encode("utf-8"))
    model.meta["version"] = digest.hexdigest()[:12]
    tmp = path / "meta.json.tmp"
    tmp.write_text(json.dumps(model.meta, indent=2), encoding="utf-8")
    tmp.replace(meta_path)
    _load_cached.cache_clear()
    return path


@lru_cache(maxsize=16)
def _load_cached(path: str, mtime: float) -> RangeModel:
    folder = Path(path).parent
    meta = json.loads(Path(path).read_text(encoding="utf-8"))
    quantiles = tuple(float(q) for q in meta["quantiles"])
    boosters = {
        (step, target, q): lgb.Booster(model_file=str(folder / _booster_file(step, target, q)))
        for step in range(1, int(meta["steps"]) + 1)
        for target in ("high", "low", "close")
        for q in quantiles
    }
    return RangeModel(
        quantiles, int(meta["steps"]), list(meta["features"]), list(meta["instruments"]), boosters, meta
    )


def load_model(exchange: str, tf: str) -> RangeModel | None:
    """The saved production model for (exchange, tf), or None when there is none."""
    meta = model_dir(exchange, tf) / "meta.json"
    if not meta.exists():
        return None
    return _load_cached(str(meta), meta.stat().st_mtime)


def ghost_ohlc(pred: np.ndarray) -> np.ndarray:
    """(n, steps, 4) open, high, low, close in ATR units from the reference close, from sorted quantile
    predictions (n, steps, 3, 3): closes follow the p50 close, each step opens at the previous close,
    and the p50 high/low are widened when needed so high >= max(open, close) and low <= min(open, close)."""
    median = pred.shape[-1] // 2
    close = pred[:, :, 2, median]
    opens = np.concatenate([np.zeros((len(pred), 1)), close[:, :-1]], axis=1)
    high = np.maximum(pred[:, :, 0, median], np.maximum(opens, close))
    low = np.minimum(pred[:, :, 1, median], np.minimum(opens, close))
    return np.stack([opens, high, low, close], axis=-1)


# ---------------------------------------------------------------- training run (walk-forward + production)


def walk_forward_path(exchange: str, tf: str) -> Path:
    return get_settings().derived_dir / MODEL_NAME / f"walk_forward_{exchange}_{tf}.npz"


def panel_fingerprint(panel: Panel) -> str:
    """Identifies the bars a walk-forward ran on, so cached predictions are never matched to other data."""
    digest = hashlib.sha256()
    digest.update(json.dumps(panel.instruments).encode())
    digest.update(np.asarray(panel.ts.asi8).tobytes())
    digest.update(np.asarray(panel.X[:, :-1], dtype=np.float32).tobytes())
    return digest.hexdigest()[:16]


def save_walk_forward(result: WalkForwardResult, panel: Panel, meta: dict) -> Path:
    path = walk_forward_path(panel.exchange, panel.tf)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npz")
    np.savez_compressed(
        tmp,
        rows=result.rows,
        pred=result.pred.astype(np.float32),
        fold=result.fold,
        meta=np.array(json.dumps({**meta, "folds": result.folds})),
    )
    tmp.replace(path)
    return path


def load_walk_forward(panel: Panel) -> WalkForwardResult | None:
    """Cached predictions for exactly this panel, config and model code, else None."""
    path = walk_forward_path(panel.exchange, panel.tf)
    if not path.exists():
        return None
    with np.load(path) as data:
        meta = json.loads(str(data["meta"]))
        if meta.get("fingerprint") != panel_fingerprint(panel) or meta.get("settings") != training_settings():
            return None
        return WalkForwardResult(data["rows"], data["pred"].astype(np.float64), data["fold"], meta["folds"])


def code_sha256() -> str:
    """Hash of the code that turns a panel into predictions (targets, splits, sampling, fitting). Features
    are covered by the panel fingerprint; this catches a change in how they are used."""
    parts = (
        range_targets,
        build_panel,
        fold_windows,
        train_rows,
        _sample,
        fit_range_model,
        RangeModel,
        walk_forward,
        fit_production,
    )
    digest = hashlib.sha256()
    for part in parts:
        digest.update(inspect.getsource(part).encode("utf-8"))
    return digest.hexdigest()[:16]


def training_settings() -> dict:
    return {
        "config_sha256": pivot_config_hashes(),
        "hyperparameters": HYPERPARAMETERS,
        "dataset_params": DATASET_PARAMS,
        "num_rounds": NUM_ROUNDS,
        "max_train_rows": MAX_TRAIN_ROWS,
        "seed": SEED,
        "code_sha256": code_sha256(),
        "lightgbm": lgb.__version__,
    }


def run_training(
    exchange: str,
    tf: str,
    *,
    load: CandleLoader | None = None,
    persist_model: bool = True,
    log: Callable[[str], None] | None = None,
) -> dict:
    """Walk-forward predictions for the validation span (cached to data/derived/range_v1) and the
    production model (saved to data/models/range_v1). Never reads the holdout."""
    pivot = load_pivot_config()
    started = datetime.now(UTC)
    panel = build_panel(exchange, tf, load=load, pivot=pivot)
    built = (datetime.now(UTC) - started).total_seconds()
    result = walk_forward(panel, pivot, log=log)
    wf_seconds = (datetime.now(UTC) - started).total_seconds() - built
    meta = {"fingerprint": panel_fingerprint(panel), "settings": training_settings(), "rows": len(panel)}
    save_walk_forward(result, panel, meta)
    summary = {
        "exchange": exchange,
        "tf": tf,
        "bars": len(panel),
        "instruments": panel.instruments,
        "panel_seconds": round(built, 1),
        "walk_forward_seconds": round(wf_seconds, 1),
        "folds": result.folds,
    }
    if persist_model:
        t0 = datetime.now(UTC)
        model = fit_production(panel, pivot)
        save_model(model, exchange, tf)
        summary["production_seconds"] = round((datetime.now(UTC) - t0).total_seconds(), 1)
        summary["production"] = {
            k: model.meta[k] for k in ("train_start", "train_end", "train_rows", "version")
        }
    if log is not None:
        log(f"{exchange} {tf} done: {json.dumps({k: v for k, v in summary.items() if k != 'folds'})}")
    return summary


def main(argv: list[str] | None = None) -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Train range_v1: walk-forward predictions + production models"
    )
    parser.add_argument("--exchange", nargs="*", default=None)
    parser.add_argument("--tf", nargs="*", default=None)
    args = parser.parse_args(argv)
    spec = load_pivot_config().range_model
    log_path = get_settings().derived_dir / MODEL_NAME / "training.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(line: str) -> None:
        stamped = f"{datetime.now(UTC).isoformat(timespec='seconds')} {line}"
        print(stamped, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(stamped + "\n")

    summaries = []
    for exchange in args.exchange or spec.exchanges:
        for tf in args.tf or spec.timeframes:
            try:
                summaries.append(run_training(exchange, tf, log=log))
            except Exception as exc:
                log(f"{exchange} {tf} FAILED: {exc!r}")
    out = get_settings().derived_dir / MODEL_NAME / "training_summary.json"
    existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    existing.update({f"{s['exchange']}_{s['tf']}": s for s in summaries})
    out.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    sys.exit(0)


if __name__ == "__main__":
    main()
