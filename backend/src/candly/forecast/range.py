"""range_v1 live forecasts: the expected next candles from the saved range model (research.range_model).

Ghost candles follow the p50 high, low and close of each step (step 1 opens at the reference close,
later steps at the previous ghost close); the bands are the close's p10/p50/p90. Direction comes only
from regime_v1, and only for 1D: when candly.research.regime.predict_regime returns a validated call with
a direction for the same reference bar, its p_up, base rate, confidence and horizon are used. Otherwise
the forecast abstains on direction ("direction unclear") and still carries its ghost candles and bands.

Model outputs are cached per (instrument, tf, reference bar, model version), so a live poll only builds
features and predicts once per closed bar. Features are computed on a short tail of the series: the last
TAIL_BARS bars (ATR, ADX and EMA50 forget their start well within it), and intraday at least the last
TAIL_SESSIONS sessions (relative volume compares each time slot with its previous 20 sessions). The
context kept with the forecast comes from the same tail, so intraday its volatility regime (which ranks
ATR against half a year of sessions) is left empty.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from candly.core.calendar import IST, get_calendar
from candly.core.instruments import exchange_of
from candly.core.schema import validate_candles
from candly.core.timeframes import is_intraday
from candly.features.context import compute_context
from candly.features.expiry import expiry_on
from candly.forecast.models import Band, Candle, Driver, Forecast, ForecastContext
from candly.forecast.timing import (
    drop_unclosed,
    future_bar_times,
    last_expected_closed_bar,
    stale_grace,
    to_unix,
)
from candly.patterns import detect_patterns, load_pattern_config
from candly.research.config import load_research_config

log = logging.getLogger(__name__)

METHOD = "range_v1"
DIRECTION_UNCLEAR = "direction unclear: range forecast only"
TAIL_BARS = 400
# relative volume needs each time slot's previous 20 sessions; 25 leaves room for slots a session skips
# (MCX's morning session is shut on NSE holidays)
TAIL_SESSIONS = 25
_TAIL_SCAN = 20_000  # bars searched for the intraday session boundary (25 MCX 5m sessions ~ 4.4k bars)
CACHE_SIZE = 512

Loader = Callable[..., pd.DataFrame | None]


class RangeUnavailable(LookupError):
    """No saved range_v1 model for this exchange and timeframe, or the instrument is outside it."""


@dataclass(frozen=True)
class _Prediction:
    q: np.ndarray  # (steps, 3 targets, 3 quantiles), ATR units from the reference close
    ghost: np.ndarray  # (steps, 4) open, high, low, close in ATR units
    atr: float
    atr_pct: float | None
    rv20_atr: float | None
    days_to_expiry: float | None
    context: ForecastContext


_cache: OrderedDict[tuple, _Prediction | None] = OrderedDict()


def clear_cache() -> None:
    _cache.clear()


def _default_loader() -> Loader:
    from candly.data.store import load_candles

    return load_candles


def _tail(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """The last TAIL_BARS bars, and intraday at least everything since the open of the TAIL_SESSIONS-th
    last session."""
    n = len(df)
    start = max(0, n - TAIL_BARS)
    if is_intraday(tf) and n:
        offset = max(0, n - _TAIL_SCAN)
        days = df["ts"].iloc[offset:].dt.tz_convert(IST).dt.date.to_numpy()
        unique = pd.unique(days)
        first = (
            offset
            if len(unique) <= TAIL_SESSIONS
            else offset + int(np.argmax(days == unique[-TAIL_SESSIONS]))
        )
        start = min(start, first)
    return df.iloc[start:].reset_index(drop=True)


def _context_series(load: Loader, exchange: str, tf: str, ref_ts: pd.Timestamp):
    from candly.research.range_features import load_context

    def get(instrument_id: str, frame_tf: str) -> pd.DataFrame | None:
        try:
            df = load(instrument_id, frame_tf)
        except Exception:
            log.warning("range_v1: no %s %s context series", instrument_id, frame_tf, exc_info=True)
            return None
        if df is None or df.empty:
            return None
        df = df[df["ts"] <= ref_ts]
        return _tail(df, frame_tf) if len(df) else None

    return load_context(exchange, tf, get)


def _finite(x) -> float | None:
    return float(x) if x is not None and np.isfinite(x) else None


def _predict(model, instrument_id: str, tf: str, df: pd.DataFrame, load: Loader) -> _Prediction | None:
    from candly.research.range_features import range_features
    from candly.research.range_model import ghost_ohlc

    exchange = exchange_of(instrument_id)
    tail = _tail(df, tf)
    ref_ts = tail["ts"].iloc[-1]
    feats = range_features(tail, tf, instrument_id, _context_series(load, exchange, tf, ref_ts)).iloc[-1]
    atr = feats["atr"]
    if not (np.isfinite(atr) and atr > 0):
        return None
    X = np.empty((1, len(model.feature_names) + 1), dtype=np.float32)
    X[0, :-1] = feats[model.feature_names].to_numpy(dtype=np.float32)
    X[0, -1] = model.instruments.index(instrument_id)
    q = model.predict(X)
    ctx = compute_context(tail, tf, exchange, instrument_id=instrument_id).iloc[-1]
    found = detect_patterns(tail, tf)
    context = ForecastContext(
        patterns=list(found.loc[found["ts"] == ref_ts, "pattern"]),
        trend=ctx["trend"] if isinstance(ctx["trend"], str) else None,
        vol_regime=ctx["vol_regime"] if isinstance(ctx["vol_regime"], str) else None,
        session_phase=ctx["session_phase"] if isinstance(ctx["session_phase"], str) else None,
        rel_volume=_finite(ctx["rel_volume"]),
        atr=float(atr),
    )
    return _Prediction(
        q=q[0],
        ghost=ghost_ohlc(q)[0],
        atr=float(atr),
        atr_pct=_finite(feats["atr_pct"]),
        rv20_atr=_finite(feats["rv20_atr"]),
        days_to_expiry=_finite(feats["days_to_expiry"]),
        context=context,
    )


def _cached_prediction(
    model, instrument_id: str, tf: str, df: pd.DataFrame, load: Loader
) -> _Prediction | None:
    last = df.iloc[-1]
    key = (
        instrument_id,
        tf,
        int(last["ts"].value),
        tuple(float(last[c]) for c in ("open", "high", "low", "close")),
        model.version,
    )
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    prediction = _predict(model, instrument_id, tf, df, load)
    _cache[key] = prediction
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return prediction


def _regime_call(instrument_id: str, candles: pd.DataFrame, now: pd.Timestamp, load: Loader):
    """regime_v1's call for the last daily bar, or None when the module or a call is unavailable."""
    try:
        from candly.research import regime
    except Exception:
        return None
    predict = getattr(regime, "predict_regime", None)
    if predict is None:
        return None
    try:
        return predict(instrument_id, candles, now, load=load)
    except Exception:
        log.warning("regime_v1 failed for %s", instrument_id, exc_info=True)
        return None


