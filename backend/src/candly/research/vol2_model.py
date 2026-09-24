"""vol_v2 forecasts of NIFTY 50 realised variance (config/edge_search_v2.yaml `vol_intraday`, PLAN.md §20c).

Target: vol_v1's, unchanged (candly.research.vol_model.forward_realized_variance): for horizon h the
annualised realised variance of the next h sessions from daily close-to-close log returns,
    RV_h(t) = 252 / h * sum_{i=1..h} r_{t+i}^2.

Inputs, each known at the close of day t (all variances annualised with 252):
- rv5m: the session's 5-minute realised variance, 252 * sum of squared 5m log returns inside the IST
  session (the first bar's open to its close, then close to close). The overnight gap is not in it.
- HAR components of rv5m: its means over the last 1, 5 and 22 regular sessions up to and including t
  (rv5m_d, rv5m_w, rv5m_m). A regular session has at least MIN_SESSION_BARS 5m bars; short ones (Muhurat,
  weekend drills, outages) and days without 5m bars are skipped by the windows and get no forecast.
- on2: the overnight squared return, 252 * g_t^2 with g_t = log(open_t / close_{t-1}) on daily bars
  (the official previous close to today's first price), kept apart from rv5m.
- vix_var: (VIX_t / 100)^2, VIX's close on t's IST date (no carry-forward, as vol_v1).

Forecasters, each fitted once on the train window and never refitted on validation:
- model (vol_v2): OLS of log RV_h on [1, log rv5m_d, log rv5m_w, log rv5m_m, log on2, log vix_var].
  log on2 floors |g_t| at OVERNIGHT_FLOOR (log 0 is undefined).
- recalibrated_vix (the baseline): OLS of log RV_h on [1, log vix_var].
- har_5m_log (context): the model without the VIX term.
Every log-OLS forecast is exp(x'b) times its Duan smearing factor, the training mean of exp(residual):
the same retransformation for every log model, so none gains from a different bias correction.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from candly.core.calendar import IST
from candly.core.schema import validate_candles
from candly.research.config import ist_midnight_utc
from candly.research.data import CandleLoader, load_research_candles
from candly.research.edge_config import load_edge_config
from candly.research.edge_v2_config import edge_v2_section
from candly.research.vol_model import (
    ANNUAL,
    forward_realized_variance,
    har_regressors,
    vix_features,
)

SECTION = "vol_intraday"
TEST_NAME = "vol_v2"
DAILY_TF, INTRADAY_TF = "1D", "5m"
HAR_5M_WINDOWS = {"rv5m_d": 1, "rv5m_w": 5, "rv5m_m": 22}
OVERNIGHT_FLOOR = 1e-4  # 1 basis point, fixed before any fit
MIN_SESSION_BARS = 60  # of the 75 in a full session; shorter sessions have no rv5m
MIN_TRAIN_ROWS = 100

REGRESSORS: dict[str, tuple[str, ...]] = {
    "vol_v2_model": ("log_rv5m_d", "log_rv5m_w", "log_rv5m_m", "log_on2", "log_vix_var"),
    "recalibrated_vix": ("log_vix_var",),
    "har_5m_log": ("log_rv5m_d", "log_rv5m_w", "log_rv5m_m", "log_on2"),
}
MODEL, BASELINE = "vol_v2_model", "recalibrated_vix"
INPUT_COLUMNS = ("rv5m", "n_5m_bars", *HAR_5M_WINDOWS, "on2", "vix_var")
LOG_COLUMNS = ("log_rv5m_d", "log_rv5m_w", "log_rv5m_m", "log_on2", "log_vix_var")


@dataclass(frozen=True)
class Vol2Spec:
    name: str
    underlying: str
    implied: str
    horizons_days: tuple[int, ...]
    train_start: pd.Timestamp  # UTC, IST midnight
    validation_start: pd.Timestamp
    holdout_start: pd.Timestamp
    qlike_ci_lower_above: float
    sharpe_ci_lower_above: float
    caveat: str
    raw: dict


def load_spec() -> Vol2Spec:
    """vol_intraday from edge_search_v2.yaml; the holdout start from edge_search.yaml common."""
    raw = edge_v2_section(SECTION)
    edge = load_edge_config()
    split = raw["split"]
    train, validation = split["train"], split["validation"]
    if validation[1] != "holdout_start" or train[1] != validation[0]:
        raise ValueError(f"{SECTION}.split must be train [a, b], validation [b, holdout_start]")
    if raw["name"] != TEST_NAME:
        raise ValueError(f"{SECTION}.name is {raw['name']!r}, this module implements {TEST_NAME}")
    gate = raw["pass_if"]
    return Vol2Spec(
        name=raw["name"],
        underlying=raw["underlying"],
        implied=raw["implied"],
        horizons_days=tuple(int(h) for h in raw["horizons_days"]),
        train_start=ist_midnight_utc(pd.Timestamp(train[0]).date()),
        validation_start=ist_midnight_utc(pd.Timestamp(train[1]).date()),
        holdout_start=edge.common.holdout_start_utc,
        qlike_ci_lower_above=float(gate["qlike_improvement_vs_recalibrated_vix_ci_lower_above"]),
        sharpe_ci_lower_above=float(gate["conditional_vs_always_short_sharpe_ci_lower_above"]),
        caveat=str(raw["caveat"]).strip(),
        raw=raw,
    )


# --- causal inputs -----------------------------------------------------------------------------------


def _ist_dates(ts: pd.Series) -> pd.Series:
    return ts.dt.tz_convert(IST).dt.date


def session_realized_variance(bars: pd.DataFrame) -> pd.DataFrame:
    """Per IST session date: rv5m (annualised) and n_5m_bars. Sessions with fewer than MIN_SESSION_BARS
    bars keep their bar count but no rv5m."""
    bars = validate_candles(bars)
    if bars.empty:
        return pd.DataFrame({"rv5m": [], "n_5m_bars": []}, index=pd.Index([], dtype=object))
    date = _ist_dates(bars["ts"])
    log_close = np.log(bars["close"])
    first = date.ne(date.shift())
    r = log_close.diff().where(~first, log_close - np.log(bars["open"]))
    grouped = pd.DataFrame({"date": date, "r2": r**2}).groupby("date", sort=True)["r2"]
    out = pd.DataFrame({"rv5m": ANNUAL * grouped.sum(), "n_5m_bars": grouped.size().astype("float64")})
    out.loc[out["n_5m_bars"] < MIN_SESSION_BARS, "rv5m"] = np.nan
    return out


def vol2_inputs(daily: pd.DataFrame, bars5m: pd.DataFrame, vix: pd.DataFrame | None) -> pd.DataFrame:
    """INPUT_COLUMNS and LOG_COLUMNS on `daily`'s index."""
    daily = validate_candles(daily)
    sessions = session_realized_variance(bars5m)
    regular = sessions["rv5m"].dropna()
    for name, n in HAR_5M_WINDOWS.items():
        sessions[name] = regular.rolling(n, min_periods=n).mean()
    on_date = sessions.reindex(_ist_dates(daily["ts"]).to_numpy())
    out = pd.DataFrame(
        {c: on_date[c].to_numpy() for c in ("rv5m", "n_5m_bars", *HAR_5M_WINDOWS)}, index=daily.index
    )
    out[list(HAR_5M_WINDOWS)] = out[list(HAR_5M_WINDOWS)].where(out["rv5m"].notna())
    gap = np.log(daily["open"] / daily["close"].shift(1))
    out["on2"] = ANNUAL * gap**2
    out["vix_var"] = (vix_features(daily["ts"], vix)["vix"] / 100.0) ** 2
    for name in HAR_5M_WINDOWS:
        out[f"log_{name}"] = np.log(out[name].where(out[name] > 0))
    out["log_on2"] = np.log(ANNUAL * np.maximum(gap**2, OVERNIGHT_FLOOR**2))
    out["log_vix_var"] = np.log(out["vix_var"].where(out["vix_var"] > 0))
    return out.astype("float64")


