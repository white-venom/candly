"""regime_v1: direction over days on daily bars (config/pivot.yaml regime_model, PLAN.md §20a).

A LightGBM classifier of close[t+h] > close[t] (flat closes dropped) for each pre-registered horizon h,
pooled across the NSE slice, with fixed conservative hyperparameters.

Walk-forward (pivot.yaml data.walk_forward): expanding training window, a refit at the start of every
`retrain_every_months` test window. For a cut at time c, the training rows of each instrument are its
bars before c minus the last `max(purge_bars, h) + embargo_bars` of them, so every training label ends
before c. Isotonic calibration is fitted inside that training set only: the same walk-forward is run over
its last CALIBRATION_WINDOWS windows (each predicted by a booster trained on data before it, same gap),
the isotonic map is fitted on those out-of-window predictions, and the final booster is trained on the
whole training set.

Baselines, all per instrument and fitted on the model's own training rows: the base rate (up-rate), the
up-rate given close above/below EMA200 (trend_rule_ema200), and the up-rate given the sign of the
20-day return (momentum_sign_20d).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import sklearn
from pydantic import BaseModel
from sklearn.isotonic import IsotonicRegression

from candly.core.calendar import IST, get_calendar
from candly.core.instruments import exchange_of, get_instrument, load_watchlist
from candly.core.schema import validate_candles
from candly.core.settings import REPO_ROOT, get_settings
from candly.research.config import load_research_config
from candly.research.data import CandleLoader, default_loader, load_research_candles
from candly.research.labels import forward_labels
from candly.research.lgbm import lgb
from candly.research.pivot_config import REGIME_NAME as MODEL_NAME
from candly.research.pivot_config import REGIME_TIMEFRAME as TIMEFRAME
from candly.research.pivot_config import PivotConfig, load_pivot_config
from candly.research.regime_features import (
    FEATURE_COLUMNS,
    REQUIRED_COLUMNS,
    regime_features,
)

MARKET_ID = "NSE:NIFTY50"
VIX_ID = "NSE:INDIAVIX"
SEED = 20260924

# Fixed before any fit (pre-registration of the model's capacity): shallow trees, large leaves, heavy
# bagging and L2. Never tuned on validation results.
LGBM_PARAMS: dict = {
    "objective": "binary",
    "learning_rate": 0.03,
    "num_leaves": 7,
    "max_depth": 3,
    "min_data_in_leaf": 1000,
    "bagging_fraction": 0.5,
    "bagging_freq": 1,
    "feature_fraction": 0.7,
    "lambda_l2": 10.0,
    "seed": SEED,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 4,
    "verbosity": -1,
}
NUM_BOOST_ROUND = 200
CALIBRATION_WINDOWS = 6
PROB_CLIP = (0.01, 0.99)
MIN_TRAIN_ROWS = 2 * LGBM_PARAMS["min_data_in_leaf"]
MIN_HISTORY_BARS = 260
STALE_AFTER = timedelta(days=7)

VALIDATION_REPORT = "2026-09-24-regime-v1-validation.json"
HASHED_CONFIGS = ("pivot.yaml", "research.yaml", "costs.yaml", "watchlist.yaml")


def config_hashes() -> dict[str, str]:
    config_dir = get_settings().config_dir
    return {name: hashlib.sha256((config_dir / name).read_bytes()).hexdigest() for name in HASHED_CONFIGS}


def hyperparameters() -> dict:
    """Everything that shapes a fit, as recorded with the model and the validation report."""
    return json.loads(
        json.dumps(
            {
                "lightgbm": LGBM_PARAMS,
                "num_boost_round": NUM_BOOST_ROUND,
                "calibration": {"method": "isotonic", "inner_windows": CALIBRATION_WINDOWS},
                "prob_clip": list(PROB_CLIP),
                "min_train_rows": MIN_TRAIN_ROWS,
                "features": list(FEATURE_COLUMNS),
                "required_features": list(REQUIRED_COLUMNS),
            }
        )
    )


def regime_universe() -> list[str]:
    """The pooled NSE slice (research.yaml go_no_go_1.slice: NSE equities and indices, minus INDIAVIX)."""
    s = load_research_config().go_no_go_1.slice
    return [
        i.id
        for i in load_watchlist()
        if i.exchange == s.exchange
        and i.kind in s.kinds
        and i.id not in s.exclude
        and i.tradable
        and TIMEFRAME in i.timeframes
    ]


# --- labels and the pooled panel (research only: labels read future bars) ------------------------


def regime_labels(df: pd.DataFrame, horizons) -> pd.DataFrame:
    """Per horizon h: y_h (1 if close[t+h] > close[t], 0 if below, NaN if flat or not yet known),
    flat_h, trade_ret_h (a long entered at the next open and exited at close[t+h]) and end_ts_h
    (the open time of bar t+h)."""
    fl = forward_labels(df, horizons)
    out: dict[str, pd.Series] = {}
    for h in horizons:
        moved = fl[f"up_{h}"] + fl[f"down_{h}"]
        out[f"y_{h}"] = fl[f"up_{h}"].where(moved == 1.0)
        out[f"flat_{h}"] = moved == 0.0
        out[f"trade_ret_{h}"] = fl[f"trade_ret_{h}"]
        out[f"end_ts_{h}"] = df["ts"].shift(-h)
    return pd.DataFrame(out, index=df.index)


def _frame_or_none(df: pd.DataFrame) -> pd.DataFrame | None:
    return df if len(df) else None


def build_panel(
    instruments,
    horizons,
    *,
    allow_holdout: bool = False,
    load: CandleLoader | None = None,
    pivot: PivotConfig | None = None,
) -> pd.DataFrame:
    """Features and labels of every instrument, stacked. Bars on or after the holdout are never loaded
    unless `allow_holdout`, which only an official go/no-go run passes."""
    pivot = pivot or load_pivot_config()
    start = pivot.daily_start_utc
    end = None if allow_holdout else pivot.holdout_start_utc

    def get(instrument_id: str) -> pd.DataFrame:
        df = load_research_candles(instrument_id, TIMEFRAME, allow_holdout=allow_holdout, load=load)
        keep = df["ts"] >= start
        if end is not None:
            keep &= df["ts"] < end
        return df[keep].reset_index(drop=True)

    market, vix = _frame_or_none(get(MARKET_ID)), _frame_or_none(get(VIX_ID))
    frames = []
    for instrument_id in instruments:
        df = get(instrument_id)
        if df.empty:
            continue
        frame = pd.concat(
            [df[["ts", "open", "close"]], regime_features(df, market, vix), regime_labels(df, horizons)],
            axis=1,
        )
        frame.insert(0, "instrument", instrument_id)
        frame.insert(1, "kind", get_instrument(instrument_id).kind)
        frame["pos"] = np.arange(len(df))
        frames.append(frame)
    if not frames:
        raise ValueError("no candles for any instrument")
    panel = pd.concat(frames, ignore_index=True)
    day = panel["ts"].dt.tz_convert(IST).dt.normalize()
    panel["day_idx"] = np.searchsorted(np.sort(day.unique()), day)
    panel["features_ok"] = panel[list(REQUIRED_COLUMNS)].notna().all(axis=1)
    return panel


# --- walk-forward ----------------------------------------------------------------------------------


Window = tuple[pd.Timestamp, pd.Timestamp]


def fold_windows(start: pd.Timestamp, end: pd.Timestamp, months: int) -> list[Window]:
    """[(lo, hi)] in UTC: `months`-long windows stepped in IST calendar months from `start`; the last
    one stops at `end`."""
    out = []
    t = start.tz_convert(IST)
    while t.tz_convert("UTC") < end:
        nxt = t + pd.DateOffset(months=months)
        out.append((t.tz_convert("UTC"), min(nxt.tz_convert("UTC"), end)))
        t = nxt
    return out


def gap_bars(h: int, pivot: PivotConfig) -> int:
    """Bars dropped before a cut: the purge covers the h-bar label overlap (at least h), the embargo adds
    the configured margin on top."""
    wf = pivot.walk_forward
    return max(wf.purge_bars, h) + wf.embargo_bars


def before_cut(panel: pd.DataFrame, cut: pd.Timestamp, gap: int) -> np.ndarray:
    """Rows of each instrument that open before `cut`, minus that instrument's last `gap` such rows."""
    before = panel["ts"] < cut
    n_before = before.groupby(panel["instrument"]).transform("sum")
    return (before & (panel["pos"] < n_before - gap)).to_numpy()


