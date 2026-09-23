import httpx
import pandas as pd
import pytest
import respx
from test_alerts_helpers import CHAT_ID, SEND_URL, TOKEN, call, ist, record

from candly.alerts import briefs, jobs, rules, telegram
from candly.alerts.config import AlertsConfig, load_alerts_config
from candly.alerts.delivery import AlertStore, default_alerts_path, is_quiet
from candly.api.routes import platform
from candly.core.schema import empty_candles
from candly.core.settings import get_settings
from candly.data import clock

AFTER_CLOSE = ist("2026-09-23 16:00")  # Wed, after the NSE close: 1D calls on the 23rd are fresh
OK_HEALTH = {"ingest": {"status": "ok", "reason": None}, "markets": [{"exchange": "NSE", "open": False}]}
REAL_HEALTH = rules._health


@pytest.fixture
def telegram_keys(monkeypatch, no_keys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT_ID)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def outbox(monkeypatch, telegram_keys):
    """Captures sent messages instead of calling Telegram."""
    box: list[str] = []
    monkeypatch.setattr(telegram, "send", lambda text: box.append(text) or True)
    return box


@pytest.fixture(autouse=True)
def quiet_background(monkeypatch):
    """Health is fine and there are no candles unless a test says otherwise."""
    monkeypatch.setattr(rules, "_health", lambda: OK_HEALTH)
    monkeypatch.setattr(rules, "load_candles", lambda *args, **kwargs: empty_candles())


def use_config(monkeypatch, **changes) -> AlertsConfig:
    cfg = load_alerts_config().model_copy(update=changes)
    for module in (rules, briefs, jobs):
        monkeypatch.setattr(module, "load_alerts_config", lambda: cfg)
    return cfg


def at(monkeypatch, now: pd.Timestamp) -> pd.Timestamp:
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    return now


# --- config and plumbing --------------------------------------------------------------------------


def test_config_file_defaults():
    cfg = load_alerts_config()
    assert cfg.enabled and cfg.timeframes == ("1D", "1h") and cfg.min_confidence == "medium"
    assert (cfg.quiet_hours.start.isoformat(), cfg.quiet_hours.end.isoformat()) == ("23:45:00", "08:30:00")
    assert cfg.max_per_hour == 12
    assert (cfg.briefs.pre_market.isoformat(), cfg.briefs.post_market.isoformat()) == ("08:45:00", "16:15:00")
    assert all(dict(cfg.types).values())


def test_disabled_without_telegram_keys(monkeypatch):
    record(monkeypatch, call())
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(url__startswith="https://api.telegram.org/")
        counts = rules.check_and_send(AFTER_CLOSE)
        assert jobs.run_alert_checks()["enabled"] is False
        assert jobs.send_pre_market_brief(ist("2026-09-24 08:45")) is False
        assert jobs.send_post_market_review(ist("2026-09-23 16:15")) is False
        assert jobs.send_test_alert() == {"sent": False, "detail": jobs.NOT_CONFIGURED}
    assert counts["enabled"] is False and counts["sent"] == 0
    assert not route.called
    assert not default_alerts_path().exists()


def test_disabled_by_config(monkeypatch, outbox):
    record(monkeypatch, call())
    use_config(monkeypatch, enabled=False)
    assert rules.check_and_send(AFTER_CLOSE)["enabled"] is False
    assert outbox == []


