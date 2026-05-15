"""Context-aware relationship classification audit report."""

from __future__ import annotations

import shutil
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..context.audit import context_decision_summary
from ..storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from .html import render_report
from .tables import df_to_html, write_csv


def generate_context_classification_audit_report(
    data_root: Path,
    output_dir: Path | None = None,
) -> Path:
    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    base_dir = output_dir or (
        data_root.parent / "reports" / "context_classification_audit" / run_id
    )
    base_dir.mkdir(parents=True, exist_ok=True)

    rels = {r.relationship_id: r for r in ParquetRelationshipCandidatesRepository(data_root).iter_latest()}
    decisions = list(ParquetContextRelationshipDecisionsRepository(data_root).iter_latest())
    rows = []
    for decision in decisions:
        rel = rels.get(decision.relationship_id)
        row = asdict(decision)
        if rel is not None:
            row.update({
                "market_id_a": rel.market_id_a,
                "market_id_b": rel.market_id_b,
                "question_a": rel.question_a,
                "question_b": rel.question_b,
                "relationship_type": rel.relationship_type,
                "relationship_subtype": rel.relationship_subtype,
                "relationship_family": rel.relationship_family,
            })
        rows.append(row)

    all_df = pd.DataFrame(rows)
    upgraded = all_df[all_df["new_strategy_eligibility"] == "eligible"] if not all_df.empty else all_df
    downgraded = all_df[all_df["strategy_lane"] == "analysis_only"] if not all_df.empty else all_df
    missing = all_df[all_df["decision_reason"].astype(str).str.contains("missing", case=False, na=False)] if not all_df.empty else all_df
    manual = all_df[all_df["strategy_lane"] == "exploratory_context_unreviewed"] if not all_df.empty else all_df

    write_csv(all_df, base_dir / "relationship_context_decisions.csv")
    write_csv(upgraded, base_dir / "upgraded_relationships.csv")
    write_csv(downgraded, base_dir / "downgraded_relationships.csv")
    write_csv(missing, base_dir / "context_missing.csv")
    write_csv(manual, base_dir / "manual_review_queue.csv")
    write_csv(downgraded, base_dir / "false_positive_prevention.csv")

    lane_counts = Counter(d.strategy_lane for d in decisions)
    context = {
        "generated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "summary": context_decision_summary(data_root),
        "lane_counts": dict(lane_counts),
        "all_table": _table(all_df),
        "upgraded_table": _table(upgraded),
        "downgraded_table": _table(downgraded),
        "missing_table": _table(missing),
        "manual_table": _table(manual),
    }
    html_path = render_report(
        "context_classification_audit.html",
        context,
        base_dir / "index.html",
    )
    _update_latest(
        base_dir,
        data_root.parent / "reports" / "context_classification_audit" / "latest",
    )
    return html_path


def _table(df: pd.DataFrame) -> str | None:
    if df.empty:
        return None
    return df_to_html(
        df.head(50),
        truncate_columns={"question_a": 80, "question_b": 80, "decision_reason": 80},
    )


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
