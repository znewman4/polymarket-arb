"""Helpers for later research-only fusion scoring."""

from __future__ import annotations

from .rulebook_models import EvidenceFusionRulebook


def weighted_score(inputs: dict[str, float], rulebook: EvidenceFusionRulebook) -> float:
    total = sum(inputs.get(k, 0.0) * weight for k, weight in rulebook.weights.items())
    return round(max(0.0, min(1.0, total)), 4)