def test_end_to_end_through_the_bot_api(monkeypatch, telegram_keys):
    record(monkeypatch, call())
    at(monkeypatch, AFTER_CLOSE)
    with respx.mock() as mock:
        route = mock.post(SEND_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        assert rules.check_and_send(AFTER_CLOSE)["new_call"] == 1
        assert rules.check_and_send(AFTER_CLOSE)["sent"] == 0
    assert route.call_count == 1
    assert "New call: Reliance Industries (NSE:RELIANCE) · 1D" in route.calls.last.request.content.decode()


# --- new calls ------------------------------------------------------------------------------------


def test_new_call_message_and_dedupe(monkeypatch, outbox):
    record(monkeypatch, call())
    at(monkeypatch, AFTER_CLOSE)
    counts = rules.check_and_send(AFTER_CLOSE)
    assert counts["new_call"] == 1 and counts["sent"] == 1
    assert outbox == [
        "<b>New call: Reliance Industries (NSE:RELIANCE) · 1D</b>\n"
        "▲ Bullish · confidence medium\n"
        "Entry 2,950.40 · Stop 2,901.10 · Target 3,012.00 · R:R 1.25\n"
        "p(up) 62% vs base rate 53%, over the next 3 bars\n"
        "Next expiry 29 Sep (monthly), 4 trading days away\n"
        "Chart: http://localhost:5173/chart/NSE:RELIANCE/1D\n"
        "<i>Educational — not investment advice.</i>"
    ]
    assert rules.check_and_send(AFTER_CLOSE + pd.Timedelta(minutes=5))["sent"] == 0
    assert len(outbox) == 1


def test_no_alert_for_abstaining_non_tradable_weak_or_other_calls(monkeypatch, outbox):
    record(monkeypatch, call("NSE:RELIANCE", abstain=True))  # p_up 0.62 but abstaining
    record(monkeypatch, call("NSE:INDIAVIX", entry=12.5))  # not tradable
    record(monkeypatch, call("NSE:TCS", confidence="low"))  # below min_confidence
    record(monkeypatch, call("NSE:INFY", method="baseline_base_rate"))  # not analog_v1
    record(monkeypatch, call("NSE:SBIN", tf="15m", ref="2026-09-23 15:00", steps=1))  # tf not alerted
    at(monkeypatch, AFTER_CLOSE)
    assert rules.check_and_send(AFTER_CLOSE)["sent"] == 0
    assert outbox == []


def test_min_confidence_is_configurable(monkeypatch, outbox):
    record(monkeypatch, call("NSE:TCS", confidence="low"))
    use_config(monkeypatch, min_confidence="low")
    assert rules.check_and_send(at(monkeypatch, AFTER_CLOSE))["new_call"] == 1


def test_call_is_not_sent_once_its_entry_bar_has_closed(monkeypatch, outbox):
    record(monkeypatch, call())
    stale = at(monkeypatch, ist("2026-09-24 15:31"))  # the 24th's daily bar closed at 15:30
    assert rules.check_and_send(stale)["sent"] == 0


def test_escaping_in_call_messages(monkeypatch, outbox):
    record(monkeypatch, call("NSE:LT", entry=3600.0, bullish=False))
    rules.check_and_send(at(monkeypatch, AFTER_CLOSE))
    assert "Larsen &amp; Toubro (NSE:LT)" in outbox[0]
    assert "&" not in outbox[0].replace("&amp;", "")
    assert "▼ Bearish" in outbox[0]


def test_hourly_call(monkeypatch, outbox):
    record(monkeypatch, call("NSE:HDFCBANK", tf="1h", ref="2026-09-23 10:15", entry=1650.0))
    now = at(monkeypatch, ist("2026-09-23 11:20"))
    assert rules.check_and_send(now)["new_call"] == 1
    assert "/chart/NSE:HDFCBANK/1h" in outbox[0]


# --- quiet hours, cap, failures -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("local", "quiet"),
    [("23:44", False), ("23:45", True), ("02:00", True), ("08:29", True), ("08:30", False), ("12:00", False)],
)
def test_quiet_hours_wrap_midnight(local, quiet):
    assert is_quiet(AlertsConfig(), ist(f"2026-09-23 {local}")) is quiet


def test_quiet_hours_defer_until_they_end(monkeypatch, outbox):
    record(monkeypatch, call())
    counts = rules.check_and_send(at(monkeypatch, ist("2026-09-23 23:50")))
    assert counts["sent"] == 0 and counts["deferred"] == 1 and outbox == []
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-24 08:35")))["new_call"] == 1


def test_hourly_cap_defers_the_rest(monkeypatch, outbox):
    use_config(monkeypatch, max_per_hour=2)
    for instrument in ("NSE:RELIANCE", "NSE:TCS", "NSE:INFY"):
        record(monkeypatch, call(instrument))
    first = rules.check_and_send(at(monkeypatch, AFTER_CLOSE))
    assert (first["sent"], first["deferred"]) == (2, 1)
    assert rules.check_and_send(at(monkeypatch, AFTER_CLOSE + pd.Timedelta(minutes=30)))["deferred"] == 1
    later = rules.check_and_send(at(monkeypatch, AFTER_CLOSE + pd.Timedelta(minutes=61)))
    assert later["sent"] == 1 and len(outbox) == 3


def test_failed_sends_are_retried_and_stop_the_run(monkeypatch, telegram_keys):
    record(monkeypatch, call("NSE:RELIANCE"))
    record(monkeypatch, call("NSE:TCS"))
    attempts = []
    monkeypatch.setattr(telegram, "send", lambda text: attempts.append(text) or False)
    counts = rules.check_and_send(at(monkeypatch, AFTER_CLOSE))
    assert counts["failed"] == 2 and counts["sent"] == 0
    assert len(attempts) == 1  # the second call was not tried once Telegram failed
    monkeypatch.setattr(telegram, "send", lambda text: True)
    assert rules.check_and_send(AFTER_CLOSE + pd.Timedelta(minutes=5))["new_call"] == 2


