"""Apply context rules to relationship candidates."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..storage.base import ContextRelationshipDecisionRow, ContextRuleRow, RelationshipCandidateRow
from ..storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from ..storage.parquet.context_rules_repo import ParquetContextRulesRepository
from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from .models import ContextSpace
from .source_registry import load_context_registry
from .template_registry import DeterministicTemplate, find_matching_template, load_template_registry
from .validators import is_reviewed, is_rule_usable, rule_family


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


_PROTECTED_LANES = frozenset({
    "exploratory_context_auto_approved",
})


def _audit_match_reason(
    rel: RelationshipCandidateRow,
    template: DeterministicTemplate,
) -> dict[str, Any]:
    """Produce a human-readable audit row for one template match."""
    cond = template.conditions
    suspicious: list[str] = []

    # Candidate-level checks
    if cond.different_candidates_required:
        if not rel.candidate_a:
            suspicious.append("candidate_a_missing")
        if not rel.candidate_b:
            suspicious.append("candidate_b_missing")

    # Team-level checks
    if cond.different_teams_required:
        if not rel.team_a:
            suspicious.append("team_a_missing")
        if not rel.team_b:
            suspicious.append("team_b_missing")

    # Outcome space proxy
    if cond.same_outcome_space_required and not rel.shared_event:
        suspicious.append("outcome_space_id_proxy_no_shared_event")

    # Low confidence vs template minimum
    if rel.final_confidence < template.min_relationship_confidence:
        suspicious.append(f"confidence_below_min ({rel.final_confidence:.2f}<{template.min_relationship_confidence:.2f})")

    # Original validator rejected this pair
    if rel.validation_status == "rejected":
        suspicious.append("validation_status_rejected")

    return {
        "relationship_id": rel.relationship_id,
        "template_id": template.template_id,
        "template_domain": template.domain,
        "review_status": "auto_applied_pending_human_review",
        "strategy_condition": template.strategy_condition,
        "context_space_id": template.context_space_id,
        "validation_status": rel.validation_status,
        "final_confidence": rel.final_confidence,
        "relationship_type": rel.relationship_type,
        "relationship_subtype": rel.relationship_subtype,
        "outcome_subtype_a": rel.outcome_subtype_a,
        "outcome_subtype_b": rel.outcome_subtype_b,
        "outcome_space_id": rel.outcome_space_id,
        "team_a": rel.team_a or "",
        "team_b": rel.team_b or "",
        "candidate_a": rel.candidate_a or "",
        "candidate_b": rel.candidate_b or "",
        "shared_event": rel.shared_event or "",
        "question_a": rel.question_a,
        "question_b": rel.question_b,
        "why_matched": (
            f"outcome_subtype_both={cond.outcome_subtype_both or set(cond.outcome_subtype_pair_any)}; "
            f"same_outcome_space={cond.same_outcome_space_required}; "
            f"diff_teams={cond.different_teams_required}; "
            f"diff_cands={cond.different_candidates_required}"
        ),
        "suspicious": "; ".join(suspicious) if suspicious else "none",
        "template_confidence": template.confidence,
        "min_rel_confidence": template.min_relationship_confidence,
        "evidence_summary": template.evidence_summary,
    }


def apply_context_decisions(
    data_root: Path,
    registry_path: Path,
    *,
    template_registry_path: Path | None = None,
    allow_unreviewed: bool = True,
    append_upgraded_relationships: bool = False,
    skip_human_reviewed: bool = True,
    audit_output_path: Path | None = None,
) -> dict[str, Any]:
    registry = load_context_registry(registry_path)
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    rule_repo = ParquetContextRulesRepository(data_root)
    decision_repo = ParquetContextRelationshipDecisionsRepository(data_root)
    rules = list(rule_repo.iter_latest())
    by_space: dict[str, list[ContextRuleRow]] = {}
    for rule in rules:
        by_space.setdefault(rule.context_space_id, []).append(rule)

    templates: list[DeterministicTemplate] = []
    if template_registry_path is not None:
        templates = load_template_registry(template_registry_path)

    # Build protected-decision index so we never silently overwrite human-reviewed lanes
    protected: dict[str, ContextRelationshipDecisionRow] = {}
    if skip_human_reviewed:
        for existing in decision_repo.iter_latest():
            if existing.strategy_lane in _PROTECTED_LANES:
                protected[existing.relationship_id] = existing

    decisions: list[ContextRelationshipDecisionRow] = []
    upgraded: list[RelationshipCandidateRow] = []
    counts: dict[str, Any] = {
        "relationships_loaded": 0,
        "decisions_written": 0,
        "skipped_protected": 0,
        "upgraded": 0,
        "downgraded": 0,
        "context_missing": 0,
        "analysis_only": 0,
        "template_matched": 0,
    }
    template_counts: Counter[str] = Counter()
    audit_rows: list[dict[str, Any]] = []

    for rel in rel_repo.iter_latest():
        counts["relationships_loaded"] += 1

        if skip_human_reviewed and rel.relationship_id in protected:
            counts["skipped_protected"] += 1
            continue

        space_id = _context_space_for_relationship(rel)
        matched_template: DeterministicTemplate | None = None

        # If no hardcoded space, try the template registry
        if not space_id and templates:
            matched_template = find_matching_template(rel, templates)
            if matched_template:
                space_id = matched_template.context_space_id
                counts["template_matched"] += 1
                template_counts[matched_template.template_id] += 1
                audit_rows.append(_audit_match_reason(rel, matched_template))

        # Always produce a decision — unmapped relationships get research_only/context_missing
        space = registry.context_spaces.get(space_id) if space_id else None
        decision = _decide(
            rel,
            space,
            by_space.get(space_id, []),
            allow_unreviewed=allow_unreviewed,
            matched_template=matched_template,
        )
        row = _decision_row(rel, decision)
        decisions.append(row)
        if decision["strategy_lane"] in {"strict_context_valid", "reviewed_context_valid"}:
            counts["upgraded"] += 1
            if append_upgraded_relationships:
                upgraded.append(_relationship_with_context(rel, decision))
        elif decision["strategy_lane"] == "analysis_only":
            counts["analysis_only"] += 1
        elif decision["context_status"] == "context_missing":
            counts["context_missing"] += 1
        else:
            counts["downgraded"] += 1

    decision_repo.append_many(decisions)
    if upgraded:
        rel_repo.append_many(upgraded)
    counts["decisions_written"] = len(decisions)
    lane_counts = Counter(row.strategy_lane for row in decisions)
    eligibility_counts = Counter(row.new_strategy_eligibility for row in decisions)
    validation_counts = Counter(row.new_validation_status for row in decisions)
    counts["lane_counts"] = dict(lane_counts)
    counts["eligibility_counts"] = dict(eligibility_counts)
    counts["validation_counts"] = dict(validation_counts)
    counts["template_counts"] = dict(template_counts)

    # Write audit CSV if requested
    if audit_output_path is not None and audit_rows:
        import csv as _csv
        audit_output_path.parent.mkdir(parents=True, exist_ok=True)
        fields = list(audit_rows[0].keys())
        with audit_output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = _csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(audit_rows)
        counts["audit_rows_written"] = len(audit_rows)
        counts["audit_path"] = str(audit_output_path)

    return counts


def _context_space_for_relationship(rel: RelationshipCandidateRow) -> str:
    subtype = rel.relationship_subtype or rel.relationship_type
    if subtype == "championship_implies_conference":
        # Use the generalised template-backed space (no market-terms requirement)
        # so these deterministically-classified pairs reach strict_context_valid.
        return "sports_championship_conference_progression"
    if subtype == "same_topic_no_trade" and _is_nba_finals_conference_pair(rel):
        # Fallback bridge for pairs not yet reclassified by the taxonomy.
        return "nba_championship_conference_progression"
    if subtype in {"exact_finish_implies_top_n", "team_exact_finish_position_vs_team_top_n_finish"}:
        # Use the generalised template-backed space (no market-terms requirement).
        return "sports_ranking_finish_position"
    if subtype == "nomination_to_general_dependency":
        return "us_2028_presidential_nomination_general"
    if subtype == "first_round_to_general_dependency":
        return "colombia_2026_presidential_election"
    if subtype == "same_reference_clock_only":
        return "same_reference_clock_before_gta_vi"
    if subtype == "candidate_to_party_dependency":
        return "us_2028_presidential_nomination_general"
    if rel.relationship_type == "mutually_exclusive_category" and "balance" in rel.outcome_space_id:
        return "balance_of_power_combinations"
    return ""


def _is_nba_finals_conference_pair(rel: RelationshipCandidateRow) -> bool:
    """Narrow bridge for same-team NBA Finals vs conference-winner markets."""
    questions = f"{rel.question_a} {rel.question_b}".lower()
    if "nba" not in questions:
        return False

    team_a = (rel.team_a or "").strip().lower()
    team_b = (rel.team_b or "").strip().lower()
    if team_a and team_b and team_a != team_b:
        return False

    if rel.stage_a and rel.stage_b:
        stages = {rel.stage_a.strip().lower(), rel.stage_b.strip().lower()}
        if not any("final" in stage or "championship" in stage for stage in stages):
            return False
        if not any("conference" in stage for stage in stages):
            return False

    q_a = rel.question_a.lower()
    q_b = rel.question_b.lower()
    a_is_championship = _is_nba_championship_question(q_a)
    b_is_championship = _is_nba_championship_question(q_b)
    a_is_conference = _is_nba_conference_question(q_a)
    b_is_conference = _is_nba_conference_question(q_b)
    if not ((a_is_championship and b_is_conference) or (b_is_championship and a_is_conference)):
        return False

    if team_a and team_b:
        return True

    return _shared_nba_team_name(q_a, q_b)


def _is_nba_championship_question(question: str) -> bool:
    return "nba" in question and ("nba finals" in question or "nba championship" in question)


def _is_nba_conference_question(question: str) -> bool:
    return (
        "nba" in question
        and "conference" in question
        and ("western" in question or "eastern" in question or "west" in question or "east" in question)
    )


_NBA_TEAM_ALIASES = (
    ("okc thunder", "oklahoma city thunder", "thunder"),
    ("san antonio spurs", "spurs"),
    ("los angeles lakers", "lakers"),
    ("minnesota timberwolves", "timberwolves"),
    ("cleveland cavaliers", "cavaliers"),
    ("new york knicks", "knicks"),
    ("boston celtics", "celtics"),
    ("denver nuggets", "nuggets"),
    ("dallas mavericks", "mavericks"),
    ("golden state warriors", "warriors"),
    ("phoenix suns", "suns"),
    ("milwaukee bucks", "bucks"),
    ("philadelphia 76ers", "76ers", "sixers"),
    ("miami heat", "heat"),
    ("houston rockets", "rockets"),
    ("memphis grizzlies", "grizzlies"),
    ("la clippers", "los angeles clippers", "clippers"),
    ("sacramento kings", "kings"),
    ("new orleans pelicans", "pelicans"),
    ("portland trail blazers", "trail blazers", "blazers"),
    ("utah jazz", "jazz"),
    ("orlando magic", "magic"),
    ("atlanta hawks", "hawks"),
    ("indiana pacers", "pacers"),
    ("chicago bulls", "bulls"),
    ("brooklyn nets", "nets"),
    ("toronto raptors", "raptors"),
    ("charlotte hornets", "hornets"),
    ("washington wizards", "wizards"),
    ("detroit pistons", "pistons"),
)


def _shared_nba_team_name(question_a: str, question_b: str) -> bool:
    for aliases in _NBA_TEAM_ALIASES:
        if any(alias in question_a for alias in aliases) and any(alias in question_b for alias in aliases):
            return True
    return False


def _decide(
    rel: RelationshipCandidateRow,
    space: ContextSpace | None,
    rules: list[ContextRuleRow],
    *,
    allow_unreviewed: bool,
    matched_template: DeterministicTemplate | None = None,
) -> dict[str, Any]:
    if space is None:
        return _decision(
            rel,
            "",
            [],
            "context_missing",
            "research_only",
            rel.validation_status,
            "ineligible",
            "context space not configured",
        )

    usable = [r for r in rules if is_rule_usable(r, allow_unreviewed=allow_unreviewed)]
    invalidating = [r for r in usable if rule_family(r) == "invalidating"]
    if invalidating and _invalidates(rel, invalidating):
        return _decision(
            rel,
            space.context_space_id,
            invalidating,
            "context_reviewed_valid" if all(is_reviewed(r) for r in invalidating) else "context_extracted_unreviewed",
            "analysis_only",
            "needs_manual_review",
            "ineligible",
            "context rule explicitly keeps this relationship analysis-only",
        )

    world = [
        r for r in usable
        if r.rule_type in space.required_world_rule_types and rule_family(r) == "world_context"
    ]
    terms = [
        r for r in usable
        if r.rule_type in space.required_market_terms_rule_types and rule_family(r) == "market_terms"
    ]
    if not world:
        return _decision(
            rel,
            space.context_space_id,
            [],
            "context_missing",
            "research_only",
            rel.validation_status,
            "ineligible",
            "missing required world-context rule",
        )

    reviewed_world = all(is_reviewed(r) for r in world)
    # A matched approved template counts as reviewed context for world rules
    # when the template itself has reviewed_status=approved.
    template_approved = matched_template is not None and matched_template.review_status == "approved"
    effective_reviewed_world = reviewed_world or template_approved

    has_all_terms = _has_required_terms(space, terms)
    # If no market-terms rules are required by this space, the terms condition
    # is trivially satisfied (no evidence needed = already fully reviewed).
    if not space.required_market_terms_rule_types:
        reviewed_terms = True
    else:
        reviewed_terms = bool(terms) and all(is_reviewed(r) for r in terms)
    rule_ids = world + terms

    template_suffix = (
        f"; template={matched_template.template_id}" if matched_template else ""
    )

    if effective_reviewed_world and reviewed_terms and has_all_terms:
        return _decision(
            rel,
            space.context_space_id,
            rule_ids,
            "context_reviewed_valid",
            "strict_context_valid",
            _upgraded_status_with_template(rel, matched_template),
            "eligible",
            f"valid world context and market terms rules{template_suffix}",
        )
    if effective_reviewed_world:
        return _decision(
            rel,
            space.context_space_id,
            rule_ids,
            "context_reviewed_valid",
            "reviewed_context_valid",
            _upgraded_status_with_template(rel, matched_template),
            "eligible",
            f"world context reviewed; market terms incomplete or unreviewed{template_suffix}",
        )
    return _decision(
        rel,
        space.context_space_id,
        rule_ids,
        "context_extracted_unreviewed",
        "exploratory_context_unreviewed",
        rel.validation_status if rel.validation_status != "rejected" else "needs_manual_review",
        "ineligible",
        "context exists but still needs review",
    )


def _has_required_terms(space: ContextSpace, terms: list[ContextRuleRow]) -> bool:
    present = {r.rule_type for r in terms}
    return set(space.required_market_terms_rule_types).issubset(present)


def _invalidates(rel: RelationshipCandidateRow, rules: list[ContextRuleRow]) -> bool:
    subtype = rel.relationship_subtype or rel.relationship_type
    for rule in rules:
        try:
            payload = json.loads(rule.rule_json or "{}")
        except json.JSONDecodeError:
            continue
        rule_subtype = str(payload.get("relationship_subtype") or "")
        if not rule_subtype or rule_subtype == subtype:
            return True
    return False


def _upgraded_status(rel: RelationshipCandidateRow) -> str:
    return _upgraded_status_with_template(rel, None)


def _upgraded_status_with_template(
    rel: RelationshipCandidateRow,
    template: DeterministicTemplate | None,
) -> str:
    if (
        rel.relationship_subtype in {"championship_implies_conference", "exact_finish_implies_top_n"}
        or _is_nba_finals_conference_pair(rel)
    ):
        return "accepted"
    if template is not None and template.review_status == "approved":
        return "accepted"
    return rel.validation_status if rel.validation_status == "accepted" else "needs_manual_review"


def _decision(
    rel: RelationshipCandidateRow,
    context_space_id: str,
    rules: list[ContextRuleRow],
    context_status: str,
    strategy_lane: str,
    new_validation_status: str,
    new_strategy_eligibility: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "relationship_id": rel.relationship_id,
        "context_space_id": context_space_id,
        "context_rule_ids": [r.context_rule_id for r in rules],
        "context_status": context_status,
        "strategy_lane": strategy_lane,
        "new_validation_status": new_validation_status,
        "new_strategy_eligibility": new_strategy_eligibility,
        "decision_reason": reason,
        "evidence_summary": _evidence_summary(rules),
    }


def _decision_row(rel: RelationshipCandidateRow, decision: dict[str, Any]) -> ContextRelationshipDecisionRow:
    raw = json.dumps([
        rel.relationship_id,
        decision["context_space_id"],
        decision["strategy_lane"],
        decision["new_validation_status"],
        decision["new_strategy_eligibility"],
    ], sort_keys=True)
    decision_id = hashlib.sha256(raw.encode()).hexdigest()[:32]
    return ContextRelationshipDecisionRow(
        decision_id=decision_id,
        relationship_id=rel.relationship_id,
        context_space_id=decision["context_space_id"],
        context_rule_ids_json=json.dumps(decision["context_rule_ids"], sort_keys=True),
        previous_validation_status=rel.validation_status,
        new_validation_status=decision["new_validation_status"],
        previous_strategy_eligibility=rel.strategy_eligibility_status,
        new_strategy_eligibility=decision["new_strategy_eligibility"],
        strategy_lane=decision["strategy_lane"],
        decision_reason=decision["decision_reason"],
        evidence_summary=decision["evidence_summary"],
        schema_version=1,
        ingested_ts_ms=_now_ms(),
    )


def _relationship_with_context(
    rel: RelationshipCandidateRow,
    decision: dict[str, Any],
) -> RelationshipCandidateRow:
    normalised = normalize_context_backed_relationship(rel, str(decision["context_space_id"]))
    data = asdict(normalised)
    data["validation_status"] = decision["new_validation_status"]
    data["relationship_validity_status"] = decision["new_validation_status"]
    data["strategy_eligibility_status"] = decision["new_strategy_eligibility"]
    data["strategy_eligible_reason"] = decision["decision_reason"]
    data["ingested_ts_ms"] = _now_ms()
    return RelationshipCandidateRow(**data)


def normalize_context_backed_relationship(
    rel: RelationshipCandidateRow,
    context_space_id: str,
) -> RelationshipCandidateRow:
    """Return the executable taxonomy implied by reviewed context, without broad promotion."""
    if context_space_id in {
        "nba_championship_conference_progression",
        "sports_championship_conference_progression",
    }:
        if _is_nba_finals_conference_pair(rel):
            data = asdict(rel)
            data["relationship_type"] = _nba_championship_conference_relationship_type(rel)
            data["relationship_subtype"] = "championship_implies_conference"
            data["relationship_family"] = "sports_progression"
            data["outcome_subtype_a"] = (
                "team_wins_championship"
                if _is_nba_championship_question(rel.question_a.lower())
                else "team_wins_conference"
            )
            data["outcome_subtype_b"] = (
                "team_wins_championship"
                if _is_nba_championship_question(rel.question_b.lower())
                else "team_wins_conference"
            )
            data["strategy_family"] = "nesting"
            data["strategy_eligible_reason"] = (
                "championship implies conference title under reviewed context"
            )
            return RelationshipCandidateRow(**data)
        # Template-matched pair: subtypes already set; just stamp the family/strategy
        if rel.outcome_subtype_a and rel.outcome_subtype_b:
            data = asdict(rel)
            if rel.relationship_type in {"nested_a_implies_b", "nested_b_implies_a"}:
                data["relationship_family"] = "sports_progression"
                data["strategy_family"] = "nesting"
                data["relationship_subtype"] = "championship_implies_conference"
            return RelationshipCandidateRow(**data)
    return rel


def _nba_championship_conference_relationship_type(rel: RelationshipCandidateRow) -> str:
    a_is_championship = _is_nba_championship_question(rel.question_a.lower())
    b_is_championship = _is_nba_championship_question(rel.question_b.lower())
    if a_is_championship and not b_is_championship:
        return "nested_a_implies_b"
    if b_is_championship and not a_is_championship:
        return "nested_b_implies_a"
    return rel.relationship_type


def _evidence_summary(rules: list[ContextRuleRow]) -> str:
    if not rules:
        return ""
    parts: list[str] = []
    for rule in rules[:3]:
        parts.append(f"{rule.rule_type}:{rule.human_review_status}:{rule.confidence:.2f}")
    return "; ".join(parts)
