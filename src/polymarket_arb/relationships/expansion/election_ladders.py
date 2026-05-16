"""Election mutual-exclusion expansion pass.

Reads ingested markets + semantics and emits deterministic same-race candidate
mutual exclusions that the standard miner may have missed.

Emitted family
--------------
1. candidate A wins race ⊥ candidate B wins same race
   (mutually_exclusive_category, primary_candidates_mutually_exclusive)

Hard guards
-----------
- Same election outcome space / shared event
- Same stage / round (nomination, first_round, general_election)
- Same year, office, jurisdiction, and party where applicable
- Different named candidates after normalisation
- Replacement / withdrawal ambiguity blocks auto-emission
- Nomination/general dependencies remain analysis-only and are not emitted

RESEARCH-ONLY — no live trading.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from ...storage.base import MarketRow, MarketSemanticsRow, RelationshipCandidateRow
from ...storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from ...storage.parquet.markets_repo import ParquetMarketsRepository
from ...storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from ..taxonomy import OutcomeSide, classify_market
from . import ExpansionResult
from .audit import (
    GENERATED_BY,
    build_classification_reason_json,
    build_evidence_json,
    extract_year,
    make_relationship_id,
    now_ms,
    slug,
)

_PASS_ID = "election_ladders_v1"
_PASS_VERSION = 1

_ELECTION_SUBTYPES = frozenset({
    "candidate_wins_nomination",
    "candidate_winner_same_election",
    "first_round_winner",
})

_AMBIGUITY_RE = re.compile(
    r"\b("
    r"replace|replaces|replacing|replacement|withdraw|withdraws|withdrawal|drop\s+out|drops\s+out|"
    r"suspend|suspends|suspended|nominee\s+replacement|be\s+replaced|"
    r"not\s+run|decline\s+to\s+run"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ElectionMarketInfo:
    """Parsed election-market facts used by the deterministic guards."""

    market_id: str
    condition_id: str | None
    question: str
    token_yes: str | None
    token_no: str | None
    outcome_space_id: str
    outcome_subtype: str
    shared_event: str
    stage: str
    candidate: str
    candidate_slug: str
    party: str
    year: str
    office: str
    jurisdiction: str
    has_replacement_ambiguity: bool


def run_election_ladder_expansion(
    data_root: Path,
    *,
    dry_run: bool = False,
    max_pairs_per_group: int = 500,
) -> ExpansionResult:
    """Run the same-race election candidate mutual-exclusion pass."""
    market_repo = ParquetMarketsRepository(data_root)
    sem_repo = ParquetMarketSemanticsRepository(data_root)
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)

    markets: dict[str, MarketRow] = {m.id: m for m in market_repo.iter_all_markets()}
    semantics: dict[str, MarketSemanticsRow] = {
        s.source_market_id: s for s in sem_repo.iter_latest()
    }
    existing_pairs: set[frozenset[str]] = {
        frozenset({r.market_id_a, r.market_id_b})
        for r in rel_repo.iter_latest()
    }

    classified: list[ElectionMarketInfo] = []
    audit_rows: list[dict[str, Any]] = []
    guard_failures: dict[str, int] = defaultdict(int)
    skipped_guard = 0
    skipped_existing = 0

    for market in markets.values():
        info = _classify_election_market(market, semantics.get(market.id))
        if info is None:
            continue
        if info.has_replacement_ambiguity:
            skipped_guard += 1
            guard_failures["replacement_or_withdrawal_ambiguity"] += 1
            audit_rows.append({
                "pass": _PASS_ID,
                "market_id": market.id,
                "note": "blocked_replacement_or_withdrawal_ambiguity",
            })
            continue
        classified.append(info)

    # Audit but do not emit cross-stage same-candidate dependencies.
    for a, b in combinations(sorted(classified, key=lambda x: x.market_id), 2):
        if (
            a.candidate_slug
            and a.candidate_slug == b.candidate_slug
            and a.stage != b.stage
            and a.year == b.year
            and a.office == b.office
        ):
            audit_rows.append({
                "pass": _PASS_ID,
                "market_a": a.market_id,
                "market_b": b.market_id,
                "note": "analysis_only_cross_stage_dependency",
                "stage_a": a.stage,
                "stage_b": b.stage,
            })

    groups: dict[tuple[str, str, str, str, str], list[ElectionMarketInfo]] = defaultdict(list)
    for info in classified:
        if not info.candidate_slug:
            continue
        key = (
            info.year,
            info.office,
            info.jurisdiction,
            info.stage,
            info.party if info.stage == "nomination" else "",
        )
        groups[key].append(info)

    new_rows: list[RelationshipCandidateRow] = []
    seen_pairs: set[frozenset[str]] = set()

    for group_key, group in groups.items():
        if len(group) < 2:
            continue
        pairs_in_group = 0
        for a, b in combinations(sorted(group, key=lambda x: x.market_id), 2):
            pair_key = frozenset({a.market_id, b.market_id})
            if pair_key in existing_pairs or pair_key in seen_pairs:
                skipped_existing += 1
                continue

            reason = _guard_pair(a, b)
            if reason is not None:
                skipped_guard += 1
                guard_failures[reason] += 1
                audit_rows.append({
                    "pass": _PASS_ID,
                    "market_a": a.market_id,
                    "market_b": b.market_id,
                    "rejected_by": reason,
                })
                continue

            row = _make_pair(a, b)
            new_rows.append(row)
            seen_pairs.add(pair_key)
            audit_rows.append({
                "pass": _PASS_ID,
                "market_a": a.market_id,
                "market_b": b.market_id,
                "relationship_type": row.relationship_type,
                "relationship_subtype": row.relationship_subtype,
                "group_key": "|".join(group_key),
                "validation_status": row.validation_status,
            })
            pairs_in_group += 1
            if pairs_in_group >= max_pairs_per_group:
                break

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


def _classify_election_market(
    market: MarketRow,
    sem: MarketSemanticsRow | None,
) -> ElectionMarketInfo | None:
    side = classify_market(market.question, sem)
    if side.outcome_subtype not in _ELECTION_SUBTYPES:
        return None
    if side.entity_type != "candidate" or not side.candidate:
        return None

    tokens = market.clob_token_ids or []
    year = _year(side, market.question)
    office = _office(market.question)
    jurisdiction = _jurisdiction(market.question, side)
    party = side.party or _party_from_question(market.question)

    return ElectionMarketInfo(
        market_id=market.id,
        condition_id=market.condition_id,
        question=market.question,
        token_yes=tokens[0] if tokens else None,
        token_no=tokens[1] if len(tokens) > 1 else None,
        outcome_space_id=side.outcome_space_id,
        outcome_subtype=side.outcome_subtype,
        shared_event=side.shared_event or side.outcome_space_id,
        stage=side.stage,
        candidate=side.candidate,
        candidate_slug=slug(side.candidate),
        party=party,
        year=year,
        office=office,
        jurisdiction=jurisdiction,
        has_replacement_ambiguity=bool(_AMBIGUITY_RE.search(market.question)),
    )


def _guard_pair(a: ElectionMarketInfo, b: ElectionMarketInfo) -> str | None:
    if a.market_id == b.market_id:
        return "same_market"
    if a.candidate_slug == b.candidate_slug:
        return "same_candidate"
    if not _same_year_office_jurisdiction(a, b):
        return "different_year_office_or_jurisdiction"
    if a.stage != b.stage:
        return "different_stage"
    if a.stage == "nomination" and a.party != b.party:
        return "different_nomination_party"
    if a.outcome_space_id and b.outcome_space_id and a.outcome_space_id != b.outcome_space_id:
        return "different_outcome_space"
    if a.shared_event and b.shared_event and a.shared_event != b.shared_event:
        return "different_shared_event"
    return None


def _make_pair(a: ElectionMarketInfo, b: ElectionMarketInfo) -> RelationshipCandidateRow:
    guard = {
        "same_race": True,
        "same_office": True,
        "same_jurisdiction": True,
        "same_year": True,
        "same_stage": True,
        "same_party_where_applicable": True,
        "different_candidates": True,
        "candidate_a": a.candidate,
        "candidate_b": b.candidate,
        "party": a.party,
        "stage": a.stage,
        "year": a.year,
        "office": a.office,
        "jurisdiction": a.jurisdiction,
    }
    parse = {
        "expansion_pass_id": _PASS_ID,
        "outcome_space_id": a.outcome_space_id or b.outcome_space_id,
        "shared_event": a.shared_event or b.shared_event,
    }
    ts = now_ms()
    return RelationshipCandidateRow(
        relationship_id=make_relationship_id(),
        market_id_a=a.market_id,
        market_id_b=b.market_id,
        condition_id_a=a.condition_id,
        condition_id_b=b.condition_id,
        token_id_a_yes=a.token_yes,
        token_id_a_no=a.token_no,
        token_id_b_yes=b.token_yes,
        token_id_b_no=b.token_no,
        question_a=a.question,
        question_b=b.question,
        relationship_type="mutually_exclusive_category",
        entity_match_score=1.0,
        time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.92,
        model_confidence=1.0,
        final_confidence=0.92,
        validation_status="accepted",
        rejection_reasons_json="[]",
        rationale_summary=(
            f"Expansion ({_PASS_ID}): same-race election candidate mutual exclusion — "
            f"{a.candidate} vs {b.candidate}"
        ),
        evidence_json=build_evidence_json(_PASS_ID, guard, parse),
        rulebook_id=GENERATED_BY,
        rulebook_version=_PASS_VERSION,
        rulebook_content_hash=_PASS_ID,
        relationship_validity_status="accepted",
        strategy_eligibility_status="eligible",
        relationship_family="mutual_exclusion",
        relationship_subtype="primary_candidates_mutually_exclusive",
        outcome_space_id=a.outcome_space_id or b.outcome_space_id,
        outcome_subtype_a=a.outcome_subtype,
        outcome_subtype_b=b.outcome_subtype,
        entity_type_a="candidate",
        entity_type_b="candidate",
        stage_a=a.stage,
        stage_b=b.stage,
        party_a=a.party or None,
        party_b=b.party or None,
        candidate_a=a.candidate,
        candidate_b=b.candidate,
        shared_event=a.shared_event or b.shared_event,
        strategy_family="pairwise_mutual_exclusion",
        strategy_eligible_reason=f"deterministic_{_PASS_ID}",
        classification_reason_json=build_classification_reason_json(
            _PASS_ID, "same-race different-candidate single-winner election"
        ),
        schema_version=1,
        ingested_ts_ms=ts,
    )


def _same_year_office_jurisdiction(a: ElectionMarketInfo, b: ElectionMarketInfo) -> bool:
    return a.year == b.year and a.office == b.office and a.jurisdiction == b.jurisdiction


def _year(side: OutcomeSide, question: str) -> str:
    if side.outcome_space_id:
        match = re.match(r"^(20\d{2}|unknown_year)_", side.outcome_space_id)
        if match:
            return match.group(1)
    return extract_year(question) or "unknown_year"


def _office(question: str) -> str:
    lower = question.lower()
    if "president" in lower:
        return "president"
    if "senate" in lower or "senator" in lower:
        return "senate"
    if "governor" in lower or "gubernatorial" in lower:
        return "governor"
    if "mayor" in lower:
        return "mayor"
    return "unknown_office"


def _jurisdiction(question: str, side: OutcomeSide) -> str:
    if side.outcome_space_id:
        space = side.outcome_space_id
        for suffix in (
            "_presidential_election_candidate_winner",
            "_presidential_election_party_winner",
            "_presidential_election_first_round",
        ):
            if space.endswith(suffix):
                prefix = space[: -len(suffix)]
                parts = prefix.split("_", 1)
                if len(parts) == 2:
                    return parts[1]
        if space.endswith("_presidential_nomination"):
            prefix = space[: -len("_presidential_nomination")]
            parts = prefix.split("_")
            if len(parts) >= 3:
                return "us"
    lower = question.lower()
    if "colomb" in lower:
        return "colombian"
    if "us " in lower or "u.s." in lower or "presidential election" in lower:
        return "us"
    return "unknown_jurisdiction"


def _party_from_question(question: str) -> str:
    lower = question.lower()
    if re.search(r"\b(democrat|democratic|democrats)\b", lower):
        return "Democrats"
    if re.search(r"\b(republican|republicans|gop)\b", lower):
        return "Republicans"
    return ""