def usable_rows(panel: pd.DataFrame, h: int) -> np.ndarray:
    return (panel[f"y_{h}"].notna() & panel["features_ok"]).to_numpy()


def training_rows(panel: pd.DataFrame, cut: pd.Timestamp, h: int, pivot: PivotConfig) -> np.ndarray:
    train = before_cut(panel, cut, gap_bars(h, pivot)) & usable_rows(panel, h)
    if not (panel.loc[train, f"end_ts_{h}"] < cut).all():
        raise AssertionError("a training label ends at or after the cut")
    return train


@dataclass(frozen=True)
class FittedRegime:
    horizon: int
    booster: lgb.Booster
    iso_x: np.ndarray
    iso_y: np.ndarray
    n_train: int
    n_calibration: int

    def predict(self, X: np.ndarray) -> np.ndarray:
        return calibrate(self.booster.predict(X), self.iso_x, self.iso_y)


def calibrate(raw: np.ndarray, iso_x: np.ndarray, iso_y: np.ndarray) -> np.ndarray:
    """The isotonic map (linear between its thresholds, flat beyond them, as sklearn predicts it)."""
    return np.clip(np.interp(raw, iso_x, iso_y), *PROB_CLIP)


def _fit_booster(X: np.ndarray, y: np.ndarray, params: dict) -> lgb.Booster:
    data = lgb.Dataset(X, label=y, feature_name=list(FEATURE_COLUMNS), free_raw_data=True)
    return lgb.train(params, data, num_boost_round=NUM_BOOST_ROUND)


