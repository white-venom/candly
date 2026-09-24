"""range_v1 features, by the groups pre-registered in config/pivot.yaml range_model.feature_groups.

Causal: the value at bar t uses that instrument's bars up to and including t, and context bars (the
exchange's market index, India VIX) that opened at or before t, so they closed by the time t closed.
Prices are scaled by ATR(14) at t, so one model can pool instruments with different price levels.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from candly.core.calendar import IST, get_calendar
from candly.core.instruments import exchange_of
from candly.core.timeframes import is_intraday, tf_delta
from candly.features.context import has_meaningful_volume
from candly.features.expiry import expiry_features
from candly.indicators import functions as f

ATR_PERIOD = 14
RV_WINDOWS = (5, 20, 60)
RECENT_BARS = 5
VIX_CHANGE_SESSIONS = 5
MARKET_INDEX = {"NSE": "NSE:NIFTY50", "BSE": "BSE:SENSEX"}
VIX_ID = "NSE:INDIAVIX"
VIX_EXCHANGES = ("NSE",)
# a context bar older than this counts as missing (intraday: no borrowing from the previous session)
CONTEXT_TOLERANCE = {"1D": pd.Timedelta(days=7), "intraday": pd.Timedelta(hours=2)}

FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "volatility": ("atr_pct", *(f"rv{n}_atr" for n in RV_WINDOWS)),
    "recent_ranges": (
        *(f"range{k}_atr" for k in range(1, RECENT_BARS + 1)),
        *(f"body{k}_atr" for k in range(1, RECENT_BARS + 1)),
        "gap_atr",
    ),
    "last_candle_shape": ("upper_wick_atr", "lower_wick_atr", "close_pos"),
    "volume": ("rel_volume",),
    "time_of_day": ("minutes_from_open", "minutes_to_close"),
    "calendar": ("day_of_week",),
    "expiry": ("expiry_day", "days_to_expiry"),
    "trend": ("adx14", "ema20_dist_atr", "ema50_dist_atr"),
    "market": ("mkt_atr_pct", "mkt_range_atr", "vix", "vix_chg_5d"),
}


def feature_columns(groups=None) -> list[str]:
    groups = FEATURE_GROUPS if groups is None else groups
    return [c for g in groups for c in FEATURE_GROUPS[g]]


@dataclass
class RangeContext:
    """Context series for one exchange and timeframe: the market index's candles (same timeframe), and
    India VIX in the same timeframe plus its daily bars (for the 5-session change). None = not used."""

    market: pd.DataFrame | None = None
    vix: pd.DataFrame | None = None
    vix_daily: pd.DataFrame | None = None


def market_index(exchange: str) -> str | None:
    return MARKET_INDEX.get(exchange)


def load_context(exchange: str, tf: str, load: Callable[[str, str], pd.DataFrame | None]) -> RangeContext:
    """Context candles through `load(instrument_id, tf)`; a series that is missing stays None."""

    def get(instrument_id: str | None, frame_tf: str) -> pd.DataFrame | None:
        if instrument_id is None:
            return None
        df = load(instrument_id, frame_tf)
        return df if df is not None and len(df) else None

    with_vix = exchange in VIX_EXCHANGES
    return RangeContext(
        market=get(market_index(exchange), tf),
        vix=get(VIX_ID, tf) if with_vix else None,
        vix_daily=get(VIX_ID, "1D") if with_vix else None,
    )


def _session_clock(ts: pd.Series, exchange: str, tf: str) -> tuple[pd.Series, pd.Series]:
    """Minutes from the session open to the bar's open, and from the bar's close to the session close."""
    cal = get_calendar()
    day = ts.dt.tz_convert(IST).dt.date
    bounds = {d: cal.session_times(exchange, d) for d in pd.unique(day)}
    opens = pd.to_datetime(day.map(lambda d: bounds[d][0]), utc=True)
    closes = pd.to_datetime(day.map(lambda d: bounds[d][1]), utc=True)
    end = ts + tf_delta(tf)
    bar_close = end.where(end < closes, closes)
    minute = pd.Timedelta(minutes=1)
    return (ts - opens) / minute, (closes - bar_close) / minute


def _session_gap(df: pd.DataFrame, intraday: bool) -> pd.Series:
    """Open minus the previous close; intraday, the session's opening gap, carried through the session."""
    gap = df["open"] - df["close"].shift(1)
    if not intraday:
        return gap
    day = df["ts"].dt.tz_convert(IST).dt.date
    first = day.ne(day.shift(1))
    return gap.where(first).groupby(day.to_numpy()).transform("first")


def _asof(ts: pd.Series, frame: pd.DataFrame, tf: str) -> pd.DataFrame:
    """`frame` (a ts column plus values) as of each time in `ts`: the last row that opened at or before it."""
    tolerance = CONTEXT_TOLERANCE["intraday" if is_intraday(tf) else "1D"]
    left = pd.DataFrame({"ts": ts.reset_index(drop=True)})
    right = frame.sort_values("ts").reset_index(drop=True)
    right["ts"] = right["ts"].astype(left["ts"].dtype)
    out = pd.merge_asof(left, right, on="ts", direction="backward", tolerance=tolerance)
    return out.drop(columns="ts").set_index(ts.index)


