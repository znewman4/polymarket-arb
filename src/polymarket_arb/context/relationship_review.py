"""Relationship-level manual review and auto-approve workflow.

Provides three entry points:
- export_relationship_review_queue: write a CSV of needs_manual_review relationships
- import_relationship_review_queue: read human-reviewed CSV, persist decisions
- auto_approve_relationships: mark all eligible relationships as
  exploratory_context_auto_approved for research use only

Auto-approved decisions never reach strict or reviewed lanes and are
explicitly excluded from the headline credibility label.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..storage.base import ContextRelationshipDecisionRow, RelationshipCandidateRow
from ..storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from ..storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

REVIEW_EXPORT_FIELDS = [
    "relationship_id",
    "question_a",
    "question_b",
    "relationship_type",
    "relationship_subtype",
    "relationship_family",
    "final_confidence",
    "validation_status",
    "strategy_eligibility_status",
    "strategy_exclusion_reasons_json",
    "context_status",
    "context_space_id",
    "current_strategy_lane",
    "proposed_review_status",
    "human_review_notes",
]

# Relationships already in these lanes are not eligible for auto-approve
_SUPERIOR_LANES = {"strict_context_valid", "reviewed_context_valid", "exploratory_context_auto_approved"}


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _decision_id(relationship_id: str, lane: str, ts_ms: int) -> str:
    raw = json.dumps([relationship_id, lane, ts_ms], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _build_decision(
    rel: RelationshipCandidateRow,
    *,
    lane: str,
    new_validation_status: str,
    new_eligibility: str,
    reason: str,
    evidence_summary: str,
    context_space_id: str,
    ts_ms: int,
) -> ContextRelationshipDecisionRow:
    return ContextRelationshipDecisionRow(
        decision_id=_decision_id(rel.relationship_id, lane, ts_ms),
        relationship_id=rel.relationship_id,
        context_space_id=context_space_id,
        context_rule_ids_json="[]",
        previous_validation_status=rel.validation_status,
        new_validation_status=new_validation_status,
        previous_strategy_eligibility=rel.strategy_eligibility_status,
        new_strategy_eligibility=new_eligibility,
        strategy_lane=lane,
        decision_reason=reason,
        evidence_summary=evidence_summary,
        schema_version=1,
        ingested_ts_ms=ts_ms,
    )


def export_relationship_review_queue(
    data_root: Path,
    output_path: Path | str,
) -> dict[str, int]:
    """Write a CSV of all needs-manual-review relationships for human inspection.

    Includes the latest context decision metadata (lane, status, space) where
    available so reviewers have full context in one file.
    """
    output_path = Path(output_path)
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    decision_repo = ParquetContextRelationshipDecisionsRepository(data_root)

    latest_decisions: dict[str, ContextRelationshipDecisionRow] = {}
    for dec in decision_repo.iter_latest():
        latest_decisions[dec.relationship_id] = dec

    rows: list[dict[str, Any]] = []
    for rel in rel_repo.iter_latest():
        if rel.validation_status != "needs_manual_review":
            continue
        ctx_dec: ContextRelationshipDecisionRow | None = latest_decisions.get(rel.relationship_id)
        rows.append({
            "relationship_id": rel.relationship_id,
            "question_a": rel.question_a,
            "question_b": rel.question_b,
            "relationship_type": rel.relationship_type,
            "relationship_subtype": rel.relationship_subtype,
            "relationship_family": rel.relationship_family,
            "final_confidence": rel.final_confidence,
            "validation_status": rel.validation_status,
            "strategy_eligibility_status": rel.strategy_eligibility_status,
            "strategy_exclusion_reasons_json": rel.strategy_exclusion_reasons_json,
            "context_status": ctx_dec.new_validation_status if ctx_dec else "not_evaluated",
            "context_space_id": ctx_dec.context_space_id if ctx_dec else "",
            "current_strategy_lane": ctx_dec.strategy_lane if ctx_dec else "none",
            "proposed_review_status": "",
            "human_review_notes": "",
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return {"exported": len(rows)}


def import_relationship_review_queue(
    data_root: Path,
    input_path: Path | str,
) -> dict[str, int]:
    """Append-only import of human review decisions from CSV.

    Rows with proposed_review_status == 'approved' become
    exploratory_context_auto_approved decisions (research only).
    Rows with 'rejected' become research_only / ineligible decisions.
    All other rows are skipped.
    """
    input_path = Path(input_path)
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    decision_repo = ParquetContextRelationshipDecisionsRepository(data_root)

    decisions: list[ContextRelationshipDecisionRow] = []
    skipped = 0
    now = _now_ms()

    with input_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            status = (row.get("proposed_review_status") or "").strip().lower()
            if status not in {"approved", "rejected"}:
                skipped += 1
                continue
            rel_id = (row.get("relationship_id") or "").strip()
            if not rel_id:
                skipped += 1
                continue
            rel = rel_repo.get_latest(rel_id)
            if rel is None:
                skipped += 1
                continue
            if status == "approved":
                lane = "exploratory_context_auto_approved"
                new_status = "auto_approved_experiment"
                eligibility = "eligible"
                reason = "human_approved_for_exploratory_research"
            else:
                lane = "research_only"
                new_status = "rejected"
                eligibility = "ineligible"
                reason = "human_rejected"
            decisions.append(_build_decision(
                rel,
                lane=lane,
                new_validation_status=new_status,
                new_eligibility=eligibility,
                reason=reason,
                evidence_summary=(row.get("human_review_notes") or "")[:240],
                context_space_id=(row.get("context_space_id") or ""),
                ts_ms=now,
            ))

    decision_repo.append_many(decisions)
    return {"imported": len(decisions), "skipped": skipped}


def auto_approve_relationships(data_root: Path) -> dict[str, Any]:
    """Auto-approve all needs_manual_review relationships for exploratory research.

    Creates ContextRelationshipDecisionRow entries with lane
    'exploratory_context_auto_approved'.  These are NEVER promoted to
    reviewed or strict lanes.  They are explicitly excluded from the
    headline credibility label.

    Skips relationships that already have a superior context decision.
    """
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    decision_repo = ParquetContextRelationshipDecisionsRepository(data_root)

    existing_lane: dict[str, str] = {
        dec.relationship_id: dec.strategy_lane
        for dec in decision_repo.iter_latest()
    }

    decisions: list[ContextRelationshipDecisionRow] = []
    skipped_superior = 0
    skipped_not_review = 0
    now = _now_ms()

    for rel in rel_repo.iter_latest():
        current_lane = existing_lane.get(rel.relationship_id, "")
        if current_lane in _SUPERIOR_LANES:
            skipped_superior += 1
            continue
        if rel.validation_status not in {"needs_manual_review"} and current_lane != "exploratory_context_unreviewed":
            skipped_not_review += 1
            continue
        decisions.append(_build_decision(
            rel,
            lane="exploratory_context_auto_approved",
            new_validation_status="auto_approved_experiment",
            new_eligibility="eligible",
            reason="auto_approved_experiment: exploratory_research_only; never_strict_or_reviewed",
            evidence_summary="",
            context_space_id=_space_for_decision(existing_lane, rel.relationship_id),
            ts_ms=now,
        ))

    decision_repo.append_many(decisions)
    return {
        "auto_approved": len(decisions),
        "skipped_already_eligible": skipped_superior,
        "skipped_not_eligible_for_review": skipped_not_review,
    }


def _space_for_decision(
    existing_lane: dict[str, str],
    relationship_id: str,
) -> str:
    return ""
