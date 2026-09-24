"""Scheduled quant jobs. The lead wires these into the scheduler; they are the only ledger writers."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

import pandas as pd

from candly.core.instruments import exchange_of, load_watchlist
from candly.core.timeframes import validate_tf
from candly.data import clock
from candly.forecast.analog import METHOD as ANALOG_METHOD
from candly.forecast.analog import make_forecast
from candly.forecast.baselines import baseline_forecasts
from candly.forecast.latest import remember
from candly.forecast.models import Forecast
from candly.forecast.range import METHOD as RANGE_METHOD
from candly.forecast.range import RangeUnavailable, make_range_forecast
from candly.ledger import DuplicateForecast, LateForecast, Ledger
from candly.research.scorecard import Scorecard, build_scorecard, load_scorecard

log = logging.getLogger(__name__)

# Abstentions that mean "no forecast was possible" rather than "no edge"; they are not recorded.
NOT_RECORDED = ("stale data", "not enough history")
ANALOG_TFS = ("1D",)

Loader = Callable[..., pd.DataFrame]


def _not_recorded(forecast: Forecast) -> bool:
    return forecast.abstain and (forecast.abstain_reason or "").startswith(NOT_RECORDED)


def _default_loader() -> Loader:
    from candly.data.store import load_candles

    return load_candles


def between(df: pd.DataFrame | None, start=None, end=None) -> pd.DataFrame | None:
    """The store's start <= ts <= end cut."""
    if df is None or (start is None and end is None):
        return df
    keep = pd.Series(True, index=df.index)
    if start is not None:
        keep &= df["ts"] >= pd.Timestamp(start)
    if end is not None:
        keep &= df["ts"] <= pd.Timestamp(end)
    return df[keep].reset_index(drop=True)


def memoized(load: Loader) -> Loader:
    """`load(instrument_id, tf, start=None, end=None)` remembering each full (instrument, tf) series for
    the lifetime of the returned loader, for context series (the market index, India VIX) that every
    instrument of a cycle reads."""
    seen: dict[tuple[str, str], pd.DataFrame | None] = {}

    def get(instrument_id: str, tf: str, start=None, end=None) -> pd.DataFrame | None:
        key = (instrument_id, tf)
        if key not in seen:
            seen[key] = load(instrument_id, tf)
        return between(seen[key], start, end)

    return get


def run_forecast_cycle(
    tf: str,
    instruments: Iterable[str] | None = None,
    *,
    load: Loader | None = None,
    ledger: Ledger | None = None,
    scorecard: Scorecard | None = None,
    now: pd.Timestamp | None = None,
    include_baselines: bool = True,
) -> dict[str, int]:
    """Forecast the last closed bar of every instrument and write each forecast to the ledger:
    range_v1 when a saved model covers the instrument, analog_v1 on ANALOG_TFS only (it scans every past
    bar, far too slow at 5m), and the baselines alongside whichever of them was recorded. The forecast
    the scanner shows (range_v1, else analog_v1) is kept in candly.forecast.latest.

    `now` only sets what the forecasts treat as the present; the ledger rejects any forecast that is
    already late by the real clock. `counts["seconds"]` is the cycle's wall time."""
    validate_tf(tf)
    started = time.perf_counter()
    now = clock.utc_now() if now is None else pd.Timestamp(now).tz_convert("UTC")
    ids = (
        list(instruments)
        if instruments is not None
        else [i.id for i in load_watchlist() if tf in i.timeframes]
    )
    load = load or _default_loader()
    context = memoized(load)  # the market index and India VIX are shared by every instrument
    ledger = ledger or Ledger()
    cards: dict[str, Scorecard | None] = {}

    def card_for(instrument_id: str) -> Scorecard | None:
        """`scorecard` if given, else the persisted build of the instrument's own exchange."""
        if scorecard is not None:
            return scorecard
        exchange = exchange_of(instrument_id)
        if exchange not in cards:
            cards[exchange] = load_scorecard(tf, exchange)
        return cards[exchange]

    counts = dict.fromkeys(
        (
            "recorded",
            "abstained",
            "not_recorded",
            "no_data",
            "duplicates",
            "late",
            "errors",
            "range_recorded",
            "range_unavailable",
        ),
        0,
    )
    for instrument_id in ids:
        try:
            candles = load(instrument_id, tf)
            if candles is None or candles.empty:
                counts["no_data"] += 1
                continue
            primary: list[Forecast] = []
            try:
                ranged = make_range_forecast(instrument_id, tf, candles, now, load=context)
                if _not_recorded(ranged):
                    counts["not_recorded"] += 1
                else:
                    primary.append(ranged)
            except RangeUnavailable:
                counts["range_unavailable"] += 1
            if tf in ANALOG_TFS:
                forecast = make_forecast(instrument_id, tf, candles, card_for(instrument_id), now=now)
                if _not_recorded(forecast):
                    counts["not_recorded"] += 1
                else:
                    counts["abstained"] += int(forecast.abstain)
                    primary.append(forecast)
            batch = list(primary)
            if primary and include_baselines:
                steps = primary[0].horizon_bars if primary[0].method == ANALOG_METHOD else None
                batch += baseline_forecasts(instrument_id, tf, candles, steps, now=now)
            if primary:
                remember(primary[0], candles)
            for fc in batch:
                try:
                    ledger.record(fc)
                    counts["recorded"] += 1
                    counts["range_recorded"] += int(fc.method == RANGE_METHOD)
                except DuplicateForecast:
                    counts["duplicates"] += 1
                except LateForecast:
                    counts["late"] += 1
        except Exception:
            log.exception("forecast failed for %s %s", instrument_id, tf)
            counts["errors"] += 1
    counts["seconds"] = round(time.perf_counter() - started, 1)
    log.info("forecast cycle %s: %s", tf, counts)
    return counts


def grade_pending_job(
    *, load: Loader | None = None, ledger: Ledger | None = None, now: pd.Timestamp | None = None
) -> int:
    ledger = ledger or Ledger()
    finished = ledger.grade_pending(load or _default_loader(), now=now)
    log.info("graded or voided %d forecasts", finished)
    return finished


def rebuild_scorecard(
    tf: str,
    instruments: Iterable[str] | None = None,
    *,
    load: Loader | None = None,
    exchange: str | None = None,
) -> dict:
    """Build and persist one exchange's scorecard (NSE by default; see build_scorecard)."""
    card = build_scorecard(tf, instruments, load=load, exchange=exchange)
    summary = {
        "tf": tf,
        "exchange": card.meta.exchange,
        "n_tests": card.meta.n_tests,
        "certified": int(card.rows["certified"].sum()) if len(card.rows) else 0,
        "train_end": card.meta.train_end,
    }
    log.info("scorecard rebuilt: %s", summary)
    return summary
