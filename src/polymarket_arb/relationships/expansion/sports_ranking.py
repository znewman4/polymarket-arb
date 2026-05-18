"""Sports ranking ladder expansion pass.

Reads all ingested markets + semantics and generates deterministic relationship
candidates for finish-position ladders that the standard miner may have missed.

Emitted pair families
---------------------
1. exact_N → top_M  (nested_a_implies_b, exact_finish_implies_top_n)
   Valid when exact_position <= top_n.  E.g. "finishes 3rd" → "finishes top 4".

2. top_N → top_M  (nested_a_implies_b, top_n_implies_broader_top_n)
   Valid when top_n_a < top_n_b (strict).  E.g. "top 2" → "top 4".

3. exact_N ⊥ exact_M  (mutually_exclusive, exact_positions_mutually_exclusive)
   Valid when exact positions differ (same team, same competition, same season).

4. winner ⊥ exact_N  (mutually_exclusive, winner_mutually_exclusive_with_exact_finish)
   Valid when exact position >= 2 (winner ≠ 2nd/3rd etc., same team/competition).

5. winner → top_N  (nested_a_implies_b, championship_implies_top_n)
   Always valid for any N >= 1.

Hard guards (any failure → reject)
-----------------------------------
- same team slug
- same outcome_space_id (same competition/league)
- same year in question text (or both questions omit year → needs_manual_review)
- numeric position parsed successfully where required
- implication direction correct (lower position implies larger top-N window)

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
from ..taxonomy import classify_market  # reuse existing slug normalisation
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

_PASS_ID = "sports_ranking_v1"
_PASS_VERSION = 1

_RANKING_SUBTYPES = frozenset({
    "team_exact_finish_position",
    "team_top_n_finish",
    "team_wins_championship",
})


# ── market info ───────────────────────────────────────────────────────────────


@dataclass
class _MarketInfo:
    market_id: str
    condition_id: str | None
    question: str
    token_yes: str | None
    token_no: str | None
    outcome_subtype: str
    team_slug: str
    space_id: str
    exact_position: int | None   # e.g. 3 from "finish 3rd"
    top_n: int | None             # e.g. 4 from "finish top 4"
    is_winner: bool
    year: str | None


def _parse_position(question: str) -> tuple[int | None, int | None, bool]:
    """Extract (exact_position, top_n, is_winner) from question text.

    Returns (None, None, False) if no pattern matches.
    """
    lower = question.lower()

    # Top-N: "finish in the top 4", "finish top 4"
    m = re.search(r"\btop[\s-]+(\d+)\b", lower)
    if m:
        return None, int(m.group(1)), False

    # Exact ordinal: "finish 2nd", "finish in 3rd", "finish 2nd place"
    m = re.search(r"\bfinish(?:es)?(?:\s+in(?:\s+the)?)?\s+(\d+)(?:st|nd|rd|th)\b", lower)
    if m:
        return int(m.group(1)), None, False

    # "will finish [exactly] Nth"
    m = re.search(r"\bfinish\b.*?\b(\d+)(?:st|nd|rd|th)\b", lower)
    if m:
        return int(m.group(1)), None, False

    # Winner / champion / finals
    if any(kw in lower for kw in (
        "wins the ", "win the ", "wins ", "champion", "finals", "stanley cup", "world series"
    )):
        return None, None, True

    return None, None, False


def _tokens(market: MarketRow) -> tuple[str | None, str | None]:
    tok = market.clob_token_ids or []
    yes = tok[0] if tok else None
    no = tok[1] if len(tok) > 1 else None
    return yes, no


def _classify_market_info(
    market: MarketRow,
    sem: MarketSemanticsRow | None,
) -> _MarketInfo | None:
    """Classify a market; return None if it's not a ranking-relevant market."""
    side = classify_market(market.question, sem)
    if side.outcome_subtype not in _RANKING_SUBTYPES:
        return None
    if not side.team:
        return None

    exact_pos, top_n, is_winner = _parse_position(market.question)
    # Validate: subtype must agree with parse
    if side.outcome_subtype == "team_exact_finish_position" and exact_pos is None:
        return None
    if side.outcome_subtype == "team_top_n_finish" and top_n is None:
        return None

    tok_yes, tok_no = _tokens(market)
    year = extract_year(market.question)

    return _MarketInfo(
        market_id=market.id,
        condition_id=market.condition_id,
        question=market.question,
        token_yes=tok_yes,
        token_no=tok_no,
        outcome_subtype=side.outcome_subtype,
        team_slug=slug(side.team),
        space_id=side.outcome_space_id or slug(market.question[:40]),
        exact_position=exact_pos,
        top_n=top_n,
        is_winner=is_winner,
        year=year,
    )


# ── guard helpers ─────────────────────────────────────────────────────────────


