"""Sports progression expansion pass.

Uses the stage-ordering registry (configs/sports/stage_ordering_v1.yaml) to
emit championship → conference implication pairs for teams where both markets
exist but were not picked up by the standard miner.

This pass is complementary to the existing
``sports_championship_implies_conference_v1`` template — it generates pairs
that the template can then auto-approve via `apply-context`, rather than
requiring manual review.

Hard guards
-----------
- Same team
- Same competition family (NBA, NHL, NFL, etc.)
- Same year
- No cross-competition inference (Premier League ≠ Champions League, etc.)
- Stage ordering from the registry is respected; only valid prerequisite
  relationships are emitted

RESEARCH-ONLY — no live trading.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from ...storage.base import MarketRow, MarketSemanticsRow, RelationshipCandidateRow
from ...storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from ...storage.parquet.markets_repo import ParquetMarketsRepository
from ...storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from ..taxonomy import classify_market
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

_PASS_ID = "sports_progression_v1"
_PASS_VERSION = 1

_REPO_ROOT = Path(__file__).resolve().parents[4]
_STAGE_REGISTRY_PATH = _REPO_ROOT / "configs" / "sports" / "stage_ordering_v1.yaml"


def _load_registry(path: Path | None = None) -> dict[str, Any]:
    p = path or _STAGE_REGISTRY_PATH
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _competition_family(outcome_space_id: str) -> str | None:
    """Map an outcome_space_id to a known competition family slug."""
    osi = (outcome_space_id or "").lower()
    if "nba" in osi:
        return "nba"
    if "nhl" in osi or "stanley" in osi:
        return "nhl"
    if "nfl" in osi or "super_bowl" in osi:
        return "nfl"
    if "premier_league" in osi or "epl" in osi:
        return "premier_league"
    return None


def run_sports_progression_expansion(
    data_root: Path,
    *,
    dry_run: bool = False,
    registry_path: Path | None = None,
) -> ExpansionResult:
    """Run the sports progression (stage-ordering) expansion pass.

    Emits championship → conference implication pairs for all teams where
    both markets exist in the same competition family, same year, and the
    stage relationship is validated by the registry.
    """
    registry = _load_registry(registry_path)
    comp_families: dict[str, Any] = registry.get("competition_families", {})

    # Build set of valid (narrow_subtype, broad_subtype) implication pairs per family
    valid_implications: dict[str, list[tuple[str, str]]] = {}
    for family_name, family_data in comp_families.items():
        pairs: list[tuple[str, str]] = []
        stages = {s["stage_id"]: s for s in family_data.get("stages", [])}
        for stage in family_data.get("stages", []):
            if not stage.get("implication_allowed"):
                continue
            narrow_subtypes = stage.get("outcome_subtypes", [])
            for prereq_id in stage.get("prerequisite_stage_ids", []):
                prereq = stages.get(prereq_id)
                if not prereq:
                    continue
                broad_subtypes = prereq.get("outcome_subtypes", [])
                for ns in narrow_subtypes:
                    for bs in broad_subtypes:
                        pairs.append((ns, bs))
        if pairs:
            valid_implications[family_name] = pairs

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

    # Classify all markets
    from dataclasses import dataclass

    @dataclass
    class _MInfo:
        market_id: str
        condition_id: str | None
        question: str
        tok_yes: str | None
        tok_no: str | None
        outcome_subtype: str
        team_slug: str
        space_id: str
        comp_family: str | None
        year: str | None

    classified: list[_MInfo] = []
    for mid, market in markets.items():
        sem = semantics.get(mid)
        side = classify_market(market.question, sem)
        if side.outcome_subtype not in ("team_wins_championship", "team_wins_conference"):
            continue
        if not side.team:
            continue
        tok = market.clob_token_ids or []
        classified.append(_MInfo(
            market_id=mid,
            condition_id=market.condition_id,
            question=market.question,
            tok_yes=tok[0] if tok else None,
            tok_no=tok[1] if len(tok) > 1 else None,
            outcome_subtype=side.outcome_subtype,
            team_slug=slug(side.team),
            space_id=side.outcome_space_id or slug(market.question[:40]),
            comp_family=_competition_family(side.outcome_space_id or ""),
            year=extract_year(market.question),
        ))

    # Group by (team_slug, comp_family) — year compatibility checked per-pair
    groups: dict[tuple, list[_MInfo]] = defaultdict(list)
    for info in classified:
        if info.team_slug and info.comp_family:
            key = (info.team_slug, info.comp_family)
            groups[key].append(info)

    new_rows: list[RelationshipCandidateRow] = []
    audit_rows: list[dict[str, Any]] = []
    skipped_existing = 0
    skipped_guard = 0
    seen_pairs: set[frozenset[str]] = set()

    for (team, family), group in groups.items():
        valid_pairs = valid_implications.get(family, [])
        if not valid_pairs:
            continue

        by_subtype: dict[str, list[_MInfo]] = defaultdict(list)
        for info in group:
            by_subtype[info.outcome_subtype].append(info)

        for narrow_subtype, broad_subtype in valid_pairs:
            for narrow_m in by_subtype.get(narrow_subtype, []):
                for broad_m in by_subtype.get(broad_subtype, []):
                    pair_key = frozenset({narrow_m.market_id, broad_m.market_id})
                    if pair_key in existing_pairs or pair_key in seen_pairs:
                        skipped_existing += 1
                        continue

                    # Year guard
                    if narrow_m.year and broad_m.year and narrow_m.year != broad_m.year:
                        skipped_guard += 1
                        continue

                    confidence = 0.95 if narrow_m.year and broad_m.year else 0.75
                    status = "accepted" if confidence >= 0.90 else "needs_manual_review"
                    guard = {
                        "same_team": True,
                        "same_family": True,
                        "competition_family": family,
                        "year_a": narrow_m.year,
                        "year_b": broad_m.year,
                        "narrow_subtype": narrow_subtype,
                        "broad_subtype": broad_subtype,
                    }
                    parse_ev = {
                        "expansion_pass_id": _PASS_ID,
                        "team_slug": team,
                        "comp_family": family,
                    }
                    ts = now_ms()
                    row = RelationshipCandidateRow(
                        relationship_id=make_relationship_id(),
                        market_id_a=narrow_m.market_id,
                        market_id_b=broad_m.market_id,
                        condition_id_a=narrow_m.condition_id,
                        condition_id_b=broad_m.condition_id,
                        token_id_a_yes=narrow_m.tok_yes,
                        token_id_a_no=narrow_m.tok_no,
                        token_id_b_yes=broad_m.tok_yes,
                        token_id_b_no=broad_m.tok_no,
                        question_a=narrow_m.question,
                        question_b=broad_m.question,
                        relationship_type="nested_a_implies_b",
                        entity_match_score=1.0,
                        time_scope_match_score=1.0 if narrow_m.year == broad_m.year and narrow_m.year else 0.7,
                        resolution_criteria_match_score=1.0,
                        threshold_relation_json="{}",
                        semantic_similarity_score=None,
                        deterministic_confidence=confidence,
                        model_confidence=1.0,
                        final_confidence=confidence,
                        validation_status=status,
                        rejection_reasons_json="[]",
                        rationale_summary=(
                            f"Expansion ({_PASS_ID}): championship_implies_conference "
                            f"team={team} family={family}"
                        ),
                        evidence_json=build_evidence_json(_PASS_ID, guard, parse_ev),
                        rulebook_id=GENERATED_BY,
                        rulebook_version=_PASS_VERSION,
                        rulebook_content_hash=_PASS_ID,
                        relationship_validity_status=status,
                        strategy_eligibility_status="eligible" if status == "accepted" else "needs_manual_review",
                        relationship_family="nesting",
                        relationship_subtype="championship_implies_conference",
                        outcome_space_id=narrow_m.space_id,
                        outcome_subtype_a=narrow_subtype,
                        outcome_subtype_b=broad_subtype,
                        entity_type_a="team",
                        entity_type_b="team",
                        team_a=team,
                        team_b=team,
                        strategy_family="nesting",
                        strategy_eligible_reason=f"deterministic_{_PASS_ID}",
                        classification_reason_json=build_classification_reason_json(
                            _PASS_ID,
                            f"championship_implies_conference team={team}",
                        ),
                        schema_version=1,
                        ingested_ts_ms=ts,
                    )
                    seen_pairs.add(pair_key)
                    new_rows.append(row)
                    audit_rows.append({
                        "pass": _PASS_ID,
                        "market_a": narrow_m.market_id,
                        "market_b": broad_m.market_id,
                        "team": team,
                        "family": family,
                        "subtype": "championship_implies_conference",
                        "status": status,
                    })

    if not dry_run and new_rows:
        rel_repo.append_many(new_rows)

    return ExpansionResult(
        pass_id=_PASS_ID,
        emitted_count=len(new_rows),
        skipped_existing=skipped_existing,
        skipped_guard_fail=skipped_guard,
        dry_run=dry_run,
        audit_rows=audit_rows,
    )
