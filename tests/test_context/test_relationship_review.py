"""Tests for the relationship review export/import/auto-approve workflow."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from polymarket_arb.context.relationship_review import (
    auto_approve_relationships,
    export_relationship_review_queue,
    import_relationship_review_queue,
)
from polymarket_arb.storage.base import RelationshipCandidateRow
from polymarket_arb.storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _rel(
    relationship_id: str,
    *,
    validation_status: str = "needs_manual_review",
    strategy_eligibility_status: str = "manual_review",
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
        question_a="Will Team win the Finals?",
        question_b="Will Team win the Conference?",
        relationship_type="nested_a_implies_b",
        entity_match_score=1.0,
        time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.9,
        model_confidence=1.0,
        final_confidence=0.9,
        validation_status=validation_status,
        rejection_reasons_json="[]",
        rationale_summary="fixture",
        evidence_json="{}",
        rulebook_id="relationship_v2",
        rulebook_version=2,
        rulebook_content_hash="hash",
        relationship_validity_status=validation_status,
        strategy_eligibility_status=strategy_eligibility_status,
        relationship_family="nesting",
        relationship_subtype="championship_implies_conference",
        ingested_ts_ms=TS,
    )


class TestExportRelationshipReviewQueue:
    def test_exports_only_needs_manual_review(self, tmp_path: Path) -> None:
        repo = ParquetRelationshipCandidatesRepository(tmp_path)
        repo.append(_rel("rel_review", validation_status="needs_manual_review"))
        repo.append(_rel("rel_accepted", validation_status="accepted"))
        repo.append(_rel("rel_rejected", validation_status="rejected"))

        output = tmp_path / "review.csv"
        result = export_relationship_review_queue(tmp_path, output)

        assert result["exported"] == 1
        assert output.exists()
        with output.open() as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1
        assert rows[0]["relationship_id"] == "rel_review"

    def test_empty_when_no_manual_review(self, tmp_path: Path) -> None:
        repo = ParquetRelationshipCandidatesRepository(tmp_path)
        repo.append(_rel("rel_accepted", validation_status="accepted"))

        output = tmp_path / "review.csv"
        result = export_relationship_review_queue(tmp_path, output)

        assert result["exported"] == 0
        with output.open() as fh:
            rows = list(csv.DictReader(fh))
        assert rows == []

    def test_output_has_expected_columns(self, tmp_path: Path) -> None:
        repo = ParquetRelationshipCandidatesRepository(tmp_path)
        repo.append(_rel("rel_1", validation_status="needs_manual_review"))

        output = tmp_path / "review.csv"
        export_relationship_review_queue(tmp_path, output)

        with output.open() as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames is not None
            assert "proposed_review_status" in reader.fieldnames
            assert "human_review_notes" in reader.fieldnames
            assert "relationship_subtype" in reader.fieldnames
            assert "current_strategy_lane" in reader.fieldnames

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        repo = ParquetRelationshipCandidatesRepository(tmp_path)
        repo.append(_rel("rel_1", validation_status="needs_manual_review"))

        output = tmp_path / "subdir" / "nested" / "review.csv"
        result = export_relationship_review_queue(tmp_path, output)

        assert output.exists()
        assert result["exported"] == 1


class TestImportRelationshipReviewQueue:
    def _write_csv(self, path: Path, rows: list[dict]) -> None:
        from polymarket_arb.context.relationship_review import REVIEW_EXPORT_FIELDS
        with path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=REVIEW_EXPORT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def test_approved_creates_auto_approved_decision(self, tmp_path: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_path)
        rel_repo.append(_rel("rel_1", validation_status="needs_manual_review"))

        csv_path = tmp_path / "review.csv"
        self._write_csv(csv_path, [{
            "relationship_id": "rel_1",
            "question_a": "Q?",
            "question_b": "Q?",
            "relationship_type": "nested_a_implies_b",
            "relationship_subtype": "championship_implies_conference",
            "relationship_family": "nesting",
            "final_confidence": "0.9",
            "validation_status": "needs_manual_review",
            "strategy_eligibility_status": "manual_review",
            "strategy_exclusion_reasons_json": "[]",
            "context_status": "",
            "context_space_id": "",
            "current_strategy_lane": "none",
            "proposed_review_status": "approved",
            "human_review_notes": "looks valid",
        }])

        result = import_relationship_review_queue(tmp_path, csv_path)
        assert result["imported"] == 1
        assert result["skipped"] == 0

        decision_repo = ParquetContextRelationshipDecisionsRepository(tmp_path)
        decisions = list(decision_repo.iter_latest())
        assert len(decisions) == 1
        assert decisions[0].strategy_lane == "exploratory_context_auto_approved"
        assert decisions[0].new_strategy_eligibility == "eligible"
        assert decisions[0].new_validation_status == "auto_approved_experiment"

    def test_rejected_creates_research_only_decision(self, tmp_path: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_path)
        rel_repo.append(_rel("rel_2", validation_status="needs_manual_review"))

        csv_path = tmp_path / "review.csv"
        self._write_csv(csv_path, [{
            "relationship_id": "rel_2",
            "question_a": "Q?",
            "question_b": "Q?",
            "relationship_type": "nested_a_implies_b",
            "relationship_subtype": "",
            "relationship_family": "",
            "final_confidence": "0.5",
            "validation_status": "needs_manual_review",
            "strategy_eligibility_status": "manual_review",
            "strategy_exclusion_reasons_json": "[]",
            "context_status": "",
            "context_space_id": "",
            "current_strategy_lane": "none",
            "proposed_review_status": "rejected",
            "human_review_notes": "not valid",
        }])

        result = import_relationship_review_queue(tmp_path, csv_path)
        assert result["imported"] == 1

        decision_repo = ParquetContextRelationshipDecisionsRepository(tmp_path)
        decisions = list(decision_repo.iter_latest())
        assert decisions[0].strategy_lane == "research_only"
        assert decisions[0].new_strategy_eligibility == "ineligible"

    def test_skips_blank_status(self, tmp_path: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_path)
        rel_repo.append(_rel("rel_3", validation_status="needs_manual_review"))

        csv_path = tmp_path / "review.csv"
        self._write_csv(csv_path, [{
            "relationship_id": "rel_3",
            "question_a": "", "question_b": "",
            "relationship_type": "", "relationship_subtype": "",
            "relationship_family": "", "final_confidence": "",
            "validation_status": "", "strategy_eligibility_status": "",
            "strategy_exclusion_reasons_json": "",
            "context_status": "", "context_space_id": "",
            "current_strategy_lane": "",
            "proposed_review_status": "",
            "human_review_notes": "",
        }])

        result = import_relationship_review_queue(tmp_path, csv_path)
        assert result["imported"] == 0
        assert result["skipped"] == 1

    def test_skips_unknown_relationship_id(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "review.csv"
        self._write_csv(csv_path, [{
            "relationship_id": "nonexistent_id",
            "question_a": "", "question_b": "",
            "relationship_type": "", "relationship_subtype": "",
            "relationship_family": "", "final_confidence": "",
            "validation_status": "", "strategy_eligibility_status": "",
            "strategy_exclusion_reasons_json": "",
            "context_status": "", "context_space_id": "",
            "current_strategy_lane": "",
            "proposed_review_status": "approved",
            "human_review_notes": "",
        }])

        result = import_relationship_review_queue(tmp_path, csv_path)
        assert result["imported"] == 0
        assert result["skipped"] == 1


class TestAutoApproveRelationships:
    def test_approves_needs_manual_review(self, tmp_path: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_path)
        rel_repo.append(_rel("rel_review", validation_status="needs_manual_review"))
        rel_repo.append(_rel("rel_accepted", validation_status="accepted"))

        result = auto_approve_relationships(tmp_path)
        assert result["auto_approved"] == 1

        decision_repo = ParquetContextRelationshipDecisionsRepository(tmp_path)
        decisions = list(decision_repo.iter_latest())
        assert len(decisions) == 1
        assert decisions[0].relationship_id == "rel_review"
        assert decisions[0].strategy_lane == "exploratory_context_auto_approved"
        assert decisions[0].new_strategy_eligibility == "eligible"
        assert decisions[0].new_validation_status == "auto_approved_experiment"

    def test_skips_already_eligible_lanes(self, tmp_path: Path) -> None:
        from polymarket_arb.storage.base import ContextRelationshipDecisionRow

        rel_repo = ParquetRelationshipCandidatesRepository(tmp_path)
        rel_repo.append(_rel("rel_strict", validation_status="needs_manual_review"))

        decision_repo = ParquetContextRelationshipDecisionsRepository(tmp_path)
        decision_repo.append(ContextRelationshipDecisionRow(
            decision_id="dec_strict",
            relationship_id="rel_strict",
            context_space_id="nba",
            context_rule_ids_json="[]",
            previous_validation_status="needs_manual_review",
            new_validation_status="accepted",
            previous_strategy_eligibility="manual_review",
            new_strategy_eligibility="eligible",
            strategy_lane="strict_context_valid",
            decision_reason="already strict",
            evidence_summary="",
            schema_version=1,
            ingested_ts_ms=TS,
        ))

        result = auto_approve_relationships(tmp_path)
        assert result["auto_approved"] == 0
        assert result["skipped_already_eligible"] == 1

    def test_skips_accepted_relationships(self, tmp_path: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_path)
        rel_repo.append(_rel("rel_acc", validation_status="accepted"))

        result = auto_approve_relationships(tmp_path)
        assert result["auto_approved"] == 0
        assert result["skipped_not_eligible_for_review"] == 1

    def test_idempotent_on_second_call(self, tmp_path: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_path)
        rel_repo.append(_rel("rel_1", validation_status="needs_manual_review"))

        auto_approve_relationships(tmp_path)
        result2 = auto_approve_relationships(tmp_path)

        assert result2["auto_approved"] == 0
        assert result2["skipped_already_eligible"] == 1

    def test_warning_not_in_strict_reviewed_lane(self, tmp_path: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_path)
        rel_repo.append(_rel("rel_1", validation_status="needs_manual_review"))

        auto_approve_relationships(tmp_path)

        decision_repo = ParquetContextRelationshipDecisionsRepository(tmp_path)
        decisions = list(decision_repo.iter_latest())
        assert all(d.strategy_lane == "exploratory_context_auto_approved" for d in decisions)
        assert all(d.strategy_lane not in {"strict_context_valid", "reviewed_context_valid"} for d in decisions)

    def test_also_approves_exploratory_unreviewed(self, tmp_path: Path) -> None:
        from polymarket_arb.storage.base import ContextRelationshipDecisionRow

        rel_repo = ParquetRelationshipCandidatesRepository(tmp_path)
        rel_repo.append(_rel("rel_explor", validation_status="accepted"))

        decision_repo = ParquetContextRelationshipDecisionsRepository(tmp_path)
        decision_repo.append(ContextRelationshipDecisionRow(
            decision_id="dec_explor",
            relationship_id="rel_explor",
            context_space_id="nba",
            context_rule_ids_json="[]",
            previous_validation_status="accepted",
            new_validation_status="needs_manual_review",
            previous_strategy_eligibility="ineligible",
            new_strategy_eligibility="ineligible",
            strategy_lane="exploratory_context_unreviewed",
            decision_reason="unreviewed context",
            evidence_summary="",
            schema_version=1,
            ingested_ts_ms=TS,
        ))

        result = auto_approve_relationships(tmp_path)
        assert result["auto_approved"] == 1


class TestAutoApprovedLaneSafety:
    """Verify auto-approved results never reach strict/reviewed credibility."""

    def test_auto_approved_decision_reason_contains_warning(self, tmp_path: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_path)
        rel_repo.append(_rel("rel_1", validation_status="needs_manual_review"))

        auto_approve_relationships(tmp_path)

        decision_repo = ParquetContextRelationshipDecisionsRepository(tmp_path)
        decisions = list(decision_repo.iter_latest())
        assert "exploratory" in decisions[0].decision_reason.lower()
        assert "never" in decisions[0].decision_reason.lower()