def _year_guard(year_a: str | None, year_b: str | None) -> tuple[bool, str]:
    """Returns (passed, reason).  Hard-fail if years are present and differ."""
    if year_a is not None and year_b is not None:
        if year_a != year_b:
            return False, f"year_mismatch:{year_a}≠{year_b}"
        return True, f"years_match:{year_a}"
    return True, "year_not_in_both_questions"


def _implication_dir(a: _MarketInfo, b: _MarketInfo) -> str | None:
    """Return 'a_implies_b' / 'b_implies_a' for implication pairs, None otherwise.

    Handles:
      exact_N → top_M  (M >= N)
      top_N → top_M    (M > N)
      winner → top_M   (any M)
      winner → exact   (NOT valid — champion does NOT imply any exact finish)
    """
    # champion → top_N: yes for any N
    if a.is_winner and b.top_n is not None:
        return "a_implies_b"
    if b.is_winner and a.top_n is not None:
        return "b_implies_a"

    # exact → top: exact_N → top_M iff M >= N
    if a.exact_position is not None and b.top_n is not None:
        return "a_implies_b" if b.top_n >= a.exact_position else None
    if b.exact_position is not None and a.top_n is not None:
        return "b_implies_a" if a.top_n >= b.exact_position else None

    # top → top: top_N → top_M iff M > N (strictly)
    if a.top_n is not None and b.top_n is not None and a.top_n != b.top_n:
        if a.top_n < b.top_n:
            return "a_implies_b"
        return "b_implies_a"

    return None


# ── pair builder ──────────────────────────────────────────────────────────────


def _make_pair(
    a: _MarketInfo,
    b: _MarketInfo,
    year_guard_result: tuple[bool, str],
) -> RelationshipCandidateRow | None:
    """Build a RelationshipCandidateRow for the (a, b) pair, or None if invalid."""
    year_ok, year_note = year_guard_result
    if not year_ok:
        return None

    # Determine relationship type and subtype
    rel_type: str | None = None
    subtype: str = ""
    implication_dir = _implication_dir(a, b)

    if implication_dir is not None:
        rel_type = "nested_a_implies_b" if implication_dir == "a_implies_b" else "nested_b_implies_a"
        if a.is_winner or b.is_winner:
            subtype = "championship_implies_top_n"
        elif (a.exact_position is not None or b.exact_position is not None):
            subtype = "exact_finish_implies_top_n"
        else:
            subtype = "top_n_implies_broader_top_n"
    elif _should_be_mutually_exclusive(a, b):
        rel_type = "mutually_exclusive"
        if (a.is_winner or b.is_winner):
            subtype = "winner_mutually_exclusive_with_exact_finish"
        else:
            subtype = "exact_positions_mutually_exclusive"
    else:
        return None

    # Determine ordering: for implication, ensure A is the narrow side
    if rel_type == "nested_b_implies_a":
        a, b = b, a
        rel_type = "nested_a_implies_b"

    confidence = 0.95 if year_ok and a.year and b.year else 0.70
    validation_status = "accepted" if confidence >= 0.90 else "needs_manual_review"

    guard_results = {
        "same_team": True,
        "same_space": True,
        "year_check": year_note,
        "year_ok": year_ok,
        "exact_pos_a": a.exact_position,
        "top_n_a": a.top_n,
        "is_winner_a": a.is_winner,
        "exact_pos_b": b.exact_position,
        "top_n_b": b.top_n,
        "is_winner_b": b.is_winner,
    }
    parse_evidence = {
        "expansion_pass_id": _PASS_ID,
        "team_slug": a.team_slug,
        "space_id": a.space_id,
        "year_a": a.year,
        "year_b": b.year,
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
        relationship_type=rel_type,
        entity_match_score=1.0,
        time_scope_match_score=1.0 if a.year == b.year and a.year else 0.7,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=confidence,
        model_confidence=1.0,
        final_confidence=confidence,
        validation_status=validation_status,
        rejection_reasons_json="[]",
        rationale_summary=(
            f"Expansion ({_PASS_ID}): {subtype} — "
            f"team={a.team_slug} space={a.space_id}"
        ),
        evidence_json=build_evidence_json(_PASS_ID, guard_results, parse_evidence),
        rulebook_id=GENERATED_BY,
        rulebook_version=_PASS_VERSION,
        rulebook_content_hash=_PASS_ID,
        relationship_validity_status=validation_status,
        strategy_eligibility_status="eligible" if validation_status == "accepted" else "needs_manual_review",
        relationship_family="nesting" if "implies" in rel_type else "category",
        relationship_subtype=subtype,
        outcome_space_id=a.space_id,
        outcome_subtype_a=a.outcome_subtype,
        outcome_subtype_b=b.outcome_subtype,
        entity_type_a="team",
        entity_type_b="team",
        team_a=a.team_slug,
        team_b=b.team_slug,
        strategy_family="nesting" if "implies" in rel_type else "mutual_exclusion",
        strategy_eligible_reason=f"deterministic_{_PASS_ID}",
        classification_reason_json=build_classification_reason_json(
            _PASS_ID, f"{subtype} — team={a.team_slug} space={a.space_id}"
        ),
        schema_version=1,
        ingested_ts_ms=ts,
    )


