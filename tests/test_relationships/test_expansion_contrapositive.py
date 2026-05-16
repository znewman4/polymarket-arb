"""Phase E contrapositive expansion tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from polymarket_arb.relationships.expansion.contrapositive import (
    contrapositive_payoff_equivalent,
    make_contrapositive_row,
    run_contrapositive_expansion,
)
from polymarket_arb.storage.base import RelationshipCandidateRow
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _rel(
    rel_id: str = "rel_ab",
    *,
    relationship_type: str = "nested_a_implies_b",
    token_id_a_no: str | None = "a_no",
    token_id_b_no: str | None = "b_no",
    final_confidence: float = 0.95,
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=rel_id,
        market_id_a="market_a",
        market_id_b="market_b",
        condition_id_a="cond_a",
        condition_id_b="cond_b",
        token_id_a_yes="a_yes",
        token_id_a_no=token_id_a_no,
        token_id_b_yes="b_yes",
        token_id_b_no=token_id_b_no,
        question_a="Will A happen?",
        question_b="Will B happen?",
        relationship_type=relationship_type,
        entity_match_score=1.0,
        time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=final_confidence,
        model_confidence=1.0,
        final_confidence=final_confidence,
        validation_status="accepted",
        rejection_reasons_json="[]",
        rationale_summary="fixture",
        evidence_json="{}",
        rulebook_id="deterministic_expansion",
        rulebook_version=1,
        rulebook_content_hash="fixture",
        relationship_validity_status="accepted",
        strategy_eligibility_status="eligible",
        relationship_family="nesting",
        relationship_subtype="threshold_higher_implies_lower",
        outcome_space_id="space_1",
        outcome_subtype_a="price_hits_level_before_date",
        outcome_subtype_b="price_hits_level_before_date",
        entity_type_a="threshold",
        entity_type_b="threshold",
        strategy_family="nesting",
        schema_version=1,
        ingested_ts_ms=TS,
    )


@pytest.mark.parametrize(
    ("p_a", "p_b"),
    [
        (Decimal("0.20"), Decimal("0.40")),
        (Decimal("0.40"), Decimal("0.40")),
        (Decimal("0.65"), Decimal("0.40")),
        (Decimal("0.99"), Decimal("0.01")),
    ],
)
def test_payoff_identity_holds_for_yes_and_no_inequalities(p_a, p_b):
    assert contrapositive_payoff_equivalent(p_a, p_b)


def test_make_contrapositive_swaps_market_order_and_token_roles():
    row = make_contrapositive_row(_rel())

    assert row.market_id_a == "market_b"
    assert row.market_id_b == "market_a"
    assert row.relationship_type == "nested_a_implies_b"
    assert row.relationship_subtype == "implication_contrapositive"
    assert row.token_id_a_yes == "b_no"
    assert row.token_id_a_no == "b_yes"
    assert row.token_id_b_yes == "a_no"
    assert row.token_id_b_no == "a_yes"
    evidence = json.loads(row.evidence_json)
    assert evidence["parse_evidence"]["generated_from_relationship_id"] == "rel_ab"
    assert evidence["guard_results"]["no_shorting_required"] is True


def test_nested_b_source_direction_still_builds_not_broad_to_not_narrow():
    row = make_contrapositive_row(_rel(relationship_type="nested_b_implies_a"))

    assert row.market_id_a == "market_a"
    assert row.market_id_b == "market_b"
    assert row.token_id_a_yes == "a_no"
    assert row.token_id_b_yes == "b_no"


def test_contrapositive_expansion_emits_one_row(tmp_data_root):
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(_rel())

    result = run_contrapositive_expansion(tmp_data_root, dry_run=False)
    stored = list(ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())

    assert result.emitted_count == 1
    assert len(stored) == 2
    assert any(r.relationship_subtype == "implication_contrapositive" for r in stored)


def test_contrapositive_deduplicates_by_source_relationship(tmp_data_root):
    repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
    repo.append(_rel())

    first = run_contrapositive_expansion(tmp_data_root, dry_run=False)
    second = run_contrapositive_expansion(tmp_data_root, dry_run=False)

    assert first.emitted_count == 1
    assert second.emitted_count == 0
    assert second.skipped_existing == 1


def test_contrapositive_requires_all_yes_and_no_tokens(tmp_data_root):
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(_rel(token_id_b_no=None))

    result = run_contrapositive_expansion(tmp_data_root, dry_run=True)

    assert result.emitted_count == 0
    assert result.guard_failure_counts["missing_b_yes_or_no_token"] == 1


def test_low_confidence_implication_not_expanded(tmp_data_root):
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(_rel(final_confidence=0.40))

    result = run_contrapositive_expansion(tmp_data_root, dry_run=True)

    assert result.emitted_count == 0
