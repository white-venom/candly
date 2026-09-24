"""vol_v1 forecasts of NIFTY 50 realised volatility (config/edge_search.yaml `volatility`, PLAN.md §20b).

Realised volatility has one definition, used for every forecaster's target and for the variance-swap P&L:
close-to-close log returns r (overnight gaps included, as in option prices and variance-swap settlement),
and for horizon h the annualised realised variance of the next h sessions,
    RV_h(t) = 252 / h * sum_{i=1..h} r_{t+i}^2        (not demeaned: the variance-swap convention),
quoted as 100 * sqrt(RV_h), annualised %, VIX's unit. The HAR and EWMA baselines use the same returns.
Range estimators (Parkinson, ATR) miss the overnight gap, so they are features only. The 5-minute bars
start in 2017-07, after the first training window, so they are not used.

Forecasters, each an annualised variance as of the close of day t:
- india_vix: (VIX_t / 100)^2 for both horizons. VIX is a 30-calendar-day implied volatility, so at h = 5
  it answers a different question (horizon mismatch), and it carries the variance risk premium.
- ewma_rv: RiskMetrics, s_t = 0.94 s_{t-1} + 0.06 r_t^2, annualised, flat across horizons.
- har_rv: OLS of RV_h(t) on the daily, weekly (5) and monthly (22) means of 252 r^2, refit per fold.
- realized_vol_model: LightGBM with the gamma objective and log link (its unit deviance is twice QLIKE,
  the evaluation loss), offset log(rv20 variance), on FEATURE_COLUMNS, refit per fold, fixed parameters.

Causality: every input at t uses underlying bars up to and including t, and VIX closes of IST dates up to
and including t's date. A date without a VIX print has no VIX features (no carry-forward), so it is not
scored.

Walk-forward: expanding window, a refit at the start of every IST calendar year from train_end. The last h
rows before a cut are purged, so every training label ends before the test window opens.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from candly.core.calendar import IST
from candly.core.schema import validate_candles
from candly.features.expiry import expiry_features, expiry_on, live_stamp
from candly.indicators import functions as f
from candly.research.data import CandleLoader, load_research_candles
from candly.research.edge_config import EdgeSearchConfig, load_edge_config
from candly.research.lgbm import lgb

MODEL_NAME = "realized_vol_model"
TIMEFRAME = "1D"
ANNUAL = 252
SEED = 20260924

RV_WINDOWS = (1, 5, 20, 60)
PARKINSON_WINDOWS = (1, 5, 20)
GAP_VOL_WINDOW = 20
RETURN_WINDOWS = (1, 5, 20)
VIX_CHANGE_SESSIONS = (1, 5, 20)
ATR_PERIOD = 14
HAR_WINDOWS = {"har_d": 1, "har_w": 5, "har_m": 22}
EWMA_LAMBDA = 0.94
OFFSET_FEATURE = "rv20"
_PARKINSON = 1.0 / (4.0 * np.log(2.0))

FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "realized_vol": tuple(f"rv{n}" for n in RV_WINDOWS),
    "range": ("atr_pct", *(f"park{n}" for n in PARKINSON_WINDOWS)),
    "gaps": ("gap1", f"gapvol{GAP_VOL_WINDOW}"),
    "vix": ("vix", *(f"vix_chg{k}" for k in VIX_CHANGE_SESSIONS), "vix_rv20_spread"),
    "calendar": ("day_of_week", "expiry_day", "days_to_expiry", "monthly_expiry_week"),
    "leverage": tuple(f"ret{n}" for n in RETURN_WINDOWS),
}
FEATURE_COLUMNS = tuple(c for cols in FEATURE_GROUPS.values() for c in cols)
HAR_COLUMNS = tuple(HAR_WINDOWS)
BASELINE_INPUTS = (*HAR_COLUMNS, "ewma_var", "vix_var", "offset_var")
FORECAST_COLUMNS = {
    MODEL_NAME: "model_var",
    "india_vix": "vix_var",
    "har_rv": "har_var",
    "ewma_rv": "ewma_var",
}
DIAGNOSTIC_COLUMNS = {"india_vix_rescaled": "vix_scaled_var"}

# Fixed before any fit and never tuned on validation: shallow trees, large leaves (training labels
# overlap for h days), bagging and L2.
LGBM_PARAMS: dict = {
    "objective": "gamma",
    "learning_rate": 0.03,
    "num_leaves": 7,
    "max_depth": 3,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.7,
    "bagging_freq": 1,
    "lambda_l2": 10.0,
    "seed": SEED,
    "deterministic": True,
    "force_row_wise": True,
    "verbose": -1,
}
NUM_BOOST_ROUND = 300
MIN_TRAIN_ROWS = 2 * LGBM_PARAMS["min_data_in_leaf"]
HAR_FLOOR_SHARE = 0.01  # HAR forecasts below this share of the training-mean target are clipped up to it


def hyperparameters() -> dict:
    """Everything that shapes a fit, as recorded with the validation report."""
    return json.loads(
        json.dumps(
            {
                "lightgbm": LGBM_PARAMS,
                "num_boost_round": NUM_BOOST_ROUND,
                "offset": f"log(({OFFSET_FEATURE} / 100)^2)",
                "min_train_rows": MIN_TRAIN_ROWS,
                "features": list(FEATURE_COLUMNS),
                "har_windows": HAR_WINDOWS,
                "har_floor_share": HAR_FLOOR_SHARE,
                "ewma_lambda": EWMA_LAMBDA,
                "annualisation_days": ANNUAL,
            }
        )
    )


# --- causal inputs -----------------------------------------------------------------------------------


def log_returns(df: pd.DataFrame) -> pd.Series:
    return np.log(df["close"].astype("float64")).diff()


def _ann_vol(mean_square: pd.Series) -> pd.Series:
    return 100.0 * np.sqrt(ANNUAL * mean_square)


def _ist_dates(ts: pd.Series) -> pd.Series:
    return ts.dt.tz_convert(IST).dt.date


def vix_features(ts: pd.Series, vix: pd.DataFrame | None) -> pd.DataFrame:
    """VIX close on each bar's IST date and its log changes (%) over VIX's own last k sessions."""
    columns = ["vix", *(f"vix_chg{k}" for k in VIX_CHANGE_SESSIONS)]
    if vix is None or vix.empty:
        return pd.DataFrame(np.nan, index=ts.index, columns=columns)
    level = vix["close"].astype("float64").reset_index(drop=True)
    log_level = np.log(level)
    values = pd.DataFrame(
        {
            "vix": level,
            **{f"vix_chg{k}": 100.0 * (log_level - log_level.shift(k)) for k in VIX_CHANGE_SESSIONS},
        }
    )
    values.index = pd.Index(_ist_dates(vix["ts"]).to_numpy())
    values = values[~values.index.duplicated(keep="last")]
    out = values.reindex(_ist_dates(ts).to_numpy())
    out.index = ts.index
    return out[columns]


def calendar_features(ts: pd.Series, instrument_id: str) -> pd.DataFrame:
    """IST weekday, expiry day, trading days to the next expiry, and whether that expiry is a monthly one
    in the bar's own calendar week. From the published expiry schedule only, so causal."""
    days = _ist_dates(ts)
    expiry = expiry_features(instrument_id, ts)
    stamp = live_stamp()
    monthly_week: dict = {}
    for d in pd.unique(days):
        info = expiry_on(instrument_id, d, stamp)
        monthly_week[d] = (
            np.nan
            if info is None
            else float(info.kind == "monthly" and info.next_expiry.isocalendar()[:2] == d.isocalendar()[:2])
        )
    return pd.DataFrame(
        {
            "day_of_week": ts.dt.tz_convert(IST).dt.weekday.astype("float64"),
            "expiry_day": expiry["expiry_day"].map({True: 1.0, False: 0.0}).astype("float64"),
            "days_to_expiry": expiry["days_to_expiry"].astype("float64"),
            "monthly_expiry_week": days.map(monthly_week).astype("float64"),
        },
        index=ts.index,
    )