def test_a_crashing_check_does_not_stop_the_others(monkeypatch, outbox):
    record(monkeypatch, call())

    def broken():
        raise RuntimeError("health exploded")

    monkeypatch.setattr(rules, "_health", broken)
    counts = rules.check_and_send(at(monkeypatch, AFTER_CLOSE))
    assert counts["errors"] == 1 and counts["new_call"] == 1


def test_type_toggles(monkeypatch, outbox):
    record(monkeypatch, call())
    cfg = load_alerts_config()
    use_config(monkeypatch, types=cfg.types.model_copy(update={"new_call": False}))
    assert rules.check_and_send(at(monkeypatch, AFTER_CLOSE))["sent"] == 0


# --- stop hits ------------------------------------------------------------------------------------


def candles_with(rows: list[dict]):
    frame = pd.DataFrame(rows)
    frame["ts"] = [ist(t) for t in frame["ts"]]
    frame["volume"], frame["oi"] = 1000.0, float("nan")
    frame = frame[["ts", "open", "high", "low", "close", "volume", "oi"]]

    def load(instrument_id, tf, start=None, end=None):
        return frame[frame["ts"] >= start].reset_index(drop=True)

    return load


def test_stop_hit_after_a_sent_call(monkeypatch, outbox):
    record(monkeypatch, call())
    rules.check_and_send(at(monkeypatch, AFTER_CLOSE))
    daily = candles_with(
        [
            {"ts": "2026-09-23 09:15", "open": 2940, "high": 2960, "low": 2930, "close": 2950.4},
            {"ts": "2026-09-24 09:15", "open": 2945, "high": 2955, "low": 2890, "close": 2910.3},
        ]
    )
    monkeypatch.setattr(rules, "load_candles", daily)
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-24 15:20")))["stop_hit"] == 0  # bar still open
    counts = rules.check_and_send(at(monkeypatch, ist("2026-09-24 15:35")))
    assert counts["stop_hit"] == 1
    assert outbox[-1] == (
        "<b>Stop hit: Reliance Industries (NSE:RELIANCE) · 1D</b>\n"
        "▲ Bullish call from the 23 Sep bar: stop 2,901.10 crossed\n"
        "Bar 24 Sep: low 2,890.00, close 2,910.30\n"
        "Entry was 2,950.40 (-1.67% to the stop)\n"
        "Chart: http://localhost:5173/chart/NSE:RELIANCE/1D\n"
        "<i>Educational — not investment advice.</i>"
    )
    assert rules.check_and_send(ist("2026-09-24 15:40"))["sent"] == 0
    assert AlertStore().watching() == []


def test_bearish_stop_uses_the_high(monkeypatch, outbox):
    record(monkeypatch, call("NSE:TCS", "1h", "2026-09-23 10:15", entry=4000.0, stop=4040.0, bullish=False))
    rules.check_and_send(at(monkeypatch, ist("2026-09-23 11:16")))
    bar = {"ts": "2026-09-23 11:15", "open": 4001, "high": 4041, "low": 3990, "close": 4020}
    monkeypatch.setattr(rules, "load_candles", candles_with([bar]))
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-23 12:16")))["stop_hit"] == 1
    assert "Bar 23 Sep 11:15 IST: high 4,041.00" in outbox[-1]


def test_stop_watch_ends_after_n_bars_without_a_hit(monkeypatch, outbox):
    use_config(monkeypatch, stop_watch_bars=2)
    record(monkeypatch, call())
    rules.check_and_send(at(monkeypatch, AFTER_CLOSE))
    assert len(AlertStore().watching()) == 1
    later_hit = candles_with(
        [
            {"ts": "2026-09-24 09:15", "open": 2950, "high": 2980, "low": 2940, "close": 2970},
            {"ts": "2026-09-25 09:15", "open": 2970, "high": 2990, "low": 2950, "close": 2985},
            {"ts": "2026-09-28 09:15", "open": 2985, "high": 2990, "low": 2800, "close": 2850},
        ]
    )
    monkeypatch.setattr(rules, "load_candles", later_hit)
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-28 16:00")))["stop_hit"] == 0
    assert AlertStore().watching() == []


def test_unsent_calls_are_not_watched(monkeypatch, outbox):
    record(monkeypatch, call("NSE:TCS", confidence="low"))
    rules.check_and_send(at(monkeypatch, AFTER_CLOSE))
    assert AlertStore().watching() == []


