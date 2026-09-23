"""The pre-market brief (08:45 IST) and the post-market review (16:15 IST), PLAN.md §13.

Each builds a JSON-ready facts dict (ledger, expiries, scanner, news) and renders it as Telegram HTML.
When `candly.llm.briefs.write_brief(kind, facts) -> str | None` returns text (Claude on, within budget,
and every number grounded in the facts), that text replaces the plain body; otherwise the plain
rendering goes out.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date, timedelta

import pandas as pd

from candly.alerts.config import AlertsConfig, load_alerts_config
from candly.alerts.rules import expiring, is_bullish, is_call, ledger_forecasts, open_ledger, tradable
from candly.alerts.text import DISCLAIMER, arrow, esc, name_of, pct, price
from candly.core.calendar import get_calendar
from candly.core.settings import get_settings
from candly.data import clock
from candly.forecast import Forecast
from candly.ledger.accuracy import accuracy_from_frame
from candly.ledger.grading import target_times
from candly.news.store import NewsStore

log = logging.getLogger(__name__)

TOP_SETUPS = 5
TOP_NEWS = 5
MAX_LISTED = 10  # calls listed per section; the rest are only counted
NEWS_SCANNED = 500
REVIEW_LOOKBACK = pd.Timedelta(days=10)  # longer than any 1D horizon plus holidays


def _utc(now: pd.Timestamp | None) -> pd.Timestamp:
    return clock.utc_now() if now is None else clock.to_utc(now)


def previous_trading_day(exchange: str, d: date) -> date:
    cal = get_calendar()
    d -= timedelta(days=1)
    while not cal.is_trading_day(exchange, d):
        d -= timedelta(days=1)
    return d


def _ref_day(fc: Forecast) -> date:
    return get_calendar().local_date(pd.Timestamp(fc.ref_time, unit="s", tz="UTC"))


def _last_target_day(fc: Forecast) -> date | None:
    times = target_times(fc)
    return get_calendar().local_date(times[-1]) if times else None


def _missing(value) -> bool:
    return value is None or pd.isna(value)


def call_facts(row: dict, fc: Forecast) -> dict:
    """One ledger call and how it has done so far: hit/miss once graded, the move since entry while open."""
    status, hit = row["status"], row.get("direction_hit")
    if status == "graded":
        result = "graded" if _missing(hit) else ("hit" if hit else "miss")
    else:
        result = {"pending": "open"}.get(status, status)
    actual = json.loads(row.get("actual") or "[]")
    trade = fc.trade
    return {
        "id": int(row["id"]),
        "instrument": fc.instrument,
        "name": name_of(fc.instrument),
        "tf": fc.tf,
        "direction": "bullish" if is_bullish(fc) else "bearish",
        "confidence": fc.confidence,
        "entry": trade.entry if trade else fc.ref_close,
        "stop": trade.stop if trade else fc.invalidation,
        "target": trade.target if trade else None,
        "status": status,
        "result": result,
        "match_score": None if _missing(row.get("match_score")) else float(row["match_score"]),
        "move_pct": 100.0 * (actual[-1]["close"] / fc.ref_close - 1.0) if actual else None,
    }


# --- facts ------------------------------------------------------------------------------------


def _calls_on(day: date, cfg: AlertsConfig) -> list[dict]:
    book = open_ledger()
    if book is None:
        return []
    rows = ledger_forecasts(book, cfg.timeframes, clock.ist_midnight(day), directional_only=True)
    return [call_facts(row, fc) for row, fc in rows if is_call(fc, cfg) and _ref_day(fc) == day]


def _scanner_rows(tf: str) -> list:
    from candly.api.routes.analytics import scanner  # the /api/scanner logic, called directly

    return scanner(tf)


def _setups() -> list[dict] | None:
    """The top directional 1D scanner rows; None when the scanner can't run."""
    try:
        rows = _scanner_rows("1D")
    except Exception as exc:
        log.warning("scanner unavailable for the pre-market brief: %s", exc)
        return None
    picked = [r for r in rows if not r.abstain and r.score > 0][:TOP_SETUPS]
    return [
        {
            "instrument": r.instrument,
            "name": r.name,
            "direction": r.direction,
            "p_up": r.p_up,
            "base_rate": r.base_rate,
            "score": r.score,
            "signal": r.top_signal.label if r.top_signal else None,
            "certified": bool(r.top_signal and r.top_signal.certified),
        }
        for r in picked
    ]


