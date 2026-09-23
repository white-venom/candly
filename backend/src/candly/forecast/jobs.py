"""Scheduled quant jobs. The lead wires these into the scheduler; they are the only ledger writers."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

import pandas as pd

from candly.core.instruments import load_watchlist
from candly.core.timeframes import validate_tf
from candly.data import clock
from candly.forecast.analog import make_forecast
from candly.forecast.baselines import baseline_forecasts
from candly.ledger import DuplicateForecast, LateForecast, Ledger
from candly.research.scorecard import Scorecard, build_scorecard, load_scorecard

log = logging.getLogger(__name__)

# Abstentions that mean "no forecast was possible" rather than "no edge"; they are not recorded.
NOT_RECORDED = ("stale data", "not enough history")

Loader = Callable[..., pd.DataFrame]


def _default_loader() -> Loader:
    from candly.data.store import load_candles

    return load_candles


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
    """Forecast the last closed bar of every instrument and write each forecast to the ledger.

    `now` only sets what the forecasts treat as the present; the ledger rejects any forecast that is
    already late by the real clock."""
    validate_tf(tf)
    now = clock.utc_now() if now is None else pd.Timestamp(now).tz_convert("UTC")
    ids = (
        list(instruments)
        if instruments is not None
        else [i.id for i in load_watchlist() if tf in i.timeframes]
    )
    load = load or _default_loader()
    ledger = ledger or Ledger()
    card = scorecard if scorecard is not None else load_scorecard(tf)
    counts = dict.fromkeys(
        ("recorded", "abstained", "not_recorded", "no_data", "duplicates", "late", "errors"), 0
    )
    for instrument_id in ids:
        try:
            candles = load(instrument_id, tf)
            if candles is None or candles.empty:
                counts["no_data"] += 1
                continue
            forecast = make_forecast(instrument_id, tf, candles, card, now=now)
            if forecast.abstain and (forecast.abstain_reason or "").startswith(NOT_RECORDED):
                counts["not_recorded"] += 1
                continue
            counts["abstained"] += int(forecast.abstain)
            batch = [forecast]
            if include_baselines:
                batch += baseline_forecasts(instrument_id, tf, candles, forecast.horizon_bars, now=now)
            for fc in batch:
                try:
                    ledger.record(fc)
                    counts["recorded"] += 1
                except DuplicateForecast:
                    counts["duplicates"] += 1
                except LateForecast:
                    counts["late"] += 1
        except Exception:
            log.exception("forecast failed for %s %s", instrument_id, tf)
            counts["errors"] += 1
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
    tf: str, instruments: Iterable[str] | None = None, *, load: Loader | None = None
) -> dict:
    card = build_scorecard(tf, instruments, load=load)
    summary = {
        "tf": tf,
        "n_tests": card.meta.n_tests,
        "certified": int(card.rows["certified"].sum()) if len(card.rows) else 0,
        "train_end": card.meta.train_end,
    }
    log.info("scorecard rebuilt: %s", summary)
    return summary
