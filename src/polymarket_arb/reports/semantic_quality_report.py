"""Generate the Semantic Quality HTML report."""

from __future__ import annotations

import shutil
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..backfill.validators import validate_no_thinking
from ..storage.parquet.market_implications_repo import ParquetMarketImplicationsRepository
from ..storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from ..storage.parquet.rulebook_evaluations_repo import ParquetRulebookEvaluationsRepository
from .charts import bar, histogram
from .html import render_report
from .tables import df_to_html, format_number, write_csv


def _generated_at() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_THINKING_FIELDS = [
    "explanation_summary",
    "flag_rationales_json",
    "uncertainty_notes_json",
    "rule_curation_notes_json",
    "canonical_question",
]


def generate_semantic_quality_report(
    data_root: Path,
    output_dir: Path | None = None,
) -> Path:
    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    base_dir = output_dir or (data_root.parent / "reports" / "semantic_quality" / run_id)
    base_dir.mkdir(parents=True, exist_ok=True)

    sem_repo = ParquetMarketSemanticsRepository(data_root)
    eval_repo = ParquetRulebookEvaluationsRepository(data_root)
    impl_repo = ParquetMarketImplicationsRepository(data_root)

    sem_rows = sem_repo.iter_latest(limit=2000, unscored_only=False)
    eval_rows = list(eval_repo.recent(limit=500))
    impl_rows = list(impl_repo.needs_review(limit=500))

    # Validate no thinking content
    no_thinking_result = validate_no_thinking(list(sem_rows), _THINKING_FIELDS)
    # Reload since iter exhausted
    sem_rows = sem_repo.iter_latest(limit=2000, unscored_only=False)
    sem_list = list(sem_rows)

    scored = [r for r in sem_list if r.ambiguity_score is not None]
    review_rows = [r for r in sem_list if r.needs_manual_review]

    stats = {
        "total_semantics": len(sem_list),
        "scored_rows": len(scored),
        "total_implications": len(impl_rows),
        "needs_review": len(review_rows),
        "avg_semantic_confidence": (
            sum(r.semantic_confidence for r in sem_list) / len(sem_list)
            if sem_list else 0.0
        ),
        "avg_ambiguity_score": (
            sum(r.ambiguity_score or 0.0 for r in scored) / len(scored)
            if scored else 0.0
        ),
    }

    # Charts
    chart_confidence_hist = chart_ambiguity_hist = chart_flag_bar = None

    if sem_list:
        histogram(
            [r.semantic_confidence for r in sem_list],
            title="Semantic Confidence Distribution",
            xlabel="Confidence",
            ylabel="# Markets",
            output_path=base_dir / "chart_confidence_hist.png",
        )
        chart_confidence_hist = "chart_confidence_hist.png"

    if scored:
        histogram(
            [r.ambiguity_score for r in scored],  # type: ignore[misc]
            title="Ambiguity Score Distribution",
            xlabel="Ambiguity Score",
            ylabel="# Markets",
            output_path=base_dir / "chart_ambiguity_hist.png",
        )
        chart_ambiguity_hist = "chart_ambiguity_hist.png"

    flag_counter: Counter = Counter()
    for r in sem_list:
        flag_counter.update(r.ambiguity_flags)
    if flag_counter:
        top_flags = flag_counter.most_common(15)
        bar(
            [f[0] for f in top_flags],
            [f[1] for f in top_flags],
            title="Ambiguity Flag Frequency",
            xlabel="Flag",
            ylabel="Count",
            output_path=base_dir / "chart_flag_bar.png",
        )
        chart_flag_bar = "chart_flag_bar.png"

    # Tables
    def _sem_df(rows: list) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "market_id": r.source_market_id,
                "question": r.question,
                "confidence": format_number(r.semantic_confidence),
                "ambiguity": format_number(r.ambiguity_score),
                "market_type": r.market_type,
                "flags": str(r.ambiguity_flags),
                "needs_review": r.needs_manual_review,
                "model": r.model_name,
                "prompt_version": r.prompt_version,
            }
            for r in rows
        ])

    semantics_html = review_html = None
    if sem_list:
        df = _sem_df(sem_list)
        semantics_html = df_to_html(df, truncate_columns={"question": 80, "flags": 60})
        write_csv(df, base_dir / "semantic_review.csv")
    if review_rows:
        df_r = _sem_df(review_rows)
        review_html = df_to_html(df_r, truncate_columns={"question": 80, "flags": 60})

    evaluations_html = None
    if eval_rows:
        eval_df = pd.DataFrame([
            {
                "evaluation_id": r.evaluation_id[:12],
                "market_id": r.market_id,
                "rulebook_id": r.rulebook_id,
                "rulebook_version": r.rulebook_version,
                "score": format_number(r.score),
                "flags": str(r.flags),
            }
            for r in eval_rows
        ])
        evaluations_html = df_to_html(eval_df)

    implications_html = None
    if impl_rows:
        impl_df = pd.DataFrame([
            {
                "market_id": r.market_id,
                "implication_type": r.implication_type,
                "statement": r.statement,
                "confidence": format_number(r.final_confidence),
                "needs_review": r.needs_manual_review,
            }
            for r in impl_rows
        ])
        implications_html = df_to_html(impl_df, truncate_columns={"statement": 80})

    val_result_str = f"{no_thinking_result.status}: {no_thinking_result.details}"

    output_path = base_dir / "index.html"
    render_report(
        "semantic_quality.html",
        {
            "generated_at": _generated_at(),
            "stats": stats,
            "chart_confidence_hist": chart_confidence_hist,
            "chart_ambiguity_hist": chart_ambiguity_hist,
            "chart_flag_bar": chart_flag_bar,
            "review_html": review_html,
            "semantics_html": semantics_html,
            "evaluations_html": evaluations_html,
            "implications_html": implications_html,
            "no_thinking_check": val_result_str,
        },
        output_path,
    )

    latest_dir = output_dir or (data_root.parent / "reports" / "semantic_quality" / "latest")
    if output_dir is None:
        _write_latest(base_dir, latest_dir)

    return output_path


def _write_latest(src: Path, latest: Path) -> None:
    try:
        if latest.is_symlink():
            latest.unlink()
        elif latest.exists():
            shutil.rmtree(latest)
        latest.symlink_to(src)
    except (OSError, NotImplementedError):
        if latest.exists():
            shutil.rmtree(latest)
        shutil.copytree(src, latest)
