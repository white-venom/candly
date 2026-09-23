"""Telegram Bot API `sendMessage` in HTML parse mode.

The bot token sits in the request URL, so it is scrubbed from everything this module logs and from
httpx's own request log line. `send` never raises: it returns False when Telegram isn't configured,
rejects the message, or can't be reached.
"""

from __future__ import annotations

import logging
import time

import httpx

from candly.core.settings import get_settings

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
MAX_LENGTH = 4096  # Telegram's limit per message, after entity parsing
MAX_RETRIES = 3
MAX_WAIT = 60.0  # a longer retry_after gives up; the caller retries on its next run
TIMEOUT = 15.0
REDACTED = "<redacted>"
_sleep = time.sleep


def _token() -> str:
    return get_settings().telegram_bot_token.get_secret_value()


def redact(text: str, token: str | None = None) -> str:
    token = _token() if token is None else token
    return text.replace(token, REDACTED) if token else text


class _RedactToken(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        token = _token()
        if token:
            message = record.getMessage()
            if token in message:
                record.msg, record.args = message.replace(token, REDACTED), None
        return True


_HTTPX_FILTER = _RedactToken()


def _guard_httpx_log() -> None:
    logger = logging.getLogger("httpx")
    if _HTTPX_FILTER not in logger.filters:
        logger.addFilter(_HTTPX_FILTER)


def chunks(text: str, limit: int = MAX_LENGTH) -> list[str]:
    """Split on line breaks (then spaces) so no HTML tag or entity is cut in half."""
    out: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                out.append(current)
                current = ""
            out.append(line[:cut])
            line = line[cut:].lstrip(" ")
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            out.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        out.append(current)
    return out


def _retry_after(response: httpx.Response) -> float:
    try:
        return float(response.json()["parameters"]["retry_after"])
    except (ValueError, KeyError, TypeError):
        return 1.0


def _description(response: httpx.Response) -> str:
    try:
        return str(response.json().get("description", ""))
    except (ValueError, AttributeError):
        return ""


def _send_one(client: httpx.Client, url: str, token: str, chat_id: str, text: str) -> bool:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    problem = "no attempt"
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.post(url, json=payload)
        except httpx.HTTPError as exc:
            problem, wait = f"{type(exc).__name__}: {exc}", 2.0**attempt
        else:
            if response.status_code == 200:
                return True
            problem = f"HTTP {response.status_code} {_description(response)}".strip()
            if response.status_code == 429:
                wait = _retry_after(response)
            elif response.status_code >= 500:
                wait = 2.0**attempt
            else:
                log.warning("Telegram rejected the message: %s", redact(problem, token))
                return False
        if attempt == MAX_RETRIES or wait > MAX_WAIT:
            break
        _sleep(wait)
    log.warning("Telegram send failed: %s", redact(problem, token))
    return False


def send(text: str) -> bool:
    """Send an HTML-formatted message (callers escape their text) to the configured chat. Long text
    goes out as several messages; returns True only if every part was delivered."""
    settings = get_settings()
    if not settings.has_telegram or not text.strip():
        return False
    token = settings.telegram_bot_token.get_secret_value()
    _guard_httpx_log()
    url = f"{API_BASE}/bot{token}/sendMessage"
    chat_id = settings.telegram_chat_id
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            return all(_send_one(client, url, token, chat_id, part) for part in chunks(text))
    except Exception as exc:  # never let an alert take down a scheduler job, and never echo the URL
        log.warning("Telegram send failed: %s", redact(f"{type(exc).__name__}: {exc}", token))
        return False