def _news_since(since: pd.Timestamp) -> list[dict]:
    """The strongest-sentiment headlines (either sign) published since `since`."""
    items = [i for i in NewsStore().query(since=since, limit=NEWS_SCANNED) if i.sentiment]
    items.sort(key=lambda i: abs(i.sentiment), reverse=True)
    return [
        {
            "title": i.title,
            "source": i.source,
            "sentiment": i.sentiment,
            "instruments": i.instruments,
            "url": i.url,
            "published_at": (i.published_at or i.fetched_at).isoformat(),
        }
        for i in items[:TOP_NEWS]
    ]


def pre_market_facts(now: pd.Timestamp | None = None) -> dict:
    now = _utc(now)
    cfg = load_alerts_config()
    cal = get_calendar()
    exchange = cfg.briefs.calendar
    today = cal.local_date(now)
    previous = previous_trading_day(exchange, today)
    return {
        "kind": "pre_market",
        "date": today.isoformat(),
        "previous_session": previous.isoformat(),
        "previous_calls": _calls_on(previous, cfg),
        "expiries": [
            {"instrument": inst.id, "name": inst.name, "kind": info.kind} for inst, info in expiring(today)
        ],
        "setups": _setups(),
        "news": _news_since(cal.session_times(exchange, previous)[1]),
    }


def post_market_facts(now: pd.Timestamp | None = None) -> dict:
    """Forecasts whose last target bar is today (they resolved today) against what happened."""
    now = _utc(now)
    cfg = load_alerts_config()
    cal = get_calendar()
    today = cal.local_date(now)
    book = open_ledger()
    rows = ledger_forecasts(book, cfg.timeframes, clock.ist_midnight(today) - REVIEW_LOOKBACK) if book else []
    rows = [(row, fc) for row, fc in rows if tradable(fc.instrument)]
    resolved = [(row, fc) for row, fc in rows if _last_target_day(fc) == today]
    summary = accuracy_from_frame(pd.DataFrame([row for row, _ in resolved])).summary
    calls = [call_facts(row, fc) for row, fc in resolved if not fc.abstain]
    graded = [c for c in calls if c["result"] in ("hit", "miss")]
    ranked = sorted(graded, key=lambda c: (c["result"] == "hit", c["match_score"] or 0.0))
    statuses = [row["status"] for row, _ in resolved]
    return {
        "kind": "post_market",
        "date": today.isoformat(),
        "resolved": len(resolved),
        "graded": summary.n_graded,
        "void": statuses.count("void"),
        "pending": statuses.count("pending"),
        "abstained": summary.n_abstained,
        "calls_graded": len(graded),
        "hits": sum(c["result"] == "hit" for c in graded),
        "hit_rate": summary.direction_hit_rate,
        "mean_match_score": summary.mean_match_score,
        "best": ranked[-1] if ranked else None,
        "worst": ranked[0] if len(ranked) > 1 else None,
        "open_calls": [
            call_facts(row, fc) for row, fc in rows if row["status"] == "pending" and is_call(fc, cfg)
        ],
    }


# --- rendering --------------------------------------------------------------------------------


def call_line(c: dict) -> str:
    head = f"{arrow(c['direction'] == 'bullish')} {esc(c['name'])} {esc(c['tf'])}"
    if c["confidence"]:
        head += f" · {esc(c['confidence'])}"
    if c["result"] in ("hit", "miss"):
        tail = c["result"] + ("" if c["match_score"] is None else f", match {c['match_score']:.0f}")
    elif c["result"] == "open":
        tail = f"open from {price(c['entry'])}"
        if c["move_pct"] is not None:
            tail += f", {c['move_pct']:+.1f}% since"
    else:
        tail = esc(c["result"])
    return f"{head} · {tail}"


def _listed(calls: list[dict]) -> list[str]:
    lines = [call_line(c) for c in calls[:MAX_LISTED]]
    if len(calls) > MAX_LISTED:
        lines.append(f"…and {len(calls) - MAX_LISTED} more")
    return lines


