"""Extract numeric threshold claims from canonical question text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from ..storage.base import MarketSemanticsRow

_UNIT_NORMALISE = {
    "k": Decimal("1000"),
    "m": Decimal("1000000"),
    "b": Decimal("1000000000"),
    "t": Decimal("1000000000000"),
}

_COMPARATOR_PHRASES = [
    (r"(?:reach(?:es?)?|exceed(?:s)?|surpass(?:es)?|go(?:es)? (?:above|over)|hits?|top(?:s)?|above|over|greater than|more than|at least)\s*\$?", ">"),
    (r"(?:fall(?:s)? (?:below|under)|go(?:es)? (?:below|under)|drop(?:s)? (?:below|under)|less than|under|below|at most)\s*\$?", "<"),
    (r"(?:equal(?:s)?|hit(?:s)?|reach(?:es?)?)\s*exactly\s*\$?", "=="),
]

# Matches numbers like 100,000 or 100k or 100.5m
# The suffix [kmbt] must be immediately adjacent (no space) to the number
_NUM_RE = re.compile(r"([0-9][0-9,\.]*)([kmbt]?)(?=\b|$)", re.IGNORECASE)

_VARIABLE_PATTERNS = [
    (re.compile(r"\bbtc\b|\bbitcoin\b", re.IGNORECASE), "btc_price"),
    (re.compile(r"\beth\b|\bethereum\b", re.IGNORECASE), "eth_price"),
    (re.compile(r"\bsol\b|\bsolana\b", re.IGNORECASE), "sol_price"),
    (re.compile(r"\bnasdaq\b|\bndx\b", re.IGNORECASE), "nasdaq_index"),
    (re.compile(r"\bs&p\b|\bspx\b|\bs&p 500\b", re.IGNORECASE), "sp500_index"),
    (re.compile(r"\bgold\b", re.IGNORECASE), "gold_price"),
    (re.compile(r"\bcpi\b|\binflation\b", re.IGNORECASE), "cpi"),
    (re.compile(r"\bunemployment\b", re.IGNORECASE), "unemployment_rate"),
    (re.compile(r"\bfed funds\b|\bfederal funds\b|\bfed rate\b", re.IGNORECASE), "fed_rate"),
]


@dataclass(frozen=True)
class ThresholdClaim:
    variable: str
    comparator: str   # ">", ">=", "<", "<=", "=="
    value: Decimal
    unit: str | None
    deadline_ms: int | None
    raw_phrase: str


def _normalise_number(num_str: str, suffix: str) -> Decimal | None:
    cleaned = num_str.replace(",", "")
    try:
        val = Decimal(cleaned)
    except InvalidOperation:
        return None
    multiplier = _UNIT_NORMALISE.get(suffix.lower(), Decimal("1"))
    return val * multiplier


def _extract_variable(text: str) -> str | None:
    for pattern, name in _VARIABLE_PATTERNS:
        if pattern.search(text):
            return name
    return None


def extract_threshold_claim(sem: MarketSemanticsRow) -> ThresholdClaim | None:
    """Parse canonical_question + positive_resolution_condition for a numeric threshold."""
    text = " ".join(filter(None, [sem.canonical_question, sem.positive_resolution_condition]))

    variable = _extract_variable(text)
    if variable is None:
        return None

    for phrase_re, comparator in _COMPARATOR_PHRASES:
        # Number followed by optional suffix letter (no space allowed before suffix)
        pattern = re.compile(phrase_re + r"([0-9][0-9,\.]*)([kmbt]?)(?:\b|$)", re.IGNORECASE)
        m = pattern.search(text)
        if m:
            num_str = m.group(len(m.groups()) - 1)
            suffix_str = m.group(len(m.groups()))
            # Only accept single-letter suffix if it appears directly after digit (no whitespace)
            # Check that the suffix matches directly after the number in the raw text
            raw = m.group(0)
            # Find where the number ends in raw; suffix must be right after
            num_end = raw.rfind(num_str[-1])
            if suffix_str and num_end < len(raw) - 1:
                # Check for space between number and suffix
                between = raw[num_end + 1:num_end + 1 + (len(raw) - num_end - len(suffix_str))]
                if " " in between or "\t" in between:
                    suffix_str = ""  # don't treat as multiplier
            value = _normalise_number(num_str, suffix_str)
            if value is None:
                continue
            unit = "usd" if variable.endswith("_price") else None
            return ThresholdClaim(
                variable=variable,
                comparator=comparator,
                value=value,
                unit=unit,
                deadline_ms=sem.exact_deadline_ms,
                raw_phrase=m.group(0),
            )
    return None


def detect_threshold_nesting(
    a: ThresholdClaim, b: ThresholdClaim
) -> Literal["a_implies_b", "b_implies_a", "none"]:
    """Determine nesting direction between two threshold claims.

    Only works for same variable, same comparator direction, and overlapping deadlines.

    For '>' comparator: higher value implies lower value (if BTC > 100k then BTC > 90k).
    For '<' comparator: lower value implies higher value (if BTC < 90k then BTC < 100k).
    """
    if a.variable != b.variable:
        return "none"
    if a.comparator != b.comparator:
        return "none"

    # Check deadline overlap: both must be present and close
    if a.deadline_ms is not None and b.deadline_ms is not None:
        diff_ms = abs(a.deadline_ms - b.deadline_ms)
        if diff_ms > 14 * 24 * 3600 * 1000:  # more than 2 weeks apart
            return "none"

    comp = a.comparator
    if comp in (">", ">="):
        # Higher threshold implies lower threshold
        if a.value > b.value:
            return "a_implies_b"
        elif b.value > a.value:
            return "b_implies_a"
    elif comp in ("<", "<="):
        # Lower threshold implies higher threshold
        if a.value < b.value:
            return "a_implies_b"
        elif b.value < a.value:
            return "b_implies_a"

    return "none"
