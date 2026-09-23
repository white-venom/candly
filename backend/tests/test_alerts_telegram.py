import json
import logging

import httpx
import pytest
import respx
from test_alerts_helpers import CHAT_ID, SEND_URL, TOKEN

from candly.alerts import telegram
from candly.alerts.text import esc
from candly.core.settings import get_settings

OK = httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})


@pytest.fixture
def telegram_keys(monkeypatch, no_keys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT_ID)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def sleeps(monkeypatch):
    waited: list[float] = []
    monkeypatch.setattr(telegram, "_sleep", waited.append)
    return waited


def test_disabled_without_keys_makes_no_request():
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(url__startswith="https://api.telegram.org/")
        assert telegram.send("hello") is False
    assert not route.called


@respx.mock
def test_send_posts_an_html_message(telegram_keys):
    route = respx.post(SEND_URL).mock(return_value=OK)
    assert telegram.send("<b>candly</b> &amp; friends") is True
    body = json.loads(route.calls.last.request.content)
    assert body["chat_id"] == CHAT_ID and body["parse_mode"] == "HTML"
    assert body["text"] == "<b>candly</b> &amp; friends"
    assert body["link_preview_options"] == {"is_disabled": True}


@respx.mock
def test_429_waits_retry_after_then_succeeds(telegram_keys, sleeps):
    limited = httpx.Response(
        429, json={"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 3",
                   "parameters": {"retry_after": 3}}
    )
    route = respx.post(SEND_URL).mock(side_effect=[limited, OK])
    assert telegram.send("hi") is True
    assert route.call_count == 2 and sleeps == [3.0]


@respx.mock
def test_long_retry_after_gives_up_without_blocking(telegram_keys, sleeps):
    route = respx.post(SEND_URL).mock(
        return_value=httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 900}})
    )
    assert telegram.send("hi") is False
    assert route.call_count == 1 and sleeps == []


@respx.mock
def test_server_errors_retry_with_backoff(telegram_keys, sleeps):
    route = respx.post(SEND_URL).mock(side_effect=[httpx.Response(502), httpx.Response(503), OK])
    assert telegram.send("hi") is True
    assert route.call_count == 3 and sleeps == [1.0, 2.0]


@respx.mock
def test_client_errors_are_not_retried(telegram_keys, sleeps):
    rejected = httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities"})
    route = respx.post(SEND_URL).mock(return_value=rejected)
    assert telegram.send("<b>broken") is False
    assert route.call_count == 1 and sleeps == []


@respx.mock
def test_token_never_reaches_logs(telegram_keys, sleeps, caplog):
    caplog.set_level(logging.DEBUG)
    refusals = [httpx.ConnectError(f"cannot reach {SEND_URL}") for _ in range(telegram.MAX_RETRIES + 1)]
    respx.post(SEND_URL).mock(side_effect=[OK, *refusals])
    assert telegram.send("first") is True
    assert telegram.send("second") is False
    assert "HTTP Request" in caplog.text  # httpx's own request line is still logged, but scrubbed
    assert TOKEN not in caplog.text
    assert telegram.REDACTED in caplog.text


def test_redact_hides_the_token(telegram_keys):
    redacted = telegram.redact(f"POST {SEND_URL} failed")
    assert redacted == "POST https://api.telegram.org/bot<redacted>/sendMessage failed"


def test_html_escaping():
    assert esc("Larsen & Toubro <b>up</b>") == "Larsen &amp; Toubro &lt;b&gt;up&lt;/b&gt;"


def test_long_text_splits_on_line_breaks():
    lines = [f"<b>line {i}</b> " + "x" * 90 for i in range(100)]
    parts = telegram.chunks("\n".join(lines))
    assert len(parts) > 1
    assert all(len(p) <= telegram.MAX_LENGTH for p in parts)
    assert "\n".join(parts).split("\n") == lines
    assert all(p.count("<b>") == p.count("</b>") for p in parts)


def test_a_single_huge_line_splits_on_spaces():
    words = " ".join(["word"] * 2000)
    parts = telegram.chunks(words)
    assert all(len(p) <= telegram.MAX_LENGTH for p in parts)
    assert " ".join(parts).split() == words.split()


@respx.mock
def test_every_part_of_a_long_message_is_sent(telegram_keys):
    route = respx.post(SEND_URL).mock(return_value=OK)
    assert telegram.send("\n".join("y" * 100 for _ in range(100))) is True
    assert route.call_count == 3
