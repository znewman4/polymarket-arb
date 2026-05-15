from __future__ import annotations

from polymarket_arb.semantics.ambiguity_scorer import score_ambiguity
from polymarket_arb.semantics.rulebook_models import AmbiguityRulebook
from tests.test_nlp.test_rulebooks import _row


def _rulebook(vague_weight: float) -> AmbiguityRulebook:
    return AmbiguityRulebook(
        rulebook_id="ambiguity",
        rulebook_version=1,
        flag_severities={"vague_deadline": vague_weight, "low_semantic_confidence": 0.6},
        combination_rule="max_then_average",
        review_threshold=0.5,
    )


def test_weight_change_changes_score():
    row = _row(temporal_resolution="vague")
    low = score_ambiguity(row, _rulebook(0.2))
    high = score_ambiguity(row, _rulebook(0.9))
    assert high.score > low.score
    assert "vague_deadline" in high.flags
