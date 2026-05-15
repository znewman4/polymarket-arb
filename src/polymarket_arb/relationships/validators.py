"""Core relationship validators: nesting, contradiction, inverse, mutually_exclusive,
mutually_exclusive_category, temporal_before/after, inverse_temporal_order."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from ..semantics.rulebook_models import RelationshipRulebook
from ..storage.base import (
    BackfillCoverageRow,
    MarketRow,
    MarketSemanticsRow,
    RelationshipCandidateRow,
)
from .candidate_generation import CandidatePair
from .semantic_similarity import (
    entity_overlap_score,
    resolution_criteria_score,
    time_scope_overlap_score,
)
from .taxonomy import RelationshipTaxonomy, classify_relationship
from .terms_similarity import (
    candidates_are_different,
    get_candidate,
    get_competition_id,
    get_shared_reference_event,
    get_winner_predicate,
    has_event_atoms,
    propositions_are_inverse,
)
from .terms_similarity import (
    outcome_space_match_score as _outcome_space_score,
)
from .terms_similarity import (
    reference_event_match_score as _ref_event_score,
)
from .terms_similarity import (
    terms_match_score as _terms_score,
)
from .threshold_extraction import ThresholdClaim, detect_threshold_nesting, extract_threshold_claim

# Relationship types introduced in Phase 5.5 D+
_NEW_TYPES = frozenset({
    "mutually_exclusive_category",
    "temporal_before",
    "temporal_after",
    "inverse_temporal_order",
    "same_entity_exclusive",
    "same_reference_clock",
    "temporal_transitive_dependency",
})


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _relationship_id(market_id_a: str, market_id_b: str, rulebook_id: str, rulebook_version: int) -> str:
    raw = f"{market_id_a}|{market_id_b}|{rulebook_id}|{rulebook_version}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _token_ids(market: MarketRow) -> tuple[str | None, str | None]:
    """Return (yes_token_id, no_token_id) for a binary market, or (None, None)."""
    if len(market.outcomes) == 2 and len(market.clob_token_ids) == 2:
        yes_idx = 0  # Polymarket convention: first token = YES
        no_idx = 1
        return market.clob_token_ids[yes_idx], market.clob_token_ids[no_idx]
    return None, None


def _compute_ambiguity_penalty(
    sem_a: MarketSemanticsRow,
    sem_b: MarketSemanticsRow,
    rulebook: RelationshipRulebook,
) -> float:
    penalties = rulebook.ambiguity_penalties
    penalty = 0.0
    if sem_a.ambiguity_flags or sem_b.ambiguity_flags:
        penalty += penalties.semantics_ambiguity_flag_present
    if sem_a.needs_manual_review or sem_b.needs_manual_review:
        penalty += penalties.needs_manual_review_either_market
    conf_threshold = 0.6
    if (sem_a.semantic_confidence < conf_threshold or sem_b.semantic_confidence < conf_threshold):
        penalty += penalties.low_semantic_confidence_either_market
    return min(penalty, 1.0)


def _compute_deterministic_confidence(
    entity_score: float,
    time_score: float,
    resolution_score: float,
    threshold_detected: bool,
    rulebook: RelationshipRulebook,
) -> float:
    w = rulebook.score_weights
    threshold_score = 1.0 if threshold_detected else 0.0
    total_w = sum([
        w.get("entity_match", 0.35),
        w.get("time_scope_match", 0.30),
        w.get("resolution_criteria_match", 0.25),
        w.get("threshold_relation", 0.10),
    ])
    raw = (
        entity_score * w.get("entity_match", 0.35)
        + time_score * w.get("time_scope_match", 0.30)
        + resolution_score * w.get("resolution_criteria_match", 0.25)
        + threshold_score * w.get("threshold_relation", 0.10)
    ) / total_w
    return max(0.0, min(1.0, raw))


def _make_row(
    *,
    pair: CandidatePair,
    sem_a: MarketSemanticsRow,
    sem_b: MarketSemanticsRow,
    relationship_type: str,
    entity_score: float,
    time_score: float,
    resolution_score: float,
    threshold_claim: ThresholdClaim | None,
    threshold_direction: str,
    det_confidence: float,
    penalty: float,
    validation_status: str,
    rejection_reasons: list[dict],
    rationale: str,
    evidence: dict,
    rulebook: RelationshipRulebook,
    rulebook_hash: str,
    # Phase 5.5 D+ optional extras
    strategy_eligibility_status: str = "unknown",
    strategy_exclusion_reasons: list[dict] | None = None,
    relationship_family: str = "",
    terms_match: float | None = None,
    outcome_space_match: float | None = None,
    ref_event_match: float | None = None,
    candidate_a: str | None = None,
    candidate_b: str | None = None,
    shared_event: str | None = None,
    reference_event: str | None = None,
    proposition_type: str | None = None,
    event_atoms_json: str | None = None,
    taxonomy: RelationshipTaxonomy | None = None,
) -> RelationshipCandidateRow:
    market_a = pair.market_a
    market_b = pair.market_b
    yes_a, no_a = _token_ids(market_a)
    yes_b, no_b = _token_ids(market_b)

    model_confidence = 1.0
    final_confidence = max(0.0, min(1.0, det_confidence * (1.0 - penalty)))
    taxonomy = taxonomy or classify_relationship(
        market_a.question,
        market_b.question,
        sem_a,
        sem_b,
        generation_source=getattr(pair, "generation_source", ""),
    )
    if taxonomy.strategy_family == "none" and strategy_eligibility_status == "eligible":
        strategy_eligibility_status = "ineligible"
        strategy_exclusion_reasons = list(strategy_exclusion_reasons or [])
        strategy_exclusion_reasons.append({
            "code": "taxonomy_not_strategy_eligible",
            "detail": taxonomy.strategy_eligible_reason,
        })

    threshold_json: dict = {}
    if threshold_claim is not None:
        threshold_json = {
            "variable": threshold_claim.variable,
            "comparator_a": threshold_claim.comparator,
            "direction": threshold_direction,
        }

    return RelationshipCandidateRow(
        relationship_id=_relationship_id(
            market_a.id, market_b.id, rulebook.rulebook_id, rulebook.rulebook_version
        ),
        market_id_a=market_a.id,
        market_id_b=market_b.id,
        condition_id_a=market_a.condition_id,
        condition_id_b=market_b.condition_id,
        token_id_a_yes=yes_a,
        token_id_a_no=no_a,
        token_id_b_yes=yes_b,
        token_id_b_no=no_b,
        question_a=market_a.question,
        question_b=market_b.question,
        relationship_type=relationship_type,
        entity_match_score=entity_score,
        time_scope_match_score=time_score,
        resolution_criteria_match_score=resolution_score,
        threshold_relation_json=json.dumps(threshold_json),
        semantic_similarity_score=None,
        deterministic_confidence=det_confidence,
        model_confidence=model_confidence,
        final_confidence=final_confidence,
        validation_status=validation_status,
        rejection_reasons_json=json.dumps(rejection_reasons),
        rationale_summary=rationale[:200],
        evidence_json=json.dumps(evidence),
        rulebook_id=rulebook.rulebook_id,
        rulebook_version=rulebook.rulebook_version,
        rulebook_content_hash=rulebook_hash,
        # Phase 5.5 D+ fields
        relationship_validity_status=validation_status,
        strategy_eligibility_status=strategy_eligibility_status,
        strategy_exclusion_reasons_json=json.dumps(strategy_exclusion_reasons or []),
        relationship_family=relationship_family,
        proposition_type=proposition_type,
        event_atoms_json=event_atoms_json,
        terms_match_score=terms_match,
        outcome_space_match_score=outcome_space_match,
        reference_event_match_score=ref_event_match,
        candidate_a=candidate_a,
        candidate_b=candidate_b,
        shared_event=shared_event,
        reference_event=reference_event,
        relationship_subtype=taxonomy.relationship_subtype,
        outcome_space_id=taxonomy.outcome_space_id,
        outcome_subtype_a=taxonomy.outcome_subtype_a,
        outcome_subtype_b=taxonomy.outcome_subtype_b,
        entity_type_a=taxonomy.entity_type_a,
        entity_type_b=taxonomy.entity_type_b,
        stage_a=taxonomy.stage_a,
        stage_b=taxonomy.stage_b,
        party_a=taxonomy.party_a or None,
        party_b=taxonomy.party_b or None,
        team_a=taxonomy.team_a or None,
        team_b=taxonomy.team_b or None,
        shared_reference_event=taxonomy.shared_reference_event or None,
        classification_reason_json=taxonomy.classification_reason_json,
        mixed_subtype_reason=taxonomy.mixed_subtype_reason,
        strategy_family=taxonomy.strategy_family,
        strategy_eligible_reason=taxonomy.strategy_eligible_reason,
        schema_version=2,
        ingested_ts_ms=_now_ms(),
    )


def _compute_strategy_eligibility(
    market_a: MarketRow,
    market_b: MarketRow,
    sem_a: MarketSemanticsRow | None,
    sem_b: MarketSemanticsRow | None,
    cov_a: BackfillCoverageRow | None,
    cov_b: BackfillCoverageRow | None,
    rulebook: RelationshipRulebook,
) -> tuple[str, list[dict]]:
    """Determine strategy eligibility independent of relationship validity."""
    reasons: list[dict] = []

    if not _has_token_ids(market_a, market_b):
        reasons.append({"code": "missing_token_ids", "detail": "one or both markets lack binary token IDs"})

    if cov_a is None:
        reasons.append({"code": "missing_coverage_a", "detail": "market_a has no backfill coverage row"})
    elif not cov_a.recommended_for_backtest:
        reasons.append({"code": "coverage_insufficient_a", "detail": f"market_a coverage_score={cov_a.coverage_score:.2f}"})
    if cov_b is None:
        reasons.append({"code": "missing_coverage_b", "detail": "market_b has no backfill coverage row"})
    elif not cov_b.recommended_for_backtest:
        reasons.append({"code": "coverage_insufficient_b", "detail": f"market_b coverage_score={cov_b.coverage_score:.2f}"})

    if sem_a and getattr(sem_a, "long_horizon", False):
        reasons.append({"code": "long_horizon_a", "detail": "market_a has long_horizon flag"})
    if sem_b and getattr(sem_b, "long_horizon", False):
        reasons.append({"code": "long_horizon_b", "detail": "market_b has long_horizon flag"})

    if sem_a and getattr(sem_a, "unresolved_reference_event", False):
        reasons.append({"code": "unresolved_reference_event_a", "detail": "market_a references an unresolved event"})
    if sem_b and getattr(sem_b, "unresolved_reference_event", False):
        reasons.append({"code": "unresolved_reference_event_b", "detail": "market_b references an unresolved event"})

    if reasons:
        return "ineligible", reasons
    return "eligible", []


def _evaluate_mutually_exclusive_category(
    pair: CandidatePair,
    sem_a: MarketSemanticsRow,
    sem_b: MarketSemanticsRow,
    cov_a: BackfillCoverageRow | None,
    cov_b: BackfillCoverageRow | None,
    rulebook: RelationshipRulebook,
    rulebook_hash: str,
) -> RelationshipCandidateRow:
    """Evaluate a mutually_exclusive_category pair (different candidates, same competition)."""
    market_a = pair.market_a
    market_b = pair.market_b
    taxonomy = classify_relationship(
        market_a.question, market_b.question, sem_a, sem_b,
        generation_source=getattr(pair, "generation_source", ""),
    )
    type_rule = rulebook.type_rules.get("mutually_exclusive_category")
    rejection_reasons: list[dict] = []

    os_score = _outcome_space_score(sem_a, sem_b)
    t_score = _terms_score(sem_a, sem_b)
    min_event_match = (type_rule.min_event_match if type_rule and type_rule.min_event_match is not None else 0.75)

    if os_score < min_event_match:
        rejection_reasons.append({
            "code": "outcome_space_mismatch",
            "detail": f"competition_match={os_score:.3f} < {min_event_match}",
        })

    if not candidates_are_different(sem_a, sem_b):
        rejection_reasons.append({
            "code": "same_candidate",
            "detail": "both markets represent the same candidate",
        })

    # winner_predicate check
    pred_a = get_winner_predicate(sem_a)
    pred_b = get_winner_predicate(sem_b)
    if pred_a and pred_b and pred_a.lower() != pred_b.lower():
        rejection_reasons.append({
            "code": "winner_predicate_mismatch",
            "detail": f"predicate_a={pred_a!r} != predicate_b={pred_b!r}",
        })

    if taxonomy.relationship_family in {"mixed_subtype", "dependency", "same_reference_only", "same_topic_no_trade"}:
        rejection_reasons.append({
            "code": taxonomy.relationship_subtype,
            "detail": taxonomy.mixed_subtype_reason or taxonomy.strategy_eligible_reason,
        })

    # Build confidence from outcome_space score + terms
    det_conf = min(1.0, os_score * 0.70 + t_score * 0.30)
    penalty = _compute_ambiguity_penalty(sem_a, sem_b, rulebook)
    final_conf = max(0.0, det_conf * (1.0 - penalty))

    entity_score = entity_overlap_score(sem_a, sem_b)
    time_score = time_scope_overlap_score(sem_a, sem_b)
    resolution_score = resolution_criteria_score(sem_a, sem_b)
    soft = rulebook.soft_gates
    if rejection_reasons:
        validation_status = taxonomy.validation_status_override or "rejected"
    elif final_conf < soft.manual_review_band[0]:
        validation_status = "rejected"
        rejection_reasons.append({"code": "manual_review_band", "detail": f"final_confidence={final_conf:.3f} below floor"})
    elif final_conf < soft.min_final_confidence_for_accept:
        validation_status = "needs_manual_review"
        rejection_reasons.append({"code": "manual_review_band", "detail": f"final_confidence={final_conf:.3f} in review band"})
    else:
        validation_status = "accepted"

    strat_status, strat_reasons = _compute_strategy_eligibility(
        market_a, market_b, sem_a, sem_b, cov_a, cov_b, rulebook
    )
    if taxonomy.strategy_family not in {"pairwise_mutual_exclusion", "party_inverse"}:
        strat_status = "ineligible"
        strat_reasons.append({
            "code": "taxonomy_not_pairwise_mutual_exclusion",
            "detail": taxonomy.strategy_eligible_reason,
        })

    comp_id = get_competition_id(sem_a) or get_competition_id(sem_b)

    rationale = (
        f"Type=mutually_exclusive_category status={validation_status}: "
        f"competition={comp_id} os_score={os_score:.2f} conf={final_conf:.2f}"
    )
    evidence = {
        "outcome_space_score": round(os_score, 4),
        "terms_score": round(t_score, 4),
        "entity_score": round(entity_score, 4),
        "sources": pair.sources,
    }

    return _make_row(
        pair=pair, sem_a=sem_a, sem_b=sem_b,
        relationship_type=taxonomy.relationship_type_override or "mutually_exclusive_category",
        entity_score=entity_score, time_score=time_score, resolution_score=resolution_score,
        threshold_claim=None, threshold_direction="none",
        det_confidence=det_conf, penalty=penalty,
        validation_status=validation_status,
        rejection_reasons=rejection_reasons,
        rationale=rationale,
        evidence=evidence,
        rulebook=rulebook, rulebook_hash=rulebook_hash,
        strategy_eligibility_status=strat_status,
        strategy_exclusion_reasons=strat_reasons,
        relationship_family=(
            "category"
            if taxonomy.strategy_family in {"pairwise_mutual_exclusion", "party_inverse"}
            else taxonomy.relationship_family if taxonomy.relationship_family != "same_topic_no_trade" else "rejected"
        ),
        outcome_space_match=os_score,
        terms_match=t_score,
        candidate_a=taxonomy.candidate_a or get_candidate(sem_a),
        candidate_b=taxonomy.candidate_b or get_candidate(sem_b),
        shared_event=comp_id or taxonomy.shared_event,
        taxonomy=taxonomy,
    )


def _evaluate_temporal(
    pair: CandidatePair,
    sem_a: MarketSemanticsRow,
    sem_b: MarketSemanticsRow,
    cov_a: BackfillCoverageRow | None,
    cov_b: BackfillCoverageRow | None,
    rulebook: RelationshipRulebook,
    rulebook_hash: str,
    rel_type: str,  # "temporal_before" | "temporal_after" | "same_reference_clock"
) -> RelationshipCandidateRow:
    """Evaluate temporal relationship types."""
    market_a = pair.market_a
    market_b = pair.market_b
    taxonomy = classify_relationship(
        market_a.question, market_b.question, sem_a, sem_b,
        generation_source=rel_type,
    )
    type_rule = rulebook.type_rules.get(rel_type)
    rejection_reasons: list[dict] = []

    ref_score = _ref_event_score(sem_a, sem_b)
    t_score = _terms_score(sem_a, sem_b)
    min_ref = (type_rule.min_reference_event_match if type_rule and type_rule.min_reference_event_match is not None else 0.65)

    if not has_event_atoms(sem_a) and not has_event_atoms(sem_b):
        rejection_reasons.append({
            "code": "missing_event_atoms",
            "detail": "neither market has extractable event atoms",
        })

    if ref_score < min_ref and not rejection_reasons:
        rejection_reasons.append({
            "code": "reference_event_mismatch",
            "detail": f"reference_event_match={ref_score:.3f} < {min_ref}",
        })

    # Apply penalties
    unresolved_penalty = 0.0
    long_horizon_penalty = 0.0
    if type_rule:
        if (getattr(sem_a, "unresolved_reference_event", False)
                or getattr(sem_b, "unresolved_reference_event", False)):
            unresolved_penalty = type_rule.unresolved_reference_event_penalty
        if getattr(sem_a, "long_horizon", False) or getattr(sem_b, "long_horizon", False):
            long_horizon_penalty = type_rule.long_horizon_penalty

    det_conf = min(1.0, ref_score * 0.60 + t_score * 0.40)
    det_conf = max(0.0, det_conf - unresolved_penalty - long_horizon_penalty)
    penalty = _compute_ambiguity_penalty(sem_a, sem_b, rulebook)
    final_conf = max(0.0, det_conf * (1.0 - penalty))

    entity_score = entity_overlap_score(sem_a, sem_b)
    time_score = time_scope_overlap_score(sem_a, sem_b)
    resolution_score = resolution_criteria_score(sem_a, sem_b)
    soft = rulebook.soft_gates
    if rejection_reasons:
        validation_status = "rejected"
    elif final_conf < soft.manual_review_band[0]:
        validation_status = "rejected"
        rejection_reasons.append({"code": "manual_review_band", "detail": f"final_confidence={final_conf:.3f} below floor"})
    elif final_conf < soft.min_final_confidence_for_accept:
        validation_status = "needs_manual_review"
        rejection_reasons.append({"code": "manual_review_band", "detail": f"final_confidence={final_conf:.3f} in review band"})
    else:
        validation_status = "accepted"

    strat_status, strat_reasons = _compute_strategy_eligibility(
        market_a, market_b, sem_a, sem_b, cov_a, cov_b, rulebook
    )
    if strat_status == "eligible":
        # Temporal markets usually need a paired inverse market — flag as needs_review unless confirmed
        strat_reasons.append({"code": "no_paired_market", "detail": "temporal relationships require a paired inverse market for arbitrage"})
        strat_status = "ineligible"

    shared_ref = get_shared_reference_event(sem_a, sem_b)

    rationale = (
        f"Type={rel_type} status={validation_status}: "
        f"ref_score={ref_score:.2f} terms={t_score:.2f} conf={final_conf:.2f}"
    )
    evidence = {
        "reference_event_score": round(ref_score, 4),
        "terms_score": round(t_score, 4),
        "unresolved_penalty": unresolved_penalty,
        "long_horizon_penalty": long_horizon_penalty,
        "sources": pair.sources,
    }

    return _make_row(
        pair=pair, sem_a=sem_a, sem_b=sem_b,
        relationship_type=taxonomy.relationship_type_override or rel_type,
        entity_score=entity_score, time_score=time_score, resolution_score=resolution_score,
        threshold_claim=None, threshold_direction="none",
        det_confidence=det_conf, penalty=penalty,
        validation_status=validation_status,
        rejection_reasons=rejection_reasons,
        rationale=rationale,
        evidence=evidence,
        rulebook=rulebook, rulebook_hash=rulebook_hash,
        strategy_eligibility_status=strat_status,
        strategy_exclusion_reasons=strat_reasons,
        relationship_family="temporal",
        ref_event_match=ref_score,
        terms_match=t_score,
        reference_event=shared_ref,
        taxonomy=taxonomy,
    )


def _evaluate_inverse_temporal_order(
    pair: CandidatePair,
    sem_a: MarketSemanticsRow,
    sem_b: MarketSemanticsRow,
    cov_a: BackfillCoverageRow | None,
    cov_b: BackfillCoverageRow | None,
    rulebook: RelationshipRulebook,
    rulebook_hash: str,
) -> RelationshipCandidateRow:
    """Evaluate inverse_temporal_order: A before B and B before A."""
    market_a = pair.market_a
    market_b = pair.market_b
    taxonomy = classify_relationship(
        market_a.question, market_b.question, sem_a, sem_b,
        generation_source="inverse_temporal_order",
    )
    rejection_reasons: list[dict] = []

    ref_score = _ref_event_score(sem_a, sem_b)
    t_score = _terms_score(sem_a, sem_b)

    if not propositions_are_inverse(sem_a, sem_b):
        rejection_reasons.append({
            "code": "ordering_not_inverse",
            "detail": "propositions do not represent opposite orderings of the same two events",
        })

    det_conf = min(1.0, ref_score * 0.55 + t_score * 0.45)
    penalty = _compute_ambiguity_penalty(sem_a, sem_b, rulebook)
    final_conf = max(0.0, det_conf * (1.0 - penalty))

    entity_score = entity_overlap_score(sem_a, sem_b)
    time_score = time_scope_overlap_score(sem_a, sem_b)
    resolution_score = resolution_criteria_score(sem_a, sem_b)

    soft = rulebook.soft_gates
    if rejection_reasons:
        validation_status = "rejected"
    elif final_conf < soft.manual_review_band[0]:
        validation_status = "rejected"
        rejection_reasons.append({"code": "manual_review_band", "detail": f"final_confidence={final_conf:.3f} below floor"})
    elif final_conf < soft.min_final_confidence_for_accept:
        validation_status = "needs_manual_review"
        rejection_reasons.append({"code": "manual_review_band", "detail": f"final_confidence={final_conf:.3f} in review band"})
    else:
        validation_status = "accepted"

    strat_status, strat_reasons = _compute_strategy_eligibility(
        market_a, market_b, sem_a, sem_b, cov_a, cov_b, rulebook
    )

    shared_ref = get_shared_reference_event(sem_a, sem_b)

    rationale = (
        f"Type=inverse_temporal_order status={validation_status}: "
        f"ref_score={ref_score:.2f} conf={final_conf:.2f}"
    )
    evidence = {
        "reference_event_score": round(ref_score, 4),
        "terms_score": round(t_score, 4),
        "is_inverse": not bool(rejection_reasons),
        "sources": pair.sources,
    }

    return _make_row(
        pair=pair, sem_a=sem_a, sem_b=sem_b,
        relationship_type="inverse_temporal_order",
        entity_score=entity_score, time_score=time_score, resolution_score=resolution_score,
        threshold_claim=None, threshold_direction="none",
        det_confidence=det_conf, penalty=penalty,
        validation_status=validation_status,
        rejection_reasons=rejection_reasons,
        rationale=rationale,
        evidence=evidence,
        rulebook=rulebook, rulebook_hash=rulebook_hash,
        strategy_eligibility_status=strat_status,
        strategy_exclusion_reasons=strat_reasons,
        relationship_family=taxonomy.relationship_family if taxonomy.relationship_family != "same_topic_no_trade" else "temporal",
        ref_event_match=ref_score,
        terms_match=t_score,
        reference_event=shared_ref,
        taxonomy=taxonomy,
    )


def _validate_pair(
    pair: CandidatePair,
    sem_a: MarketSemanticsRow | None,
    sem_b: MarketSemanticsRow | None,
    cov_a: BackfillCoverageRow | None,
    cov_b: BackfillCoverageRow | None,
    rulebook: RelationshipRulebook,
    rulebook_hash: str,
) -> RelationshipCandidateRow:
    market_a = pair.market_a
    market_b = pair.market_b

    # 1. Missing semantics
    if sem_a is None or sem_b is None:
        return _make_row(
            pair=pair, sem_a=sem_a or _empty_sem(market_a), sem_b=sem_b or _empty_sem(market_b),
            relationship_type="rejected", entity_score=0.0, time_score=0.0, resolution_score=0.0,
            threshold_claim=None, threshold_direction="none",
            det_confidence=0.0, penalty=0.0,
            validation_status="rejected",
            rejection_reasons=[{"code": "missing_semantics", "detail": "one or both markets missing semantics"}],
            rationale="Missing semantics; cannot validate relationship.",
            evidence={"has_sem_a": sem_a is not None, "has_sem_b": sem_b is not None},
            rulebook=rulebook, rulebook_hash=rulebook_hash,
            strategy_eligibility_status="ineligible",
            strategy_exclusion_reasons=[{"code": "missing_semantics"}],
        )

    # 2. Identical pair guard
    if market_a.id == market_b.id:
        return _make_row(
            pair=pair, sem_a=sem_a, sem_b=sem_b,
            relationship_type="rejected", entity_score=0.0, time_score=0.0, resolution_score=0.0,
            threshold_claim=None, threshold_direction="none",
            det_confidence=0.0, penalty=0.0,
            validation_status="rejected",
            rejection_reasons=[{"code": "identical_market_pair", "detail": "same market_id"}],
            rationale="Identical markets.",
            evidence={},
            rulebook=rulebook, rulebook_hash=rulebook_hash,
            strategy_eligibility_status="ineligible",
            strategy_exclusion_reasons=[{"code": "identical_market_pair"}],
        )

    # 3. Dispatch to type-specific evaluators based on generation_source hint
    gen_source = getattr(pair, "generation_source", "")
    if gen_source == "mutually_exclusive_category":
        return _evaluate_mutually_exclusive_category(
            pair, sem_a, sem_b, cov_a, cov_b, rulebook, rulebook_hash
        )
    if gen_source == "inverse_temporal_order":
        return _evaluate_inverse_temporal_order(
            pair, sem_a, sem_b, cov_a, cov_b, rulebook, rulebook_hash
        )
    if gen_source in ("temporal_before", "temporal_after", "same_reference_clock"):
        return _evaluate_temporal(
            pair, sem_a, sem_b, cov_a, cov_b, rulebook, rulebook_hash, gen_source
        )

    entity_score = entity_overlap_score(sem_a, sem_b)
    time_score = time_scope_overlap_score(sem_a, sem_b)
    resolution_score = resolution_criteria_score(sem_a, sem_b)

    # Extract threshold claims from each market
    claim_a = extract_threshold_claim(sem_a)
    claim_b = extract_threshold_claim(sem_b)

    threshold_claim = None
    threshold_direction = "none"
    threshold_detected = False

    if claim_a and claim_b:
        direction = detect_threshold_nesting(claim_a, claim_b)
        if direction != "none":
            threshold_claim = claim_a
            threshold_direction = direction
            threshold_detected = True

    taxonomy = classify_relationship(
        market_a.question,
        market_b.question,
        sem_a,
        sem_b,
        generation_source=gen_source,
    )

    det_conf = _compute_deterministic_confidence(
        entity_score, time_score, resolution_score, threshold_detected, rulebook
    )
    penalty = _compute_ambiguity_penalty(sem_a, sem_b, rulebook)
    final_conf = max(0.0, min(1.0, det_conf * (1.0 - penalty)))

    gates = rulebook.hard_gates
    rejection_reasons: list[dict] = []

    if entity_score < gates.min_entity_match:
        rejection_reasons.append({
            "code": "entity_mismatch",
            "detail": f"entity_overlap={entity_score:.3f} < {gates.min_entity_match}",
        })
    if time_score < gates.min_time_scope_match:
        rejection_reasons.append({
            "code": "time_scope_mismatch",
            "detail": f"time_scope={time_score:.3f} < {gates.min_time_scope_match}",
        })

    # Determine relationship type
    soft = rulebook.soft_gates
    manual_lo, _manual_hi = soft.manual_review_band

    # Taxonomy-specific deterministic relationships take priority over broad
    # entity/time overlap. Mixed subtype and staged dependency pairs must never
    # fall through into contradiction.
    if taxonomy.relationship_type_override in {"dependency", "mixed_subtype"}:
        rel_type = taxonomy.relationship_type_override
        rejection_reasons.append({
            "code": taxonomy.relationship_subtype,
            "detail": taxonomy.mixed_subtype_reason or taxonomy.strategy_eligible_reason,
        })
    elif taxonomy.relationship_type_override in {"nested_a_implies_b", "nested_b_implies_a"}:
        rel_type = taxonomy.relationship_type_override
        threshold_detected = True
    elif taxonomy.relationship_type_override in {"inverse", "mutually_exclusive_category"}:
        rel_type = taxonomy.relationship_type_override
    elif not rejection_reasons and threshold_detected:
        rel_type = f"nested_{threshold_direction.replace('a_implies_b', 'a_implies_b').replace('b_implies_a', 'b_implies_a')}"
        if rel_type not in ("nested_a_implies_b", "nested_b_implies_a"):
            rel_type = "nested_a_implies_b"
        _check_type_rules(rel_type, entity_score, time_score, rulebook, rejection_reasons)
    elif not rejection_reasons:
        # Check for contradiction / inverse: need same entity, same time scope
        # Both resolution conditions should be semantically opposite
        rel_type = _infer_non_threshold_type(sem_a, sem_b, entity_score, time_score)
    else:
        rel_type = "rejected"

    if rejection_reasons:
        validation_status = taxonomy.validation_status_override or "rejected"
        rel_type = "rejected"
    elif final_conf < manual_lo:
        validation_status = "rejected"
        rel_type = "same_topic_no_trade"
        rejection_reasons = [{"code": "manual_review_band", "detail": f"final_confidence={final_conf:.3f} below floor"}]
    elif final_conf < soft.min_final_confidence_for_accept:
        validation_status = "needs_manual_review"
        rejection_reasons = [{"code": "manual_review_band", "detail": f"final_confidence={final_conf:.3f} in review band"}]
    elif not _has_token_ids(market_a, market_b) and gates.require_token_ids_when_strategy_eligible:
        validation_status = "needs_manual_review"
        rejection_reasons = [{"code": "missing_token_ids", "detail": "one or both markets missing binary token IDs"}]
    else:
        validation_status = "accepted"

    # Infer relationship_family from type
    family = taxonomy.relationship_family if taxonomy.relationship_family != "same_topic_no_trade" else "nesting"
    if rel_type in ("contradiction", "inverse", "same_entity_exclusive"):
        family = taxonomy.relationship_family if taxonomy.relationship_family != "same_topic_no_trade" else "contradiction"
    elif rel_type == "same_topic_no_trade":
        family = "unknown"
    elif rel_type == "rejected":
        family = ""

    strat_status, strat_reasons = _compute_strategy_eligibility(
        market_a, market_b, sem_a, sem_b, cov_a, cov_b, rulebook
    )
    if taxonomy.strategy_family == "none" and strat_status == "eligible":
        strat_status = "ineligible"
        strat_reasons.append({
            "code": "taxonomy_not_strategy_eligible",
            "detail": taxonomy.strategy_eligible_reason,
        })

    evidence = {
        "entity_score": round(entity_score, 4),
        "time_score": round(time_score, 4),
        "resolution_score": round(resolution_score, 4),
        "threshold_detected": threshold_detected,
        "threshold_direction": threshold_direction,
        "sources": pair.sources,
        "coverage_a_ok": cov_a is not None and cov_a.recommended_for_backtest if cov_a else False,
        "coverage_b_ok": cov_b is not None and cov_b.recommended_for_backtest if cov_b else False,
    }

    rationale = _make_rationale(rel_type, entity_score, time_score, final_conf, validation_status)

    return _make_row(
        pair=pair, sem_a=sem_a, sem_b=sem_b,
        relationship_type=rel_type,
        entity_score=entity_score, time_score=time_score, resolution_score=resolution_score,
        threshold_claim=threshold_claim, threshold_direction=threshold_direction,
        det_confidence=det_conf, penalty=penalty,
        validation_status=validation_status,
        rejection_reasons=rejection_reasons,
        rationale=rationale,
        evidence=evidence,
        rulebook=rulebook, rulebook_hash=rulebook_hash,
        strategy_eligibility_status=strat_status,
        strategy_exclusion_reasons=strat_reasons,
        relationship_family=family,
        taxonomy=taxonomy,
    )


def _check_type_rules(rel_type: str, entity_score: float, time_score: float,
                      rulebook: RelationshipRulebook, rejection_reasons: list) -> None:
    rules = rulebook.type_rules.get(rel_type)
    if not rules:
        return
    if rules.requires_same_entity and entity_score < 0.5:
        rejection_reasons.append({"code": "entity_mismatch", "detail": "type rule requires same entity"})
    if rules.requires_overlapping_time_scope and time_score < 0.3:
        rejection_reasons.append({"code": "time_scope_mismatch", "detail": "type rule requires overlapping time scope"})
    if rules.requires_same_time_scope and time_score < 0.8:
        rejection_reasons.append({"code": "time_scope_mismatch", "detail": "type rule requires same time scope"})


def _infer_non_threshold_type(
    sem_a: MarketSemanticsRow,
    sem_b: MarketSemanticsRow,
    entity_score: float,
    time_score: float,
) -> str:
    """Infer relationship type when no threshold relation was detected."""
    if entity_score >= 0.7 and time_score >= 0.7:
        # Check if resolution conditions look like opposites
        pos_a = (sem_a.positive_resolution_condition or "").lower()
        neg_a = (sem_a.negative_resolution_condition or "").lower()
        pos_b = (sem_b.positive_resolution_condition or "").lower()
        neg_b = (sem_b.negative_resolution_condition or "").lower()

        # Simple heuristic: if positive condition of A matches negative condition of B
        def _similar(s1: str, s2: str) -> bool:
            w1 = set(s1.split())
            w2 = set(s2.split())
            if not w1 or not w2:
                return False
            overlap = len(w1 & w2) / min(len(w1), len(w2))
            return overlap > 0.5

        if _similar(pos_a, neg_b) or _similar(pos_b, neg_a):
            return "inverse"
        return "contradiction"
    return "same_topic_no_trade"


def _has_token_ids(market_a: MarketRow, market_b: MarketRow) -> bool:
    return (
        len(market_a.outcomes) == 2 and len(market_a.clob_token_ids) == 2
        and len(market_b.outcomes) == 2 and len(market_b.clob_token_ids) == 2
    )


def _make_rationale(rel_type: str, entity: float, time: float, confidence: float, status: str) -> str:
    return (
        f"Type={rel_type} status={status}: "
        f"entity={entity:.2f} time={time:.2f} confidence={confidence:.2f}"
    )


def _empty_sem(market: MarketRow) -> MarketSemanticsRow:
    """Minimal empty semantics row for missing-semantics rejection rows."""
    from datetime import datetime, timezone
    ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    return MarketSemanticsRow(
        source_market_id=market.id,
        source_condition_id=market.condition_id,
        question=market.question,
        canonical_question=market.question,
        market_type="binary",
        subject_entities=[],
        event_entities=[],
        temporal_phrase=None,
        temporal_phrase_normalized=None,
        temporal_resolution="unknown",
        exact_deadline_ms=None,
        date_constraints_json="{}",
        jurisdiction=None,
        positive_resolution_condition="",
        negative_resolution_condition="",
        necessary_conditions_for_yes=[],
        sufficient_conditions_for_yes=[],
        necessary_conditions_for_no=[],
        sufficient_conditions_for_no=[],
        evidence_required=[],
        ambiguity_flags=[],
        ambiguity_score=None,
        semantic_confidence=0.0,
        needs_manual_review=True,
        explanation_summary=None,
        flag_rationales_json=None,
        uncertainty_notes_json=None,
        rule_curation_notes_json=None,
        raw_response_hash="",
        model_name="none",
        prompt_version="none",
        rulebook_id=None,
        rulebook_version=None,
        extraction_id="",
        schema_version=1,
        ingested_ts_ms=ts,
    )


def validate_all_pairs(
    pairs,
    semantics_by_market: dict[str, MarketSemanticsRow],
    coverage_by_market: dict[str, BackfillCoverageRow],
    rulebook: RelationshipRulebook,
    rulebook_hash: str = "",
):
    """Validate every pair and yield one RelationshipCandidateRow each."""
    for pair in pairs:
        sem_a = semantics_by_market.get(pair.market_a.id)
        sem_b = semantics_by_market.get(pair.market_b.id)
        cov_a = coverage_by_market.get(pair.market_a.id)
        cov_b = coverage_by_market.get(pair.market_b.id)
        yield _validate_pair(pair, sem_a, sem_b, cov_a, cov_b, rulebook, rulebook_hash)
