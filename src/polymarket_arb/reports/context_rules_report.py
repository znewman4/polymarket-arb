"""Context rules HTML report."""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..context.audit import context_rule_summary, rows_as_dicts
from ..storage.parquet.context_documents_repo import ParquetContextDocumentsRepository
from ..storage.parquet.context_rules_repo import ParquetContextRulesRepository
from ..storage.parquet.context_sources_repo import ParquetContextSourcesRepository
from .html import render_report
from .tables import df_to_html, write_csv


def generate_context_rules_report(data_root: Path, output_dir: Path | None = None) -> Path:
    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    base_dir = output_dir or (data_root.parent / "reports" / "context_rules" / run_id)
    base_dir.mkdir(parents=True, exist_ok=True)

    sources = list(ParquetContextSourcesRepository(data_root).iter_latest())
    documents = list(ParquetContextDocumentsRepository(data_root).iter_latest())
    rules = list(ParquetContextRulesRepository(data_root).iter_latest())
    needs_review = [r for r in rules if r.needs_manual_review and r.human_review_status == "pending"]

    sources_df = pd.DataFrame(rows_as_dicts(sources))
    docs_df = pd.DataFrame(rows_as_dicts(documents))
    rules_df = pd.DataFrame(rows_as_dicts(rules))
    review_df = pd.DataFrame(rows_as_dicts(needs_review))

    write_csv(sources_df, base_dir / "context_sources.csv")
    write_csv(docs_df, base_dir / "context_documents.csv")
    write_csv(rules_df, base_dir / "context_rules.csv")
    write_csv(review_df, base_dir / "rules_needing_review.csv")
    write_csv(pd.DataFrame(), base_dir / "failed_extractions.csv")

    context = {
        "generated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "summary": context_rule_summary(data_root),
        "sources_table": _table(sources_df),
        "documents_table": _table(docs_df),
        "rules_table": _table(rules_df),
        "needs_review_table": _table(review_df),
    }
    html_path = render_report("context_rules.html", context, base_dir / "index.html")
    _update_latest(base_dir, data_root.parent / "reports" / "context_rules" / "latest")
    return html_path


def _table(df: pd.DataFrame) -> str | None:
    if df.empty:
        return None
    return df_to_html(df.head(50), truncate_columns={"rule_json": 100, "quoted_evidence_json": 100})


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
