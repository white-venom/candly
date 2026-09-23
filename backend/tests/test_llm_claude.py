"""candly.llm with a fake Anthropic client: no key, no network, no spend."""

import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import anthropic
import httpx2
import pandas as pd
import pytest
from anthropic.types import Message, TextBlock, Usage
from anthropic.types.beta import (
    BetaFallbackMessageIterationUsage,
    BetaMessage,
    BetaMessageIterationUsage,
    BetaTextBlock,
    BetaUsage,
)

import candly.llm as llm
from candly.core.calendar import get_calendar
from candly.core.instruments import load_watchlist
from candly.core.settings import get_settings
from candly.data import clock
from candly.forecast.models import Forecast
from candly.ledger import Ledger
from candly.llm import client as client_mod
from candly.llm import explain, news_tagger
from candly.llm.config import load_llm_config
from candly.llm.grounding import ungrounded_numbers
from candly.llm.store import LLMStore
from candly.news.models import NewsItem, news_id
from candly.news.store import NewsStore
from candly.research.synthetic import synthetic_candles

NOW = pd.Timestamp("2026-09-23 06:00", tz="UTC")  # 11:30 IST
TEST_KEY = "sk-ant-test-0000000000"
URL = "https://api.anthropic.com/v1/messages"


# ---------------------------------------------------------------- fakes and fixtures


def message(text="", *, model="claude-haiku-4-5", stop="end_turn", inp=1000, out=200, cw=0, cr=0) -> Message:
    return Message(
        id="msg_test",
        type="message",
        role="assistant",
        model=model,
        content=[TextBlock(type="text", text=text)] if text else [],
        stop_reason=stop,
        stop_sequence=None,
        usage=Usage(
            input_tokens=inp, output_tokens=out, cache_creation_input_tokens=cw, cache_read_input_tokens=cr
        ),
    )


def beta_message(text, *, model="claude-opus-5", iterations=None, inp=1500, out=300) -> BetaMessage:
    return BetaMessage(
        id="msg_test",
        type="message",
        role="assistant",
        model=model,
        content=[BetaTextBlock(type="text", text=text)],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=BetaUsage(
            input_tokens=inp,
            output_tokens=out,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            iterations=iterations,
        ),
    )


