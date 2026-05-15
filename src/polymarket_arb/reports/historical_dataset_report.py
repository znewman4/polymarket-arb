"""Generate the Historical Dataset HTML report."""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..backfill.coverage import verify_dataset
from ..backfill.models import BackfillConfig
from ..storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from ..storage.parquet.markets_repo import ParquetMarketsRepository
from .charts import histogram
from .html import render_report
from .tables import df_to_html, format_ts_ms, write_csv


def _generated_at() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_historical_dataset_report(
    data_root: Path,
    output_dir: Path | None = None,
) -> Path:
    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    base_dir = output_dir or (data_root.parent / "reports" / "historical_dataset" / run_id)
    base_dir.mkdir(parents=True, exist_ok=True)

    cov_repo = ParquetBackfillCoverageRepository(data_root)
    mkt_repo = ParquetMarketsRepository(data_root)

    cov_rows = list(cov_repo.iter_latest())
    markets = list(mkt_repo.iter_active_markets())

    cfg = BackfillConfig()
    validation_results = verify_dataset(data_root, cfg)

    # Coverage DataFrame
    if cov_rows:
        cov_df = pd.DataFrame([
            {
                "market_id": r.market_id,
                "token_id": r.condition_id,
                "question": r.question,
                "coverage_score": float(r.coverage_score),
                "price_points": r.price_points_count,
                "trade_points": r.trade_points_count,
                "recommended": r.recommended_for_backtest,
                "largest_gap_ms": r.largest_price_gap_ms,
                "first_price_ts": format_ts_ms(r.first_price_ts_ms),
                "last_price_ts": format_ts_ms(r.last_price_ts_ms),
                "has_semantics": r.has_semantics,
                "has_score": r.has_rulebook_score,
                "has_implications": r.has_implications,
            }
            for r in cov_rows
        ])
    else:
        cov_df = pd.DataFrame()

    # Markets DataFrame
    if markets:
        mkt_df = pd.DataFrame([
            {
                "market_id": m.id,
                "token_id": m.clob_token_ids[0] if m.clob_token_ids else "",
                "question": m.question,
                "active": m.active,
                "closed": m.closed,
                "end_date_ms": format_ts_ms(m.end_date_ms),
            }
            for m in markets
        ])
    else:
        mkt_df = pd.DataFrame()

    # Stats
    total_markets = len(cov_rows) if cov_rows else len(markets)
    markets_with_ph = sum(1 for r in cov_rows if r.has_price_history) if cov_rows else 0
    recommended = sum(1 for r in cov_rows if r.recommended_for_backtest) if cov_rows else 0
    total_pts = sum(r.price_points_count for r in cov_rows) if cov_rows else 0
    avg_cov = (sum(float(r.coverage_score) for r in cov_rows) / len(cov_rows)) if cov_rows else 0.0

    stats = {
        "total_markets": total_markets,
        "markets_with_price_history": markets_with_ph,
        "recommended_for_backtest": recommended,
        "total_price_points": total_pts,
        "avg_coverage_score": avg_cov,
    }

    # Charts
    chart_coverage_hist = None
    chart_gap_dist = None
    if cov_rows:
        scores = [float(r.coverage_score) for r in cov_rows]
        p = histogram(
            scores,
            title="Coverage Score Distribution",
            xlabel="Coverage Score",
            ylabel="# Markets",
            output_path=base_dir / "chart_coverage_hist.png",
        )
        chart_coverage_hist = p.name

        gaps_hours = [r.largest_price_gap_ms / 3_600_000 for r in cov_rows if r.largest_price_gap_ms > 0]
        if gaps_hours:
            p2 = histogram(
                gaps_hours,
                title="Largest Price Gap per Market (hours)",
                xlabel="Gap (hours)",
                ylabel="# Markets",
                output_path=base_dir / "chart_gap_dist.png",
            )
            chart_gap_dist = p2.name

    # Tables
    top_markets_html = None
    worst_markets_html = None
    all_markets_html = None

    if not cov_df.empty:
        top = cov_df.nlargest(20, "coverage_score")
        worst = cov_df.nsmallest(20, "coverage_score")
        top_markets_html = df_to_html(top, truncate_columns={"question": 80})
        worst_markets_html = df_to_html(worst, truncate_columns={"question": 80})
        write_csv(cov_df, base_dir / "coverage.csv")

    if not mkt_df.empty:
        all_markets_html = df_to_html(mkt_df, truncate_columns={"question": 80})
        write_csv(mkt_df, base_dir / "market_coverage.csv")

    # Validation results as list of dicts
    val_display = [
        {"name": r.name, "status": r.status, "details": str(r.details)}
        for r in validation_results
    ]

    output_path = base_dir / "index.html"
    render_report(
        "historical_dataset.html",
        {
            "generated_at": _generated_at(),
            "stats": stats,
            "validation_results": val_display,
            "chart_coverage_hist": chart_coverage_hist,
            "chart_gap_dist": chart_gap_dist,
            "top_markets_html": top_markets_html,
            "worst_markets_html": worst_markets_html,
            "all_markets_html": all_markets_html,
        },
        output_path,
    )

    # Write latest/ symlink or copy
    latest_dir = output_dir or (data_root.parent / "reports" / "historical_dataset" / "latest")
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
