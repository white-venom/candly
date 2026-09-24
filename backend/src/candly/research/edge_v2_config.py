"""Reader for the pre-registered edge search v2 (config/edge_search_v2.yaml, PLAN.md §20c).

Each test module reads its own section and checks the values it implements. The file is binding and must
never be edited to fit a result; its sha256 goes into every report.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from candly.core.settings import get_settings

EDGE_V2_FILE = "edge_search_v2.yaml"
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


def edge_v2_path() -> Path:
    return get_settings().config_dir / EDGE_V2_FILE


def edge_v2_sha256(path: Path | None = None) -> str:
    return hashlib.sha256((path or edge_v2_path()).read_bytes()).hexdigest()


def load_edge_v2(path: Path | None = None) -> dict:
    raw = yaml.safe_load((path or edge_v2_path()).read_bytes())
    if not isinstance(raw, dict) or set(raw) != SECTIONS:
        found = set(raw) if isinstance(raw, dict) else set()
        raise ValueError(
            f"{EDGE_V2_FILE}: unknown sections {sorted(found - SECTIONS)}, missing {sorted(SECTIONS - found)}"
        )
    return raw


def edge_v2_section(name: str, path: Path | None = None) -> dict:
    if name not in SECTIONS:
        raise KeyError(name)
    return load_edge_v2(path)[name]
