from __future__ import annotations

from polymarket_arb.nlp.implications import (
    IMPLICATION_RULEBOOK_ID,
    IMPLICATION_RULEBOOK_VERSION,
    extract_implications_from_semantics,
)
from polymarket_arb.storage.base import MarketSemanticsRow


def _row() -> MarketSemanticsRow:
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
        temporal_resolution="exact_date",
        exact_deadline_ms=1,
        date_constraints_json="{}",
        jurisdiction=None,
        positive_resolution_condition="y",
        negative_resolution_condition="n",
        necessary_conditions_for_yes=["official source confirms X"],
        sufficient_conditions_for_yes=["X happens before deadline"],
        necessary_conditions_for_no=["official source rejects X"],
        sufficient_conditions_for_no=["X cannot happen before deadline"],
        evidence_required=[],
        ambiguity_flags=[],
        ambiguity_score=0.2,
        semantic_confidence=0.8,
        needs_manual_review=False,
        explanation_summary=None,
        flag_rationales_json=None,
        uncertainty_notes_json=None,
        rule_curation_notes_json=None,
        raw_response_hash="r" * 64,
        model_name="mock",
        prompt_version="market_semantics_v1",
        rulebook_id="semantic_ambiguity",
        rulebook_version=1,
        extraction_id="e1",
        schema_version=1,
        ingested_ts_ms=1,
    )


def test_extract_implications_from_semantics():
    rows = extract_implications_from_semantics(_row())
    assert {r.implication_type for r in rows} == {
        "necessary_for_yes",
        "sufficient_for_yes",
        "necessary_for_no",
        "sufficient_for_no",
    }
    assert all(r.rulebook_id == IMPLICATION_RULEBOOK_ID for r in rows)
    assert all(r.rulebook_version == IMPLICATION_RULEBOOK_VERSION for r in rows)
    assert all(0 <= r.deterministic_score <= 1 for r in rows)
    assert all(0 <= r.final_confidence <= 1 for r in rows)
    assert all(len(r.implication_id) == 64 for r in rows)
