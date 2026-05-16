"""Phase E transitive closure expansion tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from polymarket_arb.relationships.expansion.transitive_closure import (
    run_transitive_closure_expansion,
)
from polymarket_arb.storage.base import ContextRelationshipDecisionRow, RelationshipCandidateRow
from polymarket_arb.storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _rel(
    rel_id: str,
    market_a: str,
    market_b: str,
    *,
    outcome_space_id: str = "btc_threshold",
    relationship_type: str = "nested_a_implies_b",
    subtype_a: str = "price_hits_level_before_date",
    subtype_b: str = "price_hits_level_before_date",
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=rel_id,
        market_id_a=market_a,
        market_id_b=market_b,
        condition_id_a=f"cond_{market_a}",
        condition_id_b=f"cond_{market_b}",
        token_id_a_yes=f"{market_a}_yes",
        token_id_a_no=f"{market_a}_no",
        token_id_b_yes=f"{market_b}_yes",
        token_id_b_no=f"{market_b}_no",
        question_a=f"Will {market_a} happen?",
        question_b=f"Will {market_b} happen?",
        relationship_type=relationship_type,
        entity_match_score=1.0,
        time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.95,
        model_confidence=1.0,
        final_confidence=0.95,
        validation_status="accepted",
        rejection_reasons_json="[]",
        rationale_summary="fixture",
        evidence_json="{}",
        rulebook_id="deterministic_expansion",
        rulebook_version=1,
        rulebook_content_hash=rel_id,
        relationship_validity_status="accepted",
        strategy_eligibility_status="eligible",
        relationship_family="nesting",
        relationship_subtype="threshold_higher_implies_lower",
        outcome_space_id=outcome_space_id,
        outcome_subtype_a=subtype_a,
        outcome_subtype_b=subtype_b,
        entity_type_a="threshold",
        entity_type_b="threshold",
        strategy_family="nesting",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _decision(rel_id: str, lane: str = "strict_context_valid") -> ContextRelationshipDecisionRow:
    return ContextRelationshipDecisionRow(
        decision_id=f"dec_{rel_id}_{lane}",
        relationship_id=rel_id,
        context_space_id="threshold_nesting",
        context_rule_ids_json=json.dumps(["threshold_monotone_nesting"]),
        previous_validation_status="needs_manual_review",
        new_validation_status="accepted",
        previous_strategy_eligibility="needs_manual_review",
        new_strategy_eligibility="eligible",
        strategy_lane=lane,
        decision_reason="fixture",
        evidence_summary="fixture",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _write(tmp_data_root, rels: list[RelationshipCandidateRow], lanes: dict[str, str] | None = None) -> None:
    ParquetRelationshipCandidatesRepository(tmp_data_root).append_many(rels)
    lane_map = lanes or {}
    ParquetContextRelationshipDecisionsRepository(tmp_data_root).append_many(
        [_decision(rel.relationship_id, lane_map.get(rel.relationship_id, "strict_context_valid")) for rel in rels]
    )


def test_threshold_closure_emits_provenance(tmp_data_root):
    rels = [
        _rel("rel_200_150", "btc_200", "btc_150"),
        _rel("rel_150_100", "btc_150", "btc_100"),
    ]
    _write(tmp_data_root, rels)

    result = run_transitive_closure_expansion(tmp_data_root, dry_run=False)
    stored = list(ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())
    closure = [r for r in stored if r.relationship_subtype == "transitive_closure"]

    assert result.emitted_count == 1
    assert len(closure) == 1
    row = closure[0]
    assert row.market_id_a == "btc_200"
    assert row.market_id_b == "btc_100"
    evidence = json.loads(row.evidence_json)
    assert evidence["parse_evidence"]["closure_depth"] == 2
    assert evidence["parse_evidence"]["inferred_from_relationship_ids"] == [
        "rel_200_150",
        "rel_150_100",
    ]
    assert evidence["parse_evidence"]["closure_source_lane"] == "strict_context_valid"


def test_existing_direct_edge_is_not_reemitted(tmp_data_root):
    rels = [
        _rel("rel_200_150", "btc_200", "btc_150"),
        _rel("rel_150_100", "btc_150", "btc_100"),
        _rel("rel_200_100", "btc_200", "btc_100"),
    ]
    _write(tmp_data_root, rels)

    result = run_transitive_closure_expansion(tmp_data_root, dry_run=True)

    assert result.emitted_count == 0
    assert result.skipped_existing >= 1


def test_cross_outcome_space_paths_do_not_close(tmp_data_root):
    rels = [
        _rel("rel_a_b", "a", "b", outcome_space_id="season_2026"),
        _rel("rel_b_c", "b", "c", outcome_space_id="season_2027"),
    ]
    _write(tmp_data_root, rels)

    result = run_transitive_closure_expansion(tmp_data_root, dry_run=True)

    assert result.emitted_count == 0


def test_strict_mode_excludes_reviewed_lane_but_exploratory_allows_it(tmp_data_root):
    rels = [
        _rel("rel_a_b", "a", "b"),
        _rel("rel_b_c", "b", "c"),
    ]
    _write(tmp_data_root, rels, lanes={"rel_b_c": "reviewed_context_valid"})

    strict = run_transitive_closure_expansion(tmp_data_root, dry_run=True, source_mode="strict")
    exploratory = run_transitive_closure_expansion(
        tmp_data_root,
        dry_run=True,
        source_mode="exploratory",
    )

    assert strict.emitted_count == 0
    assert strict.guard_failure_counts["lane_not_allowed"] == 1
    assert exploratory.emitted_count == 1
    source_lanes = exploratory.audit_rows[0]["source_lanes"].split(",")
    assert source_lanes == ["strict_context_valid", "reviewed_context_valid"]


def test_exploratory_mixed_lane_label_in_evidence(tmp_data_root):
    rels = [
        _rel("rel_a_b", "a", "b"),
        _rel("rel_b_c", "b", "c"),
    ]
    _write(tmp_data_root, rels, lanes={"rel_b_c": "exploratory_context_unreviewed"})

    result = run_transitive_closure_expansion(
        tmp_data_root,
        dry_run=False,
        source_mode="exploratory",
    )
    stored = list(ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())
    closure = next(r for r in stored if r.relationship_subtype == "transitive_closure")
    evidence = json.loads(closure.evidence_json)

    assert result.emitted_count == 1
    assert evidence["parse_evidence"]["closure_source_lane"] == "mixed"


def test_nested_b_direction_is_normalised_before_closure(tmp_data_root):
    rels = [
        _rel("rel_b_a", "a", "b", relationship_type="nested_b_implies_a"),
        _rel("rel_a_c", "a", "c"),
    ]
    _write(tmp_data_root, rels)

    result = run_transitive_closure_expansion(tmp_data_root, dry_run=False)
    stored = list(ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())
    closure = next(r for r in stored if r.relationship_subtype == "transitive_closure")

    assert result.emitted_count == 1
    assert closure.market_id_a == "b"
    assert closure.market_id_b == "c"