def _ist_days(ts: pd.Series) -> np.ndarray:
    return ts.dt.tz_convert(IST).dt.tz_localize(None).to_numpy().astype("datetime64[D]")


def _market_features(ts: pd.Series, market: pd.DataFrame | None, tf: str) -> pd.DataFrame:
    if market is None or market.empty:
        return pd.DataFrame({"mkt_atr_pct": np.nan, "mkt_range_atr": np.nan}, index=ts.index)
    atr = f.atr(market, ATR_PERIOD)
    values = pd.DataFrame(
        {
            "ts": market["ts"],
            "mkt_atr_pct": 100.0 * atr / market["close"],
            "mkt_range_atr": (market["high"] - market["low"]) / atr,
        }
    )
    return _asof(ts, values, tf)


def _vix_features(ts: pd.Series, ctx: RangeContext, tf: str) -> pd.DataFrame:
    level = pd.Series(np.nan, index=ts.index)
    change = pd.Series(np.nan, index=ts.index)
    if ctx.vix is not None and not ctx.vix.empty:
        level = _asof(ts, ctx.vix[["ts", "close"]], tf)["close"]
    daily = ctx.vix_daily
    if daily is not None and not daily.empty and level.notna().any():
        days, bar_days = _ist_days(daily["ts"]), _ist_days(ts)
        # the daily bar VIX_CHANGE_SESSIONS sessions before the bar's own session (closed days ago)
        pos = np.searchsorted(days, bar_days, side="left") - VIX_CHANGE_SESSIONS
        closes = daily["close"].to_numpy(dtype=float)
        past = np.where(pos >= 0, closes[np.clip(pos, 0, None)], np.nan)
        change = pd.Series(np.log(level.to_numpy(dtype=float) / past), index=ts.index)
    return pd.DataFrame({"vix": level, "vix_chg_5d": change}, index=ts.index)


def range_features(
    df: pd.DataFrame, tf: str, instrument_id: str, context: RangeContext | None = None
) -> pd.DataFrame:
    """Every feature column of FEATURE_GROUPS plus `atr` (ATR(14) in price units, the scale of the
    targets), on `df`'s index."""
    context = context or RangeContext()
    exchange = exchange_of(instrument_id)
    intraday = is_intraday(tf)
    o, h, lo, c = (df[k].astype("float64") for k in ("open", "high", "low", "close"))
    atr = f.atr(df, ATR_PERIOD)
    atr_frac = atr / c
    log_ret = np.log(c).diff()
    bar_range, body = h - lo, c - o
    out: dict[str, pd.Series] = {"atr": atr, "atr_pct": 100.0 * atr_frac}
    for n in RV_WINDOWS:
        out[f"rv{n}_atr"] = log_ret.rolling(n, min_periods=n).std() / atr_frac
    for k in range(1, RECENT_BARS + 1):
        out[f"range{k}_atr"] = bar_range.shift(k - 1) / atr
    for k in range(1, RECENT_BARS + 1):
        out[f"body{k}_atr"] = body.shift(k - 1) / atr
    out["gap_atr"] = _session_gap(df, intraday) / atr
    out["upper_wick_atr"] = (h - np.maximum(o, c)) / atr
    out["lower_wick_atr"] = (np.minimum(o, c) - lo) / atr
    out["close_pos"] = ((c - lo) / bar_range).where(bar_range > 0, 0.5)
    rel_volume = f.rel_volume(df, intraday)
    out["rel_volume"] = rel_volume if has_meaningful_volume(instrument_id) else rel_volume * np.nan
    if intraday and len(df):
        out["minutes_from_open"], out["minutes_to_close"] = _session_clock(df["ts"], exchange, tf)
    else:
        out["minutes_from_open"] = out["minutes_to_close"] = pd.Series(np.nan, index=df.index)
    out["day_of_week"] = df["ts"].dt.tz_convert(IST).dt.weekday.astype("float64")
    expiry = expiry_features(instrument_id, df["ts"])
    out["expiry_day"] = expiry["expiry_day"].map({True: 1.0, False: 0.0}).astype("float64")
    out["days_to_expiry"] = expiry["days_to_expiry"]
    out["adx14"] = f.adx(df, ATR_PERIOD)["adx14"]
    out["ema20_dist_atr"] = (c - f.ema(c, 20)) / atr
    out["ema50_dist_atr"] = (c - f.ema(c, 50)) / atr
    frame = pd.DataFrame(out, index=df.index)
    frame = pd.concat(
        [
            frame,
            _market_features(df["ts"], context.market, tf),
            _vix_features(df["ts"], context, tf),
        ],
        axis=1,
    )
    frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame[["atr", *feature_columns()]].astype("float64")
