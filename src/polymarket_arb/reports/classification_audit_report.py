"""Strict relationship classification audit report."""

from __future__ import annotations

import json
import shutil
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from .charts import bar, histogram
from .html import render_report
from .tables import df_to_html, truncate, write_csv


def generate_classification_audit_report(
    data_root: Path,
    output_dir: Path | None = None,
) -> Path:
    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    base_dir = output_dir or (data_root.parent / "reports" / "classification_audit" / run_id)
    base_dir.mkdir(parents=True, exist_ok=True)

    rows = list(ParquetRelationshipCandidatesRepository(data_root).iter_latest())
    for row in rows:
        assert "<think>" not in (row.rationale_summary or "").lower()

    accepted = [r for r in rows if r.validation_status == "accepted"]
    manual = [r for r in rows if r.validation_status == "needs_manual_review"]
    rejected = [r for r in rows if r.validation_status == "rejected"]
    eligible = [r for r in rows if r.validation_status == "accepted" and r.strategy_eligibility_status == "eligible"]
    ineligible_valid = [r for r in accepted if r.strategy_eligibility_status != "eligible"]
    mixed = [r for r in rows if _taxonomy_family(r) == "mixed_subtype" or r.mixed_subtype_reason]
    same_ref = [r for r in rows if r.relationship_subtype == "same_reference_clock_only"]
    same_topic = [r for r in rows if r.relationship_type == "same_topic_no_trade"]

    family_counts = Counter(_taxonomy_family(r) for r in rows)
    subtype_counts = Counter(r.relationship_subtype or r.relationship_type for r in rows)
    status_counts = Counter(r.validation_status for r in rows)
    strategy_counts = Counter(r.strategy_eligibility_status for r in rows)
    rejection_counts: Counter[str] = Counter()
    for r in rows:
        for reason in _json_list(r.rejection_reasons_json) + _json_list(r.strategy_exclusion_reasons_json):
            rejection_counts[reason.get("code", "unknown")] += 1

    _write_counter_chart(base_dir, "chart_family.png", "Relationship Family Count", family_counts)
    _write_counter_chart(base_dir, "chart_subtype.png", "Relationship Subtype Count", subtype_counts)
    _write_counter_chart(base_dir, "chart_validation.png", "Validation Status Count", status_counts)
    _write_counter_chart(base_dir, "chart_strategy.png", "Strategy Eligibility Count", strategy_counts)
    _write_counter_chart(base_dir, "chart_rejections.png", "Rejection Reason Count", rejection_counts)
    if rows:
        histogram(
            [r.final_confidence for r in rows],
            title="Final Confidence Histogram",
            xlabel="Final confidence",
            ylabel="Count",
            output_path=base_dir / "chart_confidence.png",
        )

    all_df = pd.DataFrame([_row_dict(r, truncate_questions=False) for r in rows])
    write_csv(all_df, base_dir / "classification_audit_all.csv")
    write_csv(pd.DataFrame([_row_dict(r, truncate_questions=False) for r in eligible]), base_dir / "accepted_strategy_eligible.csv")
    write_csv(pd.DataFrame([_row_dict(r, truncate_questions=False) for r in mixed]), base_dir / "mixed_subtype.csv")
    write_csv(pd.DataFrame([_row_dict(r, truncate_questions=False) for r in manual]), base_dir / "manual_review.csv")
    write_csv(pd.DataFrame([_row_dict(r, truncate_questions=False) for r in rejected]), base_dir / "rejected.csv")
    write_csv(pd.DataFrame([_row_dict(r, truncate_questions=False) for r in same_ref]), base_dir / "same_reference_only.csv")
    write_csv(pd.DataFrame(), base_dir / "bundles.csv")

    context = {
        "generated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "summary": {
            "total_candidate_pairs": len(rows),
            "accepted_strategy_eligible": len(eligible),
            "accepted_strategy_ineligible": len(ineligible_valid),
            "manual_review": len(manual),
            "rejected": len(rejected),
            "same_topic_only": len(same_topic),
            "mixed_subtype": len(mixed),
            "same_reference_only": len(same_ref),
        },
        "family_counts": dict(family_counts.most_common()),
        "subtype_counts": dict(subtype_counts.most_common(15)),
        "rejection_counts": dict(rejection_counts.most_common(15)),
        "accepted_table": _table(eligible[:30]),
        "mixed_table": _table(mixed[:30]),
        "candidate_party_table": _table([r for r in rows if r.relationship_subtype == "candidate_to_party_dependency"][:20]),
        "nomination_general_table": _table([r for r in rows if r.relationship_subtype == "nomination_to_general_dependency"][:20]),
        "same_reference_table": _table(same_ref[:20]),
        "same_topic_table": _table(same_topic[:20]),
        "manual_table": _table(manual[:20]),
        "no_thinking_check": "PASS",
        "charts": [
            "chart_family.png",
            "chart_subtype.png",
            "chart_validation.png",
            "chart_strategy.png",
            "chart_rejections.png",
            "chart_confidence.png",
        ],
    }
    html_path = render_report("classification_audit.html", context, base_dir / "index.html")
    _update_latest(base_dir, data_root.parent / "reports" / "classification_audit" / "latest")
    return html_path


