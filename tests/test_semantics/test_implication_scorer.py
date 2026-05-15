from __future__ import annotations

from polymarket_arb.semantics.implication_scorer import score_implication
from polymarket_arb.semantics.rulebook_models import ImplicationRulebook


def test_ambiguity_reduces_final_confidence():
    rb = ImplicationRulebook(
        rulebook_id="implication",
        rulebook_version=1,
        type_weights={"sufficient_for_yes": 0.9},
        ambiguity_penalty_weight=0.5,
        review_threshold=0.5,
    )
    clean = score_implication(
        implication_type="sufficient_for_yes",
        model_confidence=0.8,
        ambiguity_score=0.1,
        rulebook=rb,
    )
    ambiguous = score_implication(
        implication_type="sufficient_for_yes",
        model_confidence=0.8,
        ambiguity_score=0.8,
        rulebook=rb,
    )
    assert clean.final_confidence > ambiguous.final_confidence
