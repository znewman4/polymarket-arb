"""Full per-relationship data funnel report: CSV + markdown.

RESEARCH-ONLY / DIAGNOSTIC-ONLY. Not trading advice.

Builds on run_coverage_audit() (bulk queries, fast) and optionally evaluates
gross strategy violations per aligned tick.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from ..backtest.coverage_audit import RelationshipCoverageRow, run_coverage_audit
from ..backtest.price_alignment import align_price_series
from ..storage.parquet.price_history_repo import ParquetPriceHistoryRepository

_LABEL = "RESEARCH-ONLY / DIAGNOSTIC-ONLY — not trading advice"
_SUM_BASED_TYPES = frozenset({
    "mutually_exclusive_category", "contradiction", "mutually_exclusive",
    "same_entity_exclusive", "inverse", "inverse_temporal_order",
})


def generate_relationship_funnel_report(
    data_root: Path,
    output_dir: Path,
    *,
    include_violations: bool = False,
    min_gross_edge_for_violations: float = 0.001,
) -> tuple[Path, Path]:
    """Generate a full per-relationship funnel report.

    Returns (csv_path, md_path).

    If include_violations=True, evaluates strategy signals per aligned tick.
    This is O(Nxticks) and can be slow for large relationship sets.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    audit_rows = run_coverage_audit(data_root)

    # Optionally compute aligned tick counts and gross violations
    enriched = _enrich(
        audit_rows,
        data_root,
        include_violations=include_violations,
        min_gross_edge=min_gross_edge_for_violations,
    )

    csv_path = output_dir / f"relationship_funnel_{ts}.csv"
    md_path = output_dir / f"relationship_funnel_{ts}.md"

    _write_csv(csv_path, enriched)
    _write_markdown(md_path, enriched, ts, include_violations=include_violations)

    return csv_path, md_path


def _enrich(
    rows: list[RelationshipCoverageRow],
    data_root: Path,
    *,
    include_violations: bool,
    min_gross_edge: float,
) -> list[dict]:
    if not rows:
        return []

    price_repo = ParquetPriceHistoryRepository(data_root)

    # Bulk-fetch all YES token price history for rows that have both
    yes_tokens = [
        tok
        for r in rows
        for tok in (r.token_id_a_yes, r.token_id_b_yes)
        if tok
    ]
    price_cache: dict[str, list] = {}
    for row in price_repo.iter_for_tokens(yes_tokens):
        price_cache.setdefault(row.token_id, []).append(row)

    out: list[dict] = []
    for r in rows:
        d: dict = {
            "relationship_id": r.relationship_id,
            "market_id_a": r.market_id_a,
            "market_id_b": r.market_id_b,
            "token_id_a_yes": r.token_id_a_yes,
            "token_id_b_yes": r.token_id_b_yes,
            "question_a": r.question_a,
            "question_b": r.question_b,
            "relationship_family": "",
            "relationship_type": r.relationship_type,
            "relationship_subtype": r.relationship_subtype,
            "strategy_lane": r.strategy_lane or "unassigned",
            "validation_status": r.validation_status,
            "final_confidence": r.final_confidence,
            "strategy_eligibility_status": r.strategy_eligibility_status,
            "gamma_exists_a": r.gamma_exists_a,
            "gamma_exists_b": r.gamma_exists_b,
            "price_history_exists_a": r.price_history_exists_a,
            "price_history_exists_b": r.price_history_exists_b,
            "coverage_score_a": r.coverage_score_a,
            "coverage_score_b": r.coverage_score_b,
            "coverage_score_pair": r.coverage_score_pair,
            "tick_count_a": r.tick_count_a,
            "tick_count_b": r.tick_count_b,
            "aligned_tick_count": 0,
            "gross_violations": -1 if not include_violations else 0,
            "routing_note": _routing_note(r),
            "final_blocker": r.final_blocker,
            "label": _LABEL,
        }

        if r.both_have_price_history and r.token_id_a_yes and r.token_id_b_yes:
            rows_a = price_cache.get(r.token_id_a_yes, [])
            rows_b = price_cache.get(r.token_id_b_yes, [])
            if rows_a and rows_b:
                aligned = align_price_series(rows_a, rows_b)
                d["aligned_tick_count"] = len(aligned)

                if include_violations and aligned:
                    from ..strategies.nesting_contradiction import evaluate_relationship_at_tick

                    stub = _build_rel_stub(r)
                    violations = 0
                    for point in aligned:
                        cand = evaluate_relationship_at_tick(
                            rel=stub, point=point, run_id="funnel",
                            min_gross_edge=min_gross_edge,
                            fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
                            min_net_edge=0.0, stake_usdc=Decimal("250"),
                        )
                        if cand is not None:
                            violations += 1
                    d["gross_violations"] = violations

        out.append(d)
    return out


