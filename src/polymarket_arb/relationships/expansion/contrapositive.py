"""Contrapositive expansion pass for deterministic implications.

For each deterministic implication A -> B, emit a companion proposition-level
relationship NOT(B) -> NOT(A). This lets the existing buy-only replay evaluate
the NO-side proposition directly by swapping the YES/NO token roles:

    original A -> B:
      violation P(A) > P(B), buy NO_A + YES_B

    contrapositive NOT(B) -> NOT(A):
      violation P(NO_B) > P(NO_A), buy YES_B + NO_A

Those are the same two buy legs. No shorting structure is introduced.

RESEARCH-ONLY — no live trading.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from ...storage.base import RelationshipCandidateRow
from ...storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from . import ExpansionResult
from .audit import (
    GENERATED_BY,
    build_classification_reason_json,
    build_evidence_json,
    make_relationship_id,
    now_ms,
)

_PASS_ID = "contrapositive_v1"
_PASS_VERSION = 1

_NESTED_TYPES = frozenset({"nested_a_implies_b", "nested_b_implies_a"})


def run_contrapositive_expansion(
    data_root: Path,
    *,
    dry_run: bool = False,
    min_confidence: float = 0.80,
) -> ExpansionResult:
    """Emit NO-side contrapositive companion rows for deterministic implications."""
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    rels = list(rel_repo.iter_latest())

    existing_source_ids = _existing_contrapositive_sources(rels)
    existing_structural_keys = {_contrapositive_key(r) for r in rels if _is_contrapositive(r)}

    new_rows: list[RelationshipCandidateRow] = []
    audit_rows: list[dict[str, Any]] = []
    skipped_existing = 0
    skipped_guard = 0
    guard_failures: dict[str, int] = defaultdict(int)

    for rel in sorted(rels, key=lambda r: r.relationship_id):
        if not _is_source_implication(rel, min_confidence=min_confidence):
            continue
        if rel.relationship_id in existing_source_ids:
            skipped_existing += 1
            continue

        reason = _source_guard_failure(rel)
        if reason is not None:
            skipped_guard += 1
            guard_failures[reason] += 1
            audit_rows.append({
                "pass": _PASS_ID,
                "source_relationship_id": rel.relationship_id,
                "rejected_by": reason,
            })
            continue

        row = make_contrapositive_row(rel)
        key = _contrapositive_key(row)
        if key in existing_structural_keys:
            skipped_existing += 1
            continue

        existing_structural_keys.add(key)
        existing_source_ids.add(rel.relationship_id)
        new_rows.append(row)
        audit_rows.append({
            "pass": _PASS_ID,
            "source_relationship_id": rel.relationship_id,
            "relationship_id": row.relationship_id,
            "source_type": rel.relationship_type,
            "contrapositive_type": row.relationship_type,
            "validation_status": row.validation_status,
        })

    if not dry_run and new_rows:
        rel_repo.append_many(new_rows)

    return ExpansionResult(
        pass_id=_PASS_ID,
        emitted_count=len(new_rows),
        skipped_existing=skipped_existing,
        skipped_guard_fail=skipped_guard,
        needs_review_count=0,
        guard_failure_counts=dict(guard_failures),
        audit_rows=audit_rows,
        dry_run=dry_run,
    )


def make_contrapositive_row(source: RelationshipCandidateRow) -> RelationshipCandidateRow:
    """Build a token-swapped NOT(B) -> NOT(A) companion relationship."""
    narrow, broad = _source_sides(source)
    ts = now_ms()

    # Contrapositive of narrow -> broad is NO_broad -> NO_narrow.
    a = broad
    b = narrow
    guard = {
        "source_relationship_id": source.relationship_id,
        "source_relationship_type": source.relationship_type,
        "source_direction": f"{narrow['market_id']} -> {broad['market_id']}",
        "contrapositive_direction": f"NO({broad['market_id']}) -> NO({narrow['market_id']})",
        "payoff_identity": "P(YES_A)<=P(YES_B) iff P(NO_B)<=P(NO_A)",
        "no_shorting_required": True,
    }
    parse = {
        "expansion_pass_id": _PASS_ID,
        "generated_from_relationship_id": source.relationship_id,
        "inherited_outcome_space_id": source.outcome_space_id,
    }

    row = RelationshipCandidateRow(
        relationship_id=make_relationship_id(),
        market_id_a=a["market_id"],
        market_id_b=b["market_id"],
        condition_id_a=a["condition_id"],
        condition_id_b=b["condition_id"],
        token_id_a_yes=a["token_no"],
        token_id_a_no=a["token_yes"],
        token_id_b_yes=b["token_no"],
        token_id_b_no=b["token_yes"],
        question_a=a["question"],
        question_b=b["question"],
        relationship_type="nested_a_implies_b",
        entity_match_score=source.entity_match_score,
        time_scope_match_score=source.time_scope_match_score,
        resolution_criteria_match_score=source.resolution_criteria_match_score,
        threshold_relation_json=source.threshold_relation_json,
        semantic_similarity_score=source.semantic_similarity_score,
        deterministic_confidence=source.deterministic_confidence,
        model_confidence=source.model_confidence,
        final_confidence=source.final_confidence,
        validation_status=source.validation_status,
        rejection_reasons_json=source.rejection_reasons_json,
        rationale_summary=(
            f"Expansion ({_PASS_ID}): contrapositive companion for "
            f"{source.relationship_id}; NOT(B) implies NOT(A)"
        ),
        evidence_json=build_evidence_json(_PASS_ID, guard, parse),
        rulebook_id=GENERATED_BY,
        rulebook_version=_PASS_VERSION,
        rulebook_content_hash=f"{_PASS_ID}:{source.relationship_id}",
        relationship_validity_status=source.relationship_validity_status or source.validation_status,
        strategy_eligibility_status=source.strategy_eligibility_status,
        strategy_exclusion_reasons_json=source.strategy_exclusion_reasons_json,
        relationship_family=source.relationship_family or "nesting",
        proposition_type=source.proposition_type,
        event_atoms_json=source.event_atoms_json,
        terms_match_score=source.terms_match_score,
        outcome_space_match_score=source.outcome_space_match_score,
        reference_event_match_score=source.reference_event_match_score,
        candidate_a=a["candidate"],
        candidate_b=b["candidate"],
        shared_event=source.shared_event,
        reference_event=source.reference_event,
        relationship_subtype="implication_contrapositive",
        outcome_space_id=source.outcome_space_id,
        outcome_subtype_a=a["outcome_subtype"],
        outcome_subtype_b=b["outcome_subtype"],
        entity_type_a=a["entity_type"],
        entity_type_b=b["entity_type"],
        stage_a=a["stage"],
        stage_b=b["stage"],
        party_a=a["party"],
        party_b=b["party"],
        team_a=a["team"],
        team_b=b["team"],
        shared_reference_event=source.shared_reference_event,
        classification_reason_json=build_classification_reason_json(
            _PASS_ID, "NO-side contrapositive of deterministic implication"
        ),
        mixed_subtype_reason=source.mixed_subtype_reason,
        strategy_family="implication_contrapositive",
        strategy_eligible_reason=f"deterministic_{_PASS_ID}",
        schema_version=source.schema_version,
        ingested_ts_ms=ts,
    )
    return row


def contrapositive_payoff_equivalent(p_yes_a: Decimal, p_yes_b: Decimal) -> bool:
    """Return True when implication and contrapositive inequalities agree.

    P(A) <= P(B) is equivalent to P(not B) <= P(not A), where
    P(not X) = 1 - P(X).
    """
    yes_inequality = p_yes_a <= p_yes_b
    no_inequality = (Decimal("1") - p_yes_b) <= (Decimal("1") - p_yes_a)
    return yes_inequality == no_inequality


def _source_sides(source: RelationshipCandidateRow) -> tuple[dict[str, Any], dict[str, Any]]:
    side_a = _side(source, "a")
    side_b = _side(source, "b")
    if source.relationship_type == "nested_a_implies_b":
        return side_a, side_b
    return side_b, side_a


def _side(source: RelationshipCandidateRow, side: str) -> dict[str, Any]:
    payload = asdict(source)
    suffix = f"_{side}"
    return {
        "market_id": payload[f"market_id{suffix}"],
        "condition_id": payload[f"condition_id{suffix}"],
        "token_yes": payload[f"token_id{suffix}_yes"],
        "token_no": payload[f"token_id{suffix}_no"],
        "question": payload[f"question{suffix}"],
        "outcome_subtype": payload.get(f"outcome_subtype{suffix}") or "",
        "entity_type": payload.get(f"entity_type{suffix}") or "",
        "stage": payload.get(f"stage{suffix}") or "",
        "candidate": payload.get(f"candidate{suffix}"),
        "party": payload.get(f"party{suffix}"),
        "team": payload.get(f"team{suffix}"),
    }


def _is_source_implication(rel: RelationshipCandidateRow, *, min_confidence: float) -> bool:
    if rel.relationship_type not in _NESTED_TYPES:
        return False
    if _is_contrapositive(rel):
        return False
    if rel.validation_status == "rejected":
        return False
    return rel.final_confidence >= min_confidence


def _source_guard_failure(rel: RelationshipCandidateRow) -> str | None:
    if not rel.token_id_a_yes or not rel.token_id_a_no:
        return "missing_a_yes_or_no_token"
    if not rel.token_id_b_yes or not rel.token_id_b_no:
        return "missing_b_yes_or_no_token"
    if rel.market_id_a == rel.market_id_b:
        return "same_market"
    return None


def _is_contrapositive(rel: RelationshipCandidateRow) -> bool:
    if rel.relationship_subtype == "implication_contrapositive":
        return True
    if rel.rulebook_content_hash.startswith(f"{_PASS_ID}:"):
        return True
    try:
        evidence = json.loads(rel.evidence_json or "{}")
    except json.JSONDecodeError:
        return False
    return evidence.get("expansion_pass_id") == _PASS_ID


def _existing_contrapositive_sources(rels: list[RelationshipCandidateRow]) -> set[str]:
    source_ids: set[str] = set()
    for rel in rels:
        if not _is_contrapositive(rel):
            continue
        if rel.rulebook_content_hash.startswith(f"{_PASS_ID}:"):
            source_ids.add(rel.rulebook_content_hash.split(":", 1)[1])
            continue
        try:
            evidence = json.loads(rel.evidence_json or "{}")
        except json.JSONDecodeError:
            continue
        parse = evidence.get("parse_evidence", {})
        source_id = parse.get("generated_from_relationship_id")
        if source_id:
            source_ids.add(str(source_id))
    return source_ids


def _contrapositive_key(rel: RelationshipCandidateRow) -> tuple[str, str, str, str, str]:
    return (
        rel.market_id_a,
        rel.market_id_b,
        rel.token_id_a_yes or "",
        rel.token_id_b_yes or "",
        rel.relationship_subtype,
    )
