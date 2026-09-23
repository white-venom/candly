"""Acceptance: no secrets in the real log files under data/logs. Reports names/counts only, never values."""

from __future__ import annotations

import pytest
from acceptance_helpers import REAL_DATA, leaks


def test_logs_hold_no_tokens_or_keys():
    logs = sorted((REAL_DATA / "logs").glob("*.log*"))
    if not logs:
        pytest.skip("no log files under data/logs")
    found = {}
    for path in logs:
        hits = leaks(path.read_text(encoding="utf-8", errors="ignore"))
        if hits:
            found[path.name] = hits
    assert not found, found
