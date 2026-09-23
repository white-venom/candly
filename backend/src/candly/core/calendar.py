from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from candly.core.settings import get_settings
from candly.core.timeframes import is_intraday, tf_delta

IST = ZoneInfo("Asia/Kolkata")
NEW_YORK = ZoneInfo("America/New_York")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _hm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _to_utc(d: date, t: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(d, t, tzinfo=IST)).tz_convert("UTC")


def _us_dst(d: date) -> bool:
    return datetime.combine(d, time(12), tzinfo=NEW_YORK).dst() != timedelta(0)


@dataclass(frozen=True)
class ExchangeSpec:
    name: str
    open: time
    close: time
    close_us_dst: time | None
    pre_open: time | None
    phases: tuple[tuple[str, str, str], ...]
    weekly_expiry_weekday: int | None
    holidays: frozenset[date]
    special_sessions: dict[date, tuple[time, time] | None]


class MarketCalendar:
    def __init__(self, raw: dict):
        holidays: dict[str, set[date]] = {}
        for h in raw.get("holidays") or []:
            for ex in h["exchanges"]:
                holidays.setdefault(ex, set()).add(h["date"])
        specials: dict[str, dict[date, tuple[time, time] | None]] = {}
        for s in raw.get("special_sessions") or []:
            timing = (_hm(s["open"]), _hm(s["close"])) if s.get("open") and s.get("close") else None
            for ex in s["exchanges"]:
                specials.setdefault(ex, {})[s["date"]] = timing
        self._specs: dict[str, ExchangeSpec] = {}
        for name, cfg in raw["exchanges"].items():
            expiry = cfg.get("weekly_expiry_weekday")
            self._specs[name] = ExchangeSpec(
                name=name,
                open=_hm(cfg["open"]),
                close=_hm(cfg["close"]),
                close_us_dst=_hm(cfg["close_us_dst"]) if cfg.get("close_us_dst") else None,
                pre_open=_hm(cfg["pre_open"]) if cfg.get("pre_open") else None,
                phases=tuple((p["name"], str(p["start"]), str(p["end"])) for p in cfg["phases"]),
                weekly_expiry_weekday=WEEKDAYS.index(expiry) if expiry else None,
                holidays=frozenset(holidays.get(name, set())),
                special_sessions=specials.get(name, {}),
            )

    @classmethod
    def from_yaml(cls, path: Path) -> MarketCalendar:
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")))

    def spec(self, exchange: str) -> ExchangeSpec:
        try:
            return self._specs[exchange]
        except KeyError:
            raise ValueError(f"unknown exchange {exchange!r}") from None

    def is_trading_day(self, exchange: str, d: date) -> bool:
        spec = self.spec(exchange)
        if d in spec.special_sessions:
            return spec.special_sessions[d] is not None
        return d.weekday() < 5 and d not in spec.holidays

    def session_times(self, exchange: str, d: date) -> tuple[pd.Timestamp, pd.Timestamp]:
        # Ignores holidays on purpose: historical bars can exist on days the holiday list doesn't know about.
        spec = self.spec(exchange)
        special = spec.special_sessions.get(d)
        if special is not None:
            return _to_utc(d, special[0]), _to_utc(d, special[1])
        close = spec.close_us_dst if spec.close_us_dst is not None and _us_dst(d) else spec.close
        return _to_utc(d, spec.open), _to_utc(d, close)

    def session(self, exchange: str, d: date) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        return self.session_times(exchange, d) if self.is_trading_day(exchange, d) else None

    @staticmethod
    def local_date(ts: pd.Timestamp) -> date:
        return ts.tz_convert(IST).date()

    def bar_close_time(self, exchange: str, ts: pd.Timestamp, tf: str) -> pd.Timestamp:
        _, close = self.session_times(exchange, self.local_date(ts))
        if not is_intraday(tf):
            return close
        end = ts + tf_delta(tf)
        return end if ts >= close else min(end, close)

    def expected_bar_opens(self, exchange: str, d: date, tf: str) -> list[pd.Timestamp]:
        sess = self.session(exchange, d)
        if sess is None:
            return []
        start, close = sess
        if not is_intraday(tf):
            return [start]
        step = tf_delta(tf)
        opens, t = [], start
        while t < close:
            opens.append(t)
            t += step
        return opens

    def session_phase(self, exchange: str, ts: pd.Timestamp) -> str:
        spec = self.spec(exchange)
        d = self.local_date(ts)
        start, close = self.session_times(exchange, d)
        if ts >= close:
            return "closed"
        if ts < start:
            if spec.pre_open is not None and ts >= _to_utc(d, spec.pre_open):
                return "pre_open"
            return "closed"
        for name, phase_start, phase_end in spec.phases:
            if self._resolve(d, phase_start, close) <= ts < self._resolve(d, phase_end, close):
                return name
        return spec.phases[-1][0]

    @staticmethod
    def _resolve(d: date, value: str, close: pd.Timestamp) -> pd.Timestamp:
        if value == "close":
            return close
        if value.startswith("close-"):
            return close - timedelta(minutes=int(value.removeprefix("close-")))
        return _to_utc(d, _hm(value))

    def is_open(self, exchange: str, now: pd.Timestamp) -> bool:
        sess = self.session(exchange, self.local_date(now))
        return sess is not None and sess[0] <= now < sess[1]

    def next_expiry(self, exchange: str, d: date, monthly: bool = False) -> date:
        if monthly:
            year, month = d.year, d.month
            while True:
                expiry = self._monthly_expiry(exchange, year, month)
                if expiry >= d:
                    return expiry
                year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        week = d
        while True:
            expiry = self._weekly_expiry(exchange, week)
            if expiry >= d:
                return expiry
            week += timedelta(days=7)

    def is_expiry_day(self, exchange: str, d: date, monthly: bool = False) -> bool:
        return self.is_trading_day(exchange, d) and self.next_expiry(exchange, d, monthly) == d

    def _expiry_weekday(self, exchange: str) -> int:
        weekday = self.spec(exchange).weekly_expiry_weekday
        if weekday is None:
            raise ValueError(f"{exchange} has no rule-based expiry; use the broker symbol master")
        return weekday

    def _shift_back(self, exchange: str, d: date) -> date:
        while not self.is_trading_day(exchange, d):
            d -= timedelta(days=1)
        return d

    def _weekly_expiry(self, exchange: str, week_of: date) -> date:
        monday = week_of - timedelta(days=week_of.weekday())
        return self._shift_back(exchange, monday + timedelta(days=self._expiry_weekday(exchange)))

    def _monthly_expiry(self, exchange: str, year: int, month: int) -> date:
        weekday = self._expiry_weekday(exchange)
        last_day = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
        last_day -= timedelta(days=(last_day.weekday() - weekday) % 7)
        return self._shift_back(exchange, last_day)


@lru_cache
def get_calendar() -> MarketCalendar:
    return MarketCalendar.from_yaml(get_settings().config_dir / "markets.yaml")