def vol_features(df: pd.DataFrame, vix: pd.DataFrame | None, instrument_id: str) -> pd.DataFrame:
    """FEATURE_COLUMNS on `df`'s index. Vols are annualised %, returns and changes are log %."""
    o, h, lo, c = (df[k].astype("float64") for k in ("open", "high", "low", "close"))
    r2 = log_returns(df) ** 2
    out: dict[str, pd.Series] = {}
    for n in RV_WINDOWS:
        out[f"rv{n}"] = _ann_vol(r2.rolling(n, min_periods=n).mean())
    out["atr_pct"] = 100.0 * f.atr(df, ATR_PERIOD) / c
    hl2 = _PARKINSON * np.log(h / lo) ** 2
    for n in PARKINSON_WINDOWS:
        out[f"park{n}"] = _ann_vol(hl2.rolling(n, min_periods=n).mean())
    gap = np.log(o / c.shift(1))
    out["gap1"] = 100.0 * gap
    gap_ms = (gap**2).rolling(GAP_VOL_WINDOW, min_periods=GAP_VOL_WINDOW).mean()
    out[f"gapvol{GAP_VOL_WINDOW}"] = _ann_vol(gap_ms)
    log_close = np.log(c)
    for n in RETURN_WINDOWS:
        out[f"ret{n}"] = 100.0 * (log_close - log_close.shift(n))
    frame = pd.concat(
        [
            pd.DataFrame(out, index=df.index),
            vix_features(df["ts"], vix),
            calendar_features(df["ts"], instrument_id),
        ],
        axis=1,
    )
    frame["vix_rv20_spread"] = frame["vix"] - frame["rv20"]
    return frame[list(FEATURE_COLUMNS)].replace([np.inf, -np.inf], np.nan).astype("float64")