# --- expiry ---------------------------------------------------------------------------------------


def test_expiry_day_reminder_once(monkeypatch, outbox):
    morning = at(monkeypatch, ist("2026-09-29 09:20"))  # last Tuesday: NSE monthly expiry
    assert rules.check_and_send(morning)["expiry_today"] == 1
    text = outbox[0]
    assert text.startswith("<b>Expiry today · Tue 29 Sep</b>\n")
    assert "• Nifty 50 (NSE:NIFTY50): monthly" in text
    assert "• Nifty Bank (NSE:BANKNIFTY): monthly" in text
    assert "• Reliance Industries (NSE:RELIANCE): monthly" in text
    assert "INDIAVIX" not in text and "SENSEX" not in text
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-29 10:20")))["sent"] == 0


def test_no_expiry_reminder_after_the_close_or_on_other_days(monkeypatch, outbox):
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-29 15:40")))["sent"] == 0
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-23 09:20")))["sent"] == 0


def test_expiry_reminder_waits_for_quiet_hours_to_end(monkeypatch, outbox):
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-29 00:10")))["deferred"] == 1
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-29 09:05")))["expiry_today"] == 1


# --- data paused ----------------------------------------------------------------------------------


def health(status: str, open_: bool) -> dict:
    reason = platform.INGEST_NOT_CONNECTED if status == "blocked" else None
    return {
        "ingest": {"status": status, "reason": reason},
        "markets": [{"exchange": "NSE", "open": open_}, {"exchange": "MCX", "open": open_}],
    }


def test_data_paused_once_per_incident_then_recovered(monkeypatch, outbox):
    report = {"value": health("blocked", True)}
    monkeypatch.setattr(rules, "_health", lambda: report["value"])
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-23 10:00")))["data_paused"] == 1
    assert outbox[0] == (
        "<b>Data paused</b>\n"
        "Fyers not connected — log in to resume data updates\n"
        "Open now: NSE, MCX. Forecasts and alerts wait for fresh bars.\n"
        "Since 23 Sep 10:00 IST"
    )
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-23 10:05")))["sent"] == 0
    report["value"] = health("ok", True)
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-23 10:30")))["data_paused"] == 1
    assert outbox[1] == (
        "<b>Data resumed</b>\nData updates can run again (paused 23 Sep 10:00 IST to 23 Sep 10:30 IST)."
    )
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-23 10:35")))["sent"] == 0
    report["value"] = health("blocked", True)  # a new incident
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-23 11:00")))["data_paused"] == 1


def test_no_data_alert_while_markets_are_closed(monkeypatch, outbox):
    monkeypatch.setattr(rules, "_health", lambda: health("blocked", False))
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-26 12:00")))["sent"] == 0
    assert AlertStore().get_state(rules.PAUSED) is None


def test_incident_resolved_before_anyone_was_told_ends_silently(monkeypatch, outbox):
    report = {"value": health("blocked", True)}
    monkeypatch.setattr(rules, "_health", lambda: report["value"])
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-23 23:50")))["deferred"] == 1  # MCX open, quiet
    report["value"] = health("ok", False)
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-24 09:05")))["data_paused"] == 0
    assert not any("Data" in text for text in outbox)
    assert AlertStore().get_state(rules.PAUSED) is None


def test_data_paused_reads_the_real_health_logic(monkeypatch, outbox):
    monkeypatch.setattr(rules, "_health", REAL_HEALTH)
    monkeypatch.setenv("DATA_SOURCE", "fyers")  # forced to Fyers without keys: ingest is blocked
    get_settings.cache_clear()
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-23 10:00")))["data_paused"] == 1
    assert platform.INGEST_NO_KEYS in outbox[0]
    monkeypatch.setenv("DATA_SOURCE", "yahoo")
    get_settings.cache_clear()
    assert rules.check_and_send(at(monkeypatch, ist("2026-09-23 10:05")))["data_paused"] == 1
    assert outbox[1].startswith("<b>Data resumed</b>")


# --- jobs -----------------------------------------------------------------------------------------


def test_register_alert_jobs_uses_config_times():
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()
    jobs.register_alert_jobs(scheduler)
    triggers = {job.id: str(job.trigger) for job in scheduler.get_jobs()}
    assert set(triggers) == {"pre_market_brief", "post_market_review"}
    assert "hour='8', minute='45'" in triggers["pre_market_brief"]
    assert "hour='16', minute='15'" in triggers["post_market_review"]


def test_test_alert(monkeypatch, outbox):
    assert jobs.send_test_alert() == {"sent": True, "detail": None}
    assert "test alert" in outbox[0]
