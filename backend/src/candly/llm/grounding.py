"""The no-invented-numbers check for Claude's prose: every number it writes must come from the facts it
was given (rounded, or a 0-1 value written as a percentage)."""

import json
import re

_NUMBER = re.compile(r"\d+(?:,\d+)*(?:\.\d+)?")
_FREE_INTEGERS = set(range(11))  # "1:1", "two of three" written as digits, "3 bars"
_ROUNDING_SLACK = 0.001  # relative: "2,845" for 2845.12


def _values(facts: object) -> set[float]:
    found = {float(m.replace(",", "")) for m in _NUMBER.findall(json.dumps(facts, default=str))}
    return found | {100 * v for v in found if v <= 1}


def _matches(shown: str, allowed: set[float]) -> bool:
    value = float(shown.replace(",", ""))
    if value in _FREE_INTEGERS:
        return True
    decimals = len(shown.split(".", 1)[1]) if "." in shown else 0
    return any(abs(value - a) <= max(0.5 * 10**-decimals, _ROUNDING_SLACK * a) + 1e-9 for a in allowed)


def ungrounded_numbers(text: str, facts: object) -> list[str]:
    """Numbers in `text` that no value in `facts` accounts for."""
    allowed = _values(facts)
    return [shown for shown in _NUMBER.findall(text) if not _matches(shown, allowed)]
