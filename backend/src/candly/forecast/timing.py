"""Bar timing helpers built on the market calendar."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from candly.core.calendar import get_calendar

_MAX_DAYS = 30
# Ingest downloads a just-closed bar ~30-60 s after the close (the daily bar at 15:45 IST), so a live
# forecast only counts as stale once the next bar is this late; otherwise it vanishes every candle.
STALE_GRACE = {"intraday": timedelta(minutes=3), "1D": timedelta(minutes=90)}


def stale_grace(tf: str) -> timedelta:
    return STALE_GRACE["1D"] if tf == "1D" else STALE_GRACE["intraday"]


def to_unix(ts: pd.Timestamp) -> int:
    return int(pd.Timestamp(ts).timestamp())


def drop_unclosed(df: pd.DataFrame, exchange: str, tf: str, now: pd.Timestamp) -> pd.DataFrame:
    """Remove trailing bars that have not closed by `now`."""
    cal = get_calendar()
    end = len(df)
    while end > 0 and cal.bar_close_time(exchange, df["ts"].iloc[end - 1], tf) > now:
        end -= 1
    return df.iloc[:end]


def future_bar_times(exchange: str, tf: str, after: pd.Timestamp, steps: int) -> list[pd.Timestamp]:
    """The next `steps` expected bar opens strictly after `after`, on upcoming trading days."""
    cal = get_calendar()
    day = cal.local_date(after)
    out: list[pd.Timestamp] = []
    for offset in range(_MAX_DAYS + 1):
        for ts in cal.expected_bar_opens(exchange, day + timedelta(days=offset), tf):
            if ts > after:
                out.append(ts)
                if len(out) == steps:
                    return out
    return out


def last_expected_closed_bar(exchange: str, tf: str, now: pd.Timestamp) -> pd.Timestamp | None:
    """Open time of the most recent bar the calendar says should have closed by `now`."""
    cal = get_calendar()
    today = cal.local_date(now)
    for back in range(_MAX_DAYS + 1):
        opens = cal.expected_bar_opens(exchange, today - timedelta(days=back), tf)
        closed = [o for o in opens if cal.bar_close_time(exchange, o, tf) <= now]
        if closed:
            return closed[-1]
    return None
