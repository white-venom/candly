"""Rule-based derivatives expiries (NSE/BSE) from config/expiry.yaml. MCX uses symbol-master dates instead."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

from candly.core.calendar import WEEKDAYS, MarketCalendar, get_calendar
from candly.core.instruments import get_instrument
from candly.core.settings import get_settings


@dataclass(frozen=True)
class Rule:
    start: date
    end: date | None
    weekday: int | None


@dataclass(frozen=True)
class ExpiryInfo:
    next_expiry: date
    kind: str                 # "weekly" | "monthly" | "contract"
    days_to_expiry: int       # trading days after d up to and including the expiry; 0 on expiry day
    is_expiry_day: bool
    is_monthly_expiry_day: bool


def _rules(periods: list[dict] | None) -> tuple[Rule, ...]:
    periods = sorted(periods or [], key=lambda p: p["from"])
    return tuple(
        Rule(
            start=p["from"],
            end=periods[i + 1]["from"] if i + 1 < len(periods) else None,
            weekday=WEEKDAYS.index(p["weekday"]) if p.get("weekday") else None,
        )
        for i, p in enumerate(periods)
    )


class ExpiryRules:
    def __init__(self, raw: dict, calendar: MarketCalendar):
        self._cal = calendar
        self._products = {
            key: (_rules(spec.get("weekly")), _rules(spec.get("monthly")))
            for key, spec in {**(raw.get("defaults") or {}), **(raw.get("products") or {})}.items()
        }

    @classmethod
    def from_yaml(cls, path: Path, calendar: MarketCalendar) -> ExpiryRules:
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")), calendar)

    def product_key(self, instrument_id: str) -> str | None:
        if instrument_id in self._products:
            return instrument_id
        inst = get_instrument(instrument_id)
        key = f"{inst.exchange}:{inst.kind}"
        return key if key in self._products else None

    def _shift_back(self, exchange: str, d: date) -> date:
        while not self._trading_day(exchange, d):
            d -= timedelta(days=1)
        return d

    def _trading_day(self, exchange: str, d: date) -> bool:
        return self._cal.is_trading_day(exchange, d)

    def _in_rule(self, rule: Rule, candidate: date) -> bool:
        return candidate >= rule.start and (rule.end is None or candidate < rule.end)

    def weekly_expiry(self, key: str, exchange: str, week_of: date) -> date | None:
        monday = week_of - timedelta(days=week_of.weekday())
        for rule in self._products[key][0]:
            if rule.weekday is None:
                continue
            candidate = monday + timedelta(days=rule.weekday)
            if self._in_rule(rule, candidate):
                return self._shift_back(exchange, candidate)
        return None

    def monthly_expiry(self, key: str, exchange: str, year: int, month: int) -> date | None:
        last_day = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
        for rule in self._products[key][1]:
            if rule.weekday is None:
                continue
            candidate = last_day - timedelta(days=(last_day.weekday() - rule.weekday) % 7)
            if self._in_rule(rule, candidate):
                return self._shift_back(exchange, candidate)
        return None

    def expiries_between(self, instrument_id: str, start: date, end: date) -> list[tuple[date, str]]:
        """All (expiry date, kind) in [start, end]; monthly wins over weekly on the same day."""
        key = self.product_key(instrument_id)
        if key is None:
            return []
        exchange = instrument_id.split(":", 1)[0]
        found: dict[date, str] = {}
        week = start - timedelta(days=start.weekday())
        while week <= end + timedelta(days=7):
            e = self.weekly_expiry(key, exchange, week)
            if e is not None and start <= e <= end:
                found.setdefault(e, "weekly")
            week += timedelta(days=7)
        year, month = start.year, start.month
        while date(year, month, 1) <= end:
            e = self.monthly_expiry(key, exchange, year, month)
            if e is not None and start <= e <= end:
                found[e] = "monthly"
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return sorted(found.items())

    def info(self, instrument_id: str, d: date, horizon_days: int = 70) -> ExpiryInfo | None:
        upcoming = self.expiries_between(instrument_id, d, d + timedelta(days=horizon_days))
        if not upcoming:
            return None
        nxt, kind = upcoming[0]
        exchange = instrument_id.split(":", 1)[0]
        days = sum(
            1 for i in range(1, (nxt - d).days + 1) if self._trading_day(exchange, d + timedelta(days=i))
        )
        return ExpiryInfo(
            next_expiry=nxt,
            kind=kind,
            days_to_expiry=days,
            is_expiry_day=nxt == d,
            is_monthly_expiry_day=nxt == d and kind == "monthly",
        )


@lru_cache
def get_expiry_rules() -> ExpiryRules:
    return ExpiryRules.from_yaml(get_settings().config_dir / "expiry.yaml", get_calendar())


def expiry_info(instrument_id: str, d: date) -> ExpiryInfo | None:
    """Rule-based expiry for NSE/BSE products; None for MCX (symbol master) and instruments without F&O."""
    return get_expiry_rules().info(instrument_id, d)
