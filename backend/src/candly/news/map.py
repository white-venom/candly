"""Map headlines to watchlist instruments by name and alias: case-insensitive, whole words only."""

import html
import re
from collections.abc import Iterable

from candly.core.instruments import Instrument

# Other names that start with a watchlist alias: listed companies ("Reliance Power" is not RIL),
# sectoral indices ("Nifty IT" is not the Nifty 50) and other commodities ("crude palm oil").
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
    "nifty": (
        "it",
        "next 50",
        "midcap",
        "mid cap",
        "midsmallcap",
        "smallcap",
        "small cap",
        "microcap",
        "largemidcap",
        "100",
        "200",
        "500",
        "auto",
        "pharma",
        "fmcg",
        "metal",
        "metals",
        "realty",
        "media",
        "energy",
        "infra",
        "infrastructure",
        "psu",
        "pse",
        "cpse",
        "private bank",
        "financial",
        "fin service",
        "fin services",
        "services",
        "commodities",
        "consumption",
        "consumer durables",
        "healthcare",
        "oil & gas",
        "oil and gas",
        "mnc",
        "chemicals",
        "india defence",
        "defence",
        "india digital",
        "india manufacturing",
        "india tourism",
        "housing",
        "ev",
        "capital markets",
        "total market",
        "alpha",
        "dividend",
        "low volatility",
        "quality",
        "value",
    ),
    "crude": ("palm", "soybean", "soyabean", "soy", "sunflower", "degummed", "edible", "steel"),
}

# Phrases in which an alias names something else; alias matches inside them are ignored.
BLOCKED_PHRASES: tuple[str, ...] = (
    "senco gold",
    "sky gold",
    "barrick gold",
    "gold fields",
    "gold loan",
    "gold loans",
    "silver lake",
    "cboe volatility index",
    "us volatility index",
    "wall street's volatility index",
)

_COMMODITY_CONTEXT = (
    "mcx",
    "comex",
    "lbma",
    "bullion",
    "commodity",
    "commodities",
    "futures",
    "spot",
    "price",
    "prices",
    "rate",
    "rates",
    "ounce",
    "ounces",
    "oz",
    "precious metal",
    "precious metals",
    "yellow metal",
    "white metal",
    "safe haven",
    "safe-haven",
    "10 grams",
    "10 gram",
    "per kg",
    "per kilogram",
)
# Aliases that are ordinary words or generic names: they count only when the text also has one of
# these context words (a company called "... Gold" is not MCX gold; the CBOE VIX is not India VIX).
NEEDS_CONTEXT: dict[str, tuple[str, ...]] = {
    "gold": _COMMODITY_CONTEXT,
    "silver": _COMMODITY_CONTEXT,
    "volatility index": ("india", "indian", "nse", "nifty", "sensex", "dalal street"),
}


def _phrase(words: str) -> str:
    """Regex for a lowercase phrase: flexible whitespace, and '&' with or without spaces around it."""
    tokens = []
    for word in words.split():
        tokens.append(r"\s*&\s*".join(re.escape(piece) for piece in word.split("&")))
    return r"\s+".join(tokens)


def _whole(phrase_regex: str) -> str:
    return rf"(?<![\w&]){phrase_regex}(?![\w&])"


def _term_pattern(term: str) -> str:
    pattern = _whole(_phrase(term))
    excluded = NOT_FOLLOWED_BY.get(term)
    if excluded:
        pattern += rf"(?!\s+(?:{'|'.join(_phrase(e) for e in excluded)})(?![\w&]))"
    return pattern


def _any_of(terms: Iterable[str], exclusions: bool = True) -> re.Pattern:
    ordered = sorted(terms, key=len, reverse=True)
    parts = [_term_pattern(t) if exclusions else _whole(_phrase(t)) for t in ordered]
    return re.compile("|".join(f"(?:{p})" for p in parts), re.IGNORECASE)


class InstrumentMapper:
    def __init__(self, instruments: Iterable[Instrument]):
        # (instrument id, alias pattern, context pattern the text must also match, or None)
        self._patterns: list[tuple[str, re.Pattern, re.Pattern | None]] = []
        for inst in instruments:
            terms = {a.lower().strip() for a in (*inst.aliases, inst.name) if a.strip()}
            plain = [t for t in terms if t not in NEEDS_CONTEXT]
            if plain:
                self._patterns.append((inst.id, _any_of(plain), None))
            for term in sorted(terms - set(plain)):
                self._patterns.append((inst.id, _any_of([term]), _any_of(NEEDS_CONTEXT[term], False)))
        self._blocked = _any_of(BLOCKED_PHRASES, False)

    def map(self, text: str) -> list[str]:
        """Instrument ids mentioned in `text`. A match inside a longer match for another instrument
        is ignored, so "Nifty Bank" maps to Bank Nifty only."""
        text = html.unescape(text or "")
        blocked = [(m.start(), m.end()) for m in self._blocked.finditer(text)]
        spans = [
            (m.start(), m.end(), inst_id)
            for inst_id, pattern, context in self._patterns
            if context is None or context.search(text)
            for m in pattern.finditer(text)
            if not any(s <= m.start() and m.end() <= e for s, e in blocked)
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
