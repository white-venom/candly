"""Map headlines to watchlist instruments by name and alias: case-insensitive, whole words only."""

import html
import re
from collections.abc import Iterable

from candly.core.instruments import Instrument

# Other listed companies whose names start with a watchlist alias ("Reliance Power" is not RIL).
NOT_FOLLOWED_BY: dict[str, tuple[str, ...]] = {
    "reliance": (
        "power",
        "infra",
        "infrastructure",
        "capital",
        "communications",
        "home finance",
        "nippon",
        "general insurance",
    ),
    "l&t": ("finance", "technology", "tech", "mindtree", "infotech"),
    "larsen & toubro": ("finance", "technology", "infotech"),
    "itc": ("hotels",),
    "sbi": ("life", "card", "cards", "general", "mutual fund", "funds", "securities"),
    "airtel": ("africa",),
}


def _phrase(words: str) -> str:
    """Regex for a lowercase phrase: flexible whitespace, and '&' with or without spaces around it."""
    tokens = []
    for word in words.split():
        tokens.append(r"\s*&\s*".join(re.escape(piece) for piece in word.split("&")))
    return r"\s+".join(tokens)


def _term_pattern(term: str) -> str:
    pattern = rf"(?<![\w&]){_phrase(term)}(?![\w&])"
    excluded = NOT_FOLLOWED_BY.get(term)
    if excluded:
        pattern += rf"(?!\s+(?:{'|'.join(_phrase(e) for e in excluded)})(?![\w&]))"
    return pattern


class InstrumentMapper:
    def __init__(self, instruments: Iterable[Instrument]):
        self._patterns: list[tuple[str, re.Pattern]] = []
        for inst in instruments:
            terms = sorted(
                {a.lower().strip() for a in (*inst.aliases, inst.name) if a.strip()}, key=len, reverse=True
            )
            if terms:
                combined = "|".join(f"(?:{_term_pattern(t)})" for t in terms)
                self._patterns.append((inst.id, re.compile(combined, re.IGNORECASE)))

    def map(self, text: str) -> list[str]:
        """Instrument ids mentioned in `text`. A match inside a longer match for another instrument
        is ignored, so "Nifty Bank" maps to Bank Nifty only."""
        text = html.unescape(text or "")
        spans = [
            (m.start(), m.end(), inst_id)
            for inst_id, pattern in self._patterns
            for m in pattern.finditer(text)
        ]
        found = []
        for start, end, inst_id in spans:
            covered = any(
                other != inst_id and s <= start and end <= e and (e - s) > (end - start)
                for s, e, other in spans
            )
            if not covered and inst_id not in found:
                found.append(inst_id)
        return found
