"""vrp_v1: does the variance risk premium predict NIFTY 50 returns? (config/edge_search_v2.yaml
`vrp_direction`, PLAN.md §20c; Bollerslev, Tauchen & Zhou 2009.)

At the close of day t, from daily bars only:
- iv2_t = (VIX_t / 100)^2 / 12: VIX's annualised variance in monthly units (BTZ's convention). VIX is its
  close on t's IST date; a date without a VIX print has no vrp (no carry-forward).
- rv22_t = sum of r^2 over the last 22 sessions, r = close-to-close log return (monthly units).
- vrp_t = iv2_t - rv22_t.
Target (future bars, research only): y_h(t) = log(close_{t+h} / close_t).

Walk-forward: expanding window from the train start, refitted at the start of every IST calendar year
from the validation start. A fold trains on rows whose target ends before the fold opens. Forecasts:
- model: OLS of y_h on [1, vrp];
- benchmark: the mean of y_h over the same training rows (the model with its slope set to 0, so the
  two are nested, as Clark-West assumes).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from candly.core.schema import validate_candles
from candly.research.config import ist_midnight_utc
from candly.research.data import CandleLoader, load_research_candles
from candly.research.edge_config import load_edge_config
from candly.research.edge_v2_config import edge_v2_section
from candly.research.vol2_model import newey_west_cov
from candly.research.vol_model import vix_features, yearly_windows

SECTION = "vrp_direction"
TEST_NAME = "vrp_v1"
TIMEFRAME = "1D"
RV_SESSIONS = 22
MONTHS_PER_YEAR = 12
MIN_TRAIN_ROWS = 250


@dataclass(frozen=True)
class VrpSpec:
    name: str
    underlying: str
    implied: str
    horizons_days: tuple[int, ...]
    train_start: pd.Timestamp  # UTC, IST midnight
    validation_start: pd.Timestamp
    holdout_start: pd.Timestamp
    clark_west_p_below: float
    timing_rule: str
    raw: dict


def load_spec() -> VrpSpec:
    raw = edge_v2_section(SECTION)
    edge = load_edge_config()
    train, validation = raw["split"]["train"], raw["split"]["validation"]
    if validation[1] != "holdout_start" or train[1] != validation[0]:
        raise ValueError(f"{SECTION}.split must be train [a, b], validation [b, holdout_start]")
    if raw["name"] != TEST_NAME:
        raise ValueError(f"{SECTION}.name is {raw['name']!r}, this module implements {TEST_NAME}")
    return VrpSpec(
        name=raw["name"],
        underlying=raw["underlying"],
        implied=edge.volatility.implied,
        horizons_days=tuple(int(h) for h in raw["horizons_days"]),
        train_start=ist_midnight_utc(pd.Timestamp(train[0]).date()),
        validation_start=ist_midnight_utc(pd.Timestamp(train[1]).date()),
        holdout_start=edge.common.holdout_start_utc,
        clark_west_p_below=float(raw["pass_if"]["clark_west_p_below"]),
        timing_rule=str(raw["timing_rule"]),
        raw=raw,
    )


# --- causal inputs -----------------------------------------------------------------------------------


def vrp_inputs(daily: pd.DataFrame, vix: pd.DataFrame | None) -> pd.DataFrame:
    """iv2, rv22 and vrp on `daily`'s index, in monthly variance units."""
    close = daily["close"].astype("float64")
    r2 = np.log(close).diff() ** 2
    iv2 = (vix_features(daily["ts"], vix)["vix"] / 100.0) ** 2 / MONTHS_PER_YEAR
    rv22 = r2.rolling(RV_SESSIONS, min_periods=RV_SESSIONS).sum()
    return pd.DataFrame({"iv2": iv2, "rv22": rv22, "vrp": iv2 - rv22}, index=daily.index).astype("float64")


def forward_log_return(daily: pd.DataFrame, h: int) -> pd.Series:
    log_close = np.log(daily["close"].astype("float64"))
    return log_close.shift(-h) - log_close


def build_frame(daily: pd.DataFrame, vix: pd.DataFrame | None, horizons) -> pd.DataFrame:
    """One row per daily bar: ts, day_idx, close, the inputs, and per horizon y_h and end_ts_h."""
    daily = validate_candles(daily).reset_index(drop=True)
    frame = pd.concat(
        [
            pd.DataFrame({"ts": daily["ts"], "day_idx": np.arange(len(daily)), "close": daily["close"]}),
            vrp_inputs(daily, vix),
        ],
        axis=1,
    )
    for h in horizons:
        frame[f"y_{h}"] = forward_log_return(daily, h)
        frame[f"end_ts_{h}"] = daily["ts"].shift(-h)
    return frame


