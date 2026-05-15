from __future__ import annotations

from polymarket_arb.nlp.rulebooks import RULEBOOK_ID, RULEBOOK_VERSION, score_semantics_row
from polymarket_arb.storage.base import MarketSemanticsRow


def _row(
    *,
    confidence: float = 0.8,
    temporal_resolution: str = "exact_date",
    positive: str = "Yes if X happens",
    negative: str = "No otherwise",
    flags: list[str] | None = None,
    needs_review: bool = False,
) -> MarketSemanticsRow:
    return MarketSemanticsRow(
        source_market_id="m1",
        source_condition_id="0xc",
        question="q?",
        canonical_question="q?",
        market_type="binary",
        subject_entities=[],
        event_entities=[],
        temporal_phrase=None,
        temporal_phrase_normalized=None,
        temporal_resolution=temporal_resolution,
        exact_deadline_ms=None,
        date_constraints_json="{}",
        jurisdiction=None,
        positive_resolution_condition=positive,
        negative_resolution_condition=negative,
        necessary_conditions_for_yes=[],
        sufficient_conditions_for_yes=[],
        necessary_conditions_for_no=[],
        sufficient_conditions_for_no=[],
        evidence_required=[],
        ambiguity_flags=flags or [],
        ambiguity_score=None,
        semantic_confidence=confidence,
        needs_manual_review=needs_review,
        explanation_summary=None,
        flag_rationales_json=None,
        uncertainty_notes_json=None,
        rule_curation_notes_json=None,
        raw_response_hash="r" * 64,
        model_name="mock",
        prompt_version="market_semantics_v1",
        rulebook_id=None,
        rulebook_version=None,
        extraction_id="e1",
        schema_version=1,
        ingested_ts_ms=1,
    )


def test_rulebook_adds_score_and_metadata():
    scored = score_semantics_row(_row())
    assert scored.rulebook_id == RULEBOOK_ID
    assert scored.rulebook_version == RULEBOOK_VERSION
    assert scored.ambiguity_score is not None
    assert 0 <= scored.ambiguity_score <= 1


def test_rulebook_flags_low_confidence_and_missing_conditions():
    scored = score_semantics_row(
        _row(
            confidence=0.3,
            temporal_resolution="vague",
            positive="(stub)",
            negative="unknown",
        )
    )
    assert scored.needs_manual_review is True
    assert "low_semantic_confidence" in scored.ambiguity_flags
    assert "vague_deadline" in scored.ambiguity_flags
    assert "missing_resolution_conditions" in scored.ambiguity_flags
    assert scored.ambiguity_score is not None and scored.ambiguity_score >= 0.5


def test_rulebook_preserves_llm_review_flag():
    scored = score_semantics_row(_row(needs_review=True))
    assert scored.needs_manual_review is True