def har_regressors(df: pd.DataFrame) -> pd.DataFrame:
    """Daily, weekly and monthly means of the annualised squared return, ending at t."""
    r2 = ANNUAL * log_returns(df) ** 2
    return pd.DataFrame({name: r2.rolling(n, min_periods=n).mean() for name, n in HAR_WINDOWS.items()})


def ewma_variance(df: pd.DataFrame, lam: float = EWMA_LAMBDA) -> pd.Series:
    """RiskMetrics variance, annualised, started from the first squared return."""
    return ANNUAL * (log_returns(df) ** 2).ewm(alpha=1.0 - lam, adjust=False).mean()


def forecast_inputs(df: pd.DataFrame, vix: pd.DataFrame | None, instrument_id: str) -> pd.DataFrame:
    """Every causal input on `df`'s index: FEATURE_COLUMNS, then BASELINE_INPUTS."""
    frame = pd.concat([vol_features(df, vix, instrument_id), har_regressors(df)], axis=1)
    frame["ewma_var"] = ewma_variance(df)
    frame["vix_var"] = (frame["vix"] / 100.0) ** 2
    frame["offset_var"] = (frame[OFFSET_FEATURE] / 100.0) ** 2
    return frame


# --- labels (future bars: research only) --------------------------------------------------------------


def forward_realized_variance(df: pd.DataFrame, h: int) -> pd.Series:
    """RV_h(t) = 252 / h * sum of r^2 over bars t+1..t+h; NaN when those bars aren't all loaded."""
    r2 = log_returns(df) ** 2
    return ANNUAL / h * r2.rolling(h, min_periods=h).sum().shift(-h)


def build_frame(
    underlying: pd.DataFrame, vix: pd.DataFrame | None, horizons, instrument_id: str
) -> pd.DataFrame:
    """One row per underlying bar: ts, day_idx (position), inputs, `features_ok`, and per horizon the
    target y_h and the open time of its last bar, end_ts_h."""
    underlying = validate_candles(underlying).reset_index(drop=True)
    inputs = forecast_inputs(underlying, vix, instrument_id)
    frame = pd.concat(
        [pd.DataFrame({"ts": underlying["ts"], "day_idx": np.arange(len(underlying))}), inputs], axis=1
    )
    required = [*FEATURE_COLUMNS, *BASELINE_INPUTS]
    frame["features_ok"] = frame[required].notna().all(axis=1) & (frame["offset_var"] > 0)
    for h in horizons:
        frame[f"y_{h}"] = forward_realized_variance(underlying, h)
        frame[f"end_ts_{h}"] = underlying["ts"].shift(-h)
    return frame


