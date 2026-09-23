"""Builders shared by the alerts tests (this module holds no tests)."""

from datetime import date, timedelta

import pandas as pd

from candly.core.calendar import IST, get_calendar
from candly.core.instruments import exchange_of
from candly.core.schema import CANDLE_COLUMNS
from candly.data import clock
from candly.forecast import Band, Candle, Forecast, ForecastContext, Trade
from candly.forecast.timing import future_bar_times, to_unix
from candly.ledger import Ledger

TOKEN = "123456789:AAtest-token-SECRET-value"
CHAT_ID = "4242"
SEND_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


def ist(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz=IST).tz_convert("UTC")


def call(
    instrument: str = "NSE:RELIANCE",
    tf: str = "1D",
    ref: str = "2026-09-23 09:15",
    *,
    bullish: bool = True,
    entry: float = 2950.40,
    stop: float | None = None,
    target: float | None = None,
    base: float = 0.53,
    confidence: str | None = "medium",
    abstain: bool = False,
    method: str = "analog_v1",
    steps: int = 3,
) -> Forecast:
    """An analog_v1-shaped forecast on the reference bar that opens at `ref` (IST)."""
    exchange = exchange_of(instrument)
    ref_ts = ist(ref)
    if stop is None:
        stop = 2901.10 if (bullish and entry == 2950.40) else entry * (0.983 if bullish else 1.017)
    if target is None:
        target = 3012.00 if (bullish and entry == 2950.40) else entry * (1.021 if bullish else 0.979)
    p_up = 0.62 if bullish else 0.41
    times = future_bar_times(exchange, tf, ref_ts, steps)
    ghosts = [
        Candle(
            time=to_unix(t), open=entry, high=max(entry, target) + 5, low=min(entry, target) - 5,
            close=target, volume=0.0,
        )
        for t in times
    ]
    bands = [Band(time=g.time, p10=target - 30, p50=target, p90=target + 30) for g in ghosts]
    made = get_calendar().bar_close_time(exchange, ref_ts, tf) + pd.Timedelta(minutes=1)
    reward_risk = (1.0 if bullish else -1.0) * (target - entry) / abs(entry - stop)
    trade = Trade(entry=entry, stop=stop, target=target, reward_risk=reward_risk)
    return Forecast(
        instrument=instrument,
        tf=tf,
        method=method,
        made_at=to_unix(made),
        ref_time=to_unix(ref_ts),
        ref_close=entry,
        horizon_bars=steps,
        p_up=p_up,
        p_up_ci=(p_up - 0.05, p_up + 0.05),
        base_rate=base,
        abstain=abstain,
        abstain_reason="edge below minimum: 0.010 < 0.03" if abstain else None,
        confidence=None if abstain else confidence,
        expected_move_pct=100.0 * (target / entry - 1.0),
        ghost_candles=ghosts,
        bands=bands,
        invalidation=None if abstain else stop,
        trade=None if abstain else trade,
        drivers=[],
        n_analogs=40,
        context=ForecastContext(atr=20.0),
    )


def record(monkeypatch, fc: Forecast) -> int:
    """Write to the default ledger with the real clock frozen just after the reference bar closed."""
    monkeypatch.setattr(clock, "utc_now", lambda: pd.Timestamp(fc.made_at, unit="s", tz="UTC"))
    return Ledger().record(fc)


def bars(
    instrument: str, tf: str, first_day: date, last_day: date, *, start: float, step: float
) -> pd.DataFrame:
    """A candle series on the exchange's bar grid that moves `step` per bar."""
    cal = get_calendar()
    exchange = exchange_of(instrument)
    opens, day = [], first_day
    while day <= last_day:
        opens += cal.expected_bar_opens(exchange, day, tf)
        day += timedelta(days=1)
    rows, price = [], start
    for ts in opens:
        o, c = price, price + step
        rows.append(
            {"ts": ts, "open": o, "high": max(o, c) + 0.5, "low": min(o, c) - 0.5, "close": c,
             "volume": 1000.0, "oi": float("nan")}
        )
        price = c
    return pd.DataFrame(rows, columns=CANDLE_COLUMNS)