def calibration_windows(cut: pd.Timestamp, pivot: PivotConfig) -> list[Window]:
    months = pivot.walk_forward.retrain_every_months
    first = cut.tz_convert(IST) - pd.DateOffset(months=months * CALIBRATION_WINDOWS)
    return fold_windows(first.tz_convert("UTC"), cut, months)


def fit_regime(
    panel: pd.DataFrame,
    train: np.ndarray,
    cut: pd.Timestamp,
    h: int,
    pivot: PivotConfig,
    params: dict | None = None,
) -> FittedRegime | None:
    """Booster on the `train` rows (from `training_rows(panel, cut, ...)`), isotonic map from inner
    walk-forward predictions inside those rows. None when there is too little data to fit."""
    params = params or LGBM_PARAMS
    if train.sum() < MIN_TRAIN_ROWS:
        return None
    gap = gap_bars(h, pivot)
    usable = usable_rows(panel, h)
    X = panel[list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    y = panel[f"y_{h}"].to_numpy(dtype=float)
    ts = panel["ts"]
    raw_parts, y_parts = [], []
    for lo, hi in calibration_windows(cut, pivot):
        inner_train = before_cut(panel, lo, gap) & usable
        inner_test = train & ((ts >= lo) & (ts < hi)).to_numpy()
        if inner_train.sum() < MIN_TRAIN_ROWS or not inner_test.any():
            continue
        booster = _fit_booster(X[inner_train], y[inner_train], params)
        raw_parts.append(booster.predict(X[inner_test]))
        y_parts.append(y[inner_test])
    if not raw_parts:
        return None
    raw, outcome = np.concatenate(raw_parts), np.concatenate(y_parts)
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(raw, outcome)
    return FittedRegime(
        horizon=h,
        booster=_fit_booster(X[train], y[train], params),
        iso_x=np.asarray(iso.X_thresholds_, dtype=float),
        iso_y=np.asarray(iso.y_thresholds_, dtype=float),
        n_train=int(train.sum()),
        n_calibration=int(raw.size),
    )


def _rate_by(train: pd.DataFrame, keys: list[str], y: str, test: pd.DataFrame) -> np.ndarray:
    rates = train.groupby(keys)[y].mean().rename("_p").reset_index()
    return test[keys].merge(rates, how="left", on=keys)["_p"].to_numpy(dtype=float)


def baseline_probs(panel: pd.DataFrame, train: np.ndarray, test: np.ndarray, h: int) -> pd.DataFrame:
    """p_base, p_trend and p_mom for the `test` rows, from the `train` rows of the same instrument.
    A conditional rate with no training rows falls back to the base rate."""
    y = f"y_{h}"

    def frame(mask: np.ndarray) -> pd.DataFrame:
        part = panel.loc[mask, ["instrument", "close_ema200_atr", "ret_20", y]]
        return part.assign(above_ema200=part["close_ema200_atr"] > 0, up_20d=part["ret_20"] > 0)

    tr, te = frame(train), frame(test)
    base = _rate_by(tr, ["instrument"], y, te)
    trend = _rate_by(tr, ["instrument", "above_ema200"], y, te)
    mom = _rate_by(tr, ["instrument", "up_20d"], y, te)
    return pd.DataFrame(
        {
            "p_base": base,
            "p_trend": np.where(np.isnan(trend), base, trend),
            "p_mom": np.where(np.isnan(mom), base, mom),
        },
        index=panel.index[test],
    )


PREDICTION_COLUMNS = ["instrument", "kind", "ts", "day_idx"]


def walk_forward_predictions(
    panel: pd.DataFrame,
    h: int,
    windows: list[Window],
    pivot: PivotConfig,
    params: dict | None = None,
) -> pd.DataFrame:
    """One row per panel row inside a window: the model and baseline probabilities from a refit at the
    window start, with the outcome columns (NaN outcome: flat, or its bars are not available)."""
    X = panel[list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    ts = panel["ts"]
    parts = []
    for k, (lo, hi) in enumerate(windows):
        in_window = ((ts >= lo) & (ts < hi)).to_numpy()
        test = in_window & panel["features_ok"].to_numpy()
        part = panel.loc[in_window, PREDICTION_COLUMNS].copy()
        part["features_ok"] = panel.loc[in_window, "features_ok"]
        part["y"] = panel.loc[in_window, f"y_{h}"]
        part["flat"] = panel.loc[in_window, f"flat_{h}"]
        part["trade_ret"] = panel.loc[in_window, f"trade_ret_{h}"]
        part["fold"] = k
        part["fold_start"] = lo
        for col in ("p_model", "p_base", "p_trend", "p_mom"):
            part[col] = np.nan
        part["n_train"] = 0
        train = training_rows(panel, lo, h, pivot)
        if test.any() and train.any():
            fitted = fit_regime(panel, train, lo, h, pivot, params)
            if fitted is not None:
                part.loc[panel.index[test], "p_model"] = fitted.predict(X[test])
                part["n_train"] = fitted.n_train
            baselines = baseline_probs(panel, train, test, h)
            part.loc[panel.index[test], list(baselines.columns)] = baselines
        parts.append(part)
    if not parts:
        return pd.DataFrame(columns=[*PREDICTION_COLUMNS, "y", "p_model"])
    return pd.concat(parts).reset_index(drop=True)


# --- production model --------------------------------------------------------------------------------


def model_dir() -> Path:
    return get_settings().data_dir / "models" / MODEL_NAME


def validation_report_path() -> Path:
    return REPO_ROOT / "docs" / "test-reports" / VALIDATION_REPORT


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json_atomic(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)
    os.replace(tmp, path)


def train_production_model(
    instruments=None,
    *,
    load: CandleLoader | None = None,
    pivot: PivotConfig | None = None,
    params: dict | None = None,
    out_dir: Path | None = None,
) -> dict:
    """Fit every horizon on all data before the holdout (same gap and in-fold calibration as the
    walk-forward) and save boosters plus meta.json to data/models/regime_v1/. Returns the metadata."""
    pivot = pivot or load_pivot_config()
    ids = list(instruments) if instruments is not None else regime_universe()
    horizons = pivot.regime_model.horizons_days
    panel = build_panel(ids, horizons, allow_holdout=False, load=load, pivot=pivot)
    cut = pivot.holdout_start_utc
    out = out_dir or model_dir()
    out.mkdir(parents=True, exist_ok=True)
    meta: dict = {
        "model": MODEL_NAME,
        "trained_at": datetime.now(UTC).isoformat(),
        "cut": cut.isoformat(),
        "instruments": ids,
        "data_last_ts": panel.groupby("instrument")["ts"].max().map(lambda t: t.isoformat()).to_dict(),
        "pivot_sha256": pivot.sha256,
        "config_hashes": config_hashes(),
        "hyperparameters": hyperparameters(),
        "versions": {
            "lightgbm": lgb.__version__, "scikit-learn": sklearn.__version__, "pandas": pd.__version__
        },
        "horizons": {},
    }
    for h in horizons:
        train = training_rows(panel, cut, h, pivot)
        fitted = fit_regime(panel, train, cut, h, pivot, params)
        if fitted is None:
            raise ValueError(f"not enough training rows to fit h={h}")
        booster_file = f"booster_h{h}.txt"
        fitted.booster.save_model(str(out / booster_file))
        rows = panel.loc[train]
        stats = rows.groupby("instrument")[f"y_{h}"].agg(["mean", "size"])
        meta["horizons"][str(h)] = {
            "booster_file": booster_file,
            "booster_sha256": _sha256_file(out / booster_file),
            "iso_x": fitted.iso_x.tolist(),
            "iso_y": fitted.iso_y.tolist(),
            "n_train": fitted.n_train,
            "n_calibration": fitted.n_calibration,
            "gap_bars": gap_bars(h, pivot),
            "train_first_ts": rows["ts"].min().isoformat(),
            "train_last_ts": rows["ts"].max().isoformat(),
            "base_rates": {k: float(v) for k, v in stats["mean"].items()},
            "base_rate_n": {k: int(v) for k, v in stats["size"].items()},
        }
    _write_json_atomic(out / "meta.json", meta)
    return meta


@dataclass(frozen=True)
class ProductionModel:
    meta: dict
    fitted: dict[int, FittedRegime]
    version: str

    def base_rate(self, instrument_id: str, h: int) -> float | None:
        return self.meta["horizons"][str(h)]["base_rates"].get(instrument_id)


@lru_cache(maxsize=4)
def _load_model_cached(directory: str, mtime_ns: int) -> ProductionModel:
    root = Path(directory)
    meta_bytes = (root / "meta.json").read_bytes()
    meta = json.loads(meta_bytes)
    fitted = {}
    for key, entry in meta["horizons"].items():
        path = root / entry["booster_file"]
        if _sha256_file(path) != entry["booster_sha256"]:
            raise ValueError(f"{path} does not match the sha256 recorded in meta.json")
        fitted[int(key)] = FittedRegime(
            horizon=int(key),
            booster=lgb.Booster(model_file=str(path)),
            iso_x=np.asarray(entry["iso_x"], dtype=float),
            iso_y=np.asarray(entry["iso_y"], dtype=float),
            n_train=int(entry["n_train"]),
            n_calibration=int(entry["n_calibration"]),
        )
    return ProductionModel(meta=meta, fitted=fitted, version=hashlib.sha256(meta_bytes).hexdigest()[:12])


def load_production_model(directory: Path | None = None) -> ProductionModel | None:
    root = directory or model_dir()
    meta = root / "meta.json"
    if not meta.exists():
        return None
    return _load_model_cached(str(root), meta.stat().st_mtime_ns)


# --- live calls ---------------------------------------------------------------------------------------


class RegimeCall(BaseModel):
    """A regime_v1 forecast for the last closed daily bar. `validated` is true only when this horizon
    passed go/no-go #2 on validation (read from the saved report, never recomputed live)."""

    instrument: str
    horizon_days: int
    p_up: float
    base_rate: float
    edge: float  # p_up - base_rate
    direction: Literal["up", "down"] | None  # only for a validated, non-low-confidence call
    confidence: Literal["high", "medium", "low"]
    validated: bool
    reason: str
    ref_time: int  # UNIX seconds: open time of the daily bar the call was made on
    made_at: int
    model: str = MODEL_NAME
    model_version: str


@lru_cache(maxsize=4)
def _report_cached(path: str, mtime_ns: int) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_validation_report(path: Path | None = None) -> dict | None:
    path = path or validation_report_path()
    if not path.exists():
        return None
    return copy.deepcopy(_report_cached(str(path), path.stat().st_mtime_ns))


def validation_status(report: dict | None, h: int, pivot: PivotConfig) -> tuple[bool, str, dict | None]:
    """(validated, why, the horizon's report entry) from a saved validation report."""
    if report is None:
        return False, "no validation report", None
    if report.get("model") != MODEL_NAME or report.get("period") != "validation":
        return False, "the report is not a regime_v1 validation report", None
    if report.get("pivot_sha256") != pivot.sha256:
        return False, "pivot.yaml changed since validation", None
    if report.get("hyperparameters") != hyperparameters():
        return False, "hyperparameters changed since validation", None
    entry = report.get("horizons", {}).get(str(h))
    if entry is None:
        return False, f"{h}d was not validated", None
    if entry.get("pass"):
        return True, f"{h}d passed go/no-go #2 on validation", entry
    failed = [name for name, gate in entry.get("gates", {}).items() if not gate.get("pass")]
    return False, f"{h}d failed go/no-go #2 on validation ({', '.join(failed)})", entry


def _closed_bars(df: pd.DataFrame, exchange: str, now: pd.Timestamp) -> pd.DataFrame:
    cal = get_calendar()
    end = len(df)
    while end > 0 and cal.bar_close_time(exchange, df["ts"].iloc[end - 1], TIMEFRAME) > now:
        end -= 1
    return df.iloc[:end]


def _context_frame(frame: pd.DataFrame | None, instrument_id: str, now, load: CandleLoader | None):
    if frame is None:
        frame = (load or default_loader())(instrument_id, TIMEFRAME, start=None, end=now)
    frame = validate_candles(frame)
    return _frame_or_none(_closed_bars(frame, exchange_of(instrument_id), now))


def _confidence(p: float, base: float, validated: bool, entry: dict | None) -> tuple[str, str | None]:
    if not validated:
        return "low", None
    min_edge = load_research_config().min_edge
    if abs(p - base) < min_edge:
        return "low", f"edge {p - base:+.3f} is below min_edge {min_edge}"
    top = ((entry or {}).get("methods", {}).get(MODEL_NAME, {}).get("top_decile") or {})
    threshold = top.get("conviction_threshold")
    if threshold is not None and abs(p - 0.5) >= threshold:
        return "high", "in the validation top decile of conviction"
    return "medium", None


def predict_regime_all(
    instrument_id: str,
    candles_1d: pd.DataFrame,
    now,
    *,
    market: pd.DataFrame | None = None,
    vix: pd.DataFrame | None = None,
    load: CandleLoader | None = None,
    model: ProductionModel | None = None,
    report: dict | None = None,
    report_path: Path | None = None,
    pivot: PivotConfig | None = None,
) -> list[RegimeCall]:
    """One RegimeCall per horizon for the last daily bar closed by `now`, or [] when no call is
    possible (no saved model, instrument outside the model's universe, too little or stale history,
    missing market or VIX context). Pass the full daily history: EMA200 depends on where it starts.

    `market` / `vix` default to the stored NIFTY 50 and India VIX series, via `load`
    (candly.data.store.load_candles by default)."""
    model = model or load_production_model()
    if model is None or instrument_id not in model.meta["instruments"]:
        return []
    pivot = pivot or load_pivot_config()
    now = pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    df = _closed_bars(validate_candles(candles_1d), exchange_of(instrument_id), now)
    if len(df) < MIN_HISTORY_BARS:
        return []
    last = df["ts"].iloc[-1]
    if get_calendar().bar_close_time(exchange_of(instrument_id), last, TIMEFRAME) < now - STALE_AFTER:
        return []
    mkt = _context_frame(market, MARKET_ID, now, load)
    vx = _context_frame(vix, VIX_ID, now, load)
    features = regime_features(df, mkt, vx).iloc[[-1]]
    if features.isna().any(axis=None):
        return []
    X = features[list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    report = report if report is not None else read_validation_report(report_path)
    model_current = model.meta.get("pivot_sha256") == pivot.sha256 and (
        model.meta.get("hyperparameters") == hyperparameters()
    )
    calls = []
    for h, fitted in sorted(model.fitted.items()):
        base = model.base_rate(instrument_id, h)
        if base is None:
            continue
        p = float(fitted.predict(X)[0])
        validated, why, entry = validation_status(report, h, pivot)
        if validated and not model_current:
            validated, why = False, "the saved model predates the current pivot.yaml or hyperparameters"
        confidence, note = _confidence(p, base, validated, entry)
        direction = None
        if validated and confidence != "low":
            if p > 0.5 and p > base:
                direction = "up"
            elif p < 0.5 and p < base:
                direction = "down"
        reason = why if note is None else f"{why}; {note}"
        calls.append(
            RegimeCall(
                instrument=instrument_id,
                horizon_days=h,
                p_up=p,
                base_rate=float(base),
                edge=p - float(base),
                direction=direction,
                confidence=confidence,
                validated=validated,
                reason=reason,
                ref_time=int(last.timestamp()),
                made_at=int(now.timestamp()),
                model_version=model.version,
            )
        )
    return calls


def predict_regime(
    instrument_id: str,
    candles_1d: pd.DataFrame,
    now,
    *,
    horizon: int | None = None,
    **kwargs,
) -> RegimeCall | None:
    """The call for `horizon`, or by default for the validated horizon with the best validation Brier
    skill (the shortest horizon when none is validated). None when no call is possible."""
    calls = predict_regime_all(instrument_id, candles_1d, now, **kwargs)
    if not calls:
        return None
    if horizon is not None:
        return next((c for c in calls if c.horizon_days == horizon), None)
    validated = [c for c in calls if c.validated]
    if not validated:
        return min(calls, key=lambda c: c.horizon_days)
    report = kwargs.get("report") or read_validation_report(kwargs.get("report_path")) or {}

    def skill(call: RegimeCall) -> float:
        entry = report.get("horizons", {}).get(str(call.horizon_days), {})
        value = entry.get("methods", {}).get(MODEL_NAME, {}).get("skill")
        return float("-inf") if value is None else float(value)

    return max(validated, key=skill)
