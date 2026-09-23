import json
import sys
import types
from datetime import date

import pandas as pd
import pytest
from test_alerts_helpers import CHAT_ID, TOKEN, bars, call, ist, record

from candly.alerts import briefs, jobs, telegram
from candly.alerts.config import QuietHours, load_alerts_config
from candly.alerts.delivery import AlertStore
from candly.core.schema import empty_candles
from candly.core.settings import get_settings
from candly.data import clock
from candly.ledger import Ledger
from candly.news.models import NewsItem
from candly.news.store import NewsStore

PRE_MARKET = ist("2026-09-24 08:45")  # Thursday; the previous session is Wed 23 Sep
POST_MARKET = ist("2026-09-23 16:15")

SERIES = {
    (instrument, tf): bars(instrument, tf, first, date(2026, 9, 23), start=start, step=step)
    for instrument, tf, first, start, step in [
        ("NSE:RELIANCE", "1h", date(2026, 9, 17), 2900.0, 0.5),
        ("NSE:TCS", "1h", date(2026, 9, 17), 3980.0, 0.5),
        ("NSE:SBIN", "1h", date(2026, 9, 17), 800.0, 0.5),
        ("NSE:HDFCBANK", "1h", date(2026, 9, 17), 1600.0, 0.5),
        ("NSE:INDIAVIX", "1h", date(2026, 9, 17), 12.0, 0.01),
        ("NSE:AXISBANK", "1D", date(2026, 9, 10), 1100.0, 5.0),
    ]
}


def load(instrument_id: str, tf: str, start=None, end=None) -> pd.DataFrame:
    return SERIES.get((instrument_id, tf), empty_candles())


def at_close(instrument: str, tf: str, ref: str, **kwargs):
    """A call whose entry is the close of its reference bar in SERIES."""
    frame = SERIES[(instrument, tf)]
    entry = float(frame.loc[frame["ts"] == ist(ref), "close"].iloc[0])
    return call(instrument, tf, ref, entry=entry, **kwargs)


@pytest.fixture
def ledger(monkeypatch):
    """Rising prices everywhere. On the 23rd: a bullish RELIANCE 1h call (hit), a bearish TCS 1h call
    (miss), a bullish INFY 1D call (still open), plus an abstention, a low-confidence call and an
    INDIAVIX call that no brief may list as calls. An AXISBANK 1D call from the 18th resolves on the 23rd."""
    forecasts = [
        at_close("NSE:RELIANCE", "1h", "2026-09-23 10:15"),
        at_close("NSE:TCS", "1h", "2026-09-23 11:15", bullish=False),
        call("NSE:INFY", "1D", "2026-09-23 09:15", entry=1520.0, confidence="high"),
        at_close("NSE:SBIN", "1h", "2026-09-23 10:15", abstain=True),
        at_close("NSE:HDFCBANK", "1h", "2026-09-23 10:15", confidence="low"),
        at_close("NSE:INDIAVIX", "1h", "2026-09-23 10:15"),
        at_close("NSE:AXISBANK", "1D", "2026-09-18 09:15"),
    ]
    for fc in forecasts:
        record(monkeypatch, fc)
    assert Ledger().grade_pending(load, now=ist("2026-09-23 16:10")) == 6


@pytest.fixture
def news():
    def item(n: int, title: str, when: str, sentiment: float | None, instruments=()) -> NewsItem:
        published = ist(when)
        return NewsItem(
            id=f"n{n}", title=title, url=f"https://example.com/{n}", source="Wire",
            published_at=published, fetched_at=published + pd.Timedelta(minutes=1),
            instruments=list(instruments), sentiment=sentiment,
        )

    NewsStore().add(
        [
            item(0, "Old news before the close", "2026-09-23 14:00", -0.95),
            item(1, "Reliance <wins> order & more", "2026-09-23 18:00", 0.8, ["NSE:RELIANCE"]),
            item(2, "Crude slides overnight", "2026-09-24 07:00", -0.6, ["MCX:CRUDEOIL"]),
            item(3, "Mild positive", "2026-09-23 20:00", 0.2),
            item(4, "Mild negative", "2026-09-23 21:00", -0.3),
            item(5, "Barely positive", "2026-09-23 22:00", 0.1),
            item(6, "Unscored", "2026-09-23 23:00", None),
            item(7, "Almost neutral", "2026-09-24 06:00", 0.05),
        ]
    )


def scanner_row(instrument: str, name: str, score: float, *, abstain=False, bullish=True, signal=None):
    top = types.SimpleNamespace(label=signal, certified=signal == "Hammer") if signal else None
    return types.SimpleNamespace(
        instrument=instrument, name=name, abstain=abstain, score=0.0 if abstain else score,
        direction="neutral" if abstain else ("bullish" if bullish else "bearish"),
        p_up=None if abstain else (0.5 + score if bullish else 0.5 - score), base_rate=0.5, top_signal=top,
    )


