"""Date/time ladder expansion pass.

Reads all market semantics and generates deterministic implication pairs for
"event before date" markets sharing the same event but different deadlines.

Reuses existing infrastructure:
  terms_similarity.infer_temporal_terms_from_question() — parse before/after markets

Emitted pair family
-------------------
1. earlier_deadline → later_deadline  (nested_a_implies_b, date_earlier_implies_later)
   If X happens before March, it also happens before June for the same event X.

Hard guards
-----------
- Same left_event_id (the thing that happens) — different events rejected
- Same relation direction (both "before") — "after" markets kept separate
- Both deadlines must be parseable to a sortable ordinal
- Earlier deadline strictly before later deadline (no equal deadlines)
- Shared "before GTA VI" style reference events → analysis_only (cannot be emitted)
  Reason: if two markets both say "before GTA VI release", their right_event is a
  shared reference event, not an absolute date — no safe implication unless proven same
- Ambiguous deadline strings → needs_manual_review rather than rejected

RESEARCH-ONLY — no live trading.
"""

from __future__ import annotations

import re
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

from ...storage.base import MarketRow, MarketSemanticsRow, RelationshipCandidateRow
from ...storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from ...storage.parquet.markets_repo import ParquetMarketsRepository
from ...storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from ..terms_similarity import infer_temporal_terms_from_question
from . import ExpansionResult
from .audit import (
    GENERATED_BY,
    build_classification_reason_json,
    build_evidence_json,
    make_relationship_id,
    now_ms,
)

_PASS_ID = "date_ladders_v1"
_PASS_VERSION = 1

# Ordinal value for "same deadline" tolerance: allow ≤1 month apart to be
# treated as candidates; tighter than the ±2-week threshold in threshold_extraction
# because dates here are often month-granularity.
_SAME_DEADLINE_TOLERANCE_ORDINAL = 0  # must be strictly different

# Month name → month number
_MONTH_NAMES = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_QUARTER_MAP = {"q1": 2, "q2": 5, "q3": 8, "q4": 11}  # mapped to mid-month of each quarter


def _parts(slug: str) -> list[str]:
    """Split a slug into lowercase tokens on non-alphanumeric separators."""
    return re.split(r"[^a-z0-9]+", slug.lower())


def _deadline_ordinal(right_event_slug: str) -> int | None:
    """Convert a deadline slug to year*100+month (1-12).  None if unparseable.

    Examples:
      "2026"          → 202600  (year only, month 0 = unspecified, sorts before any month)
      "q1_2026"       → 202602  (Q1 = approx Feb midpoint)
      "march_2026"    → 202603
      "end_of_2025"   → 202512
      "year_end_2026" → 202612
    """
    tokens = _parts(right_event_slug)
    year: int | None = None
    month_num: int | None = None
    quarter_num: int | None = None
    is_year_end = False

    for t in tokens:
        # Year
        if re.fullmatch(r"20\d{2}", t):
            year = int(t)
        # Quarter
        elif re.fullmatch(r"q[1-4]", t):
            quarter_num = _QUARTER_MAP.get(t)
        # Month name
        elif t in _MONTH_NAMES:
            month_num = _MONTH_NAMES[t]
        # year-end markers
        elif t in ("end", "yearend"):
            is_year_end = True

    if year is None:
        return None

    # year_end / end_of_year patterns
    if is_year_end and month_num is None and quarter_num is None:
        return year * 100 + 12

    if quarter_num is not None:
        return year * 100 + quarter_num
    if month_num is not None:
        return year * 100 + month_num
    return year * 100  # year-only


def _is_shared_reference_clock(right_event_slug: str) -> bool:
    """Return True if the right_event looks like a shared reference event, not an absolute date.

    Shared reference examples: "gta_vi_release", "super_bowl", "election_day"
    These are NOT safe for date ladder implication — they're clocks, not deadlines.
    Absolute date examples: "2026", "march_2026", "q1_2026"
    """
    tokens = _parts(right_event_slug)
    # Any 4-digit year → absolute date reference
    if any(re.fullmatch(r"20\d{2}", t) for t in tokens):
        return False
    # Any month name → absolute date
    if any(t in _MONTH_NAMES for t in tokens):
        return False
    # Quarter token q1/q2/q3/q4 → absolute date
    if any(re.fullmatch(r"q[1-4]", t) for t in tokens):
        return False
    # No date indicator → shared reference clock
    return True


