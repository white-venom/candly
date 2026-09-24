"""Cross-sectional panel for xs_v1 and pv2_xs (config/edge_search.yaml `cross_section`, edge_search_v2.yaml
`cross_section_patterns`, PLAN.md §20b-c).

The universe's daily bars become date x stock matrices on one master calendar: the NIFTY 50 trading days
before the holdout start. Nothing on or after the holdout start is loaded, so no feature, label or
schedule can see it.

Causal features, each known at the close of day t from bars up to and including t. Cf is the last traded
close (carried over days without a bar); "sessions" are master-calendar days.
- reversal_1w: log(Cf_t / Cf_{t-5})
- return_1m: log(Cf_t / Cf_{t-21})
- momentum_12_1: log(Cf_{t-21} / Cf_{t-252})
- volatility_60d: annualised sd of the daily log returns of the last 60 sessions (at least 40 of them)
- distance_52w_high: log(Cf_t / highest high of the last 252 sessions (at least 200 bars))
- volume_change: log(mean volume of the last 5 sessions / mean volume of the last 60)
- beta_1y: OLS beta of daily log returns on NIFTY 50's over the last 252 sessions (at least 200 pairs)
- sector_relative_return: return_1m minus the mean return_1m of the eligible stocks of the same industry
  on the same day (industry = today's classification in the universe file)
- turnover: log of the mean traded value (close x volume) of the last 20 sessions (at least 10 bars)
- candlestick_patterns (pv2_xs only): the number of confirmed bullish and bearish patterns
  (candly.patterns.detect_patterns on the stock's own bars; neutral ones ignored) whose last bar is within
  the last 5 and 20 sessions. A pattern is confirmed at the close of its last bar, so it counts from then.
The model sees every feature as its percentile rank among the eligible stocks of the same day.

Eligible at t: a bar on t, close_t >= min_price_inr, and the three baseline signals (reversal_1w,
momentum_12_1, volatility_60d) defined, which needs about a year of history.

Forward returns (labels, future bars, research only): entry at the open of session t+1, exit at the open of
session t+1+h, or at the last traded close before it when the stock has no bar that day.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from candly.core.calendar import IST
from candly.core.settings import get_settings
from candly.patterns import detect_patterns
from candly.research.data import CandleLoader, load_research_candles

TIMEFRAME = "1D"
MARKET_ID = "NSE:NIFTY50"
ANNUAL = 252

REVERSAL_SESSIONS = 5
MONTH_SESSIONS = 21
YEAR_SESSIONS = 252
VOL_SESSIONS, VOL_MIN = 60, 40
HIGH_MIN = 200
VOLUME_SHORT, VOLUME_SHORT_MIN = 5, 3
VOLUME_LONG, VOLUME_LONG_MIN = 60, 40
BETA_MIN = 200
TURNOVER_SESSIONS, TURNOVER_MIN = 20, 10

BASE_FEATURES = (
    "reversal_1w",
    "return_1m",
    "momentum_12_1",
    "volatility_60d",
    "distance_52w_high",
    "volume_change",
    "beta_1y",
    "sector_relative_return",
    "turnover",
)
PATTERN_GROUP = "candlestick_patterns"
PATTERN_WINDOWS = (5, 20)
PATTERN_FEATURES = tuple(f"{d}_patterns_{n}" for n in PATTERN_WINDOWS for d in ("bullish", "bearish"))
# Each baseline ranks stocks by one raw signal times a sign (higher score = more attractive).
BASELINES: dict[str, tuple[str, float]] = {
    "reversal_1w": ("reversal_1w", -1.0),
    "momentum_12_1": ("momentum_12_1", 1.0),
    "low_volatility": ("volatility_60d", -1.0),
}
ELIGIBILITY_SIGNALS = tuple(sorted({col for col, _ in BASELINES.values()}))
REBALANCE_RULES = ("weekly_first_trading_day", "monthly_first_trading_day")
OHLCV = ("open", "high", "low", "close", "volume")


def rank_column(name: str) -> str:
    return f"rank_{name}"


def label_column(h: int) -> str:
    return f"fwd_{h}"


# --- loading -------------------------------------------------------------------------------------------


@dataclass
class Panel:
    """Date x stock matrices on the master calendar (NaN where a stock has no bar)."""

    dates: pd.DatetimeIndex
    ids: list[str]
    industry: pd.Series
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    market_close: pd.Series
    bullish: pd.DataFrame | None
    bearish: pd.DataFrame | None
    missing: list[str]
    n_off_calendar: int

    @property
    def traded(self) -> pd.DataFrame:
        return self.close.notna()


def universe_members(universe_file: str) -> tuple[list[str], dict[str, str]]:
    """Equity ids of a research universe file and each one's industry (from the same file)."""
    from candly.data.ingest import load_universe

    stem = Path(universe_file).stem
    name = stem.removeprefix("universe_")
    ids = [i.id for i in load_universe(name) if i.kind == "equity"]
    raw = yaml.safe_load((get_settings().config_dir / Path(universe_file).name).read_text(encoding="utf-8"))
    industry = {str(i["id"]): str(i.get("industry") or "unknown") for i in raw["instruments"]}
    return ids, {i: industry.get(i, "unknown") for i in ids}