def _setup_line(rank: int, s: dict) -> str:
    line = f"{rank}. {arrow(s['direction'] == 'bullish')} {esc(s['name'])}"
    if s["p_up"] is not None and s["base_rate"] is not None:
        line += f" · p(up) {pct(s['p_up'])} vs {pct(s['base_rate'])}"
    if s["signal"]:
        line += f" · {esc(s['signal'])}" + (" (certified)" if s["certified"] else "")
    return line


def _news_line(n: dict) -> str:
    line = f"• [{n['sentiment']:+.2f}] {esc(n['title'])} ({esc(n['source'])})"
    if n["instruments"]:
        line += f" · {esc(', '.join(n['instruments']))}"
    return line


def render_pre_market(facts: dict) -> str:
    day = date.fromisoformat(facts["date"])
    previous = date.fromisoformat(facts["previous_session"])
    lines = [f"<b>Pre-market brief · {day:%a %d %b %Y}</b>", "", f"<b>Calls on {previous:%a %d %b}</b>"]
    lines += _listed(facts["previous_calls"]) or ["No calls."]
    lines += ["", "<b>Expiries today</b>"]
    expiries = [f"• {esc(e['name'])} ({esc(e['instrument'])}): {esc(e['kind'])}" for e in facts["expiries"]]
    lines += expiries or ["None."]
    lines += ["", "<b>Top 1D setups</b>"]
    if facts["setups"] is None:
        lines.append("Scanner unavailable.")
    else:
        lines += [_setup_line(i, s) for i, s in enumerate(facts["setups"], 1)] or ["No directional setups."]
    lines += ["", f"<b>News since the {previous:%d %b} close</b>"]
    lines += [_news_line(n) for n in facts["news"]] or ["No scored news."]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def render_post_market(facts: dict) -> str:
    day = date.fromisoformat(facts["date"])
    lines = [f"<b>Post-market review · {day:%a %d %b %Y}</b>", ""]
    if not facts["resolved"]:
        lines.append("No forecasts resolved today.")
    else:
        lines.append(
            f"Forecasts resolved today: {facts['resolved']} (graded {facts['graded']}, void {facts['void']}, "
            f"pending {facts['pending']}; {facts['abstained']} abstained)"
        )
        if facts["calls_graded"]:
            rate = "" if facts["hit_rate"] is None else f" ({pct(facts['hit_rate'])})"
            lines.append(f"Directional calls: {facts['hits']} of {facts['calls_graded']} hit{rate}")
        else:
            lines.append("No directional calls graded today.")
        if facts["mean_match_score"] is not None:
            lines.append(f"Mean match score: {facts['mean_match_score']:.0f}/100")
        if facts["best"]:
            lines.append(f"Best: {call_line(facts['best'])}")
        if facts["worst"]:
            lines.append(f"Worst: {call_line(facts['worst'])}")
    lines += ["", "<b>Open calls</b>"]
    lines += _listed(facts["open_calls"]) or ["None."]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


# --- briefs -----------------------------------------------------------------------------------


def _written(kind: str, facts: dict) -> str | None:
    if not get_settings().has_anthropic:
        return None
    try:
        from candly.llm.briefs import write_brief
    except ImportError:
        return None
    try:
        text = write_brief(kind, facts)
    except Exception as exc:
        log.warning("%s brief from Claude failed, sending the plain one: %s", kind, type(exc).__name__)
        return None
    return text.strip() if isinstance(text, str) and text.strip() else None


def _compose(kind: str, facts: dict, render: Callable[[dict], str]) -> str:
    plain = render(facts)
    written = _written(kind, facts)
    if written is None:
        return plain
    title = plain.partition("\n")[0]
    return f"{title}\n\n{esc(written)}\n\n{DISCLAIMER}"


def pre_market_brief(now: pd.Timestamp | None = None) -> str:
    return _compose("pre_market", pre_market_facts(now), render_pre_market)


def post_market_review(now: pd.Timestamp | None = None) -> str:
    return _compose("post_market", post_market_facts(now), render_post_market)
