"""Deterministic post-processing validators.

The most load-bearing one is ``apply_temporal_safety_net``: an independent
regex over the original ``question`` decides whether the model is allowed
to claim an exact deadline. If the source text uses a vague phrase like
"before June 2026", we overwrite the LLM's ``exact_deadline_ms`` to null
and force the temporal_resolution to a non-exact value. Gamma's ``endDate``
(when present) is the only authoritative source for ``exact_deadline_ms``.
"""

from __future__ import annotations

import re
from typing import Literal

from .schemas import MarketSemantics

# Vague temporal markers — non-exhaustive but catches the common cases.
# Each tuple: (regex, resolved_class)
_VAGUE_PATTERNS: list[tuple[re.Pattern[str], Literal["month", "quarter", "year", "vague"]]] = [
    # "by/before/in <Month> <Year>"
    (re.compile(
        r"\b(by|before|in|during)\s+"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
        r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+"
        r"\d{4}\b",
        re.IGNORECASE,
    ), "month"),
    # "by/before/in Q[1-4] <Year>"
    (re.compile(
        r"\b(by|before|in|during)\s+q[1-4]\s+\d{4}\b",
        re.IGNORECASE,
    ), "quarter"),
    # "by the end of <Year>", "in <Year>", "this year". Bare "by 2028"
    # is intentionally left to the model unless Gamma supplies an endDate:
    # it is a deadline-ish phrase, but often too underspecified to treat as
    # a deterministic year bucket.
    (re.compile(
        r"\b((by|before)\s+(the\s+)?end\s+of\s+\d{4}|(in|during)\s+\d{4})\b",
        re.IGNORECASE,
    ), "year"),
    (re.compile(r"\bthis\s+year\b", re.IGNORECASE), "year"),
    # "this summer/winter/spring/fall/autumn", "next month/year"
    (re.compile(
        r"\b(this|next)\s+(summer|winter|spring|fall|autumn|month|year)\b",
        re.IGNORECASE,
    ), "vague"),
    # "in the next N <unit>"
    (re.compile(
        r"\bin\s+the\s+next\s+\d+\s+(days?|weeks?|months?|years?)\b",
        re.IGNORECASE,
    ), "vague"),
]

# An exact-date hint: a fully qualified ISO-ish date inside the text.
_EXACT_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|"
    r"\b\d{1,2}\s+(january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\s+\d{4}\b",
    re.IGNORECASE,
)


def detect_temporal_phrase(question: str) -> tuple[str | None, str]:
    """Return ``(phrase, resolution)`` from a deterministic regex over the
    original question. ``resolution`` is one of ``exact_date``, ``month``,
    ``quarter``, ``year``, ``vague``, ``open_ended``."""

    if not question:
        return None, "open_ended"
    if (m := _EXACT_PATTERN.search(question)):
        return m.group(0), "exact_date"
    for regex, resolution in _VAGUE_PATTERNS:
        if (m := regex.search(question)):
            return m.group(0), resolution
    return None, "open_ended"


def apply_temporal_safety_net(
    sem: MarketSemantics,
    *,
    gamma_end_date_ms: int | None = None,
) -> MarketSemantics:
    """Override the LLM's temporal claims using deterministic logic.

    1. Run ``detect_temporal_phrase`` over ``sem.question``.
    2. If the source text is non-exact, force ``exact_deadline_ms = None``
       and set ``temporal_resolution`` accordingly. Add ``vague_deadline``
       to ``ambiguity_flags`` if not already present.
    3. ``gamma_end_date_ms`` is the only authoritative source for
       ``exact_deadline_ms`` — if present, use it.
    """

    phrase, resolution = detect_temporal_phrase(sem.question)
    new_flags = list(sem.ambiguity_flags)
    new_resolution = sem.temporal_resolution
    new_deadline_ms: int | None = sem.exact_deadline_ms

    if phrase is not None and resolution != "exact_date":
        new_resolution = resolution  # type: ignore[assignment]
        new_deadline_ms = None
        if "vague_deadline" not in new_flags and resolution != "open_ended":
            new_flags.append("vague_deadline")

    if gamma_end_date_ms is not None:
        new_deadline_ms = gamma_end_date_ms
        if new_resolution == "vague" or new_resolution == "open_ended":
            new_resolution = "exact_date"  # Gamma supplied one
            if "vague_deadline" in new_flags:
                new_flags.remove("vague_deadline")

    return sem.model_copy(update={
        "temporal_phrase": phrase if phrase is not None else sem.temporal_phrase,
        "temporal_resolution": new_resolution,
        "exact_deadline_ms": new_deadline_ms,
        "ambiguity_flags": new_flags,
    })