def _stop(ref_close: float, atr: float, bullish: bool) -> float:
    """research.yaml abstain.fallback_stop_atr beyond the reference close, never closer than
    patterns.yaml min_stop_atr."""
    away = max(load_research_config().fallback_stop_atr, float(load_pattern_config()["min_stop_atr"])) * atr
    return ref_close - away if bullish else ref_close + away


def _price(x: float) -> str:
    return f"{x:,.2f}"


def covers(instrument_id: str, tf: str) -> bool:
    """Whether a saved range_v1 model forecasts this instrument and timeframe."""
    from candly.research.range_model import load_model

    model = load_model(exchange_of(instrument_id), tf)
    return model is not None and instrument_id in model.instruments


def make_range_forecast(
    instrument_id: str,
    tf: str,
    candles: pd.DataFrame,
    now: pd.Timestamp | None = None,
    *,
    steps: int | None = None,
    load: Loader | None = None,
    check_stale: bool = True,
    model=None,
) -> Forecast:
    """range_v1 forecast from the last bar closed by `now`. Raises RangeUnavailable without a saved model
    for the instrument (the API then falls back to analog_v1) and ValueError without closed candles.
    `load(instrument_id, tf)` supplies the context series (market index, India VIX)."""
    from candly.research.range_features import feature_columns
    from candly.research.range_model import load_model

    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    exchange = exchange_of(instrument_id)
    model = model or load_model(exchange, tf)
    if model is None:
        raise RangeUnavailable(f"no {METHOD} model for {exchange} {tf} yet; run candly.research.range_model")
    if instrument_id not in model.instruments:
        raise RangeUnavailable(f"{instrument_id} is not in the {METHOD} {exchange} {tf} model")
    if not set(model.feature_names) <= set(feature_columns()):
        raise RangeUnavailable(f"the saved {METHOD} {exchange} {tf} model uses other features; retrain it")
    df = drop_unclosed(validate_candles(candles), exchange, tf, now)
    if df.empty:
        raise ValueError(f"no closed candles for {instrument_id} {tf}")
    load = load or _default_loader()
    steps = model.steps if steps is None else max(1, min(int(steps), model.steps))
    ref_ts, ref_close = df["ts"].iloc[-1], float(df["close"].iloc[-1])
    base = {
        "instrument": instrument_id,
        "tf": tf,
        "method": METHOD,
        "made_at": to_unix(now),
        "ref_time": to_unix(ref_ts),
        "ref_close": ref_close,
        "horizon_bars": steps,
        "p_up": None,
        "p_up_ci": None,
        "base_rate": None,
        "abstain": True,
        "abstain_reason": None,
        "confidence": None,
        "expected_move_pct": None,
        "ghost_candles": [],
        "bands": [],
        "invalidation": None,
        "trade": None,
        "drivers": [],
        "n_analogs": 0,
        "explanation": None,
    }
    expected = last_expected_closed_bar(exchange, tf, now - stale_grace(tf)) if check_stale else None
    if expected is not None and ref_ts < expected:
        reason = f"stale data: last closed bar {ref_ts.isoformat()}, expected {expected.isoformat()}"
        return Forecast(**{**base, "abstain_reason": reason})
    pred = _cached_prediction(model, instrument_id, tf, df, load)
    if pred is None:
        return Forecast(**{**base, "abstain_reason": f"not enough history: {len(df)} bars, need ATR(14)"})

    times = future_bar_times(exchange, tf, ref_ts, steps)
    ghosts, bands = [], []
    for s, t in enumerate(times):
        o, h, lo, c = (ref_close + float(v) * pred.atr for v in pred.ghost[s])
        ghosts.append(Candle(time=to_unix(t), open=o, high=h, low=lo, close=c, volume=0.0))
        p10, p50, p90 = (ref_close + float(v) * pred.atr for v in pred.q[s, 2])
        bands.append(Band(time=to_unix(t), p10=p10, p50=p50, p90=p90))
    fields = {
        **base,
        "ghost_candles": ghosts,
        "bands": bands,
        "expected_move_pct": 100.0 * (ghosts[-1].close - ref_close) / ref_close if ghosts else None,
        "context": pred.context,
        "abstain_reason": DIRECTION_UNCLEAR,
    }
    coverage = round(100 * (model.spec_quantiles[-1] - model.spec_quantiles[0]))
    drivers = _drivers(tf, ghosts, bands, pred, instrument_id, ref_ts, coverage)
    call = _regime_call(instrument_id, df, now, load) if tf == "1D" else None
    if call is not None and call.ref_time != to_unix(ref_ts):
        call = None
    if call is not None and call.validated and call.direction is not None:
        return Forecast(
            **{
                **fields,
                "horizon_bars": int(call.horizon_days),
                "p_up": float(call.p_up),
                "base_rate": float(call.base_rate),
                "confidence": call.confidence,
                "abstain": False,
                "abstain_reason": None,
                "invalidation": _stop(ref_close, pred.atr, call.direction == "up"),
                "drivers": [_regime_driver(call), *drivers],
            }
        )
    unclear = Driver(
        name="Direction",
        effect="neutral",
        detail="Unclear: no validated direction call for this bar; this is a range forecast only.",
    )
    drivers.append(unclear)
    if call is not None:
        drivers.append(_regime_driver(call))
    return Forecast(**{**fields, "drivers": drivers})