def _routing_note(r: RelationshipCoverageRow) -> str:
    if r.relationship_type in _SUM_BASED_TYPES:
        if r.final_blocker == "none":
            return "eligible for pairwise overround — verify P(A)+P(B) economic viability"
        return "sum-based type — check P(A)+P(B) thresholds before pairwise backtest"
    return ""


def _build_rel_stub(r: RelationshipCoverageRow):
    """Build a minimal RelationshipCandidateRow stub from a coverage row."""
    from datetime import datetime
    from datetime import timezone as tz

    from ..storage.base import RelationshipCandidateRow
    ts = int(datetime.now(tz.utc).timestamp() * 1000)
    return RelationshipCandidateRow(
        relationship_id=r.relationship_id,
        market_id_a=r.market_id_a,
        market_id_b=r.market_id_b,
        condition_id_a=None, condition_id_b=None,
        token_id_a_yes=r.token_id_a_yes,
        token_id_a_no=None,
        token_id_b_yes=r.token_id_b_yes,
        token_id_b_no=None,
        question_a=r.question_a, question_b=r.question_b,
        relationship_type=r.relationship_type,
        entity_match_score=1.0, time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=r.final_confidence,
        model_confidence=r.final_confidence,
        final_confidence=r.final_confidence,
        validation_status=r.validation_status,
        rejection_reasons_json="[]",
        rationale_summary="", evidence_json="{}",
        rulebook_id="funnel", rulebook_version=1, rulebook_content_hash="",
        schema_version=1, ingested_ts_ms=ts,
    )


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(
    path: Path,
    rows: list[dict],
    ts: str,
    *,
    include_violations: bool,
) -> None:
    total = len(rows)
    both_ph = sum(1 for r in rows if r["price_history_exists_a"] and r["price_history_exists_b"])
    no_blocker = sum(1 for r in rows if r["final_blocker"] == "none")
    has_violations = sum(1 for r in rows if r["gross_violations"] > 0)

    lines = [
        f"# Relationship Data Funnel Report — {ts}",
        "",
        f"> {_LABEL}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Total relationships | {total} |",
        f"| Both markets have price history | {both_ph} |",
        f"| No blockers (fully covered) | {no_blocker} |",
        f"| With gross violations (>0) | {has_violations if include_violations else 'not evaluated'} |",
        "",
        "## Per-relationship funnel",
        "",
        "| id | type | lane | conf | cov_pair | has_ph | aligned | violations | blocker |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in sorted(rows, key=lambda x: -(x.get("gross_violations") or 0)):
        rid = str(r["relationship_id"])[:18]
        v = r["gross_violations"] if include_violations else "—"
        lines.append(
            f"| `{rid}` "
            f"| {r['relationship_type']} "
            f"| {r['strategy_lane']} "
            f"| {float(r['final_confidence']):.2f} "
            f"| {float(r['coverage_score_pair']):.2f} "
            f"| {'✓✓' if r['price_history_exists_a'] and r['price_history_exists_b'] else '✗'} "
            f"| {r['aligned_tick_count']} "
            f"| {v} "
            f"| `{r['final_blocker']}` |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")
