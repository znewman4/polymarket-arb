"""Deterministic relationship template registry.

Templates are the unit of human review. Every relationship candidate that
matches a template's conditions inherits the template's approval and is routed
to the appropriate context space without requiring individual pair-by-pair review.

YAML contract (templates_v1.yaml):
  template_id, domain, review_status, confidence, min_relationship_confidence,
  match.{outcome_subtype_pair_any, outcome_subtype_both, relationship_types,
          same_team_required, same_event_required, different_candidates_required},
  context_space_id, strategy_condition, required_rule_types, evidence_summary
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..storage.base import RelationshipCandidateRow


@dataclass(frozen=True)
class TemplateMatchConditions:
    """Conditions that must all be satisfied for a relationship to match."""

    # Each element is a frozenset of two outcome_subtype values.
    # The relationship matches if {outcome_subtype_a, outcome_subtype_b} equals
    # any element.  Empty tuple = no constraint.
    outcome_subtype_pair_any: tuple[frozenset[str], ...]

    # Both outcome_subtype_a and outcome_subtype_b must be members of this set.
    # Empty frozenset = no constraint.
    outcome_subtype_both: frozenset[str]

    # relationship_type must be in this set.  Empty = no constraint.
    relationship_types: frozenset[str]

    same_team_required: bool
    # True → team_a and team_b must differ (when both known).
    different_teams_required: bool
    same_event_required: bool
    # True → candidate_a and candidate_b must differ when both known;
    # if one or both are unknown (empty/None) the check is skipped.
    different_candidates_required: bool
    # True → outcome_space_id must be non-empty (shared competition detected).
    same_outcome_space_required: bool


@dataclass(frozen=True)
class DeterministicTemplate:
    """One deterministic template entry from the registry."""

    template_id: str
    version: int
    domain: str
    description: str
    review_status: str          # "approved" | "pending" | "deprecated"
    confidence: float
    min_relationship_confidence: float
    evidence_summary: str

    conditions: TemplateMatchConditions

    context_space_id: str
    strategy_condition: str     # "narrow_implies_broad" | "mutually_exclusive"
    required_rule_types: tuple[str, ...]


def load_template_registry(path: Path) -> list[DeterministicTemplate]:
    """Load and parse templates from a YAML file.

    Returns all templates regardless of review_status; callers filter on
    ``review_status == 'approved'``.
    """
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    templates: list[DeterministicTemplate] = []
    for entry in data.get("templates", []):
        match_cfg: dict[str, Any] = entry.get("match", {})

        pair_any: list[frozenset[str]] = []
        for pair in match_cfg.get("outcome_subtype_pair_any", []):
            pair_any.append(frozenset(str(s) for s in pair))

        both_raw = match_cfg.get("outcome_subtype_both", [])
        outcome_both = frozenset(str(s) for s in both_raw)

        rel_types_raw = match_cfg.get("relationship_types", [])
        rel_types = frozenset(str(t) for t in rel_types_raw)

        conditions = TemplateMatchConditions(
            outcome_subtype_pair_any=tuple(pair_any),
            outcome_subtype_both=outcome_both,
            relationship_types=rel_types,
            same_team_required=bool(match_cfg.get("same_team_required", False)),
            different_teams_required=bool(match_cfg.get("different_teams_required", False)),
            same_event_required=bool(match_cfg.get("same_event_required", False)),
            different_candidates_required=bool(
                match_cfg.get("different_candidates_required", False)
            ),
            same_outcome_space_required=bool(
                match_cfg.get("same_outcome_space_required", False)
            ),
        )

        templates.append(DeterministicTemplate(
            template_id=str(entry["template_id"]),
            version=int(entry.get("version", 1)),
            domain=str(entry.get("domain", "")),
            description=str(entry.get("description", "")),
            review_status=str(entry.get("review_status", "pending")),
            confidence=float(entry.get("confidence", 0.9)),
            min_relationship_confidence=float(
                entry.get("min_relationship_confidence", 0.35)
            ),
            evidence_summary=str(entry.get("evidence_summary", "")),
            conditions=conditions,
            context_space_id=str(entry["context_space_id"]),
            strategy_condition=str(entry.get("strategy_condition", "")),
            required_rule_types=tuple(
                str(r) for r in entry.get("required_rule_types", [])
            ),
        ))

    return templates


def find_matching_template(
    rel: RelationshipCandidateRow,
    templates: list[DeterministicTemplate],
) -> DeterministicTemplate | None:
    """Return the first approved template that matches this relationship.

    Returns None if no approved template matches.
    """
    for template in templates:
        if template.review_status != "approved":
            continue
        if _matches(rel, template.conditions):
            return template
    return None


def _matches(rel: RelationshipCandidateRow, cond: TemplateMatchConditions) -> bool:
    """Check all match conditions against a relationship candidate."""

    # ── Relationship type ────────────────────────────────────────────────────
    if cond.relationship_types and rel.relationship_type not in cond.relationship_types:
        return False

    # ── Outcome subtype pair (exact frozenset match) ──────────────────────────
    if cond.outcome_subtype_pair_any:
        pair = frozenset([
            rel.outcome_subtype_a or "",
            rel.outcome_subtype_b or "",
        ])
        # Require both to be non-empty for a meaningful pair match
        if not (rel.outcome_subtype_a and rel.outcome_subtype_b):
            return False
        if not any(pair == p for p in cond.outcome_subtype_pair_any):
            return False

    # ── Outcome subtype both-in-set ───────────────────────────────────────────
    if cond.outcome_subtype_both:
        if not rel.outcome_subtype_a or not rel.outcome_subtype_b:
            return False
        if (
            rel.outcome_subtype_a not in cond.outcome_subtype_both
            or rel.outcome_subtype_b not in cond.outcome_subtype_both
        ):
            return False

    # ── Same team ────────────────────────────────────────────────────────────
    if cond.same_team_required:
        team_a = (rel.team_a or "").strip().lower()
        team_b = (rel.team_b or "").strip().lower()
        if team_a and team_b and team_a != team_b:
            return False

    # ── Different teams ───────────────────────────────────────────────────────
    if cond.different_teams_required:
        team_a = (rel.team_a or "").strip().lower()
        team_b = (rel.team_b or "").strip().lower()
        # Both must be known and must differ
        if not team_a or not team_b:
            return False
        if team_a == team_b:
            return False

    # ── Same event ───────────────────────────────────────────────────────────
    if cond.same_event_required:
        if not rel.shared_event and not rel.shared_reference_event:
            return False

    # ── Same outcome space (shared competition detected by taxonomy) ──────────
    if cond.same_outcome_space_required:
        space = (rel.outcome_space_id or "").strip()
        if not space or space in ("same_topic_no_trade", ""):
            return False

    # ── Different candidates ─────────────────────────────────────────────────
    if cond.different_candidates_required:
        cand_a = (rel.candidate_a or "").strip()
        cand_b = (rel.candidate_b or "").strip()
        # If both are known, they must differ.
        # If one or both are unknown, we allow the match (will be flagged in audit).
        if cand_a and cand_b and cand_a.lower() == cand_b.lower():
            return False

    return True
