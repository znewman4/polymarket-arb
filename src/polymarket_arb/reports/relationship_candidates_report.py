"""Generate the Relationship Candidates HTML report."""

from __future__ import annotations

import json
import shutil
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from .charts import bar, histogram
from .html import render_report
from .tables import df_to_html, truncate, write_csv


def _generated_at() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _no_thinking(text: str) -> str:
    """Strip any <think> content from display text."""
    import re
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def generate_relationship_candidates_report(
    data_root: Path,
    output_dir: Path | None = None,
) -> Path:
    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    base_dir = output_dir or (data_root.parent / "reports" / "relationship_candidates" / run_id)
    base_dir.mkdir(parents=True, exist_ok=True)

    repo = ParquetRelationshipCandidatesRepository(data_root)
    rows = list(repo.iter_latest())

    # Safety: no thinking content
    for row in rows:
        assert "<think>" not in (row.rationale_summary or ""), "chain-of-thought in rationale_summary"

    # Split into buckets
    accepted = [r for r in rows if r.validation_status == "accepted"]
    rejected = [r for r in rows if r.validation_status == "rejected"]
    manual_review = [r for r in rows if r.validation_status == "needs_manual_review"]

    # Phase 5.5 D+: strategy eligibility split
    strategy_eligible = [
        r for r in accepted
        if getattr(r, "strategy_eligibility_status", "unknown") == "eligible"
    ]
    strategy_ineligible_but_valid = [
        r for r in accepted
        if getattr(r, "strategy_eligibility_status", "unknown") != "eligible"
    ]

    # Counts by type
    type_counts = Counter(r.relationship_type for r in rows)
    # Counts by rejection reason
    rejection_reason_counts: Counter = Counter()
    for r in rejected:
        reasons = json.loads(r.rejection_reasons_json or "[]")
        for reason in reasons:
            rejection_reason_counts[reason.get("code", "unknown")] += 1
    # Counts by strategy exclusion reason
    strategy_exclusion_counts: Counter = Counter()
    for r in strategy_ineligible_but_valid:
        reasons_str = getattr(r, "strategy_exclusion_reasons_json", "[]") or "[]"
        try:
            reasons = json.loads(reasons_str)
        except (json.JSONDecodeError, TypeError):
            reasons = []
        for reason in reasons:
            strategy_exclusion_counts[reason.get("code", "unknown")] += 1

    # Confidence distribution
    confidence_values = [r.final_confidence for r in rows]

    # Charts
    charts_dir = base_dir
    type_chart_path: str | None = None
    conf_chart_path: str | None = None
    rejection_chart_path: str | None = None
    strat_eligibility_chart_path: str | None = None

    if type_counts:
        type_labels = list(type_counts.keys())
        type_values = [float(type_counts[k]) for k in type_labels]
        p = bar(
            type_labels, type_values,
            title="Relationship Type Counts",
            xlabel="Type", ylabel="Count",
            output_path=charts_dir / "chart_types.png",
        )
        type_chart_path = str(p)

    if confidence_values:
        p = histogram(
            confidence_values,
            title="Final Confidence Distribution",
            xlabel="Confidence", ylabel="Count",
            output_path=charts_dir / "chart_confidence.png",
        )
        conf_chart_path = str(p)

    if rejection_reason_counts:
        rej_labels = [k for k, _ in rejection_reason_counts.most_common(10)]
        rej_values = [float(rejection_reason_counts[k]) for k in rej_labels]
        p = bar(
            rej_labels, rej_values,
            title="Rejection Reason Counts",
            xlabel="Reason", ylabel="Count",
            output_path=charts_dir / "chart_rejections.png",
        )
        rejection_chart_path = str(p)

    eligibility_counts = {
        "strategy_eligible": len(strategy_eligible),
        "valid_but_ineligible": len(strategy_ineligible_but_valid),
        "rejected": len(rejected),
        "manual_review": len(manual_review),
    }
    if any(v > 0 for v in eligibility_counts.values()):
        p = bar(
            list(eligibility_counts.keys()),
            [float(v) for v in eligibility_counts.values()],
            title="Strategy Eligibility",
            xlabel="Status", ylabel="Count",
            output_path=charts_dir / "chart_strategy_eligibility.png",
        )
        strat_eligibility_chart_path = str(p)

    # Build DataFrames for tables
    def _row_to_dict(r, truncate_questions: bool = True) -> dict:
        q_a = _no_thinking(r.question_a)
        q_b = _no_thinking(r.question_b)
        return {
            "relationship_id": r.relationship_id,
            "market_id_a": r.market_id_a,
            "market_id_b": r.market_id_b,
            "type": r.relationship_type,
            "family": getattr(r, "relationship_family", ""),
            "status": r.validation_status,
            "strategy_eligibility": getattr(r, "strategy_eligibility_status", "unknown"),
            "question_a": truncate(q_a, 80) if truncate_questions else q_a,
            "question_b": truncate(q_b, 80) if truncate_questions else q_b,
            "final_confidence": round(r.final_confidence, 4),
            "entity_score": round(r.entity_match_score, 3),
            "time_score": round(r.time_scope_match_score, 3),
            "threshold": r.threshold_relation_json if r.threshold_relation_json != "{}" else "",
            "candidate_a": getattr(r, "candidate_a", "") or "",
            "candidate_b": getattr(r, "candidate_b", "") or "",
            "shared_event": getattr(r, "shared_event", "") or "",
        }

    top_accepted = sorted(accepted, key=lambda r: r.final_confidence, reverse=True)[:20]
    # Split accepted by family for specific sections
    mutex_category = [r for r in accepted if r.relationship_type == "mutually_exclusive_category"][:20]
    temporal_deps = [r for r in accepted if r.relationship_type in (
        "temporal_before", "temporal_after", "same_reference_clock")][:20]
    inverse_temporal = [r for r in accepted if r.relationship_type == "inverse_temporal_order"][:20]
    top_contradiction = [r for r in accepted if r.relationship_type in (
        "contradiction", "inverse", "mutually_exclusive", "same_entity_exclusive")][:20]
    example_rejected = [r for r in rejected if r.relationship_type == "same_topic_no_trade"][:10]
    if not example_rejected:
        example_rejected = sorted(rejected, key=lambda r: r.final_confidence, reverse=True)[:10]
    example_manual = manual_review[:10]

    accepted_df = pd.DataFrame([_row_to_dict(r) for r in top_accepted]) if top_accepted else pd.DataFrame()
    contradiction_df = pd.DataFrame([_row_to_dict(r) for r in top_contradiction]) if top_contradiction else pd.DataFrame()
    mutex_category_df = pd.DataFrame([_row_to_dict(r) for r in mutex_category]) if mutex_category else pd.DataFrame()
    temporal_deps_df = pd.DataFrame([_row_to_dict(r) for r in temporal_deps]) if temporal_deps else pd.DataFrame()
    inverse_temporal_df = pd.DataFrame([_row_to_dict(r) for r in inverse_temporal]) if inverse_temporal else pd.DataFrame()
    rejected_df = pd.DataFrame([_row_to_dict(r) for r in example_rejected]) if example_rejected else pd.DataFrame()
    manual_df = pd.DataFrame([_row_to_dict(r) for r in example_manual]) if example_manual else pd.DataFrame()
    ineligible_df = pd.DataFrame([_row_to_dict(r) for r in strategy_ineligible_but_valid[:20]]) if strategy_ineligible_but_valid else pd.DataFrame()

    # Write CSVs (full untruncated text)
    all_df = pd.DataFrame([_row_to_dict(r, truncate_questions=False) for r in rows]) if rows else pd.DataFrame()
    accepted_csv_df = pd.DataFrame([_row_to_dict(r, truncate_questions=False) for r in accepted]) if accepted else pd.DataFrame()
    rejected_csv_df = pd.DataFrame([_row_to_dict(r, truncate_questions=False) for r in rejected]) if rejected else pd.DataFrame()
    manual_csv_df = pd.DataFrame([_row_to_dict(r, truncate_questions=False) for r in manual_review]) if manual_review else pd.DataFrame()
    eligible_csv_df = pd.DataFrame([_row_to_dict(r, truncate_questions=False) for r in strategy_eligible]) if strategy_eligible else pd.DataFrame()
    mutex_cat_csv_df = pd.DataFrame([_row_to_dict(r, truncate_questions=False) for r in mutex_category]) if mutex_category else pd.DataFrame()

    write_csv(all_df, base_dir / "relationships.csv")
    write_csv(accepted_csv_df, base_dir / "accepted_relationships.csv")
    write_csv(rejected_csv_df, base_dir / "rejected_relationships.csv")
    write_csv(manual_csv_df, base_dir / "manual_review_relationships.csv")
    write_csv(eligible_csv_df, base_dir / "strategy_eligible_relationships.csv")
    write_csv(mutex_cat_csv_df, base_dir / "mutually_exclusive_category_relationships.csv")

    # Render HTML
    context = {
        "generated_at": _generated_at(),
        "run_id": run_id,
        "total_considered": len(rows),
        "total_accepted": len(accepted),
        "total_rejected": len(rejected),
        "total_manual_review": len(manual_review),
        "total_strategy_eligible": len(strategy_eligible),
        "total_strategy_ineligible_but_valid": len(strategy_ineligible_but_valid),
        "type_counts": dict(type_counts.most_common()),
        "top_type": type_counts.most_common(1)[0][0] if type_counts else "N/A",
        "type_chart": type_chart_path,
        "conf_chart": conf_chart_path,
        "rejection_chart": rejection_chart_path,
        "strat_eligibility_chart": strat_eligibility_chart_path,
        "rejection_counts": dict(rejection_reason_counts.most_common(10)),
        "strategy_exclusion_counts": dict(strategy_exclusion_counts.most_common(10)),
        "accepted_table": df_to_html(accepted_df) if not accepted_df.empty else None,
        "contradiction_table": df_to_html(contradiction_df) if not contradiction_df.empty else None,
        "mutex_category_table": df_to_html(mutex_category_df) if not mutex_category_df.empty else None,
        "temporal_deps_table": df_to_html(temporal_deps_df) if not temporal_deps_df.empty else None,
        "inverse_temporal_table": df_to_html(inverse_temporal_df) if not inverse_temporal_df.empty else None,
        "rejected_table": df_to_html(rejected_df) if not rejected_df.empty else None,
        "manual_table": df_to_html(manual_df) if not manual_df.empty else None,
        "ineligible_but_valid_table": df_to_html(ineligible_df) if not ineligible_df.empty else None,
        "no_thinking_check": "PASS",
    }

    html_path = render_report("relationship_candidates.html", context, base_dir / "index.html")

    # Create/update latest/ symlink (copy fallback)
    latest_dir = (data_root.parent / "reports" / "relationship_candidates" / "latest")
    _update_latest(base_dir, latest_dir)

    return html_path


def _update_latest(run_dir: Path, latest_dir: Path) -> None:
    """Update the latest/ directory (symlink or copy)."""
    try:
        if latest_dir.is_symlink():
            latest_dir.unlink()
        elif latest_dir.exists():
            shutil.rmtree(latest_dir)
        latest_dir.parent.mkdir(parents=True, exist_ok=True)
        latest_dir.symlink_to(run_dir.resolve())
    except (OSError, NotImplementedError):
        if latest_dir.exists():
            shutil.rmtree(latest_dir)
        shutil.copytree(run_dir, latest_dir)
