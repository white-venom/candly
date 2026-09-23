"""Event alerts: new directional calls, stop hits, expiry-day reminders and data pauses.

`check_and_send()` runs after every pipeline cycle. Each event is sent once (deduplicated in
data/db/alerts.sqlite). Quiet hours and the hourly cap only defer an event: a later check sends it if it
still applies. Everything is a no-op without TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date

import pandas as pd

from candly.alerts.config import CONFIDENCE_RANK, AlertsConfig, load_alerts_config
from candly.alerts.delivery import AlertStore, Outcome, deliver
from candly.alerts.text import DISCLAIMER, arrow, bar_time, chart_url, esc, ist, label, pct, price, unix_ts
from candly.core.calendar import get_calendar
from candly.core.expiry import ExpiryInfo
from candly.core.instruments import Instrument, UnknownInstrument, exchange_of, get_instrument, load_watchlist
from candly.core.settings import get_settings
from candly.data import clock
from candly.data.expiries import expiry_info
from candly.data.store import load_candles
from candly.forecast import METHOD, Forecast
from candly.ledger import Ledger, default_ledger_path
from candly.ledger.grading import target_times

log = logging.getLogger(__name__)

LEDGER_LOOKBACK = pd.Timedelta(days=7)  # covers a 1D call made before a long weekend
PAUSED = "data_paused"
KINDS = ("data_paused", "stop_hit", "expiry_today", "new_call")  # most urgent first


# --- ledger helpers (shared with briefs) ------------------------------------------------------


def open_ledger() -> Ledger | None:
    path = default_ledger_path()
    return Ledger(path) if path.exists() else None


def ledger_forecasts(
    book: Ledger, tfs: tuple[str, ...], since: pd.Timestamp, *, directional_only: bool = False
) -> list[tuple[dict, Forecast]]:
    """(ledger row, forecast) for every analog_v1 forecast made since `since` on `tfs`, oldest first."""
    out = []
    for tf in tfs:
        frame = book.frame(tf=tf, method=METHOD, since=clock.epoch_seconds(since))
        for row in frame.to_dict("records"):
            if directional_only and row["abstain"]:
                continue
            out.append((row, Forecast.model_validate_json(row["payload"])))
    out.sort(key=lambda item: (item[0]["made_at"], item[0]["id"]))
    return out


def tradable(instrument_id: str) -> bool:
    try:
        return get_instrument(instrument_id).tradable
    except UnknownInstrument:
        return False


def is_bullish(fc: Forecast) -> bool:
    return fc.p_up is not None and fc.base_rate is not None and fc.p_up > fc.base_rate


def is_call(fc: Forecast, cfg: AlertsConfig) -> bool:
    """A directional analog_v1 call on an alerted timeframe, for a tradable instrument, at or above
    `min_confidence`. Abstaining forecasts never qualify, whatever their p_up."""
    return (
        fc.method == METHOD
        and not fc.abstain
        and fc.trade is not None
        and fc.p_up is not None
        and fc.base_rate is not None
        and fc.tf in cfg.timeframes
        and fc.confidence is not None
        and CONFIDENCE_RANK[fc.confidence] >= CONFIDENCE_RANK[cfg.min_confidence]
        and tradable(fc.instrument)
    )


def actionable(fc: Forecast, now: pd.Timestamp) -> bool:
    """A call is worth sending until the first bar after its reference bar has closed."""
    first = target_times(fc)[:1]
    return bool(first) and now < get_calendar().bar_close_time(exchange_of(fc.instrument), first[0], fc.tf)


def expiring(day: date) -> list[tuple[Instrument, ExpiryInfo]]:
    """Tradable watchlist instruments with a derivatives expiry on the IST date `day`."""
    out = []
    for inst in load_watchlist():
        if not inst.tradable:
            continue
        info = expiry_info(inst.id, day)
        if info is not None and info.is_expiry_day:
            out.append((inst, info))
    return out


# --- messages ---------------------------------------------------------------------------------


def expiry_note(instrument_id: str, day: date) -> str | None:
    info = expiry_info(instrument_id, day)
    if info is None:
        return None
    if info.is_expiry_day:
        return f"Expiry today ({info.kind})"
    days = info.days_to_expiry
    return (
        f"Next expiry {info.next_expiry:%d %b} ({info.kind}), "
        f"{days} trading day{'' if days == 1 else 's'} away"
    )


def call_message(fc: Forecast, now: pd.Timestamp) -> str:
    trade = fc.trade
    lines = [
        f"<b>New call: {esc(label(fc.instrument))} · {esc(fc.tf)}</b>",
        f"{arrow(is_bullish(fc))} · confidence {esc(fc.confidence)}",
        f"Entry {price(trade.entry)} · Stop {price(trade.stop)} · Target {price(trade.target)} · "
        f"R:R {trade.reward_risk:.2f}",
        f"p(up) {pct(fc.p_up)} vs base rate {pct(fc.base_rate)}, over the next {fc.horizon_bars} bars",
    ]
    note = expiry_note(fc.instrument, get_calendar().local_date(now))
    if note:
        lines.append(esc(note))
    lines += [f"Chart: {esc(chart_url(fc.instrument, fc.tf))}", DISCLAIMER]
    return "\n".join(lines)


def stop_message(watch: sqlite3.Row, bar: pd.Series) -> str:
    bullish, tf = bool(watch["bullish"]), watch["tf"]
    extreme = ("low", bar["low"]) if bullish else ("high", bar["high"])
    to_stop = 100.0 * (watch["stop"] / watch["entry"] - 1.0)
    return "\n".join(
        [
            f"<b>Stop hit: {esc(label(watch['instrument']))} · {esc(tf)}</b>",
            f"{arrow(bullish)} call from the {bar_time(unix_ts(watch['ref_time']), tf)} bar: "
            f"stop {price(watch['stop'])} crossed",
            f"Bar {bar_time(bar['ts'], tf)}: {extreme[0]} {price(extreme[1])}, close {price(bar['close'])}",
            f"Entry was {price(watch['entry'])} ({to_stop:+.2f}% to the stop)",
            f"Chart: {esc(chart_url(watch['instrument'], tf))}",
            DISCLAIMER,
        ]
    )


def expiry_message(day: date, items: list[tuple[str, ExpiryInfo]]) -> str:
    lines = [f"<b>Expiry today · {day:%a %d %b}</b>"]
    lines += [f"• {esc(label(instrument_id))}: {esc(info.kind)}" for instrument_id, info in items]
    return "\n".join(lines)


def paused_message(reason: str | None, open_markets: list[str], since: pd.Timestamp) -> str:
    return "\n".join(
        [
            "<b>Data paused</b>",
            esc(reason or "Data updates are blocked"),
            f"Open now: {esc(', '.join(open_markets))}. Forecasts and alerts wait for fresh bars.",
            f"Since {ist(since)}",
        ]
    )


def resumed_message(since: pd.Timestamp, now: pd.Timestamp) -> str:
    return f"<b>Data resumed</b>\nData updates can run again (paused {ist(since)} to {ist(now)})."


# --- checks -----------------------------------------------------------------------------------


@dataclass
class _Run:
    store: AlertStore
    cfg: AlertsConfig
    now: pd.Timestamp
    offline: bool = False  # set after a failed send, so one Telegram outage costs one retry cycle

    def send(self, key: str, kind: str, text: str) -> Outcome:
        if self.offline:
            return "failed"
        outcome = deliver(self.store, self.cfg, key, kind, text, self.now)
        self.offline = outcome == "failed"
        return outcome


def _health() -> dict:
    # Lazy: keeps FastAPI out of plain job imports, and lets the API import this package.
    from candly.api.routes.platform import health

    return health()


def _check_data(run: _Run) -> list[Outcome]:
    """One alert when ingest becomes blocked during market hours (as /api/health reports it), and one
    recovery message once it is unblocked. An incident nobody was told about ends silently."""
    report = _health()
    ingest = report.get("ingest") or {"status": "ok", "reason": None}
    open_markets = [m["exchange"] for m in report.get("markets", []) if m.get("open")]
    incident = run.store.get_state(PAUSED)
    if ingest["status"] == "blocked":
        if incident is None:
            if not open_markets:
                return []
            incident = {"since": clock.epoch_seconds(run.now), "reason": ingest["reason"], "notified": False}
            run.store.set_state(PAUSED, incident)
        if incident["notified"] or not open_markets:
            return []
        since = unix_ts(incident["since"])
        text = paused_message(ingest["reason"], open_markets, since)
        outcome = run.send(f"data_paused:{incident['since']}", "data_paused", text)
        if outcome in ("sent", "duplicate"):
            run.store.set_state(PAUSED, {**incident, "notified": True})
        return [outcome]
    if incident is None:
        return []
    if not incident["notified"]:
        run.store.clear_state(PAUSED)
        return []
    text = resumed_message(unix_ts(incident["since"]), run.now)
    outcome = run.send(f"data_resumed:{incident['since']}", "data_paused", text)
    if outcome in ("sent", "duplicate"):
        run.store.clear_state(PAUSED)
    return [outcome]


def _check_stops(run: _Run) -> list[Outcome]:
    cal = get_calendar()
    outcomes = []
    for watch in run.store.watching():
        instrument_id, tf = watch["instrument"], watch["tf"]
        after = unix_ts(watch["ref_time"]) + pd.Timedelta(seconds=1)
        bars = load_candles(instrument_id, tf, start=after).head(run.cfg.stop_watch_bars)
        exchange = exchange_of(instrument_id)
        closed = [cal.bar_close_time(exchange, ts, tf) <= run.now for ts in bars["ts"]]
        bars = bars[pd.Series(closed, index=bars.index, dtype=bool)]
        stop = watch["stop"]
        crossed = bars[bars["low"] <= stop] if watch["bullish"] else bars[bars["high"] >= stop]
        if crossed.empty:
            if len(bars) >= run.cfg.stop_watch_bars:
                run.store.unwatch(watch["forecast_id"])
            continue
        outcome = run.send(f"stop:{watch['forecast_id']}", "stop_hit", stop_message(watch, crossed.iloc[0]))
        if outcome in ("sent", "duplicate"):
            run.store.unwatch(watch["forecast_id"])
        outcomes.append(outcome)
    return outcomes


def _check_expiry(run: _Run) -> list[Outcome]:
    """One reminder per day, listing the tradable instruments that expire today and are yet to close."""
    cal = get_calendar()
    today = cal.local_date(run.now)
    key = f"expiry:{today.isoformat()}"
    if run.store.seen(key):
        return []
    items = [
        (inst.id, info)
        for inst, info in expiring(today)
        if run.now < cal.session_times(inst.exchange, today)[1]
    ]
    return [run.send(key, "expiry_today", expiry_message(today, items))] if items else []


def _check_calls(run: _Run) -> list[Outcome]:
    book = open_ledger()
    if book is None:
        return []
    outcomes = []
    since = run.now - LEDGER_LOOKBACK
    for row, fc in ledger_forecasts(book, run.cfg.timeframes, since, directional_only=True):
        key = f"call:{row['id']}"
        if not is_call(fc, run.cfg) or not actionable(fc, run.now) or run.store.seen(key):
            continue
        outcome = run.send(key, "new_call", call_message(fc, run.now))
        if outcome == "sent":
            trade = fc.trade
            run.store.watch(
                int(row["id"]), fc.instrument, fc.tf, is_bullish(fc), trade.entry, trade.stop, fc.ref_time
            )
        outcomes.append(outcome)
    return outcomes


_CHECKS = {
    "data_paused": _check_data,
    "stop_hit": _check_stops,
    "expiry_today": _check_expiry,
    "new_call": _check_calls,
}


def check_and_send(now: pd.Timestamp | None = None) -> dict:
    """Run every enabled check once. Returns counts: `sent` per kind, plus `deferred` (quiet hours or the
    hourly cap), `failed` (Telegram errors; retried next time) and `errors` (a check that crashed)."""
    now = clock.utc_now() if now is None else clock.to_utc(now)
    cfg = load_alerts_config()
    counts: dict = {"enabled": cfg.enabled and get_settings().has_telegram}
    counts |= dict.fromkeys(KINDS, 0) | {"sent": 0, "deferred": 0, "failed": 0, "errors": 0}
    if not counts["enabled"]:
        return counts
    run = _Run(AlertStore(), cfg, now)
    for kind in KINDS:
        if not getattr(cfg.types, kind):
            continue
        try:
            outcomes = _CHECKS[kind](run)
        except Exception:
            log.exception("%s alert check failed", kind)
            counts["errors"] += 1
            continue
        for outcome in outcomes:
            if outcome == "sent":
                counts[kind] += 1
                counts["sent"] += 1
            elif outcome in ("quiet", "capped"):
                counts["deferred"] += 1
            elif outcome == "failed":
                counts["failed"] += 1
    if counts["sent"] or counts["failed"]:
        log.info("alerts: %s", counts)
    return counts