def _should_be_mutually_exclusive(a: _MarketInfo, b: _MarketInfo) -> bool:
    """True if these two markets are logically mutually exclusive."""
    # Two different exact positions for the same team/season
    if a.exact_position is not None and b.exact_position is not None:
        return a.exact_position != b.exact_position

    # Winner ⊥ any exact finish position >= 2
    if a.is_winner and b.exact_position is not None:
        return b.exact_position >= 2
    if b.is_winner and a.exact_position is not None:
        return a.exact_position >= 2

    return False


# ── main entry point ──────────────────────────────────────────────────────────


def run_sports_ranking_expansion(
    data_root: Path,
    *,
    dry_run: bool = False,
    max_pairs_per_group: int = 500,
) -> ExpansionResult:
    """Run the sports ranking ladder expansion pass.

    Reads all market semantics, classifies markets for ranking subtypes,
    groups by (team, competition), and emits ladder relationship pairs.

    Args:
        data_root: Root of the Parquet data lake.
        dry_run: If True, compute but do not write to the store.
        max_pairs_per_group: Safety cap per (team, space) group.

    Returns:
        ExpansionResult with counts and audit rows.
    """
    market_repo = ParquetMarketsRepository(data_root)
    sem_repo = ParquetMarketSemanticsRepository(data_root)
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)

    # Load all markets and semantics
    markets: dict[str, MarketRow] = {m.id: m for m in market_repo.iter_all_markets()}
    semantics: dict[str, MarketSemanticsRow] = {
        s.source_market_id: s for s in sem_repo.iter_latest()
    }

    # Load existing pairs to avoid duplicates
    existing_pairs: set[frozenset[str]] = {
        frozenset({r.market_id_a, r.market_id_b})
        for r in rel_repo.iter_latest()
    }

    # Classify all markets for ranking-relevant subtypes
    classified: list[_MarketInfo] = []
    for market_id, market in markets.items():
        sem = semantics.get(market_id)
        info = _classify_market_info(market, sem)
        if info is not None:
            classified.append(info)

    # Group by (team_slug, space_id)
    groups: dict[tuple[str, str], list[_MarketInfo]] = defaultdict(list)
    for info in classified:
        if info.team_slug and info.space_id:
            groups[(info.team_slug, info.space_id)].append(info)

    new_rows: list[RelationshipCandidateRow] = []
    audit_rows: list[dict[str, Any]] = []
    skipped_existing = 0
    skipped_guard = 0
    needs_review = 0
    guard_failures: dict[str, int] = defaultdict(int)
    seen_pairs: set[frozenset[str]] = set()

    for (team_slug, space_id), group in groups.items():
        if len(group) < 2:
            continue
        pairs_in_group = 0
        for a, b in combinations(sorted(group, key=lambda x: x.market_id), 2):
            pair_key = frozenset({a.market_id, b.market_id})

            if pair_key in existing_pairs or pair_key in seen_pairs:
                skipped_existing += 1
                continue

            # Year guard
            year_ok, year_note = _year_guard(a.year, b.year)
            if not year_ok:
                skipped_guard += 1
                guard_failures["year_mismatch"] += 1
                audit_rows.append({
                    "pass": _PASS_ID,
                    "market_a": a.market_id,
                    "market_b": b.market_id,
                    "rejected_by": "year_mismatch",
                    "detail": year_note,
                })
                continue

            row = _make_pair(a, b, (year_ok, year_note))
            if row is None:
                skipped_guard += 1
                guard_failures["no_valid_relationship"] += 1
                continue

            seen_pairs.add(pair_key)
            new_rows.append(row)
            if row.validation_status == "needs_manual_review":
                needs_review += 1
            audit_rows.append({
                "pass": _PASS_ID,
                "market_a": a.market_id,
                "market_b": b.market_id,
                "relationship_type": row.relationship_type,
                "relationship_subtype": row.relationship_subtype,
                "team": team_slug,
                "space": space_id,
                "validation_status": row.validation_status,
            })
            pairs_in_group += 1
            if pairs_in_group >= max_pairs_per_group:
                break  # safety cap

    if not dry_run and new_rows:
        rel_repo.append_many(new_rows)

    return ExpansionResult(
        pass_id=_PASS_ID,
        emitted_count=len(new_rows),
        skipped_existing=skipped_existing,
        skipped_guard_fail=skipped_guard,
        needs_review_count=needs_review,
        guard_failure_counts=dict(guard_failures),
        audit_rows=audit_rows,
        dry_run=dry_run,
    )
