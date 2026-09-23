"""Fallback sentiment until Claude tagging is wired in: a finance keyword lexicon plus regex event types."""

import re

METHOD = "lexicon"

_POSITIVE_PHRASES = (
    "record high",
    "all-time high",
    "52-week high",
    "beats estimates",
    "beat estimates",
    "above estimates",
    "order win",
    "wins order",
    "bags order",
    "price target raised",
    "raises target",
    "strong demand",
    "profit rises",
    "profit jumps",
    "net profit up",
    "margin expansion",
)
_NEGATIVE_PHRASES = (
    "52-week low",
    "record low",
    "misses estimates",
    "miss estimates",
    "below estimates",
    "net loss",
    "profit falls",
    "profit drops",
    "profit declines",
    "cuts target",
    "target cut",
    "weak demand",
    "margin pressure",
    "show cause",
    "profit booking",
    "profit-taking",
    "profit taking",
)
_POSITIVE = frozenset(
    """
    surge surges surged soar soars soared jump jumps jumped rally rallies rallied gain gains gained rise rises
    rising rose climb climbs climbed advance advances beat beats upgrade upgraded upgrades outperform
    outperforms bullish strong stronger growth grows boost boosts boosted win wins won bags secures secured
    approval approves approved dividend bonus buyback expansion expands recovery recovers rebound rebounds
    positive optimistic upbeat robust record profit profits profitable accelerates
    """.split()
)
_NEGATIVE = frozenset(
    """
    fall falls fell drop drops dropped decline declines declined slump slumps slumped plunge plunges plunged
    crash crashes crashed tumble tumbles tumbled slide slides slid sink sinks sank loss losses miss misses
    missed downgrade downgraded downgrades underperform underperforms bearish weak weaker weakness penalty
    fine fined probe raid raided fraud default defaults lawsuit ban banned resigns resignation concern
    concerns warning warns pressure slowdown selloff sell-off negative pessimistic slips slipped slip
    halt halted suspended scam investigation outflows
    """.split()
)
_NEGATORS = frozenset({"not", "no", "never", "without", "fails", "failed", "unable"})

EVENT_TYPES: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("results", r"\b(q[1-4]\s?(fy)?\d{0,2}|quarterly|results?|earnings|net profit|revenue|ebitda|pat)\b"),
        (
            "rating_change",
            r"\b(upgrades?|upgraded|downgrades?|downgraded|target price|price target|overweight|underweight"
            r"|outperform|underperform|initiates coverage|(buy|sell|hold|neutral) rating|rating)\b",
        ),
        (
            "order_win",
            r"\b(order win|wins? (an? )?(\w+ )?(order|contract)|bags? (\w+ )?(order|contract)s?"
            r"|secures? (\w+ )?(order|contract)s?|order inflow|letter of (award|intent)|orders? worth)\b",
        ),
        (
            "regulatory",
            r"\b(sebi|penalty|penalised|penalized|fined?|probe|investigation|show[- ]cause|nclt|nclat|cci"
            r"|court|tribunal|ed raid|raids?|tax demand|gst demand|regulator|ban(ned)?"
            r"|licen[cs]e cancel\w*)\b",
        ),
        (
            "deal",
            r"\b(acquisitions?|acquires?|acquired|merger|merges?|takeover|stake|joint venture|jv|divest\w*"
            r"|block deal|bulk deal|deal|ipo|qip|fund ?rais\w*|buyout)\b",
        ),
        (
            "management",
            r"\b(ceo|cfo|coo|chairman|chairperson|managing director|resigns?|resignation|appoints?|appointed"
            r"|appointment|steps down|succession|new md)\b",
        ),
        (
            "macro",
            r"\b(rbi|repo rate|inflation|cpi|wpi|gdp|monetary policy|mpc|fed|fomc|interest rates?|rate cut"
            r"|rate hike|fiscal|budget|rupee|forex|trade deficit|iip|pmi|crude prices?|monsoon)\b",
        ),
    )
)


def _count_phrases(text: str, phrases: tuple[str, ...]) -> tuple[int, int, str]:
    """(plain matches, negated matches, text with the phrases removed)."""
    plain = negated = 0
    negators = "|".join(_NEGATORS)
    for phrase in phrases:
        for match in re.finditer(rf"\b(?:({negators})\s+)?{re.escape(phrase)}\b", text):
            if match.group(1):
                negated += 1
            else:
                plain += 1
        text = re.sub(rf"\b{re.escape(phrase)}\b", " ", text)
    return plain, negated, text


def lexicon_sentiment(text: str) -> float:
    """Score in (-1, 1): (positive - negative) / (positive + negative + 1); 0.0 when no cue words."""
    lowered = (text or "").lower()
    pos, neg_from_pos, lowered = _count_phrases(lowered, _POSITIVE_PHRASES)
    neg, pos_from_neg, lowered = _count_phrases(lowered, _NEGATIVE_PHRASES)
    pos, neg = pos + pos_from_neg, neg + neg_from_pos
    tokens = re.findall(r"[a-z][a-z'-]*", lowered)
    for i, token in enumerate(tokens):
        polarity = 1 if token in _POSITIVE else -1 if token in _NEGATIVE else 0
        if polarity and i and tokens[i - 1] in _NEGATORS:
            polarity = -polarity
        if polarity > 0:
            pos += 1
        elif polarity < 0:
            neg += 1
    if pos == neg == 0:
        return 0.0
    return round((pos - neg) / (pos + neg + 1), 4)


def event_type(text: str) -> str | None:
    """The first matching event type, checked in a fixed priority order."""
    for name, pattern in EVENT_TYPES:
        if pattern.search(text or ""):
            return name
    return None