def build_frame(
    daily: pd.DataFrame, bars5m: pd.DataFrame, vix: pd.DataFrame | None, horizons
) -> pd.DataFrame:
    """One row per daily bar: ts, day_idx, vol_v2 inputs, vol_v1's HAR inputs (har_d/w/m, for the
    context baseline), `inputs_ok`, and per horizon the target y_h and its last bar's open time."""
    daily = validate_candles(daily).reset_index(drop=True)
    frame = pd.concat(
        [
            pd.DataFrame({"ts": daily["ts"], "day_idx": np.arange(len(daily))}),
            vol2_inputs(daily, bars5m, vix),
            har_regressors(daily),
        ],
        axis=1,
    )
    frame["inputs_ok"] = np.isfinite(frame[list(LOG_COLUMNS)]).all(axis=1)
    for h in horizons:
        frame[f"y_{h}"] = forward_realized_variance(daily, h)
        frame[f"end_ts_{h}"] = daily["ts"].shift(-h)
    return frame


def load_inputs(
    spec: Vol2Spec, load: CandleLoader | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Daily and 5m bars of the underlying and daily VIX, every bar on or after the holdout start
    dropped before anything is computed."""
    out = []
    for instrument, tf in (
        (spec.underlying, DAILY_TF),
        (spec.underlying, INTRADAY_TF),
        (spec.implied, DAILY_TF),
    ):
        df = load_research_candles(instrument, tf, allow_holdout=False, load=load)
        out.append(df[df["ts"] < spec.holdout_start].reset_index(drop=True))
    return out[0], out[1], out[2]


# --- fits (future bars as labels: research only) -----------------------------------------------------


def train_mask(frame: pd.DataFrame, spec: Vol2Spec, h: int) -> np.ndarray:
    """Train-window rows whose target ends before the validation start, with every input."""
    y = frame[f"y_{h}"]
    mask = (
        (frame["ts"] >= spec.train_start)
        & (frame["ts"] < spec.validation_start)
        & (frame[f"end_ts_{h}"] < spec.validation_start)
        & frame["inputs_ok"]
        & y.notna()
        & (y > 0)
    )
    return mask.to_numpy()


def validation_mask(frame: pd.DataFrame, spec: Vol2Spec, h: int) -> np.ndarray:
    """Validation rows with every input and a target that ends before the holdout start."""
    y = frame[f"y_{h}"]
    mask = (
        (frame["ts"] >= spec.validation_start)
        & (frame["ts"] < spec.holdout_start)
        & (frame[f"end_ts_{h}"] < spec.holdout_start)
        & frame["inputs_ok"]
        & y.notna()
        & (y > 0)
    )
    return mask.to_numpy()


def newey_west_cov(X: np.ndarray, resid: np.ndarray, lags: int) -> np.ndarray:
    """HAC (Bartlett) covariance of OLS coefficients."""
    n = len(X)
    scores = X * resid[:, None]
    meat = scores.T @ scores
    for j in range(1, min(lags, n - 1) + 1):
        w = 1.0 - j / (lags + 1.0)
        gamma = scores[j:].T @ scores[:-j]
        meat += w * (gamma + gamma.T)
    bread = np.linalg.inv(X.T @ X)
    return bread @ meat @ bread


@dataclass(frozen=True)
class LogOLS:
    names: tuple[str, ...]
    coef: np.ndarray  # intercept first
    smear: float
    n: int
    r2_log: float
    hac_se: np.ndarray

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        X = frame[list(self.names)].to_numpy(dtype=float)
        return np.exp(self.coef[0] + X @ self.coef[1:]) * self.smear

    def to_dict(self) -> dict:
        names = ("intercept", *self.names)
        return {
            "coef": {k: float(v) for k, v in zip(names, self.coef, strict=True)},
            "hac_se": {k: float(v) for k, v in zip(names, self.hac_se, strict=True)},
            "smearing_factor": self.smear,
            "n_train": self.n,
            "r2_log_train": self.r2_log,
        }


def fit_log_ols(rows: pd.DataFrame, names: tuple[str, ...], h: int) -> LogOLS:
    """OLS of log y_h on [1, names]; HAC standard errors with h lags (the targets overlap by h - 1)."""
    y = np.log(rows[f"y_{h}"].to_numpy(dtype=float))
    X = np.column_stack([np.ones(len(rows)), rows[list(names)].to_numpy(dtype=float)])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    tss = float(((y - y.mean()) ** 2).sum())
    return LogOLS(
        names=names,
        coef=coef,
        smear=float(np.mean(np.exp(resid))),
        n=len(rows),
        r2_log=float(1.0 - (resid**2).sum() / tss) if tss > 0 else float("nan"),
        hac_se=np.sqrt(np.diag(newey_west_cov(X, resid, h))),
    )


def fit_forecasters(frame: pd.DataFrame, spec: Vol2Spec, h: int) -> dict[str, LogOLS]:
    rows = frame[train_mask(frame, spec, h)]
    if len(rows) < MIN_TRAIN_ROWS:
        raise ValueError(f"only {len(rows)} training rows at h={h}")
    return {name: fit_log_ols(rows, names, h) for name, names in REGRESSORS.items()}
