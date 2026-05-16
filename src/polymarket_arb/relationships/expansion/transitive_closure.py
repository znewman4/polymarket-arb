"""Transitive closure expansion for deterministic nesting relationships.

Builds a DAG of context-approved implication edges within each outcome space
and emits inferred edges such as:

    exact 1st -> top 2
    top 2    -> top 4
    -----------------
    exact 1st -> top 4

Strict mode closes only over ``strict_context_valid`` edges. Exploratory mode
also allows reviewed/exploratory lanes and labels mixed-lane output in the
evidence JSON.

RESEARCH-ONLY — no live trading.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ...storage.base import ContextRelationshipDecisionRow, RelationshipCandidateRow
from ...storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from ...storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from . import ExpansionResult
from .audit import (
    GENERATED_BY,
    build_classification_reason_json,
    build_evidence_json,
    make_relationship_id,
    now_ms,
)

_PASS_ID = "transitive_closure_v1"
_PASS_VERSION = 1

_STRICT_LANES = frozenset({"strict_context_valid"})
_EXPLORATORY_LANES = frozenset({
    "strict_context_valid",
    "reviewed_context_valid",
    "exploratory_context_unreviewed",
    "exploratory_context_auto_approved",
})


@dataclass(frozen=True)
class _Edge:
    src: str
    dst: str
    relationship_id: str
    rel: RelationshipCandidateRow
    lane: str


def run_transitive_closure_expansion(
    data_root: Path,
    *,
    dry_run: bool = False,
    source_mode: str = "strict",
    max_depth: int | None = None,
    max_pairs_per_group: int = 500,
) -> ExpansionResult:
    """Run transitive closure over context-approved nesting edges."""
    if source_mode not in {"strict", "exploratory"}:
        raise ValueError("source_mode must be 'strict' or 'exploratory'")
    if max_depth is None:
        max_depth = 3 if source_mode == "strict" else 4

    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    decision_repo = ParquetContextRelationshipDecisionsRepository(data_root)

    rels = list(rel_repo.iter_latest())
    decisions_by_rel = _latest_decisions_by_relationship(decision_repo.iter_latest())
    allowed_lanes = _STRICT_LANES if source_mode == "strict" else _EXPLORATORY_LANES

    direct_edges: set[tuple[str, str, str, str]] = set()
    groups: dict[tuple[str, str], list[_Edge]] = defaultdict(list)
    audit_rows: list[dict[str, Any]] = []
    guard_failures: dict[str, int] = defaultdict(int)
    skipped_guard = 0

    for rel in rels:
        direction = _edge_direction(rel)
        if direction is None:
            continue
        group_key = _group_key(rel)
        direct_edges.add((*direction, group_key[0], group_key[1]))

        decision = decisions_by_rel.get(rel.relationship_id)
        if decision is None:
            skipped_guard += 1
            guard_failures["missing_context_decision"] += 1
            continue
        if decision.strategy_lane not in allowed_lanes:
            skipped_guard += 1
            guard_failures["lane_not_allowed"] += 1
            continue
        if decision.new_strategy_eligibility != "eligible":
            skipped_guard += 1
            guard_failures["not_strategy_eligible"] += 1
            continue

        src, dst = direction
        groups[group_key].append(_Edge(src, dst, rel.relationship_id, rel, decision.strategy_lane))

    new_rows: list[RelationshipCandidateRow] = []
    seen_closures: set[tuple[str, str, str, str]] = set()
    skipped_existing = 0

    for group_key, edges in groups.items():
        if len(edges) < 2:
            continue
        adjacency: dict[str, list[_Edge]] = defaultdict(list)
        for edge in edges:
            adjacency[edge.src].append(edge)

        emitted_in_group = 0
        for start in sorted(adjacency):
            stack: list[tuple[str, list[_Edge], set[str]]] = [(start, [], {start})]
            while stack:
                node, path, visited = stack.pop()
                if len(path) >= max_depth:
                    continue
                for edge in sorted(adjacency.get(node, []), key=lambda e: e.relationship_id):
                    if edge.dst in visited:
                        guard_failures["cycle_detected"] += 1
                        continue
                    next_path = [*path, edge]
                    next_visited = {*visited, edge.dst}
                    if len(next_path) >= 2:
                        closure_key = (start, edge.dst, group_key[0], group_key[1])
                        if closure_key in direct_edges or closure_key in seen_closures:
                            skipped_existing += 1
                        else:
                            row = _make_closure_row(
                                start=start,
                                end=edge.dst,
                                path=next_path,
                                group_key=group_key,
                                source_mode=source_mode,
                            )
                            if row is not None:
                                new_rows.append(row)
                                seen_closures.add(closure_key)
                                audit_rows.append({
                                    "pass": _PASS_ID,
                                    "relationship_id": row.relationship_id,
                                    "market_a": row.market_id_a,
                                    "market_b": row.market_id_b,
                                    "closure_depth": len(next_path),
                                    "source_relationship_ids": ",".join(e.relationship_id for e in next_path),
                                    "source_lanes": ",".join(e.lane for e in next_path),
                                })
                                emitted_in_group += 1
                                if emitted_in_group >= max_pairs_per_group:
                                    break
                    stack.append((edge.dst, next_path, next_visited))
                if emitted_in_group >= max_pairs_per_group:
                    break
            if emitted_in_group >= max_pairs_per_group:
                break

    if not dry_run and new_rows:
        rel_repo.append_many(new_rows)

    return ExpansionResult(
        pass_id=_PASS_ID,
        emitted_count=len(new_rows),
        skipped_existing=skipped_existing,
        skipped_guard_fail=skipped_guard,
        needs_review_count=sum(1 for row in new_rows if row.validation_status == "needs_manual_review"),
        guard_failure_counts=dict(guard_failures),
        audit_rows=audit_rows,
        dry_run=dry_run,
    )


def _latest_decisions_by_relationship(
    decisions: Any,
) -> dict[str, ContextRelationshipDecisionRow]:
    by_rel: dict[str, ContextRelationshipDecisionRow] = {}
    for decision in decisions:
        current = by_rel.get(decision.relationship_id)
        if current is None or decision.ingested_ts_ms > current.ingested_ts_ms:
            by_rel[decision.relationship_id] = decision
    return by_rel


def _edge_direction(rel: RelationshipCandidateRow) -> tuple[str, str] | None:
    if rel.relationship_type == "nested_a_implies_b":
        return rel.market_id_a, rel.market_id_b
    if rel.relationship_type == "nested_b_implies_a":
        return rel.market_id_b, rel.market_id_a
    return None


def _group_key(rel: RelationshipCandidateRow) -> tuple[str, str]:
    return (
        rel.outcome_space_id or rel.shared_event or "unknown_outcome_space",
        rel.relationship_family or rel.strategy_family or "nesting",
    )


def _make_closure_row(
    *,
    start: str,
    end: str,
    path: list[_Edge],
    group_key: tuple[str, str],
    source_mode: str,
) -> RelationshipCandidateRow | None:
    start_side = _side_for_market(path[0].rel, start)
    end_side = _side_for_market(path[-1].rel, end)
    if start_side is None or end_side is None:
        return None

    source_rels = [edge.rel for edge in path]
    source_lanes = [edge.lane for edge in path]
    source_ids = [edge.relationship_id for edge in path]
    source_lane_label = source_lanes[0] if len(set(source_lanes)) == 1 else "mixed"
    confidence = min(rel.final_confidence for rel in source_rels)
    status = "accepted" if confidence >= 0.80 else "needs_manual_review"

    guard = {
        "same_outcome_space": True,
        "same_closure_family": True,
        "acyclic_path": True,
        "max_depth": len(path),
        "source_mode": source_mode,
        "closure_source_lane": source_lane_label,
    }
    parse = {
        "expansion_pass_id": _PASS_ID,
        "inferred_from_relationship_ids": source_ids,
        "closure_depth": len(path),
        "closure_family": group_key[1],
        "closure_source_lane": source_lane_label,
    }
    ts = now_ms()
    return RelationshipCandidateRow(
        relationship_id=make_relationship_id(),
        market_id_a=start,
        market_id_b=end,
        condition_id_a=start_side["condition_id"],
        condition_id_b=end_side["condition_id"],
        token_id_a_yes=start_side["token_yes"],
        token_id_a_no=start_side["token_no"],
        token_id_b_yes=end_side["token_yes"],
        token_id_b_no=end_side["token_no"],
        question_a=start_side["question"],
        question_b=end_side["question"],
        relationship_type="nested_a_implies_b",
        entity_match_score=min(rel.entity_match_score for rel in source_rels),
        time_scope_match_score=min(rel.time_scope_match_score for rel in source_rels),
        resolution_criteria_match_score=min(rel.resolution_criteria_match_score for rel in source_rels),
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=min(rel.deterministic_confidence for rel in source_rels),
        model_confidence=min(rel.model_confidence for rel in source_rels),
        final_confidence=confidence,
        validation_status=status,
        rejection_reasons_json="[]",
        rationale_summary=(
            f"Expansion ({_PASS_ID}): transitive closure depth={len(path)} "
            f"from {' -> '.join(source_ids)}"
        ),
        evidence_json=build_evidence_json(_PASS_ID, guard, parse),
        rulebook_id=GENERATED_BY,
        rulebook_version=_PASS_VERSION,
        rulebook_content_hash=f"{_PASS_ID}:{'|'.join(source_ids)}",
        relationship_validity_status=status,
        strategy_eligibility_status="eligible" if status == "accepted" else "needs_manual_review",
        relationship_family=group_key[1],
        proposition_type=start_side["proposition_type"] or end_side["proposition_type"],
        event_atoms_json=start_side["event_atoms_json"] or end_side["event_atoms_json"],
        terms_match_score=min(
            (rel.terms_match_score for rel in source_rels if rel.terms_match_score is not None),
            default=None,
        ),
        outcome_space_match_score=min(
            (rel.outcome_space_match_score for rel in source_rels if rel.outcome_space_match_score is not None),
            default=None,
        ),
        reference_event_match_score=min(
            (
                rel.reference_event_match_score
                for rel in source_rels
                if rel.reference_event_match_score is not None
            ),
            default=None,
        ),
        candidate_a=start_side["candidate"],
        candidate_b=end_side["candidate"],
        shared_event=source_rels[0].shared_event,
        reference_event=source_rels[0].reference_event,
        relationship_subtype="transitive_closure",
        outcome_space_id=group_key[0],
        outcome_subtype_a=start_side["outcome_subtype"],
        outcome_subtype_b=end_side["outcome_subtype"],
        entity_type_a=start_side["entity_type"],
        entity_type_b=end_side["entity_type"],
        stage_a=start_side["stage"],
        stage_b=end_side["stage"],
        party_a=start_side["party"],
        party_b=end_side["party"],
        team_a=start_side["team"],
        team_b=end_side["team"],
        shared_reference_event=source_rels[0].shared_reference_event,
        classification_reason_json=build_classification_reason_json(
            _PASS_ID, f"transitive closure depth={len(path)}"
        ),
        strategy_family="nesting_transitive_closure",
        strategy_eligible_reason=f"deterministic_{_PASS_ID}",
        schema_version=1,
        ingested_ts_ms=ts,
    )


def _side_for_market(rel: RelationshipCandidateRow, market_id: str) -> dict[str, Any] | None:
    payload = asdict(rel)
    if market_id == rel.market_id_a:
        suffix = "_a"
    elif market_id == rel.market_id_b:
        suffix = "_b"
    else:
        return None
    return {
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
        "proposition_type": rel.proposition_type,
        "event_atoms_json": rel.event_atoms_json,
    }
