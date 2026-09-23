"""Baselines analog_v1 must beat (PLAN.md §9). They never abstain, so the ledger can grade them."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps

from candly.core.instruments import exchange_of
from candly.core.schema import validate_candles
from candly.forecast.models import Band, Candle, Driver, Forecast
from candly.forecast.timing import drop_unclosed, future_bar_times, to_unix
from candly.indicators.functions import atr as atr_fn
from candly.research.config import load_research_config
from candly.research.labels import forward_labels

VOL_LOOKBACK_BARS = 250


def _shell(instrument_id, tf, method, df, now, steps, base) -> dict:
    return {
        "instrument": instrument_id,
        "tf": tf,
        "method": method,
        "made_at": to_unix(now),
        "ref_time": to_unix(df["ts"].iloc[-1]),
        "ref_close": float(df["close"].iloc[-1]),
        "horizon_bars": steps,
        "p_up": None,
        "p_up_ci": None,
        "base_rate": base,
        "abstain": False,
        "abstain_reason": None,
        "confidence": None,
        "expected_move_pct": None,
        "ghost_candles": [],
        "bands": [],
        "invalidation": None,
        "drivers": [],
        "n_analogs": 0,
        "explanation": None,
    }


def baseline_forecasts(
    instrument_id: str,
    tf: str,
    candles: pd.DataFrame,
    steps: int | None = None,
    *,
    now: pd.Timestamp | None = None,
) -> list[Forecast]:
    """base_rate, persistence and random-walk forecasts for the last closed bar."""
    cfg = load_research_config()
    steps = int(steps or cfg.forecast_steps)
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    exchange = exchange_of(instrument_id)
    df = drop_unclosed(validate_candles(candles), exchange, tf, now)
    if len(df) < 2:
        return []
    up = forward_labels(df, [steps])[f"up_{steps}"].dropna()
    base = float(up.mean()) if len(up) else None
    times = [to_unix(t) for t in future_bar_times(exchange, tf, df["ts"].iloc[-1], steps)]
    ref_close = float(df["close"].iloc[-1])
    last = df.iloc[-1]

    base_rate = Forecast(
        **{
            **_shell(instrument_id, tf, "baseline_base_rate", df, now, steps, base),
            "p_up": base,
            "n_analogs": len(up),
            "drivers": [Driver(name="Base rate", effect="neutral", detail=f"{len(up)} past bars")],
        }
    )

    move = float(last["close"] - last["open"])
    persist_p = 1.0 if move > 0 else 0.0 if move < 0 else base
    upper_wick = float(last["high"] - max(last["open"], last["close"]))
    lower_wick = float(min(last["open"], last["close"]) - last["low"])
    ghosts, prev = [], ref_close
    for t in times:
        o, c = prev, prev + move
        ghosts.append(
            Candle(
                time=t, open=o, high=max(o, c) + upper_wick, low=min(o, c) - lower_wick, close=c, volume=0.0
            )
        )
        prev = c
    persistence = Forecast(
        **{
            **_shell(instrument_id, tf, "baseline_persistence", df, now, steps, base),
            "p_up": persist_p,
            "ghost_candles": ghosts,
            "n_analogs": 1,
            "expected_move_pct": 100.0 * (ghosts[-1].close - ref_close) / ref_close if ghosts else None,
            "drivers": [
                Driver(
                    name="Last bar",
                    effect="bullish" if move > 0 else "bearish" if move < 0 else "neutral",
                    detail="the next bars repeat the last bar",
                )
            ],
        }
    )

    log_ret = np.log(df["close"]).diff().dropna().iloc[-VOL_LOOKBACK_BARS:]
    sigma = float(log_ret.std(ddof=1)) if len(log_ret) > 1 else float("nan")
    atr = float(atr_fn(df, 14).iloc[-1])
    half = 0.5 * atr if np.isfinite(atr) else 0.0
    z = sps.norm.ppf(cfg.bands[:3])
    rw_ghosts, rw_bands = [], []
    for s, t in enumerate(times, start=1):
        rw_ghosts.append(
            Candle(
                time=t,
                open=ref_close,
                high=ref_close + half,
                low=ref_close - half,
                close=ref_close,
                volume=0.0,
            )
        )
        if np.isfinite(sigma):
            p10, p50, p90 = (ref_close * float(np.exp(q * sigma * np.sqrt(s))) for q in z)
            rw_bands.append(Band(time=t, p10=p10, p50=p50, p90=p90))
    random_walk = Forecast(
        **{
            **_shell(instrument_id, tf, "baseline_random_walk", df, now, steps, base),
            "p_up": 0.5,
            "ghost_candles": rw_ghosts,
            "bands": rw_bands,
            "n_analogs": len(log_ret),
            "expected_move_pct": 0.0,
            "drivers": [
                Driver(
                    name="Random walk", effect="neutral", detail=f"bands from {len(log_ret)}-bar volatility"
                )
            ],
        }
    )
    return [base_rate, persistence, random_walk]
