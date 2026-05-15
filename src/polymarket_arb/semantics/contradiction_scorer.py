"""Placeholder pure contradiction scorer for future pairwise graph work."""

from __future__ import annotations

from .rulebook_models import ContradictionRulebook


def score_contradiction(pair_type: str, rulebook: ContradictionRulebook) -> float:
    return max(0.0, min(1.0, rulebook.pair_type_weights.get(pair_type, 0.0)))