def _row_dict(r: Any, *, truncate_questions: bool) -> dict[str, Any]:
    q_a = _strip_thinking(r.question_a or "")
    q_b = _strip_thinking(r.question_b or "")
    return {
        "relationship_id": r.relationship_id,
        "market_id_a": r.market_id_a,
        "market_id_b": r.market_id_b,
        "question_a": truncate(q_a, 90) if truncate_questions else q_a,
        "question_b": truncate(q_b, 90) if truncate_questions else q_b,
        "relationship_type": r.relationship_type,
        "relationship_family": _taxonomy_family(r),
        "relationship_subtype": r.relationship_subtype,
        "outcome_space_id": r.outcome_space_id,
        "outcome_subtype_a": r.outcome_subtype_a,
        "outcome_subtype_b": r.outcome_subtype_b,
        "entity_type_a": r.entity_type_a,
        "entity_type_b": r.entity_type_b,
        "stage_a": r.stage_a,
        "stage_b": r.stage_b,
        "candidate_a": r.candidate_a,
        "candidate_b": r.candidate_b,
        "party_a": r.party_a,
        "party_b": r.party_b,
        "team_a": r.team_a,
        "team_b": r.team_b,
        "validation_status": r.validation_status,
        "strategy_eligibility_status": r.strategy_eligibility_status,
        "strategy_family": r.strategy_family,
        "mixed_subtype_reason": r.mixed_subtype_reason,
        "strategy_eligible_reason": r.strategy_eligible_reason,
        "rejection_reasons_json": r.rejection_reasons_json,
        "strategy_exclusion_reasons_json": r.strategy_exclusion_reasons_json,
        "final_confidence": round(r.final_confidence, 4),
    }


def _taxonomy_family(r: Any) -> str:
    try:
        data = json.loads(r.classification_reason_json or "{}")
        _ = data
    except (TypeError, json.JSONDecodeError):
        pass
    if r.strategy_family == "pairwise_mutual_exclusion":
        return "mutual_exclusion"
    if r.strategy_family == "party_inverse":
        return "inverse"
    if r.relationship_subtype == "same_reference_clock_only":
        return "same_reference_only"
    if r.mixed_subtype_reason:
        return "mixed_subtype"
    return r.relationship_family or "unknown"


def _table(rows: list[Any]) -> str | None:
    if not rows:
        return None
    return df_to_html(pd.DataFrame([_row_dict(r, truncate_questions=True) for r in rows]))


def _json_list(raw: str | None) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _write_counter_chart(base_dir: Path, filename: str, title: str, counts: Counter) -> None:
    if not counts:
        return
    labels = [k for k, _ in counts.most_common(15)]
    bar(labels, [float(counts[k]) for k in labels], title=title, xlabel="Bucket", ylabel="Count", output_path=base_dir / filename)


def _strip_thinking(text: str) -> str:
    import re
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def _update_latest(run_dir: Path, latest_dir: Path) -> None:
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