def load_inputs(spec: VrpSpec, load: CandleLoader | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Daily bars of the underlying and of VIX, every bar on or after the holdout start dropped first."""
    out = []
    for instrument in (spec.underlying, spec.implied):
        df = load_research_candles(instrument, TIMEFRAME, allow_holdout=False, load=load)
        out.append(df[df["ts"] < spec.holdout_start].reset_index(drop=True))
    return out[0], out[1]


# --- walk-forward -----------------------------------------------------------------------------------


def training_mask(frame: pd.DataFrame, spec: VrpSpec, cut: pd.Timestamp, h: int) -> np.ndarray:
    y = frame[f"y_{h}"]
    mask = (frame["ts"] >= spec.train_start) & (frame[f"end_ts_{h}"] < cut) & frame["vrp"].notna() & y.notna()
    return mask.to_numpy()


@dataclass(frozen=True)
class VrpFit:
    intercept: float
    slope: float
    slope_hac_t: float
    benchmark_mean: float
    n: int
    first_ts: pd.Timestamp
    last_ts: pd.Timestamp
    last_label_end_ts: pd.Timestamp


def fit_fold(rows: pd.DataFrame, h: int) -> VrpFit:
    y = rows[f"y_{h}"].to_numpy(dtype=float)
    X = np.column_stack([np.ones(len(rows)), rows["vrp"].to_numpy(dtype=float)])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    se = np.sqrt(np.diag(newey_west_cov(X, y - X @ coef, h)))
    return VrpFit(
        intercept=float(coef[0]),
        slope=float(coef[1]),
        slope_hac_t=float(coef[1] / se[1]) if se[1] > 0 else float("nan"),
        benchmark_mean=float(y.mean()),
        n=len(rows),
        first_ts=rows["ts"].iloc[0],
        last_ts=rows["ts"].iloc[-1],
        last_label_end_ts=rows[f"end_ts_{h}"].max(),
    )


PREDICTION_COLUMNS = ["ts", "day_idx", "fold", "vrp", "y", "end_ts", "forecast", "benchmark"]


def walk_forward(frame: pd.DataFrame, spec: VrpSpec, h: int) -> tuple[pd.DataFrame, list[dict]]:
    """Out-of-sample model and benchmark forecasts for every validation row with a vrp (resolved target
    or not), and one record per fold."""
    parts, folds = [], []
    for k, (lo, hi) in enumerate(yearly_windows(spec.validation_start, spec.holdout_start)):
        test = ((frame["ts"] >= lo) & (frame["ts"] < hi) & frame["vrp"].notna()).to_numpy()
        train = training_mask(frame, spec, lo, h)
        if not test.any():
            continue
        if train.sum() < MIN_TRAIN_ROWS:
            raise ValueError(f"fold {k}: only {int(train.sum())} training rows at h={h}")
        fit = fit_fold(frame[train], h)
        if not fit.last_label_end_ts < lo:
            raise AssertionError("a training label ends at or after the fold start")
        rows = frame[test]
        parts.append(
            pd.DataFrame(
                {
                    "ts": rows["ts"],
                    "day_idx": rows["day_idx"],
                    "fold": k,
                    "vrp": rows["vrp"],
                    "y": rows[f"y_{h}"],
                    "end_ts": rows[f"end_ts_{h}"],
                    "forecast": fit.intercept + fit.slope * rows["vrp"],
                    "benchmark": fit.benchmark_mean,
                }
            )
        )
        folds.append(
            {
                "fold": k,
                "test_start": lo.isoformat(),
                "test_end": hi.isoformat(),
                "n_test": int(test.sum()),
                "n_train": fit.n,
                "first_train_ts": fit.first_ts.isoformat(),
                "last_train_ts": fit.last_ts.isoformat(),
                "last_label_end_ts": fit.last_label_end_ts.isoformat(),
                "intercept": fit.intercept,
                "slope": fit.slope,
                "slope_hac_t": fit.slope_hac_t,
                "benchmark_mean": fit.benchmark_mean,
            }
        )
    preds = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=PREDICTION_COLUMNS)
    return preds[PREDICTION_COLUMNS], folds