@pytest.fixture
def scanner(monkeypatch):
    rows = [
        scanner_row("NSE:ITC", "ITC", 0.12, signal="Hammer"),
        scanner_row("NSE:LT", "Larsen & Toubro", 0.10, bullish=False, signal="Bearish engulfing"),
        scanner_row("NSE:SBIN", "State Bank of India", 0.08),
        scanner_row("NSE:TCS", "Tata Consultancy Services", 0.06),
        scanner_row("NSE:INFY", "Infosys", 0.05),
        scanner_row("NSE:AXISBANK", "Axis Bank", 0.04),
        scanner_row("NSE:RELIANCE", "Reliance Industries", 0.0, abstain=True),
    ]
    monkeypatch.setattr(briefs, "_scanner_rows", lambda tf: rows)


@pytest.fixture
def outbox(monkeypatch, no_keys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT_ID)
    get_settings.cache_clear()
    box: list[str] = []
    monkeypatch.setattr(telegram, "send", lambda text: box.append(text) or True)
    return box


def test_pre_market_facts(ledger, news, scanner):
    facts = briefs.pre_market_facts(PRE_MARKET)
    assert facts["date"] == "2026-09-24" and facts["previous_session"] == "2026-09-23"
    calls = {c["instrument"]: c for c in facts["previous_calls"]}
    assert list(calls) == ["NSE:RELIANCE", "NSE:TCS", "NSE:INFY"]  # abstaining, low and VIX left out
    assert calls["NSE:RELIANCE"]["result"] == "hit" and calls["NSE:RELIANCE"]["match_score"] is not None
    assert calls["NSE:TCS"]["result"] == "miss" and calls["NSE:TCS"]["direction"] == "bearish"
    assert calls["NSE:INFY"]["result"] == "open" and calls["NSE:INFY"]["move_pct"] is None
    assert facts["expiries"] == [{"instrument": "BSE:SENSEX", "name": "Sensex", "kind": "monthly"}]
    setups = [s["instrument"] for s in facts["setups"]]
    assert setups == ["NSE:ITC", "NSE:LT", "NSE:SBIN", "NSE:TCS", "NSE:INFY"]  # top 5, abstaining row skipped
    assert [n["sentiment"] for n in facts["news"]] == [0.8, -0.6, -0.3, 0.2, 0.1]
    json.dumps(facts)


def test_pre_market_brief_text(ledger, news, scanner):
    text = briefs.pre_market_brief(PRE_MARKET)
    lines = text.split("\n")
    assert lines[0] == "<b>Pre-market brief · Thu 24 Sep 2026</b>"
    assert "<b>Calls on Wed 23 Sep</b>" in lines
    assert any(line.startswith("▲ Bullish Reliance Industries 1h · medium · hit, match ") for line in lines)
    tcs = "▼ Bearish Tata Consultancy Services 1h · medium · miss, match "
    assert any(line.startswith(tcs) for line in lines)
    assert "▲ Bullish Infosys 1D · high · open from 1,520.00" in lines
    assert "• Sensex (BSE:SENSEX): monthly" in lines
    assert "1. ▲ Bullish ITC · p(up) 62% vs 50% · Hammer (certified)" in lines
    assert "2. ▼ Bearish Larsen &amp; Toubro · p(up) 40% vs 50% · Bearish engulfing" in lines
    assert "<b>News since the 23 Sep close</b>" in lines
    assert "• [+0.80] Reliance &lt;wins&gt; order &amp; more (Wire) · NSE:RELIANCE" in lines
    assert "Old news" not in text and "Almost neutral" not in text
    assert lines[-1] == "<i>Educational — not investment advice.</i>"


def test_pre_market_brief_with_nothing_to_report(monkeypatch):
    def no_scanner(tf):
        raise RuntimeError("no data")

    monkeypatch.setattr(briefs, "_scanner_rows", no_scanner)
    text = briefs.pre_market_brief(ist("2026-09-23 08:45"))
    assert "No calls." in text and "None." in text and "Scanner unavailable." in text
    assert "No scored news." in text


