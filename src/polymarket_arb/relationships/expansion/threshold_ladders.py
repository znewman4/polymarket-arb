"""Threshold ladder expansion pass.

Reads all market semantics and generates deterministic implication pairs for
threshold markets on the same asset/variable with the same deadline.

Reuses existing infrastructure:
  threshold_extraction.extract_threshold_claim()   — parse a threshold claim
  threshold_extraction.detect_threshold_nesting()  — determine implication direction

Emitted pair families
---------------------
1. higher_gt → lower_gt  (nested_a_implies_b, threshold_higher_implies_lower)
   If BTC > $200k then BTC > $150k (same asset, same deadline, same direction).

2. lower_lt → higher_lt  (nested_a_implies_b, threshold_lower_implies_higher)
   If CPI < 2% then CPI < 4% (same asset, same deadline, same direction).

Hard guards
-----------
- Same variable (btc_price, eth_price, cpi, etc.) — different assets rejected
- Same comparator direction (both ">" or both "<") — mixed direction rejected
- Deadline match: either both have no deadline, or both have deadlines within
  the ±2-week tolerance already enforced by detect_threshold_nesting()
- Threshold values must differ (no pair where value_a == value_b)
- Shared resolution source (from sem.resolution_source) if available and mismatched

RESEARCH-ONLY — no live trading.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

from ...storage.base import MarketRow, MarketSemanticsRow, RelationshipCandidateRow
from ...storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from ...storage.parquet.markets_repo import ParquetMarketsRepository
from ...storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from ..threshold_extraction import ThresholdClaim, detect_threshold_nesting, extract_threshold_claim
from . import ExpansionResult
from .audit import (
    GENERATED_BY,
    build_classification_reason_json,
    build_evidence_json,
    make_relationship_id,
    now_ms,
)

_PASS_ID = "threshold_ladders_v1"
_PASS_VERSION = 1


def run_threshold_ladder_expansion(
    data_root: Path,
    *,
    dry_run: bool = False,
    max_pairs_per_group: int = 200,
) -> ExpansionResult:
    """Run the threshold ladder expansion pass.

    Reads all market semantics, extracts threshold claims, groups by variable
    + comparator + deadline bucket, and emits implication pairs.

    Args:
        data_root: Root of the Parquet data lake.
        dry_run: If True, compute but do not write to the store.
        max_pairs_per_group: Safety cap per (variable, comparator) group.
    """
    market_repo = ParquetMarketsRepository(data_root)
    sem_repo = ParquetMarketSemanticsRepository(data_root)
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)

    markets: dict[str, MarketRow] = {m.id: m for m in market_repo.iter_all_markets()}
    existing_pairs: set[frozenset[str]] = {
        frozenset({r.market_id_a, r.market_id_b})
        for r in rel_repo.iter_latest()
    }

    # Extract claims from all semantics rows
    _MInfo = _make_market_info

    classified: list[tuple[str, ThresholdClaim, MarketSemanticsRow]] = []
    for sem in sem_repo.iter_latest():
        claim = extract_threshold_claim(sem)
        if claim is None:
            continue
        classified.append((sem.source_market_id, claim, sem))

    # Group by (variable, comparator) — deadline comparison happens per-pair
    groups: dict[tuple[str, str], list[tuple[str, ThresholdClaim, MarketSemanticsRow]]] = defaultdict(list)
    for mid, claim, sem in classified:
        key = (claim.variable, claim.comparator)
        groups[key].append((mid, claim, sem))

    new_rows: list[RelationshipCandidateRow] = []
    audit_rows: list[dict[str, Any]] = []
    skipped_existing = 0
    skipped_guard = 0
    seen_pairs: set[frozenset[str]] = set()
    guard_failures: dict[str, int] = defaultdict(int)

    for (variable, comparator), group in groups.items():
        pairs_emitted = 0
        for (mid_a, claim_a, sem_a), (mid_b, claim_b, sem_b) in combinations(
            sorted(group, key=lambda x: x[0]), 2
        ):
            pair_key = frozenset({mid_a, mid_b})
            if pair_key in existing_pairs or pair_key in seen_pairs:
                skipped_existing += 1
                continue

            direction = detect_threshold_nesting(claim_a, claim_b)
            if direction == "none":
                skipped_guard += 1
                guard_failures["nesting_direction_none"] += 1
                continue

            # Resolution source guard: reject if both present and different
            res_a = getattr(sem_a, "resolution_source", None) or ""
            res_b = getattr(sem_b, "resolution_source", None) or ""
            if res_a and res_b and res_a.strip() != res_b.strip():
                skipped_guard += 1
                guard_failures["resolution_source_mismatch"] += 1
                audit_rows.append({
                    "pass": _PASS_ID,
                    "market_a": mid_a,
                    "market_b": mid_b,
                    "rejected_by": "resolution_source_mismatch",
                    "res_a": res_a[:60],
                    "res_b": res_b[:60],
                })
                continue

            # Build the row — narrow side first (a_implies_b means A is narrow/harder)
            if direction == "a_implies_b":
                narrow_mid, narrow_claim, narrow_sem = mid_a, claim_a, sem_a
                broad_mid, broad_claim, broad_sem = mid_b, claim_b, sem_b
            else:
                narrow_mid, narrow_claim, narrow_sem = mid_b, claim_b, sem_b
                broad_mid, broad_claim, broad_sem = mid_a, claim_a, sem_a

            market_a = markets.get(narrow_mid)
            market_b = markets.get(broad_mid)
            if market_a is None or market_b is None:
                skipped_guard += 1
                guard_failures["market_row_missing"] += 1
                continue

            subtype = (
                "threshold_higher_implies_lower"
                if comparator in (">", ">=")
                else "threshold_lower_implies_higher"
            )

            has_deadline = narrow_claim.deadline_ms is not None
            confidence = 0.95 if has_deadline else 0.80
            status = "accepted" if confidence >= 0.90 else "needs_manual_review"

            guard = {
                "same_variable": True,
                "same_comparator": True,
                "comparator": comparator,
                "narrow_value": str(narrow_claim.value),
                "broad_value": str(broad_claim.value),
                "narrow_deadline_ms": narrow_claim.deadline_ms,
                "broad_deadline_ms": broad_claim.deadline_ms,
                "deadline_matched": has_deadline,
                "resolution_source_a": res_a[:80],
                "resolution_source_b": res_b[:80],
            }
            parse_ev = {
                "expansion_pass_id": _PASS_ID,
                "variable": variable,
                "narrow_raw": narrow_claim.raw_phrase,
                "broad_raw": broad_claim.raw_phrase,
            }

            tok_a = market_a.clob_token_ids or []
            tok_b = market_b.clob_token_ids or []
            ts = now_ms()

            row = RelationshipCandidateRow(
                relationship_id=make_relationship_id(),
                market_id_a=narrow_mid,
                market_id_b=broad_mid,
                condition_id_a=market_a.condition_id,
                condition_id_b=market_b.condition_id,
                token_id_a_yes=tok_a[0] if tok_a else None,
                token_id_a_no=tok_a[1] if len(tok_a) > 1 else None,
                token_id_b_yes=tok_b[0] if tok_b else None,
                token_id_b_no=tok_b[1] if len(tok_b) > 1 else None,
                question_a=narrow_sem.question,
                question_b=broad_sem.question,
                relationship_type="nested_a_implies_b",
                entity_match_score=1.0,
                time_scope_match_score=1.0 if has_deadline else 0.75,
                resolution_criteria_match_score=0.9 if not (res_a and res_b) else 1.0,
                threshold_relation_json="{}",
                semantic_similarity_score=None,
                deterministic_confidence=confidence,
                model_confidence=1.0,
                final_confidence=confidence,
                validation_status=status,
                rejection_reasons_json="[]",
                rationale_summary=(
                    f"Expansion ({_PASS_ID}): {subtype} — "
                    f"{variable} {comparator} {narrow_claim.value} → "
                    f"{variable} {comparator} {broad_claim.value}"
                ),
                evidence_json=build_evidence_json(_PASS_ID, guard, parse_ev),
                rulebook_id=GENERATED_BY,
                rulebook_version=_PASS_VERSION,
                rulebook_content_hash=_PASS_ID,
                relationship_validity_status=status,
                strategy_eligibility_status="eligible" if status == "accepted" else "needs_manual_review",
                relationship_family="nesting",
                relationship_subtype=subtype,
                outcome_space_id=f"{variable}_threshold",
                outcome_subtype_a="price_hits_level_before_date",
                outcome_subtype_b="price_hits_level_before_date",
                entity_type_a="threshold",
                entity_type_b="threshold",
                strategy_family="nesting",
                strategy_eligible_reason=f"deterministic_{_PASS_ID}",
                classification_reason_json=build_classification_reason_json(
                    _PASS_ID, f"{subtype} {variable} {comparator}"
                ),
                schema_version=1,
                ingested_ts_ms=ts,
            )

            seen_pairs.add(pair_key)
            new_rows.append(row)
            audit_rows.append({
                "pass": _PASS_ID,
                "market_a": narrow_mid,
                "market_b": broad_mid,
                "variable": variable,
                "subtype": subtype,
                "narrow_value": str(narrow_claim.value),
                "broad_value": str(broad_claim.value),
                "status": status,
            })
            pairs_emitted += 1
            if pairs_emitted >= max_pairs_per_group:
                break

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


def _make_market_info() -> None:
    pass  # placeholder for type hint alias