def run_date_ladder_expansion(
    data_root: Path,
    *,
    dry_run: bool = False,
    max_pairs_per_group: int = 200,
) -> ExpansionResult:
    """Run the date/time ladder expansion pass.

    Reads all market semantics, parses before-date markets, groups by left_event,
    and emits implication pairs for different deadlines on the same event.

    Args:
        data_root: Root of the Parquet data lake.
        dry_run: If True, compute but do not write to the store.
        max_pairs_per_group: Safety cap per left_event group.
    """
    market_repo = ParquetMarketsRepository(data_root)
    sem_repo = ParquetMarketSemanticsRepository(data_root)
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)

    markets: dict[str, MarketRow] = {m.id: m for m in market_repo.iter_all_markets()}
    existing_pairs: set[frozenset[str]] = {
        frozenset({r.market_id_a, r.market_id_b})
        for r in rel_repo.iter_latest()
    }

    # Parse temporal propositions from all semantics
    from dataclasses import dataclass

    @dataclass
    class _DateInfo:
        market_id: str
        sem: MarketSemanticsRow
        left_event_id: str
        right_event_id: str
        relation: str
        deadline_ordinal: int | None
        is_reference_clock: bool

    classified: list[_DateInfo] = []
    for sem in sem_repo.iter_latest():
        _, prop = infer_temporal_terms_from_question(sem.question)
        if prop is None:
            continue
        if prop.get("relation") != "before":
            continue  # only "before" markets form ladders
        left_id = prop.get("left_event", "")
        right_id = prop.get("right_event", "")
        if not left_id or not right_id:
            continue

        is_clock = _is_shared_reference_clock(right_id)
        ordinal = _deadline_ordinal(right_id)

        classified.append(_DateInfo(
            market_id=sem.source_market_id,
            sem=sem,
            left_event_id=left_id,
            right_event_id=right_id,
            relation="before",
            deadline_ordinal=ordinal,
            is_reference_clock=is_clock,
        ))

    # Group by left_event_id — only non-reference-clock markets
    groups: dict[str, list[_DateInfo]] = defaultdict(list)
    for info in classified:
        if not info.is_reference_clock:
            groups[info.left_event_id].append(info)

    new_rows: list[RelationshipCandidateRow] = []
    audit_rows: list[dict[str, Any]] = []
    skipped_existing = 0
    skipped_guard = 0
    seen_pairs: set[frozenset[str]] = set()
    guard_failures: dict[str, int] = defaultdict(int)

    for left_event_id, group in groups.items():
        if len(group) < 2:
            continue
        pairs_emitted = 0
        for a, b in combinations(sorted(group, key=lambda x: x.market_id), 2):
            pair_key = frozenset({a.market_id, b.market_id})
            if pair_key in existing_pairs or pair_key in seen_pairs:
                skipped_existing += 1
                continue

            # Both deadlines must be parseable OR ambiguous (→ needs_manual_review)
            has_a = a.deadline_ordinal is not None
            has_b = b.deadline_ordinal is not None

            if not has_a or not has_b:
                # At least one unparseable → ambiguous → needs_manual_review, still emit
                confidence = 0.65
                status = "needs_manual_review"
                direction = "unknown"
                narrow, broad = a, b  # arbitrary order when ambiguous
            else:
                if a.deadline_ordinal == b.deadline_ordinal:
                    skipped_guard += 1
                    guard_failures["equal_deadlines"] += 1
                    continue
                if a.deadline_ordinal < b.deadline_ordinal:
                    # a is earlier → a implies b
                    direction = "a_implies_b"
                    narrow, broad = a, b
                else:
                    direction = "b_implies_a"
                    narrow, broad = b, a
                confidence = 0.92
                status = "accepted"

            market_a = markets.get(narrow.market_id)
            market_b = markets.get(broad.market_id)
            if market_a is None or market_b is None:
                skipped_guard += 1
                guard_failures["market_row_missing"] += 1
                continue

            guard = {
                "same_left_event": True,
                "left_event_id": left_event_id,
                "narrow_right_event": narrow.right_event_id,
                "broad_right_event": broad.right_event_id,
                "narrow_ordinal": narrow.deadline_ordinal,
                "broad_ordinal": broad.deadline_ordinal,
                "direction": direction,
                "is_reference_clock": False,
            }
            parse_ev = {
                "expansion_pass_id": _PASS_ID,
                "left_event_id": left_event_id,
            }

            tok_a = market_a.clob_token_ids or []
            tok_b = market_b.clob_token_ids or []
            ts = now_ms()

            row = RelationshipCandidateRow(
                relationship_id=make_relationship_id(),
                market_id_a=narrow.market_id,
                market_id_b=broad.market_id,
                condition_id_a=market_a.condition_id,
                condition_id_b=market_b.condition_id,
                token_id_a_yes=tok_a[0] if tok_a else None,
                token_id_a_no=tok_a[1] if len(tok_a) > 1 else None,
                token_id_b_yes=tok_b[0] if tok_b else None,
                token_id_b_no=tok_b[1] if len(tok_b) > 1 else None,
                question_a=narrow.sem.question,
                question_b=broad.sem.question,
                relationship_type="nested_a_implies_b",
                entity_match_score=1.0,
                time_scope_match_score=1.0 if has_a and has_b else 0.7,
                resolution_criteria_match_score=1.0,
                threshold_relation_json="{}",
                semantic_similarity_score=None,
                deterministic_confidence=confidence,
                model_confidence=1.0,
                final_confidence=confidence,
                validation_status=status,
                rejection_reasons_json="[]",
                rationale_summary=(
                    f"Expansion ({_PASS_ID}): date_earlier_implies_later — "
                    f"{left_event_id} before {narrow.right_event_id} → "
                    f"{left_event_id} before {broad.right_event_id}"
                ),
                evidence_json=build_evidence_json(_PASS_ID, guard, parse_ev),
                rulebook_id=GENERATED_BY,
                rulebook_version=_PASS_VERSION,
                rulebook_content_hash=_PASS_ID,
                relationship_validity_status=status,
                strategy_eligibility_status="eligible" if status == "accepted" else "needs_manual_review",
                relationship_family="nesting",
                relationship_subtype="date_earlier_implies_later",
                outcome_space_id=f"{left_event_id}_deadline",
                outcome_subtype_a="event_a_before_event_b",
                outcome_subtype_b="event_a_before_event_b",
                entity_type_a="event",
                entity_type_b="event",
                strategy_family="nesting",
                strategy_eligible_reason=f"deterministic_{_PASS_ID}",
                classification_reason_json=build_classification_reason_json(
                    _PASS_ID, f"date_earlier_implies_later event={left_event_id}"
                ),
                schema_version=1,
                ingested_ts_ms=ts,
            )

            seen_pairs.add(pair_key)
            new_rows.append(row)
            audit_rows.append({
                "pass": _PASS_ID,
                "market_a": narrow.market_id,
                "market_b": broad.market_id,
                "left_event": left_event_id,
                "narrow_deadline": narrow.right_event_id,
                "broad_deadline": broad.right_event_id,
                "status": status,
            })
            pairs_emitted += 1
            if pairs_emitted >= max_pairs_per_group:
                break

    # Record analysis_only clock pairs in audit (but do NOT emit as relationships)
    clock_pairs = [info for info in classified if info.is_reference_clock]
    for info in clock_pairs:
        audit_rows.append({
            "pass": _PASS_ID,
            "market_id": info.market_id,
            "note": "analysis_only_shared_reference_clock",
            "right_event": info.right_event_id,
        })

    if not dry_run and new_rows:
        rel_repo.append_many(new_rows)

    return ExpansionResult(
        pass_id=_PASS_ID,
        emitted_count=len(new_rows),
        skipped_existing=skipped_existing,
        skipped_guard_fail=skipped_guard,
        needs_review_count=sum(1 for r in new_rows if r.validation_status == "needs_manual_review"),
        guard_failure_counts=dict(guard_failures),
        audit_rows=audit_rows,
        dry_run=dry_run,
    )