def load_inputs(
    edge: EdgeSearchConfig, allow_holdout: bool = False, load: CandleLoader | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Daily bars of the underlying and of the implied-vol index. Without `allow_holdout` (only the
    official go/no-go run passes it), nothing on or after the holdout start is returned."""
    spec = edge.volatility
    underlying = load_research_candles(spec.underlying, TIMEFRAME, allow_holdout, load)
    vix = load_research_candles(spec.implied, TIMEFRAME, allow_holdout, load)
    if not allow_holdout:
        cutoff = edge.common.holdout_start_utc
        underlying = underlying[underlying["ts"] < cutoff].reset_index(drop=True)
        vix = vix[vix["ts"] < cutoff].reset_index(drop=True)
    return underlying, vix


def build_vol_frame(
    edge: EdgeSearchConfig | None = None, allow_holdout: bool = False, load: CandleLoader | None = None
) -> pd.DataFrame:
    edge = edge or load_edge_config()
    underlying, vix = load_inputs(edge, allow_holdout, load)
    return build_frame(underlying, vix, edge.volatility.horizons_days, edge.volatility.underlying)


# --- walk-forward --------------------------------------------------------------------------------------

Window = tuple[pd.Timestamp, pd.Timestamp]


def yearly_windows(start: pd.Timestamp, end: pd.Timestamp) -> list[Window]:
    """[(lo, hi)] in UTC: 12-month windows stepped in IST calendar time from `start`; the last one
    stops at `end`."""
    out = []
    t = start.tz_convert(IST)
    while t.tz_convert("UTC") < end:
        nxt = t + pd.DateOffset(years=1)
        out.append((t.tz_convert("UTC"), min(nxt.tz_convert("UTC"), end)))
        t = nxt
    return out


def training_mask(frame: pd.DataFrame, cut: pd.Timestamp, h: int) -> np.ndarray:
    """Rows before `cut` minus the last h of them (purge), with every input and a positive target."""
    n_before = int((frame["ts"] < cut).sum())
    y = frame[f"y_{h}"]
    mask = (frame["day_idx"] < n_before - h) & frame["features_ok"] & y.notna() & (y > 0)
    if not (frame.loc[mask, f"end_ts_{h}"] < cut).all():
        raise AssertionError("a training label ends at or after the cut")
    return mask.to_numpy()


def predict_model(booster: lgb.Booster, X: np.ndarray, offset_var: np.ndarray) -> np.ndarray:
    return np.exp(booster.predict(X, raw_score=True) + np.log(offset_var))


def fit_har(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    design = np.column_stack([np.ones(len(X)), X])
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    return coef


def predict_har(coef: np.ndarray, X: np.ndarray, floor: float) -> tuple[np.ndarray, int]:
    raw = coef[0] + X @ coef[1:]
    return np.maximum(raw, floor), int((raw < floor).sum())


@dataclass(frozen=True)
class FoldFit:
    booster: lgb.Booster
    har_coef: np.ndarray
    har_floor: float
    vix_scale: float  # training mean of RV / VIX^2: the QLIKE-optimal constant rescaling (diagnostic)
    margin_vol: float  # std of (realised vol - model vol) on the training rows, annualised %
    n_train: int
    first_train_ts: pd.Timestamp
    last_train_ts: pd.Timestamp
    last_label_end_ts: pd.Timestamp


def fit_fold(frame: pd.DataFrame, train: np.ndarray, h: int, params: dict | None = None) -> FoldFit | None:
    rows = frame[train]
    if len(rows) < MIN_TRAIN_ROWS:
        return None
    X = rows[list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    y = rows[f"y_{h}"].to_numpy(dtype=float)
    offset = rows["offset_var"].to_numpy(dtype=float)
    data = lgb.Dataset(X, label=y, init_score=np.log(offset), feature_name=list(FEATURE_COLUMNS))
    booster = lgb.train(params or LGBM_PARAMS, data, num_boost_round=NUM_BOOST_ROUND)
    fitted = predict_model(booster, X, offset)
    return FoldFit(
        booster=booster,
        har_coef=fit_har(rows[list(HAR_COLUMNS)].to_numpy(dtype=float), y),
        har_floor=HAR_FLOOR_SHARE * float(y.mean()),
        vix_scale=float(np.mean(y / rows["vix_var"].to_numpy(dtype=float))),
        margin_vol=float(np.std(100.0 * np.sqrt(y) - 100.0 * np.sqrt(fitted), ddof=1)),
        n_train=len(rows),
        first_train_ts=rows["ts"].iloc[0],
        last_train_ts=rows["ts"].iloc[-1],
        last_label_end_ts=rows[f"end_ts_{h}"].max(),
    )


PREDICTION_COLUMNS = [
    "ts",
    "day_idx",
    "fold",
    "features_ok",
    "y",
    "vix_var",
    "ewma_var",
    "har_var",
    "model_var",
    "vix_scaled_var",
    "margin_vol",
]


def walk_forward(
    frame: pd.DataFrame, h: int, windows: list[Window], params: dict | None = None
) -> tuple[pd.DataFrame, list[dict]]:
    """Out-of-sample forecasts for every row inside `windows` (PREDICTION_COLUMNS), and one record per
    fold. Rows of a fold without enough training data keep NaN model/HAR forecasts."""
    parts, folds = [], []
    for k, (lo, hi) in enumerate(windows):
        test = ((frame["ts"] >= lo) & (frame["ts"] < hi)).to_numpy()
        if not test.any():
            continue
        fit = fit_fold(frame, training_mask(frame, lo, h), h, params)
        rows = frame[test]
        out = pd.DataFrame(
            {
                "ts": rows["ts"],
                "day_idx": rows["day_idx"],
                "fold": k,
                "features_ok": rows["features_ok"].astype(bool),
                "y": rows[f"y_{h}"],
                "vix_var": rows["vix_var"],
                "ewma_var": rows["ewma_var"],
            }
        )
        for col in ("har_var", "model_var", "vix_scaled_var", "margin_vol"):
            out[col] = np.nan
        n_clipped = 0
        ok = out["features_ok"].to_numpy()
        if fit is not None and ok.any():
            X = rows.loc[ok, list(FEATURE_COLUMNS)].to_numpy(dtype=float)
            offset = rows.loc[ok, "offset_var"].to_numpy(dtype=float)
            out.loc[ok, "model_var"] = predict_model(fit.booster, X, offset)
            har, n_clipped = predict_har(
                fit.har_coef, rows.loc[ok, list(HAR_COLUMNS)].to_numpy(dtype=float), fit.har_floor
            )
            out.loc[ok, "har_var"] = har
            out.loc[ok, "vix_scaled_var"] = fit.vix_scale * rows.loc[ok, "vix_var"]
            out["margin_vol"] = fit.margin_vol
        folds.append(
            {
                "fold": k,
                "test_start": lo.isoformat(),
                "test_end": hi.isoformat(),
                "n_test": int(test.sum()),
                "fitted": fit is not None,
                "n_train": fit.n_train if fit else int(training_mask(frame, lo, h).sum()),
                "first_train_ts": fit.first_train_ts.isoformat() if fit else None,
                "last_train_ts": fit.last_train_ts.isoformat() if fit else None,
                "last_label_end_ts": fit.last_label_end_ts.isoformat() if fit else None,
                "margin_vol": fit.margin_vol if fit else None,
                "vix_scale": fit.vix_scale if fit else None,
                "har_coef": [float(c) for c in fit.har_coef] if fit else None,
                "har_clipped": n_clipped,
            }
        )
        parts.append(out)
    preds = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=PREDICTION_COLUMNS)
    return preds[PREDICTION_COLUMNS], folds
