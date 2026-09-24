"""Causal daily features for regime_v1 (config/pivot.yaml regime_model.feature_groups).

Every value at bar t uses bars up to and including t only: the instrument's own OHLC, and the market
index (NIFTY 50) and India VIX rows whose bar opened on or before t (as-of join). All three are NSE daily
bars that close together at 15:30 IST, so a forecast made after bar t's close sees all of them.
Price distances are in ATR(14) units so instruments of different price and volatility pool together.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from candly.indicators.functions import adx, atr, ema

RETURN_WINDOWS = (5, 20, 60, 120, 250)
VOL_WINDOW = 60
FAST_VOL_WINDOW = 20
YEAR_BARS = 250
ATR_PERIOD = 14
ASOF_TOLERANCE = pd.Timedelta(days=5)  # market / VIX rows older than this are treated as missing
MARKET_SOURCE_COLUMNS = (
    "ret_5", "ret_20", "ret_60", "close_ema200_atr", "ema50_ema200_atr", "dist_high_250_atr",
)

FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "momentum": (
        *(f"ret_{n}" for n in RETURN_WINDOWS),
        *(f"ret_{n}_vol" for n in RETURN_WINDOWS),
        "ret_250_skip20",
    ),
    "trend_structure": (
        "close_ema20_atr",
        "close_ema50_atr",
        "close_ema200_atr",
        "ema20_ema50_atr",
        "ema50_ema200_atr",
        "ema20_slope_atr",
        "ema50_slope_atr",
        "ema200_slope_atr",
        "adx14",
        "di_diff14",
    ),
    "volatility_regime": ("natr14", "natr14_pctile_250", "vol_20", "vol_ratio_20_60"),
    "distance_from_extremes": ("dist_high_250_atr", "dist_low_250_atr", "range_pos_250"),
    "market_trend": (*(f"mkt_{c}" for c in MARKET_SOURCE_COLUMNS), "rel_ret_60"),
    "vix": ("vix", "vix_chg_20"),
}
FEATURE_COLUMNS: tuple[str, ...] = tuple(c for cols in FEATURE_GROUPS.values() for c in cols)
OWN_COLUMNS: tuple[str, ...] = tuple(
    c for g in ("momentum", "trend_structure", "volatility_regime", "distance_from_extremes")
    for c in FEATURE_GROUPS[g]
)
MARKET_COLUMNS = FEATURE_GROUPS["market_trend"]
VIX_COLUMNS = FEATURE_GROUPS["vix"]
# VIX starts in 2008, so older training rows go without it; everything else must be present.
REQUIRED_COLUMNS: tuple[str, ...] = (*OWN_COLUMNS, *MARKET_COLUMNS)

# (ema span, slope lookback in bars)
_EMAS = ((20, 5), (50, 10), (200, 20))


def own_features(df: pd.DataFrame) -> pd.DataFrame:
    """Momentum, trend structure, volatility regime and distance from 52-week extremes, row-aligned."""
    close, high, low = df["close"], df["high"], df["low"]
    log_close = np.log(close)
    daily = log_close.diff()
    vol_slow = daily.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std()
    vol_fast = daily.rolling(FAST_VOL_WINDOW, min_periods=FAST_VOL_WINDOW).std()
    atr_ = atr(df, ATR_PERIOD)
    per_atr = atr_.where(atr_ > 0)

    out: dict[str, pd.Series] = {}
    for n in RETURN_WINDOWS:
        out[f"ret_{n}"] = log_close.diff(n)
    for n in RETURN_WINDOWS:
        out[f"ret_{n}_vol"] = out[f"ret_{n}"] / (vol_slow * np.sqrt(n))
    out["ret_250_skip20"] = log_close.shift(20) - log_close.shift(YEAR_BARS)

    emas = {span: ema(close, span) for span, _ in _EMAS}
    for span, _ in _EMAS:
        out[f"close_ema{span}_atr"] = (close - emas[span]) / per_atr
    out["ema20_ema50_atr"] = (emas[20] - emas[50]) / per_atr
    out["ema50_ema200_atr"] = (emas[50] - emas[200]) / per_atr
    for span, lookback in _EMAS:
        out[f"ema{span}_slope_atr"] = emas[span].diff(lookback) / per_atr
    dmi = adx(df, ATR_PERIOD)
    out["adx14"] = dmi["adx14"]
    out["di_diff14"] = dmi["plus_di14"] - dmi["minus_di14"]

    natr = atr_ / close
    out["natr14"] = natr
    out["natr14_pctile_250"] = natr.rolling(YEAR_BARS, min_periods=YEAR_BARS).rank(pct=True)
    out["vol_20"] = vol_fast
    out["vol_ratio_20_60"] = vol_fast / vol_slow.where(vol_slow > 0)

    year_high = high.rolling(YEAR_BARS, min_periods=YEAR_BARS).max()
    year_low = low.rolling(YEAR_BARS, min_periods=YEAR_BARS).min()
    span = year_high - year_low
    out["dist_high_250_atr"] = (close - year_high) / per_atr
    out["dist_low_250_atr"] = (close - year_low) / per_atr
    out["range_pos_250"] = (close - year_low) / span.where(span > 0)
    return pd.DataFrame(out, index=df.index).astype("float64")


def _asof(ts: pd.Series, right: pd.DataFrame) -> pd.DataFrame:
    """Rows of `right` (a `ts` column plus values) as of each time in `ts`: the latest row at or before
    it, no older than ASOF_TOLERANCE."""
    left = pd.DataFrame({"ts": ts.reset_index(drop=True)})
    joined = pd.merge_asof(left, right, on="ts", direction="backward", tolerance=ASOF_TOLERANCE)
    return joined.drop(columns="ts")


def market_features(market: pd.DataFrame) -> pd.DataFrame:
    """`ts` plus the market index's own trend features, prefixed mkt_."""
    own = own_features(market)[list(MARKET_SOURCE_COLUMNS)].add_prefix("mkt_")
    return pd.concat([market[["ts"]], own], axis=1)


def vix_features(vix: pd.DataFrame) -> pd.DataFrame:
    close = vix["close"]
    return pd.DataFrame({"ts": vix["ts"], "vix": close, "vix_chg_20": np.log(close).diff(20)})


def _missing(df: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(np.nan, index=df.index, columns=list(columns))


def regime_features(
    df: pd.DataFrame, market: pd.DataFrame | None = None, vix: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Every FEATURE_COLUMNS column, row-aligned with `df` (candles sorted by ts). Market and VIX
    columns are NaN when their frame is missing or has no row within ASOF_TOLERANCE."""
    own = own_features(df)
    if market is not None and len(market):
        mkt = _asof(df["ts"], market_features(market)).set_axis(df.index)
    else:
        mkt = _missing(df, tuple(f"mkt_{c}" for c in MARKET_SOURCE_COLUMNS))
    mkt["rel_ret_60"] = own["ret_60"] - mkt["mkt_ret_60"]
    if vix is not None and len(vix):
        vx = _asof(df["ts"], vix_features(vix)).set_axis(df.index)
    else:
        vx = _missing(df, VIX_COLUMNS)
    return pd.concat([own, mkt, vx], axis=1)[list(FEATURE_COLUMNS)].astype("float64")