def _regime_driver(call) -> Driver:
    """A validated call shows its probability; an unvalidated model's probability is never shown."""
    name = f"Regime ({call.horizon_days} sessions)"
    if not call.validated:
        return Driver(name=name, effect="neutral", detail=f"regime_v1 makes no call: {call.reason}.")
    effect = {"up": "bullish", "down": "bearish"}.get(call.direction or "", "neutral")
    return Driver(
        name=name,
        effect=effect,
        detail=(
            f"regime_v1: p(up) {100 * call.p_up:.1f}% vs base rate {100 * call.base_rate:.1f}%, "
            f"{call.confidence} confidence; {call.reason}"
        ),
    )


def _drivers(
    tf: str,
    ghosts: list[Candle],
    bands: list[Band],
    pred: _Prediction,
    instrument_id: str,
    ref_ts: pd.Timestamp,
    coverage: int,
) -> list[Driver]:
    out = []
    if ghosts:
        first, band = ghosts[0], bands[0]
        unit = "session" if tf == "1D" else f"{tf} bar"
        out.append(
            Driver(
                name="Expected range",
                effect="neutral",
                detail=(
                    f"Next {unit}: close {_price(band.p10)} to {_price(band.p90)} in {coverage}% of cases "
                    f"(median {_price(band.p50)}); expected high {_price(first.high)}, "
                    f"low {_price(first.low)}."
                ),
            )
        )
    if pred.atr_pct is not None:
        rv = f"; 20-bar realised volatility {pred.rv20_atr:.2f} x ATR" if pred.rv20_atr is not None else ""
        out.append(
            Driver(name="Volatility", effect="neutral", detail=f"ATR(14) {pred.atr_pct:.2f}% of price{rv}")
        )
    if pred.days_to_expiry is not None and pred.days_to_expiry <= 1:
        info = expiry_on(instrument_id, get_calendar().local_date(ref_ts))
        if info is not None:
            when = "on" if pred.days_to_expiry == 0 else "1 trading day before"
            out.append(
                Driver(
                    name="Expiry",
                    effect="neutral",
                    detail=(
                        f"The reference bar is {when} the {info.kind} expiry "
                        f"({info.next_expiry.isoformat()})."
                    ),
                )
            )
    return out
