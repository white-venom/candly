"""Reader for the pre-registered edge search v2-v4 (config/edge_search_v2.yaml, _v3.yaml, _v4.yaml, PLAN.md
§20c-e).

Each test module reads its own section and checks the values it implements. The files are binding and must
never be edited to fit a result; their sha256 goes into every report.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from candly.core.settings import get_settings

EDGE_V2_FILE = "edge_search_v2.yaml"
EDGE_V3_FILE = "edge_search_v3.yaml"
EDGE_V4_FILE = "edge_search_v4.yaml"
SECTIONS = {
    "family",
    "cross_section_patterns",
    "tsmom",
    "intraday_momentum",
    "vol_intraday",
    "vrp_direction",
    "patterns_range",
    "patterns_pooled",
}
V3_SECTIONS = {"family", "xs_pit"}
V4_SECTIONS = {"family", "patterns_levels", "opening_range_breakout", "gap_fade", "intraday_cross_section"}


def _load(path: Path, sections: set[str]) -> dict:
    raw = yaml.safe_load(path.read_bytes())
    found = set(raw) if isinstance(raw, dict) else set()
    if found != sections:
        raise ValueError(
            f"{path.name}: unknown sections {sorted(found - sections)}, missing {sorted(sections - found)}"
        )
    return raw


def edge_v2_path() -> Path:
    return get_settings().config_dir / EDGE_V2_FILE


def edge_v3_path() -> Path:
    return get_settings().config_dir / EDGE_V3_FILE


def edge_v2_sha256(path: Path | None = None) -> str:
    return hashlib.sha256((path or edge_v2_path()).read_bytes()).hexdigest()


def edge_v3_sha256(path: Path | None = None) -> str:
    return hashlib.sha256((path or edge_v3_path()).read_bytes()).hexdigest()


def load_edge_v2(path: Path | None = None) -> dict:
    return _load(path or edge_v2_path(), SECTIONS)


def load_edge_v3(path: Path | None = None) -> dict:
    return _load(path or edge_v3_path(), V3_SECTIONS)


def edge_v2_section(name: str, path: Path | None = None) -> dict:
    if name not in SECTIONS:
        raise KeyError(name)
    return load_edge_v2(path)[name]


def edge_v3_section(name: str, path: Path | None = None) -> dict:
    if name not in V3_SECTIONS:
        raise KeyError(name)
    return load_edge_v3(path)[name]


def edge_v4_path() -> Path:
    return get_settings().config_dir / EDGE_V4_FILE


def edge_v4_sha256(path: Path | None = None) -> str:
    return hashlib.sha256((path or edge_v4_path()).read_bytes()).hexdigest()


def edge_v4_section(name: str, path: Path | None = None) -> dict:
    if name not in V4_SECTIONS:
        raise KeyError(name)
    return _load(path or edge_v4_path(), V4_SECTIONS)[name]