class FakeAnthropic:
    """Stands in for anthropic.Anthropic. `reply(params)` returns a message or an exception to raise."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[tuple[str, dict]] = []
        self.messages = SimpleNamespace(create=lambda **p: self._send("messages", p))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **p: self._send("beta", p)))

    def _send(self, path, params):
        self.calls.append((path, params))
        out = self.reply(params)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(clock, "utc_now", lambda: NOW)
    return NOW


@pytest.fixture
def fake(monkeypatch):
    """Installs a fake client (never a real one); tests set .reply. Without a key it must stay unused."""
    fake = FakeAnthropic(lambda params: message("{}"))
    monkeypatch.setattr(client_mod, "_anthropic", lambda cfg: fake)
    return fake


@pytest.fixture
def key(monkeypatch, no_keys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", TEST_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def set_config(monkeypatch, **update):
    cfg = load_llm_config().model_copy(update=update)
    for module in (client_mod, explain, news_tagger):
        monkeypatch.setattr(module, "load_llm_config", lambda: cfg)
    return cfg


def calls_rows() -> list[dict]:
    with sqlite3.connect(LLMStore().path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM calls ORDER BY id")]


def status_error(cls, code):
    request = httpx2.Request("POST", URL)
    return cls(f"status {code}", response=httpx2.Response(code, request=request), body=None)


# ---------------------------------------------------------------- client


def test_everything_is_off_without_a_key(fake, frozen):
    assert not llm.enabled()
    assert client_mod.complete("news_tagging", "system", "prompt") is None
    assert llm.tag_pending_news() == 0
    assert llm.explain_forecast(call_forecast()) is None
    assert llm.explain_recent_calls() == 0
    assert llm.write_brief("pre_market", {"calls": []}) is None
    assert llm.get_explanation("NSE:RELIANCE", "1D", 1, "analog_v1") is None
    assert fake.calls == []


def test_config_switches(monkeypatch, key):
    assert llm.enabled() and llm.enabled("news_tagging")
    set_config(monkeypatch, features={"news_tagging": True, "explanations": True, "briefs": False})
    assert llm.enabled("explanations") and not llm.enabled("briefs")
    set_config(monkeypatch, enabled=False)
    assert not llm.enabled() and not llm.enabled("news_tagging")


def test_client_is_created_lazily_from_settings(monkeypatch, key):
    monkeypatch.setattr(client_mod, "_client", None)
    cfg = load_llm_config()
    first = client_mod._anthropic(cfg)
    assert isinstance(first, anthropic.Anthropic)
    assert first.api_key == TEST_KEY and first.max_retries == cfg.max_retries
    assert client_mod._anthropic(cfg) is first


def test_every_call_is_logged_with_its_cost(fake, key, frozen):
    fake.reply = lambda p: message('{"items": []}', inp=1000, out=200, cw=4000, cr=10000)
    done = client_mod.complete("news_tagging", "SYSTEM TEXT", "PROMPT TEXT", json_schema={"type": "object"})
    assert done.ok and done.text == '{"items": []}'
    path, params = fake.calls[0]
    assert path == "messages" and params["model"] == "claude-haiku-4-5"
    assert params["system"] == [
        {"type": "text", "text": "SYSTEM TEXT", "cache_control": {"type": "ephemeral"}}
    ]
    assert params["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}}
    [row] = calls_rows()
    # Haiku 4.5 at $1/$5 per MTok; cache writes 1.25x input, cache reads 0.1x input
    assert row["cost_usd"] == pytest.approx((1000 * 1 + 200 * 5 + 4000 * 1.25 + 10000 * 0.1) / 1e6)
    tokens = ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")
    assert [row[t] for t in tokens] == [1000, 200, 4000, 10000]
    assert row["prompt"] == "PROMPT TEXT" and row["response"] == '{"items": []}'
    assert row["status"] == "ok" and row["purpose"] == "news_tagging" and row["day"] == "2026-09-23"
    with sqlite3.connect(LLMStore().path) as conn:
        sql = "SELECT text FROM system_prompts WHERE sha = ?"
        assert conn.execute(sql, (row["system_sha"],)).fetchone()[0] == "SYSTEM TEXT"
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_dated_model_ids_are_priced_by_prefix(fake, key, frozen):
    fake.reply = lambda p: message("x", model="claude-haiku-4-5-20251001", inp=1_000_000, out=0)
    client_mod.complete("news_tagging", "s", "p")
    assert calls_rows()[0]["cost_usd"] == pytest.approx(1.0)


def test_opus_jobs_use_effort_and_server_side_fallbacks(fake, key, frozen):
    fake.reply = lambda p: beta_message("text")
    client_mod.complete("explanations", "s", "p")
    path, params = fake.calls[0]
    assert path == "beta" and params["model"] == "claude-opus-5"
    assert params["betas"] == [client_mod.FALLBACK_BETA] and params["fallbacks"] == "default"
    assert params["output_config"] == {"effort": "medium"}
    assert calls_rows()[0]["cost_usd"] == pytest.approx((1500 * 5 + 300 * 25) / 1e6)


def test_fallback_attempts_are_costed_at_each_models_price(fake, key, frozen):
    iterations = [
        BetaMessageIterationUsage(
            type="message", input_tokens=1000, output_tokens=0, cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
        BetaFallbackMessageIterationUsage(
            type="fallback_message", model="claude-opus-4-8", input_tokens=1000, output_tokens=100,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    ]
    fake.reply = lambda p: beta_message("text", model="claude-opus-4-8", iterations=iterations)
    client_mod.complete("explanations", "s", "p")
    [row] = calls_rows()
    assert row["served_by"] == "claude-opus-4-8"
    assert row["cost_usd"] == pytest.approx((1000 * 5 + 1000 * 5 + 100 * 25) / 1e6)


def test_daily_budget_is_enforced_per_ist_day(fake, key, monkeypatch):
    set_config(monkeypatch, daily_budget_usd=0.01)
    store = LLMStore()
    base = {"ts": 0, "purpose": "news_tagging", "model": "claude-haiku-4-5", "system": "s", "prompt": "p"}
    store.log_call(**base, day="2026-09-23", status="ok", cost_usd=0.02)
    # 18:29 UTC is 23:59 IST on the 23rd: that day's budget is spent
    monkeypatch.setattr(clock, "utc_now", lambda: pd.Timestamp("2026-09-23 18:29", tz="UTC"))
    assert client_mod.complete("news_tagging", "s", "p") is None
    assert fake.calls == []
    assert calls_rows()[-1]["status"] == "budget_reached"
    # 18:31 UTC is 00:01 IST on the 24th: a fresh budget
    monkeypatch.setattr(clock, "utc_now", lambda: pd.Timestamp("2026-09-23 18:31", tz="UTC"))
    assert client_mod.complete("news_tagging", "s", "p").ok
    assert len(fake.calls) == 1 and calls_rows()[-1]["day"] == "2026-09-24"


@pytest.mark.parametrize(
    "error",
    [
        status_error(anthropic.RateLimitError, 429),
        status_error(anthropic.OverloadedError, 529),
        status_error(anthropic.AuthenticationError, 401),
        status_error(anthropic.NotFoundError, 404),
        status_error(anthropic.BadRequestError, 400),
        anthropic.APIConnectionError(request=httpx2.Request("POST", URL)),
    ],
    ids=lambda e: type(e).__name__,
)
def test_api_errors_are_logged_and_never_raise(fake, key, frozen, error):
    fake.reply = lambda p: error
    assert client_mod.complete("news_tagging", "s", "p") is None
    [row] = calls_rows()
    assert row["status"] == "error" and row["error"].startswith(type(error).__name__)
    assert row["cost_usd"] == 0 and TEST_KEY not in row["error"]


def test_refusal_and_cutoff_are_returned_and_costed(fake, key, frozen):
    fake.reply = lambda p: message("", stop="refusal", inp=500, out=0)
    refused = client_mod.complete("news_tagging", "s", "p")
    assert refused.status == "refusal" and not refused.ok
    fake.reply = lambda p: message('{"items": [', stop="max_tokens", out=4096)
    assert client_mod.complete("news_tagging", "s", "p").status == "max_tokens"
    assert [r["status"] for r in calls_rows()] == ["refusal", "max_tokens"]
    assert calls_rows()[1]["cost_usd"] > 0


# ---------------------------------------------------------------- news tagging


def news(title: str, minutes_ago: int = 10, instruments=()) -> NewsItem:
    url = f"https://news.test/{abs(hash(title))}"
    return NewsItem(
        id=news_id(title, url),
        title=title,
        url=url,
        source="Test feed",
        published_at=(NOW - timedelta(minutes=minutes_ago + 1)).to_pydatetime(),
        fetched_at=(NOW - timedelta(minutes=minutes_ago)).to_pydatetime(),
        instruments=list(instruments),
        sentiment=0.2,
        sentiment_method="lexicon",
        event_type="results",
        summary="Feed summary text.",
    )


def batch_items(params) -> list[dict]:
    return json.loads(params["messages"][0]["content"].split("\n\n", 1)[1])


def tag_all(params):
    items = [
        {
            "id": item["id"],
            "instruments": ["NSE:TCS"],
            "event_type": "order_win",
            "sentiment": 0.6,
            "magnitude": 2,
            "summary": "TCS wins a large contract.",
        }
        for item in batch_items(params)
    ]
    return message(json.dumps({"items": items}))


def stored(item_id: str) -> NewsItem:
    return next(n for n in NewsStore().query(limit=1000) if n.id == item_id)


def tag_row(item_id: str) -> dict | None:
    with sqlite3.connect(LLMStore().path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM news_tags WHERE news_id = ?", (item_id,)).fetchone()
    return dict(row) if row else None


def test_tagging_batches_and_writes_back(fake, key, frozen):
    items = [news(f"Headline {k}", minutes_ago=k, instruments=["NSE:INFY"]) for k in range(45)]
    NewsStore().add(items)
    fake.reply = tag_all
    assert llm.tag_pending_news() == 45
    assert [len(batch_items(p)) for _, p in fake.calls] == [20, 20, 5]
    assert batch_items(fake.calls[0][1])[0]["title"] == "Headline 0"  # newest first
    got = stored(items[0].id)
    assert got.sentiment_method == "claude" and got.sentiment == 0.6 and got.event_type == "order_win"
    assert got.summary == "TCS wins a large contract."
    assert got.instruments == ["NSE:INFY", "NSE:TCS"]  # Claude's instruments are added to the mapper's
    row = tag_row(items[0].id)
    assert row["magnitude"] == 2 and json.loads(row["instruments"]) == ["NSE:TCS"]
    assert row["prev_sentiment"] == 0.2 and row["prev_method"] == "lexicon"
    assert row["prev_summary"] == "Feed summary text."
    assert llm.tag_pending_news() == 0 and len(fake.calls) == 3  # nothing left to tag


def test_tagging_system_prompt_is_stable_and_lists_the_watchlist(fake, key, frozen):
    NewsStore().add([news("A"), news("B")])
    fake.reply = tag_all
    llm.tag_pending_news()
    system = fake.calls[0][1]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    watchlist = load_watchlist()
    assert system[0]["text"] == news_tagger.system_prompt(watchlist)
    assert news_tagger.system_prompt(watchlist) == news_tagger.system_prompt(load_watchlist())
    for inst in watchlist:
        assert inst.id in system[0]["text"] and all(alias in system[0]["text"] for alias in inst.aliases)
    fmt = fake.calls[0][1]["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False


def test_bad_output_is_repaired_or_rejected_per_item(fake, key, frozen):
    good, clamped, trimmed, bad_type, missing, omitted = (news(t) for t in "ABCDEF")
    NewsStore().add([good, clamped, trimmed, bad_type, missing, omitted])
    long_summary = " ".join(f"w{k}" for k in range(40))
    reply = {
        "items": [
            {"id": good.id, "instruments": ["NSE:TCS", "NSE:TCS", "NYSE:IBM"], "event_type": "results",
             "sentiment": -0.4, "magnitude": 1, "summary": "Profit fell."},
            {"id": good.id, "instruments": [], "event_type": "deal", "sentiment": 0.9, "magnitude": 3,
             "summary": "Duplicate, ignored."},
            {"id": clamped.id, "instruments": [{"id": "NSE:TCS"}, "NSE:INFY"], "event_type": "macro",
             "sentiment": 1.7, "magnitude": 5, "summary": "RBI holds rates."},
            {"id": trimmed.id, "instruments": [], "event_type": "other", "sentiment": 0, "magnitude": 0,
             "summary": long_summary},
            {"id": bad_type.id, "instruments": [], "event_type": "rumour", "sentiment": 0, "magnitude": 1,
             "summary": "x"},
            {"id": missing.id, "instruments": [], "event_type": "other", "sentiment": 0.1},
            {"id": "not-in-this-batch", "instruments": [], "event_type": "other", "sentiment": 0,
             "magnitude": 0, "summary": "x"},
        ]
    }
    fake.reply = lambda p: message(json.dumps(reply))
    assert llm.tag_pending_news() == 3
    assert stored(good.id).sentiment == -0.4 and stored(good.id).event_type == "results"
    assert json.loads(tag_row(good.id)["instruments"]) == ["NSE:TCS"]
    assert stored(clamped.id).sentiment == 1.0 and tag_row(clamped.id)["magnitude"] == 3
    assert json.loads(tag_row(clamped.id)["instruments"]) == ["NSE:INFY"]
    assert stored(trimmed.id).summary == " ".join(f"w{k}" for k in range(25)) + "…"
    for rejected in (bad_type, missing, omitted):
        assert stored(rejected.id).sentiment_method == "lexicon"  # untouched, retried next run
        assert tag_row(rejected.id)["attempts"] == 1 and tag_row(rejected.id)["tagged_at"] is None


def test_unparseable_output_and_give_up_after_max_attempts(fake, key, frozen, monkeypatch):
    set_config(monkeypatch, tag_max_attempts=2)
    item = news("Unlucky headline")
    NewsStore().add([item])
    fake.reply = lambda p: message("this is not json")
    assert llm.tag_pending_news() == 0
    fake.reply = lambda p: message("", stop="refusal")
    assert llm.tag_pending_news() == 0
    assert tag_row(item.id)["attempts"] == 2
    assert llm.tag_pending_news() == 0
    assert len(fake.calls) == 2  # given up: left to the lexicon
    assert stored(item.id).sentiment_method == "lexicon"


def test_only_fresh_untagged_news_is_tagged(fake, key, frozen):
    fresh, stale = news("Fresh", minutes_ago=30), news("Old", minutes_ago=25 * 60)
    done = news("Done").model_copy(update={"sentiment_method": "claude"})
    NewsStore().add([fresh, stale, done])
    fake.reply = tag_all
    assert llm.tag_pending_news() == 1
    assert [item["id"] for item in batch_items(fake.calls[0][1])] == [fresh.id]
    assert stored(stale.id).sentiment_method == "lexicon"  # never backfilled


def test_tagging_stops_when_the_budget_is_spent_or_the_api_fails(fake, key, frozen, monkeypatch):
    NewsStore().add([news(f"H{k}", minutes_ago=k) for k in range(30)])
    fake.reply = lambda p: status_error(anthropic.InternalServerError, 500)
    assert llm.tag_pending_news() == 0 and len(fake.calls) == 1  # the next batch waits for the next run
    set_config(monkeypatch, daily_budget_usd=0.0)
    fake.reply = tag_all
    assert llm.tag_pending_news() == 0 and len(fake.calls) == 1
    assert all(tag_row(n.id) is None for n in NewsStore().query(limit=100))  # failures cost no attempts


# ---------------------------------------------------------------- explanations

REF = pd.Timestamp("2026-09-22 03:45", tz="UTC")  # a 1D bar's session open (09:15 IST)
GOOD_TEXT = (
    "Bullish over 3 bars: p_up is 56% against a 51% base rate from 64 analogs, led by a certified hammer "
    "that hit 58% in training. A close below 2,790 invalidates the call. The risk is a reward/risk of only "
    "0.43 and an interval of 52% to 61%."
)


def call_forecast(abstain=False, instrument="NSE:RELIANCE", ref=REF) -> dict:
    ref_time = int(ref.timestamp())
    times = [ref_time + 86400 * k for k in (1, 2, 3)]
    return Forecast(
        instrument=instrument,
        tf="1D",
        method="analog_v1",
        made_at=ref_time + 23400,
        ref_time=ref_time,
        ref_close=2845.1234,
        horizon_bars=3,
        p_up=0.5634,
        p_up_ci=(0.52, 0.61),
        base_rate=0.51,
        abstain=abstain,
        abstain_reason="edge below minimum: |p_up - base_rate| = 0.020 < 0.03" if abstain else None,
        confidence=None if abstain else "medium",
        expected_move_pct=0.84,
        ghost_candles=[dict(time=t, open=2850, high=2870, low=2840, close=2860, volume=0) for t in times],
        bands=[dict(time=t, p10=2800.5, p50=2869.0, p90=2921.7) for t in times],
        invalidation=None if abstain else 2790.0,
        trade=None if abstain else dict(entry=2845.12, stop=2790.0, target=2869.0, reward_risk=0.43),
        drivers=[
            dict(
                name="Hammer",
                effect="bullish",
                detail="Confirmed on the reference bar. Scorecard (train, 3 bars): hit 58.0% vs base 51.0%, "
                "n=212, q=0.041, certified",
            )
        ],
        n_analogs=64,
    ).model_dump()


def test_abstaining_forecasts_are_never_explained(fake, key, frozen):
    assert llm.explain_forecast(call_forecast(abstain=True)) is None
    assert fake.calls == []


def test_explanation_is_cached_by_forecast_key(fake, key, frozen):
    fake.reply = lambda p: beta_message(GOOD_TEXT)
    forecast = call_forecast()
    headlines = [{"title": "Reliance wins 5G order", "source": "ET", "time_ist": "2026-09-22 08:10"}]
    assert llm.explain_forecast(forecast, {"headlines": headlines}) == GOOD_TEXT
    assert llm.explain_forecast(forecast) == GOOD_TEXT  # cache hit
    assert len(fake.calls) == 1
    assert llm.get_explanation("NSE:RELIANCE", "1D", forecast["ref_time"], "analog_v1") == GOOD_TEXT
    assert llm.get_explanation("NSE:RELIANCE", "1D", forecast["ref_time"], "other_method") is None
    facts = json.loads(fake.calls[0][1]["messages"][0]["content"].removeprefix("FACTS:\n"))
    assert facts["p_up"] == 0.563 and facts["base_rate"] == 0.51 and facts["call"] == "bullish"
    assert facts["trade"] == {"entry": 2845.12, "stop": 2790.0, "target": 2869.0, "reward_risk": 0.43}
    assert facts["reference_close"] == 2845.12 and facts["reference_bar_open_ist"] == "2026-09-22 09:15"
    assert facts["headlines"] == headlines and facts["drivers"][0]["name"] == "Hammer"
    assert "explanation" not in facts and "abstain_reason" not in facts


def test_invented_numbers_are_rejected(fake, key, frozen):
    fake.reply = lambda p: beta_message(GOOD_TEXT + " I put the odds at 77% and see 3,050 next.")
    forecast = call_forecast()
    assert llm.explain_forecast(forecast) is None
    assert llm.get_explanation("NSE:RELIANCE", "1D", forecast["ref_time"], "analog_v1") is None
    row = calls_rows()[0]
    assert row["status"] == "rejected" and "77" in row["error"] and "3,050" in row["error"]


def test_grounding_allows_rounding_and_percentages():
    facts = {"p_up": 0.563, "stop": 2790.0, "close": 2845.12, "detail": "hit 58.0% vs base 51.0%"}
    assert ungrounded_numbers("56% vs 51%, stop 2,790, close 2,845.1, hit 58%", facts) == []
    assert ungrounded_numbers("odds of 77% and a target of 2,950.5", facts) == ["77", "2,950.5"]


def test_daily_explanation_cap(fake, key, frozen, monkeypatch):
    set_config(monkeypatch, max_explanations_per_day=1)
    fake.reply = lambda p: status_error(anthropic.OverloadedError, 529)
    assert llm.explain_forecast(call_forecast()) is None  # a failed call doesn't use up the cap
    fake.reply = lambda p: beta_message(GOOD_TEXT)
    assert llm.explain_forecast(call_forecast()) == GOOD_TEXT
    assert llm.explain_forecast(call_forecast(instrument="NSE:TCS")) is None
    assert len(fake.calls) == 2
    assert llm.explain_forecast(call_forecast()) == GOOD_TEXT  # cache hits are not capped


def test_explain_recent_calls_skips_abstentions_and_baselines(fake, key, monkeypatch):
    candles = synthetic_candles("1D", "2026-06-01", "2026-09-22", seed=3)
    k = len(candles) - 5
    ref = candles["ts"].iloc[k]
    closed = get_calendar().bar_close_time("NSE", ref, "1D") + pd.Timedelta(minutes=1)
    monkeypatch.setattr(clock, "utc_now", lambda: closed)
    ledger = Ledger()
    times = [int(t.timestamp()) for t in candles["ts"].iloc[k + 1 : k + 4]]

    def at_ref(forecast: dict, **update) -> Forecast:
        forecast.update(ref_time=int(ref.timestamp()), made_at=int(closed.timestamp()), **update)
        for step, t in zip(forecast["ghost_candles"] + forecast["bands"], times * 2, strict=True):
            step["time"] = t
        return Forecast(**forecast)

    ledger.record(at_ref(call_forecast()))
    ledger.record(at_ref(call_forecast(abstain=True, instrument="NSE:TCS")))
    ledger.record(at_ref(call_forecast(instrument="NSE:INFY"), method="baseline_persistence"))
    fake.reply = lambda p: beta_message("Bullish: hammer and uptrend agree. A close below the stop ends it.")
    assert llm.explain_recent_calls(limit=10) == 1
    facts = json.loads(fake.calls[0][1]["messages"][0]["content"].removeprefix("FACTS:\n"))
    assert facts["instrument"] == "NSE:RELIANCE" and "headlines" in facts
    assert llm.explain_recent_calls(limit=10) == 0 and len(fake.calls) == 1  # already explained


# ---------------------------------------------------------------- briefs

BRIEF_FACTS = {
    "date": "2026-09-24",
    "calls": [{"instrument": "NSE:RELIANCE", "direction": "bullish", "p_up": 0.563, "invalidation": 2790.0}],
    "headlines": ["RBI keeps repo rate unchanged"],
}


def test_brief_from_facts(fake, key, frozen):
    fake.reply = lambda p: beta_message("- Reliance: bullish (56%), stop 2,790.\n- RBI held rates.")
    text = llm.write_brief("pre_market", BRIEF_FACTS)
    assert text.startswith("- Reliance")
    path, params = fake.calls[0]
    assert path == "beta" and params["model"] == "claude-opus-5"
    prompt = params["messages"][0]["content"]
    assert "08:45 IST" in prompt and '"p_up": 0.563' in prompt


def test_brief_with_invented_numbers_or_bad_kind_is_none(fake, key, frozen):
    fake.reply = lambda p: beta_message("Nifty should add 150 points today.")
    assert llm.write_brief("post_market", BRIEF_FACTS) is None
    assert calls_rows()[0]["status"] == "rejected"
    assert llm.write_brief("midday", BRIEF_FACTS) is None
    assert len(fake.calls) == 1
