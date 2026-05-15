"""Pure-function semantic similarity helpers for relationship mining.

All functions are deterministic, have no IO, and return floats in [0, 1].
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from ..storage.base import MarketSemanticsRow


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Jaccard similarity of two string sets (case-insensitive)."""
    sa = {s.lower().strip() for s in a if s}
    sb = {s.lower().strip() for s in b if s}
    if not sa and not sb:
        return 1.0
    inter = sa & sb
    union = sa | sb
    return len(inter) / len(union) if union else 0.0


def entity_overlap_score(sem_a: MarketSemanticsRow, sem_b: MarketSemanticsRow) -> float:
    """Weighted Jaccard over subject_entities + event_entities."""
    subjects_j = jaccard(sem_a.subject_entities, sem_b.subject_entities)

    # If neither market has any entities, score is zero (can't confirm overlap)
    all_a = set(sem_a.subject_entities) | set(sem_a.event_entities)
    all_b = set(sem_b.subject_entities) | set(sem_b.event_entities)
    if not all_a or not all_b:
        return 0.0

    combined_j = jaccard(
        list(sem_a.subject_entities) + list(sem_a.event_entities),
        list(sem_b.subject_entities) + list(sem_b.event_entities),
    )
    # Weight towards subject entities
    if sem_a.subject_entities and sem_b.subject_entities:
        return 0.7 * subjects_j + 0.3 * combined_j
    return combined_j


def time_scope_overlap_score(sem_a: MarketSemanticsRow, sem_b: MarketSemanticsRow) -> float:
    """Estimate time-scope overlap.

    If both have exact_deadline_ms: compare them directly.
    If both have the same temporal_resolution (year/quarter/month): 1.0.
    If one or both are ambiguous: 0.3 (might overlap).
    """
    # Both have exact deadlines
    if sem_a.exact_deadline_ms is not None and sem_b.exact_deadline_ms is not None:
        diff_ms = abs(sem_a.exact_deadline_ms - sem_b.exact_deadline_ms)
        week_ms = 7 * 24 * 3600 * 1000
        if diff_ms == 0:
            return 1.0
        elif diff_ms <= week_ms:
            return 0.9
        elif diff_ms <= 4 * week_ms:
            return 0.7
        elif diff_ms <= 13 * week_ms:
            return 0.4
        else:
            return 0.0

    res_a = (sem_a.temporal_resolution or "").lower()
    res_b = (sem_b.temporal_resolution or "").lower()

    if res_a and res_b and res_a == res_b and res_a not in ("ambiguous", "unknown", "open"):
        return 0.8
    if "ambiguous" in (res_a, res_b) or "unknown" in (res_a, res_b):
        return 0.3
    if not res_a or not res_b:
        return 0.2
    return 0.4


def resolution_criteria_score(sem_a: MarketSemanticsRow, sem_b: MarketSemanticsRow) -> float:
    """Token-level Jaccard over resolution condition texts."""
    def _tokens(s: str | None) -> list[str]:
        if not s:
            return []
        return [t.lower() for t in s.replace(",", " ").replace(".", " ").split() if len(t) > 2]

    conds_a: list[str] = []
    conds_b: list[str] = []
    for attr in ("positive_resolution_condition", "negative_resolution_condition"):
        conds_a += _tokens(getattr(sem_a, attr, None))
        conds_b += _tokens(getattr(sem_b, attr, None))
    for attr in ("necessary_conditions_for_yes", "sufficient_conditions_for_yes",
                 "necessary_conditions_for_no", "sufficient_conditions_for_no"):
        for cond in (getattr(sem_a, attr, None) or []):
            conds_a += _tokens(cond)
        for cond in (getattr(sem_b, attr, None) or []):
            conds_b += _tokens(cond)

    if not conds_a or not conds_b:
        return 0.0
    return jaccard(conds_a, conds_b)