def _load(instrument_id: str, cutoff: pd.Timestamp, load: CandleLoader | None) -> pd.DataFrame:
    df = load_research_candles(instrument_id, TIMEFRAME, allow_holdout=False, load=load)
    return df[df["ts"] < cutoff].reset_index(drop=True)


def _pattern_counts(df: pd.DataFrame, dates: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
    patterns = detect_patterns(df, TIMEFRAME)
    confirmed = patterns[patterns["state"] == "confirmed"]
    out = []
    for direction in ("bullish", "bearish"):
        counts = confirmed.loc[confirmed["direction"] == direction, "ts"].value_counts()
        out.append(counts.reindex(dates, fill_value=0).astype("float64"))
    return out[0], out[1]


def build_panel(
    ids: list[str],
    industry: dict[str, str],
    cutoff: pd.Timestamp,
    *,
    load: CandleLoader | None = None,
    market_id: str = MARKET_ID,
    with_patterns: bool = True,
) -> Panel:
    """Load every stock's closed daily bars before `cutoff` (never later than the research holdout) onto
    the market index's trading days."""
    market = _load(market_id, cutoff, load)
    dates = pd.DatetimeIndex(market["ts"])
    columns: dict[str, dict[str, pd.Series]] = {k: {} for k in OHLCV}
    bullish, bearish = {}, {}
    missing, off = [], 0
    for iid in ids:
        df = _load(iid, cutoff, load)
        if df.empty:
            missing.append(iid)
            continue
        on = df["ts"].isin(dates).to_numpy()
        off += int((~on).sum())
        if with_patterns:
            bullish[iid], bearish[iid] = _pattern_counts(df, dates)
        bars = df[on].set_index("ts")
        for k in OHLCV:
            columns[k][iid] = bars[k].astype("float64")
    present = [i for i in ids if i not in missing]

    def matrix(data: dict[str, pd.Series]) -> pd.DataFrame:
        return pd.DataFrame(data, index=dates, columns=present, dtype="float64")

    return Panel(
        dates=dates,
        ids=present,
        industry=pd.Series({i: industry.get(i, "unknown") for i in present}, dtype="object"),
        **{k: matrix(columns[k]) for k in OHLCV},
        market_close=pd.Series(market["close"].to_numpy(dtype="float64"), index=dates),
        bullish=matrix(bullish) if with_patterns else None,
        bearish=matrix(bearish) if with_patterns else None,
        missing=missing,
        n_off_calendar=off,
    )


# --- causal features -------------------------------------------------------------------------------------


def rolling_beta(r: pd.DataFrame, m: pd.Series, window: int, min_periods: int) -> pd.DataFrame:
    """OLS slope of each column of `r` on `m` over the last `window` rows, pairwise-complete."""
    x = pd.DataFrame(np.broadcast_to(m.to_numpy()[:, None], r.shape), index=r.index, columns=r.columns)
    valid = r.notna() & x.notna()
    x, y = x.where(valid, 0.0), r.where(valid, 0.0)

    def total(d: pd.DataFrame) -> pd.DataFrame:
        return d.rolling(window, min_periods=1).sum()

    n = total(valid.astype("float64"))
    sx, sy = total(x), total(y)
    with np.errstate(divide="ignore", invalid="ignore"):
        var = total(x * x) - sx * sx / n
        cov = total(x * y) - sx * sy / n
        beta = cov / var
    return beta.where((n >= min_periods) & (var > 0))


def _log_positive(frame: pd.DataFrame) -> pd.DataFrame:
    return np.log(frame.where(frame > 0))


def base_feature_matrices(p: Panel) -> dict[str, pd.DataFrame]:
    """Raw causal features except sector_relative_return, which needs the eligible set."""
    log_cf = np.log(p.close.ffill())
    daily = log_cf.diff().where(p.traded)
    market = np.log(p.market_close).diff()
    volume = p.volume.where(p.volume > 0)
    traded_value = p.close * volume
    return {
        "reversal_1w": log_cf - log_cf.shift(REVERSAL_SESSIONS),
        "return_1m": log_cf - log_cf.shift(MONTH_SESSIONS),
        "momentum_12_1": log_cf.shift(MONTH_SESSIONS) - log_cf.shift(YEAR_SESSIONS),
        "volatility_60d": daily.rolling(VOL_SESSIONS, min_periods=VOL_MIN).std() * math.sqrt(ANNUAL),
        "distance_52w_high": log_cf - np.log(p.high.rolling(YEAR_SESSIONS, min_periods=HIGH_MIN).max()),
        "volume_change": _log_positive(
            volume.rolling(VOLUME_SHORT, min_periods=VOLUME_SHORT_MIN).mean()
            / volume.rolling(VOLUME_LONG, min_periods=VOLUME_LONG_MIN).mean()
        ),
        "beta_1y": rolling_beta(daily, market, YEAR_SESSIONS, BETA_MIN),
        "turnover": _log_positive(traded_value.rolling(TURNOVER_SESSIONS, min_periods=TURNOVER_MIN).mean()),
    }


def eligibility(p: Panel, features: dict[str, pd.DataFrame], min_price: float) -> pd.DataFrame:
    ok = p.traded & (p.close >= min_price)
    for name in ELIGIBILITY_SIGNALS:
        ok &= features[name].notna()
    return ok


def sector_relative(ret: pd.DataFrame, eligible: pd.DataFrame, industry: pd.Series) -> pd.DataFrame:
    groups = industry.reindex(ret.columns).to_numpy()
    peer_mean = ret.where(eligible).T.groupby(groups).transform("mean").T
    return ret - peer_mean


def pattern_feature_matrices(p: Panel) -> dict[str, pd.DataFrame]:
    if p.bullish is None or p.bearish is None:
        raise ValueError("the panel was built without pattern counts")
    counts = {"bullish": p.bullish, "bearish": p.bearish}
    return {
        f"{d}_patterns_{n}": counts[d].rolling(n, min_periods=1).sum()
        for n in PATTERN_WINDOWS
        for d in ("bullish", "bearish")
    }


def feature_matrices(p: Panel, min_price: float, with_patterns: bool) -> tuple[dict, pd.DataFrame]:
    features = base_feature_matrices(p)
    eligible = eligibility(p, features, min_price)
    features["sector_relative_return"] = sector_relative(features["return_1m"], eligible, p.industry)
    if with_patterns:
        features.update(pattern_feature_matrices(p))
    return features, eligible


# --- labels (future bars: research only) -----------------------------------------------------------------


def forward_returns(p: Panel, h: int) -> pd.DataFrame:
    """Simple return from the open of session t+1 to the open of session t+1+h (last traded close before
    it when the stock has no bar that day). NaN without an open at t+1 or beyond the loaded bars."""
    open_mark = p.open.where(p.traded, p.close.ffill().shift(1))
    return open_mark.shift(-(1 + h)) / p.open.shift(-1) - 1.0


# --- the long frame -----------------------------------------------------------------------------------------


def panel_frame(p: Panel, min_price: float, horizons, with_patterns: bool) -> pd.DataFrame:
    """One row per eligible (day, stock), sorted by day then stock: t (calendar position), stock (column
    position), ts, id, every raw feature, its daily percentile rank (rank_<name>), and fwd_<h> labels."""
    features, eligible = feature_matrices(p, min_price, with_patterns)
    t, j = np.nonzero(eligible.to_numpy())
    out: dict[str, np.ndarray] = {
        "t": t.astype(np.int64),
        "stock": j.astype(np.int64),
        "ts": p.dates[t],
        "id": np.asarray(p.ids, dtype=object)[j],
    }
    for name, m in features.items():
        out[name] = m.to_numpy(dtype="float64")[t, j]
        ranked = m.where(eligible).rank(axis=1, pct=True)
        out[rank_column(name)] = ranked.to_numpy(dtype="float64")[t, j]
    for h in horizons:
        out[label_column(h)] = forward_returns(p, h).to_numpy(dtype="float64")[t, j]
    return pd.DataFrame(out)


# --- rebalance schedule ------------------------------------------------------------------------------------


def period_keys(dates: pd.DatetimeIndex, rule: str) -> np.ndarray:
    ist = dates.tz_convert(IST)
    if rule == "weekly_first_trading_day":
        iso = ist.isocalendar()
        return iso["year"].to_numpy(dtype=np.int64) * 100 + iso["week"].to_numpy(dtype=np.int64)
    if rule == "monthly_first_trading_day":
        return ist.year.to_numpy(dtype=np.int64) * 100 + ist.month.to_numpy(dtype=np.int64)
    raise ValueError(f"rebalance rule {rule!r} is not implemented; expected one of {REBALANCE_RULES}")


def first_trading_days(dates: pd.DatetimeIndex, rule: str) -> np.ndarray:
    """Calendar positions of the first trading day of each week or month."""
    key = period_keys(dates, rule)
    return np.flatnonzero(np.r_[True, key[1:] != key[:-1]])


def rebalance_periods(
    dates: pd.DatetimeIndex, rule: str, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    """Holding periods: enter at the open of each first trading day in [start, end), exit at the open of the
    next one. The signal day is the session before the entry. A period whose exit isn't a loaded bar (it
    would fall on or after the holdout) is dropped."""
    firsts = first_trading_days(dates, rule)
    firsts = firsts[firsts > 0]
    inside = firsts[(dates[firsts] >= start) & (dates[firsts] < end)]
    if inside.size == 0:
        return pd.DataFrame(columns=["signal", "entry", "exit", "entry_ts", "exit_ts"])
    after = firsts[firsts > inside[-1]]
    exits = np.r_[inside[1:], after[:1]]
    entries = inside[: exits.size]
    return pd.DataFrame(
        {
            "signal": entries - 1,
            "entry": entries,
            "exit": exits,
            "entry_ts": dates[entries],
            "exit_ts": dates[exits],
        }
    )
