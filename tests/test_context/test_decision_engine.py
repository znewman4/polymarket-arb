"""Context-aware relationship decision gates."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from polymarket_arb.context.decision_engine import apply_context_decisions
from polymarket_arb.storage.base import (
    ContextRelationshipDecisionRow,
    ContextRuleRow,
    RelationshipCandidateRow,
)
from polymarket_arb.storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from polymarket_arb.storage.parquet.context_rules_repo import ParquetContextRulesRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

TS = int(datetime.now(timezone.utc).timestamp() * 1000)
REGISTRY = "configs/context_spaces/context_spaces_v1.yaml"


def _relationship(
    relationship_id: str,
    *,
    subtype: str,
    relationship_type: str = "nested_a_implies_b",
    family: str = "nesting",
    question_a: str = "Will Team win the NBA Finals?",
    question_b: str = "Will Team win its conference?",
    team_a: str | None = None,
    team_b: str | None = None,
    stage_a: str = "",
    stage_b: str = "",
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=relationship_id,
        market_id_a="market_a",
        market_id_b="market_b",
        condition_id_a="cond_a",
        condition_id_b="cond_b",
        token_id_a_yes="a_yes",
        token_id_a_no="a_no",
        token_id_b_yes="b_yes",
        token_id_b_no="b_no",
        question_a=question_a,
        question_b=question_b,
        relationship_type=relationship_type,
        entity_match_score=1.0,
        time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.9,
        model_confidence=1.0,
        final_confidence=0.9,
        validation_status="needs_manual_review",
        rejection_reasons_json="[]",
        rationale_summary="fixture",
        evidence_json="{}",
        rulebook_id="relationship_v2",
        rulebook_version=2,
        rulebook_content_hash="hash",
        relationship_validity_status="needs_manual_review",
        strategy_eligibility_status="manual_review",
        relationship_family=family,
        relationship_subtype=subtype,
        outcome_space_id="fixture_space",
        outcome_subtype_a=subtype,
        outcome_subtype_b=subtype,
        team_a=team_a,
        team_b=team_b,
        stage_a=stage_a,
        stage_b=stage_b,
        strategy_family=family,
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _rule(space: str, rule_type: str, family: str, *, approved: bool = True) -> ContextRuleRow:
    return ContextRuleRow(
        context_rule_id=f"{space}_{rule_type}",
        context_space_id=space,
        context_type="fixture",
        rule_type=rule_type,
        rule_json=json.dumps({"rule_family": family}),
        source_document_ids_json="[]",
        quoted_evidence_json="[]",
        confidence=0.9,
        needs_manual_review=not approved,
        human_review_status="approved" if approved else "pending",
        human_review_notes="fixture",
        valid_from_ms=None,
        valid_to_ms=None,
        extraction_model="manual",
        extraction_prompt_version="manual_rules_v1",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def test_championship_implies_conference_uses_general_sports_space(tmp_data_root):
    """championship_implies_conference subtype routes to the general sports space,
    which has no market-terms requirement — so a single approved world rule gives
    strict_context_valid directly."""
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(
        _relationship("rel_nba", subtype="championship_implies_conference")
    )
    rule_repo = ParquetContextRulesRepository(tmp_data_root)
    # Add world rule for the GENERAL sports space (not NBA-specific)
    space = "sports_championship_conference_progression"
    rule_repo.append(_rule(space, "championship_implies_conference", "world_context"))

    result = apply_context_decisions(tmp_data_root, registry_path=REGISTRY)
    # No market terms required → goes straight to strict_context_valid
    assert result["lane_counts"]["strict_context_valid"] == 1


def test_okc_finals_west_conference_same_topic_maps_to_nba_context(tmp_data_root):
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(
        _relationship(
            "rel_okc_same_topic",
            subtype="same_topic_no_trade",
            relationship_type="same_topic",
            question_a="Will the OKC Thunder win the 2026 NBA Finals?",
            question_b="Will the OKC Thunder win the NBA Western Conference Finals?",
            team_a="OKC Thunder",
            team_b="OKC Thunder",
        )
    )
    ParquetContextRulesRepository(tmp_data_root).append(
        _rule(
            "nba_championship_conference_progression",
            "championship_implies_conference",
            "world_context",
        )
    )

    result = apply_context_decisions(tmp_data_root, registry_path=REGISTRY)

    assert result["lane_counts"]["reviewed_context_valid"] == 1
    decision = next(ParquetContextRelationshipDecisionsRepository(tmp_data_root).iter_latest())
    assert decision.context_space_id == "nba_championship_conference_progression"


def test_okc_finals_west_conference_writes_sports_progression_taxonomy(tmp_data_root):
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(
        _relationship(
            "rel_okc_taxonomy",
            subtype="same_topic_no_trade",
            relationship_type="inverse",
            family="contradiction",
            question_a="Will the Oklahoma City Thunder win the 2026 NBA Finals?",
            question_b="Will the Oklahoma City Thunder win the NBA Western Conference Finals?",
            team_a="Oklahoma City Thunder",
            team_b="Oklahoma City Thunder",
        )
    )
    ParquetContextRulesRepository(tmp_data_root).append(
        _rule(
            "nba_championship_conference_progression",
            "championship_implies_conference",
            "world_context",
        )
    )

    result = apply_context_decisions(
        tmp_data_root,
        registry_path=REGISTRY,
        append_upgraded_relationships=True,
    )

    assert result["lane_counts"]["reviewed_context_valid"] == 1
    rel = ParquetRelationshipCandidatesRepository(tmp_data_root).get_latest("rel_okc_taxonomy")
    assert rel is not None
    assert rel.relationship_type == "nested_a_implies_b"
    assert rel.relationship_subtype == "championship_implies_conference"
    assert rel.relationship_family == "sports_progression"
    assert rel.validation_status == "accepted"
    assert rel.strategy_eligibility_status == "eligible"


def test_spurs_finals_west_conference_same_topic_maps_to_nba_context(tmp_data_root):
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(
        _relationship(
            "rel_spurs_same_topic",
            subtype="same_topic_no_trade",
            relationship_type="same_topic",
            question_a="Will the San Antonio Spurs win the 2026 NBA Finals?",
            question_b="Will the San Antonio Spurs win the NBA Western Conference Finals?",
        )
    )
    ParquetContextRulesRepository(tmp_data_root).append(
        _rule(
            "nba_championship_conference_progression",
            "championship_implies_conference",
            "world_context",
        )
    )

    result = apply_context_decisions(tmp_data_root, registry_path=REGISTRY)

    assert result["lane_counts"]["reviewed_context_valid"] == 1
    decision = next(ParquetContextRelationshipDecisionsRepository(tmp_data_root).iter_latest())
    assert decision.context_space_id == "nba_championship_conference_progression"


def test_unrelated_same_topic_no_trade_stays_research_only(tmp_data_root):
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(
        _relationship(
            "rel_unrelated_same_topic",
            subtype="same_topic_no_trade",
            relationship_type="same_topic",
            question_a="Will the OKC Thunder win the 2026 NBA Finals?",
            question_b="Will the Los Angeles Lakers win the NBA Western Conference Finals?",
            team_a="OKC Thunder",
            team_b="Los Angeles Lakers",
        )
    )

    result = apply_context_decisions(tmp_data_root, registry_path=REGISTRY)

    assert result["lane_counts"]["research_only"] == 1
    decision = next(ParquetContextRelationshipDecisionsRepository(tmp_data_root).iter_latest())
    assert decision.context_space_id == ""


def test_candidate_vs_party_remains_ineligible(tmp_data_root):
    rel = _relationship(
        "rel_candidate_party",
        subtype="candidate_to_party_dependency",
        relationship_type="mutually_exclusive",
        family="dependency",
    )
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(rel)

    result = apply_context_decisions(tmp_data_root, registry_path=REGISTRY)
    assert result["eligibility_counts"]["ineligible"] == 1
    assert result["lane_counts"]["research_only"] == 1


def test_apply_context_writes_decision_for_unmapped_relationship(tmp_data_root):
    """Relationships with unknown subtype must receive research_only, not be silently skipped."""
    rel = _relationship(
        "rel_unknown",
        subtype="totally_unknown_subtype_xyz",
        relationship_type="mutually_exclusive_category",
        family="unknown_family",
    )
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(rel)

    result = apply_context_decisions(tmp_data_root, registry_path=REGISTRY)

    assert result["decisions_written"] == 1
    assert result["context_missing"] == 1
    assert result["lane_counts"].get("research_only", 0) == 1
    assert result["eligibility_counts"].get("ineligible", 0) == 1

    decisions = list(ParquetContextRelationshipDecisionsRepository(tmp_data_root).iter_latest())
    assert len(decisions) == 1
    assert decisions[0].strategy_lane == "research_only"


def test_apply_context_does_not_overwrite_protected_by_default(tmp_data_root):
    """Protected lane decisions are preserved when skip_human_reviewed=True (default).

    Only exploratory_context_auto_approved is protected by default — it represents
    a deliberate human action (auto-approve command), not an engine-generated decision.
    """
    rel = _relationship("rel_prot", subtype="totally_unknown_subtype_prot",
                        relationship_type="mutually_exclusive_category")
    rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
    dec_repo = ParquetContextRelationshipDecisionsRepository(tmp_data_root)
    rel_repo.append(rel)

    # Seed an exploratory_context_auto_approved decision (protected)
    dec_repo.append(ContextRelationshipDecisionRow(
        decision_id="existing_auto",
        relationship_id="rel_prot",
        context_space_id="",
        context_rule_ids_json="[]",
        previous_validation_status="needs_manual_review",
        new_validation_status="needs_manual_review",
        previous_strategy_eligibility="manual_review",
        new_strategy_eligibility="ineligible",
        strategy_lane="exploratory_context_auto_approved",
        decision_reason="auto_approved_by_command",
        evidence_summary="",
        schema_version=1,
        ingested_ts_ms=TS,
    ))

    result = apply_context_decisions(tmp_data_root, registry_path=REGISTRY, skip_human_reviewed=True)

    assert result["decisions_written"] == 0
    assert result["skipped_protected"] == 1
    # The protected decision must still be the only one
    decisions = list(dec_repo.iter_latest())
    assert len(decisions) == 1
    assert decisions[0].strategy_lane == "exploratory_context_auto_approved"


def test_apply_context_overwrites_protected_when_flag_set(tmp_data_root):
    """skip_human_reviewed=False allows overwriting protected decisions."""
    rel = _relationship("rel_over", subtype="totally_unknown_subtype_xyz", relationship_type="mutually_exclusive_category")
    rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
    dec_repo = ParquetContextRelationshipDecisionsRepository(tmp_data_root)
    rel_repo.append(rel)

    dec_repo.append(ContextRelationshipDecisionRow(
        decision_id="prot_over",
        relationship_id="rel_over",
        context_space_id="",
        context_rule_ids_json="[]",
        previous_validation_status="needs_manual_review",
        new_validation_status="needs_manual_review",
        previous_strategy_eligibility="manual_review",
        new_strategy_eligibility="ineligible",
        strategy_lane="exploratory_context_auto_approved",
        decision_reason="auto_approved",
        evidence_summary="",
        schema_version=1,
        ingested_ts_ms=TS,
    ))

    result = apply_context_decisions(tmp_data_root, registry_path=REGISTRY, skip_human_reviewed=False)

    assert result["decisions_written"] == 1
    assert result["skipped_protected"] == 0
    # Latest decision should now be research_only (unknown subtype → context_missing)
    decisions = list(dec_repo.iter_latest())
    assert decisions[0].strategy_lane == "research_only"


def test_same_reference_clock_remains_analysis_only_with_invalidating_rule(tmp_data_root):
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(
        _relationship("rel_clock", subtype="same_reference_clock_only", family="same_reference_only")
    )
    ParquetContextRulesRepository(tmp_data_root).append(
        _rule("same_reference_clock_before_gta_vi", "shared_reference_not_tradeable", "invalidating")
    )

    result = apply_context_decisions(tmp_data_root, registry_path=REGISTRY)
    assert result["lane_counts"]["analysis_only"] == 1
    assert result["eligibility_counts"]["ineligible"] == 1
