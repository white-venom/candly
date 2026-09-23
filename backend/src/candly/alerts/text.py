"""Formatting shared by alerts and briefs. Every value that comes from data goes through `esc`."""

from __future__ import annotations

import html
from urllib.parse import quote

import pandas as pd

from candly.core.calendar import IST
from candly.core.instruments import UnknownInstrument, get_instrument
from candly.core.settings import get_settings

DISCLAIMER = "<i>Educational — not investment advice.</i>"


def esc(value: object) -> str:
    """Escape for Telegram's HTML parse mode (it needs &, < and > escaped)."""
    return html.escape(str(value), quote=False)


def price(value: float) -> str:
    return f"{value:,.2f}"


def pct(probability: float) -> str:
    return f"{100 * probability:.0f}%"


def arrow(bullish: bool) -> str:
    return "▲ Bullish" if bullish else "▼ Bearish"


def name_of(instrument_id: str) -> str:
    try:
        return get_instrument(instrument_id).name
    except UnknownInstrument:
        return instrument_id


def label(instrument_id: str) -> str:
    return f"{name_of(instrument_id)} ({instrument_id})"


def chart_url(instrument_id: str, tf: str) -> str:
    """Same path as the dashboard's chartPath(): the colon stays readable, anything else unsafe is encoded."""
    base = get_settings().frontend_url.rstrip("/")
    return f"{base}/chart/{quote(instrument_id, safe=':')}/{quote(tf, safe='')}"


def ist(ts: pd.Timestamp, fmt: str = "%d %b %H:%M IST") -> str:
    return pd.Timestamp(ts).tz_convert(IST).strftime(fmt)


def bar_time(ts: pd.Timestamp, tf: str) -> str:
    """A daily bar is named by its date; an intraday bar by its IST open time."""
    return ist(ts, "%d %b") if tf == "1D" else ist(ts)


def unix_ts(seconds: int) -> pd.Timestamp:
    return pd.Timestamp(seconds, unit="s", tz="UTC")
