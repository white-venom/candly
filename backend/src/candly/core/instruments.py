import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from candly.core.settings import get_settings
from candly.core.timeframes import validate_tf

EXCHANGES = ("NSE", "BSE", "MCX")
_ID_PATTERN = re.compile(r"^(NSE|BSE|MCX):[A-Z0-9&_-]+$")


class UnknownInstrument(KeyError):
    pass


class Instrument(BaseModel):
    id: str
    name: str
    kind: Literal["equity", "index", "future"]
    tradable: bool = True
    timeframes: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    sources: dict[str, str | None] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _ID_PATTERN.match(value):
            raise ValueError(f"instrument id {value!r} must look like EXCHANGE:SYMBOL")
        return value

    @field_validator("timeframes")
    @classmethod
    def _check_timeframes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_tf(tf) for tf in value)

    @property
    def exchange(self) -> str:
        return self.id.split(":", 1)[0]

    @property
    def symbol(self) -> str:
        return self.id.split(":", 1)[1]

    def source_symbol(self, source: str) -> str | None:
        return self.sources.get(source)


def load_watchlist(path: Path | None = None) -> list[Instrument]:
    path = path or get_settings().config_dir / "watchlist.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    default_tfs = raw.get("defaults", {}).get("timeframes", [])
    instruments = [Instrument(**{"timeframes": default_tfs, **item}) for item in raw["instruments"]]
    ids = [i.id for i in instruments]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate instrument ids in {path}: {duplicates}")
    return instruments


@lru_cache
def _watchlist_index() -> dict[str, Instrument]:
    return {i.id: i for i in load_watchlist()}


def get_instrument(instrument_id: str) -> Instrument:
    try:
        return _watchlist_index()[instrument_id]
    except KeyError:
        raise UnknownInstrument(instrument_id) from None


def exchange_of(instrument_id: str) -> str:
    return instrument_id.split(":", 1)[0]
