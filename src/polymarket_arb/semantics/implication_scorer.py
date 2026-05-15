"""Pure implication confidence scoring."""

from __future__ import annotations

from dataclasses import dataclass

from .rulebook_models import ImplicationRulebook


@dataclass(frozen=True)
class ImplicationScore:
    deterministic_score: float
    final_confidence: float
    needs_manual_review: bool


def score_implication(
    *,
    implication_type: str,
    model_confidence: float,
    ambiguity_score: float,
    rulebook: ImplicationRulebook,
) -> ImplicationScore:
    base = rulebook.type_weights.get(implication_type, 0.25)
    deterministic_score = round(max(0.0, min(1.0, base * (1.0 - ambiguity_score))), 4)
    penalty = ambiguity_score * rulebook.ambiguity_penalty_weight
    final_confidence = round(
        max(0.0, min(1.0, deterministic_score * model_confidence * (1.0 - penalty))),
        4,
    )
    return ImplicationScore(
        deterministic_score=deterministic_score,
        final_confidence=final_confidence,
        needs_manual_review=final_confidence < rulebook.review_threshold,
    )