def test_post_market_review(ledger):
    facts = briefs.post_market_facts(POST_MARKET)
    # resolved today: RELIANCE, TCS, SBIN (abstained), HDFCBANK (low) and the AXISBANK 1D call; not VIX
    counts = tuple(facts[k] for k in ("resolved", "graded", "abstained", "void", "pending"))
    assert counts == (5, 5, 1, 0, 0)
    assert (facts["hits"], facts["calls_graded"]) == (3, 4) and facts["hit_rate"] == pytest.approx(0.75)
    assert facts["worst"]["instrument"] == "NSE:TCS" and facts["best"]["result"] == "hit"
    assert [c["instrument"] for c in facts["open_calls"]] == ["NSE:INFY"]
    text = briefs.post_market_review(POST_MARKET)
    lines = text.split("\n")
    assert lines[0] == "<b>Post-market review · Wed 23 Sep 2026</b>"
    assert "Forecasts resolved today: 5 (graded 5, void 0, pending 0; 1 abstained)" in lines
    assert "Directional calls: 3 of 4 hit (75%)" in lines
    assert any(line.startswith("Mean match score: ") and line.endswith("/100") for line in lines)
    assert any(line.startswith("Best: ▲ Bullish ") for line in lines)
    worst = "Worst: ▼ Bearish Tata Consultancy Services 1h · medium · miss"
    assert any(line.startswith(worst) for line in lines)
    json.dumps(facts)
    assert "▲ Bullish Infosys 1D · high · open from 1,520.00" in lines
    assert lines[-1] == "<i>Educational — not investment advice.</i>"


def test_post_market_review_on_an_empty_ledger():
    text = briefs.post_market_review(POST_MARKET)
    assert "No forecasts resolved today." in text and "<b>Open calls</b>\nNone." in text


def test_claude_text_replaces_the_plain_body_when_available(monkeypatch, ledger):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    get_settings.cache_clear()
    seen = {}

    def write_brief(kind, facts):
        seen[kind] = facts
        return "- 3 of 4 calls hit <today> & TCS missed."

    fake = types.ModuleType("candly.llm.briefs")
    fake.write_brief = write_brief
    monkeypatch.setitem(sys.modules, "candly.llm.briefs", fake)
    assert briefs.post_market_review(POST_MARKET) == (
        "<b>Post-market review · Wed 23 Sep 2026</b>\n\n"
        "- 3 of 4 calls hit &lt;today&gt; &amp; TCS missed.\n\n"
        "<i>Educational — not investment advice.</i>"
    )
    assert seen["post_market"]["hits"] == 3 and seen["post_market"]["calls_graded"] == 4
    json.dumps(seen["post_market"])  # the facts must be JSON-ready for the prompt

    def broken(kind, facts):
        raise RuntimeError("API down")

    fake.write_brief = broken
    plain = briefs.render_post_market(briefs.post_market_facts(POST_MARKET))
    assert briefs.post_market_review(POST_MARKET) == plain


def test_llm_absent_or_disabled_falls_back_to_plain(monkeypatch):
    monkeypatch.setitem(sys.modules, "candly.llm.briefs", None)  # import fails
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    get_settings.cache_clear()
    plain = briefs.render_post_market(briefs.post_market_facts(POST_MARKET))
    assert briefs.post_market_review(POST_MARKET) == plain


# --- jobs -----------------------------------------------------------------------------------------


def test_briefs_are_sent_once_on_trading_days(monkeypatch, outbox, ledger, scanner):
    monkeypatch.setattr(clock, "utc_now", lambda: PRE_MARKET)
    assert jobs.send_pre_market_brief(PRE_MARKET) is True
    assert jobs.send_pre_market_brief(PRE_MARKET) is False
    assert outbox[0].startswith("<b>Pre-market brief · Thu 24 Sep 2026</b>")
    assert jobs.send_post_market_review(POST_MARKET) is True
    assert outbox[1].startswith("<b>Post-market review · Wed 23 Sep 2026</b>")
    assert jobs.send_pre_market_brief(ist("2026-09-26 08:45")) is False  # Saturday
    assert jobs.send_post_market_review(ist("2026-10-02 16:15")) is False  # Gandhi Jayanti
    assert len(outbox) == 2


def test_pre_market_brief_ignores_quiet_hours_and_the_cap(monkeypatch, outbox, scanner):
    cfg = load_alerts_config().model_copy(
        update={"quiet_hours": QuietHours(start="20:00", end="09:30"), "max_per_hour": 1}
    )
    for module in (briefs, jobs):
        monkeypatch.setattr(module, "load_alerts_config", lambda: cfg)
    AlertStore().claim("call:1", "new_call", PRE_MARKET - pd.Timedelta(minutes=10))  # uses up the hour's cap
    assert jobs.send_pre_market_brief(PRE_MARKET) is True
    assert jobs.send_post_market_review(ist("2026-09-24 21:00")) is False  # the review respects quiet hours
    assert len(outbox) == 1


def test_brief_type_toggle(monkeypatch, outbox, scanner):
    cfg = load_alerts_config()
    cfg = cfg.model_copy(update={"types": cfg.types.model_copy(update={"pre_market_brief": False})})
    monkeypatch.setattr(jobs, "load_alerts_config", lambda: cfg)
    assert jobs.send_pre_market_brief(PRE_MARKET) is False and outbox == []
