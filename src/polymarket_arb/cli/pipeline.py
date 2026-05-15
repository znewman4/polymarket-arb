"""``polymarket-arb pipeline run-all`` — end-to-end pipeline convenience command.

Runs the full sequence: ingest → backfill → semantics → relationships →
backtest → null baseline → sensitivity grid → reports.

Each underlying step remains independently invocable. This command
orchestrates them in the correct order with a final summary.

Safety: read-only research + simulated backtest only. No live orders.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

import click

from ..settings import Settings


@click.group(name="pipeline")
def pipeline_cmd() -> None:
    """End-to-end pipeline orchestration (research-only, no live trading)."""


@pipeline_cmd.command(name="run-all")
@click.option("--markets", type=int, default=500, show_default=True,
              help="Target number of markets to ingest.")
@click.option("--days", type=int, default=180, show_default=True,
              help="Days of price history to backfill.")
@click.option("--starting-cash", type=float, default=10_000.0, show_default=True)
@click.option("--fee-bps", type=int, default=0, show_default=True)
@click.option("--slippage-bps", type=int, default=50, show_default=True)
@click.option("--min-net-edge", type=float, default=0.01, show_default=True)
@click.option("--sensitivity-grid", default="full",
              type=click.Choice(["full", "slim"]), show_default=True)
@click.option("--rulebook", default="relationship_v2", show_default=True,
              help="Relationship rulebook filename (without .yaml).")
@click.option("--skip-ingest/--no-skip-ingest", default=False,
              help="Skip Gamma ingest step (use existing lake).")
@click.option("--skip-backfill/--no-skip-backfill", default=False,
              help="Skip price history backfill (use existing prices).")
@click.option("--skip-semantics/--no-skip-semantics", default=False,
              help="Skip semantic extraction (use existing semantics).")
@click.pass_context
def run_all(
    ctx: click.Context,
    markets: int,
    days: int,
    starting_cash: float,
    fee_bps: int,
    slippage_bps: int,
    min_net_edge: float,
    sensitivity_grid: str,
    rulebook: str,
    skip_ingest: bool,
    skip_backfill: bool,
    skip_semantics: bool,
) -> None:
    """Run the full research pipeline end-to-end.

    Steps executed (unless skipped):
      1. Gamma market ingest (--markets)
      2. CLOB price history backfill (--days)
      3. Backfill coverage computation
      4. Semantic extraction (Ollama)
      5. Relationship mining
      6. Strategy backtest
      7. Null baseline
      8. Sensitivity grid
      9. HTML reports

    Prints final summary to stdout.
    """
    from ..backfill.coverage import compute_all_coverage
    from ..backfill.models import BackfillConfig
    from ..backfill.price_history import run_price_history_backfill
    from ..backfill.semantic_pipeline import run_semantic_pipeline
    from ..relationships import generate_relationships
    from ..storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
    from ..storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
    from ..storage.parquet.markets_repo import ParquetMarketsRepository

    settings: Settings = ctx.obj["settings"]
    data_root = settings.data_root
    run_id = uuid.uuid4().hex

    click.echo(f"\n[pipeline] Starting run_id={run_id}")

    # ── Step 1: Ingest ───────────────────────────────────────────────────────
    if not skip_ingest:
        click.echo(f"[1/9] Gamma ingest (limit={markets}) ...")
        try:
            from ..http.client import AsyncHttpClient
            from ..ingest.gamma.client import GammaClient
            from ..storage.parquet.raw_writer import RawWriter

            async def _ingest():
                raw_writer = RawWriter(data_root)
                m_repo = ParquetMarketsRepository(
                    data_root,
                    compression=settings.storage.parquet.compression,
                    row_group_size=settings.storage.parquet.row_group_size,
                )
                async with AsyncHttpClient(settings.http) as http:
                    client = GammaClient(
                        gamma_host=settings.gamma_host, http=http,
                        raw_writer=raw_writer, page_size=min(markets, 500),
                    )
                    market_rows = []
                    async for row in client.iter_markets(max_pages=None):
                        market_rows.append(row)
                        if len(market_rows) >= markets:
                            break
                return m_repo.upsert_markets(market_rows)

            n_markets = asyncio.run(_ingest())
            click.echo(f"    ✓ ingested {n_markets} markets")
        except Exception as exc:
            click.echo(f"    ✗ ingest failed: {exc} (use --skip-ingest to skip)")
            n_markets = sum(1 for _ in ParquetMarketsRepository(data_root).iter_active_markets())
    else:
        click.echo("[1/9] Skipping ingest (--skip-ingest)")
        n_markets = sum(1 for _ in ParquetMarketsRepository(data_root).iter_active_markets())

    # ── Step 2: Price history backfill ──────────────────────────────────────
    if not skip_backfill:
        click.echo(f"[2/9] Price history backfill ({days}d hourly) ...")
        try:
            cfg = BackfillConfig(requested_days=days, market_limit=markets)
            result = asyncio.run(run_price_history_backfill(settings, cfg))
            click.echo(f"    ✓ backfilled {result.markets_succeeded} markets, "
                       f"{result.total_rows_written} rows")
            n_with_prices = result.markets_succeeded
        except Exception as exc:
            click.echo(f"    ✗ backfill failed: {exc}")
            n_with_prices = 0
    else:
        click.echo("[2/9] Skipping backfill (--skip-backfill)")
        n_with_prices = 0

    # ── Step 3: Coverage computation ─────────────────────────────────────────
    click.echo("[3/9] Computing backfill coverage ...")
    try:
        cfg = BackfillConfig(requested_days=days, market_limit=markets)
        cov_repo = ParquetBackfillCoverageRepository(
            data_root,
            compression=settings.storage.parquet.compression,
            row_group_size=settings.storage.parquet.row_group_size,
        )
        n_with_coverage = 0
        n_with_prices_cov = 0
        for cov_row in compute_all_coverage(data_root, cfg):
            cov_repo.append(cov_row)
            n_with_coverage += 1
            if cov_row.has_price_history:
                n_with_prices_cov += 1
        n_with_prices = max(n_with_prices, n_with_prices_cov)
        click.echo(f"    ✓ coverage computed for {n_with_coverage} markets")
    except Exception as exc:
        click.echo(f"    ✗ coverage computation failed: {exc}")
        n_with_coverage = 0

    # ── Step 4: Semantic extraction ──────────────────────────────────────────
    if not skip_semantics:
        click.echo("[4/9] Semantic extraction (Ollama) ...")
        try:
            cfg = BackfillConfig(requested_days=days, market_limit=markets)
            sem_result = asyncio.run(
                run_semantic_pipeline(settings, cfg, only_missing=True, allow_rerun_stale=False)
            )
            n_with_semantics = sem_result.semantics_extracted
            click.echo(f"    ✓ extracted semantics for {n_with_semantics} markets")
        except Exception as exc:
            click.echo(f"    ✗ semantic extraction failed: {exc}")
            n_with_semantics = ParquetMarketSemanticsRepository(data_root).latest_count()
    else:
        click.echo("[4/9] Skipping semantics (--skip-semantics)")
        n_with_semantics = ParquetMarketSemanticsRepository(data_root).latest_count()

    # ── Step 5: Relationship mining ──────────────────────────────────────────
    click.echo("[5/9] Relationship mining ...")
    n_candidates = n_accepted = n_eligible = 0
    try:
        config_dir = Path(__file__).resolve().parents[3] / "configs"
        rb_filename = f"{rulebook}.yaml"
        rb_path = config_dir / "semantic_rules" / rb_filename
        if not rb_path.exists():
            rb_path = config_dir / "semantic_rules" / "relationship_v2.yaml"

        rel_result = generate_relationships(
            settings,
            rulebook_path_override=rb_path,
            use_embeddings=False,
            market_limit=markets,
        )
        n_candidates = rel_result.total_candidates_considered
        n_accepted = rel_result.accepted

        # Count strategy-eligible from the latest lake
        from ..storage.parquet.relationship_candidates_repo import (
            ParquetRelationshipCandidatesRepository,
        )
        rel_repo = ParquetRelationshipCandidatesRepository(data_root)
        n_eligible = sum(
            1 for r in rel_repo.iter_accepted()
            if getattr(r, "strategy_eligibility_status", "unknown") == "eligible"
        )
        click.echo(f"    ✓ {n_candidates} candidates, {n_accepted} accepted, {n_eligible} strategy-eligible")
        if rel_result.top_rejection_reasons:
            click.echo("    top rejections: " + ", ".join(
                f"{k}={v}" for k, v in list(rel_result.top_rejection_reasons.items())[:5]
            ))
    except Exception as exc:
        click.echo(f"    ✗ relationship mining failed: {exc}")

    # ── Step 6a: Relationship report ─────────────────────────────────────────
    click.echo("[6a/9] Relationship candidates report ...")
    try:
        from ..reports.relationship_candidates_report import generate_relationship_candidates_report
        rel_report = generate_relationship_candidates_report(data_root)
        click.echo(f"    ✓ {rel_report}")
    except Exception as exc:
        click.echo(f"    ✗ relationship report failed: {exc}")

    # ── Step 6b: Strategy backtest ────────────────────────────────────────────
    click.echo("[6b/9] Strategy backtest ...")
    n_trades = 0
    net_pnl = 0.0
    total_return = 0.0
    max_dd = 0.0
    credibility = "data_insufficient"
    try:
        from ..backtest.relationship_replay import run_relationship_backtest
        from ..strategies.models import RelationshipBacktestConfig

        bt_cfg = RelationshipBacktestConfig(
            run_id=run_id,
            starting_cash_usdc=Decimal(str(starting_cash)),
            fee_bps=Decimal(str(fee_bps)),
            slippage_bps=Decimal(str(slippage_bps)),
            min_net_edge=min_net_edge,
        )
        bt_result = run_relationship_backtest(data_root, bt_cfg)
        m = bt_result["metrics"]
        n_trades = m.trades_executed
        net_pnl = float(m.net_pnl_usdc)
        total_return = m.total_return_pct
        max_dd = m.max_drawdown_pct
        credibility = m.credibility_label
        click.echo(f"    ✓ trades={n_trades} net_pnl={net_pnl:.2f} USDC credibility={credibility}")
    except Exception as exc:
        click.echo(f"    ✗ backtest failed: {exc}")

    # ── Step 7: Null baseline ─────────────────────────────────────────────────
    click.echo("[7/9] Null baseline ...")
    try:
        from ..backtest.null_baseline import run_null_baseline
        from ..strategies.models import RelationshipBacktestConfig

        null_cfg = RelationshipBacktestConfig(run_id=run_id)
        null_result = run_null_baseline(data_root, null_cfg)
        click.echo(f"    ✓ null baseline: {null_result.get('message', 'done')}")
    except Exception as exc:
        click.echo(f"    ✗ null baseline failed: {exc}")

    # ── Step 8: Sensitivity grid ──────────────────────────────────────────────
    click.echo(f"[8/9] Sensitivity grid ({sensitivity_grid}) ...")
    try:
        from ..backtest.sensitivity import run_sensitivity_grid
        from ..strategies.models import RelationshipBacktestConfig

        sens_cfg = RelationshipBacktestConfig(run_id=run_id)
        sens_results = run_sensitivity_grid(data_root, sens_cfg, slim=(sensitivity_grid == "slim"))
        n_cells = len(sens_results)
        n_pos = sum(1 for r in sens_results if r.get("net_pnl_usdc", -1) > 0)
        click.echo(f"    ✓ {n_cells} cells, {n_pos} with positive PnL")
    except Exception as exc:
        click.echo(f"    ✗ sensitivity grid failed: {exc}")

    # ── Step 9: Reports ──────────────────────────────────────────────────────
    click.echo("[9/9] Generating final reports ...")
    reports_root = data_root.parent / "reports"

    try:
        from ..reports.historical_dataset_report import generate_historical_dataset_report
        hd_report = generate_historical_dataset_report(data_root)
        click.echo(f"    ✓ historical dataset: {hd_report}")
    except Exception as exc:
        click.echo(f"    ✗ historical dataset report failed: {exc}")

    try:
        from ..reports.semantic_quality_report import generate_semantic_quality_report
        sq_report = generate_semantic_quality_report(data_root)
        click.echo(f"    ✓ semantic quality: {sq_report}")
    except Exception as exc:
        click.echo(f"    ✗ semantic quality report failed: {exc}")

    try:
        from ..reports.strategy_backtest_report import generate_strategy_backtest_report
        st_report = generate_strategy_backtest_report(data_root, run_id=run_id)
        click.echo(f"    ✓ strategy backtest: {st_report}")
    except Exception as exc:
        click.echo(f"    ✗ strategy backtest report failed: {exc}")

    # ── Final summary ─────────────────────────────────────────────────────────
    metrics_path = (
        data_root / "backtests" / run_id / "relationship_strategy" / "metrics.json"
    )
    sensitivity_path = (
        data_root / "backtests" / run_id / "sensitivity" / "grid_summary.parquet"
    )

    click.echo("\n" + "=" * 60)
    click.echo("PIPELINE COMPLETE")
    click.echo("=" * 60)
    click.echo(f"markets_loaded:                  {n_markets}")
    click.echo(f"markets_with_price_history:      {n_with_prices}")
    click.echo(f"markets_with_semantics:          {n_with_semantics}")
    click.echo(f"markets_with_coverage:           {n_with_coverage}")
    click.echo(f"relationship_candidates:         {n_candidates}")
    click.echo(f"relationships_accepted:          {n_accepted}")
    click.echo(f"relationships_strategy_eligible: {n_eligible}")
    click.echo(f"simulated_trades:                {n_trades}")
    click.echo(f"net_pnl_usdc:                    ${net_pnl:.2f}")
    click.echo(f"total_return_pct:                {total_return:.2f}%")
    click.echo(f"max_drawdown_pct:                {max_dd:.1f}%")
    click.echo(f"credibility_label:               {credibility}")
    click.echo("")
    click.echo(f"{reports_root}/historical_dataset/latest/index.html")
    click.echo(f"{reports_root}/semantic_quality/latest/index.html")
    click.echo(f"{reports_root}/relationship_candidates/latest/index.html")
    click.echo(f"{reports_root}/strategy_backtests/latest/index.html")
    click.echo(f"{metrics_path}")
    click.echo(f"{sensitivity_path}")


@pipeline_cmd.command(name="context-run-all")
@click.option("--markets", type=int, default=500, show_default=True)
@click.option("--days", type=int, default=180, show_default=True)
@click.option("--starting-cash", type=float, default=10_000.0, show_default=True)
@click.option("--fee-bps", type=int, default=0, show_default=True)
@click.option("--slippage-bps", type=int, default=50, show_default=True)
@click.option("--min-net-edge", type=float, default=0.01, show_default=True)
@click.option("--sensitivity-grid", default="full", type=click.Choice(["full", "slim"]), show_default=True)
@click.option("--run-id", default="context_phase56", show_default=True)
@click.option("--registry", default="configs/context_spaces/context_spaces_v1.yaml", show_default=True)
@click.option("--manual-rules", default="configs/context_spaces/manual_rules_v1.yaml", show_default=True)
@click.pass_context
def context_run_all(
    ctx: click.Context,
    markets: int,
    days: int,
    starting_cash: float,
    fee_bps: int,
    slippage_bps: int,
    min_net_edge: float,
    sensitivity_grid: str,
    run_id: str,
    registry: str,
    manual_rules: str,
) -> None:
    """Run the evidence-backed context pipeline on the local lake."""
    from ..backtest.context_aware_replay import (
        run_context_aware_backtest,
        run_context_null_baseline,
        run_context_sensitivity_grid,
    )
    from ..context.decision_engine import apply_context_decisions
    from ..context.manual_rules import export_review_queue, import_manual_rules
    from ..context.source_registry import registry_audit
    from ..relationships import generate_relationships
    from ..reports.context_classification_audit_report import (
        generate_context_classification_audit_report,
    )
    from ..reports.context_rules_report import generate_context_rules_report
    from ..reports.context_strategy_backtest_report import generate_context_strategy_backtest_report
    from ..reports.relationship_candidates_report import generate_relationship_candidates_report
    from ..strategies.models import ContextAwareBacktestConfig

    settings: Settings = ctx.obj["settings"]
    data_root = settings.data_root

    click.echo(f"\n[context-pipeline] Starting run_id={run_id} days={days} markets={markets}")
    click.echo("[1/9] Context registry audit ...")
    audit = registry_audit(Path(registry))
    click.echo(f"    ✓ spaces={audit['spaces']} completeness={audit['completeness_class_counts']}")

    click.echo("[2/9] Importing curated manual context rules ...")
    imported = import_manual_rules(data_root, Path(manual_rules))
    click.echo(
        f"    ✓ sources={imported['sources']} documents={imported['documents']} rules={imported['rules']}"
    )

    click.echo("[3/9] Exporting manual review queue ...")
    review_path = data_root / "context" / "manual_review_queue.csv"
    export_count = export_review_queue(data_root, review_path)
    click.echo(f"    ✓ pending_review={export_count} output={review_path}")

    click.echo("[4/9] Context rules report ...")
    rules_report = generate_context_rules_report(data_root)
    click.echo(f"    ✓ {rules_report}")

    click.echo("[5/9] Regenerating relationships ...")
    rel_result = generate_relationships(settings, use_embeddings=False, market_limit=markets)
    click.echo(
        f"    ✓ candidates={rel_result.total_candidates_considered} accepted={rel_result.accepted}"
    )

    click.echo("[6/9] Applying context decisions ...")
    decision_result = apply_context_decisions(data_root, registry_path=Path(registry))
    click.echo(
        f"    ✓ decisions={decision_result['decisions_written']} "
        f"strict={decision_result['lane_counts'].get('strict_context_valid', 0)} "
        f"reviewed={decision_result['lane_counts'].get('reviewed_context_valid', 0)} "
        f"exploratory={decision_result['lane_counts'].get('exploratory_context_unreviewed', 0)}"
    )

    click.echo("[7/9] Relationship and context audit reports ...")
    rel_report = generate_relationship_candidates_report(data_root)
    audit_report = generate_context_classification_audit_report(data_root)
    click.echo(f"    ✓ relationships={rel_report}")
    click.echo(f"    ✓ context_audit={audit_report}")

    click.echo("[8/9] Context-aware lane backtests ...")
    lane_results = {}
    for lane in ("strict_context_valid", "reviewed_context_valid", "exploratory_context_unreviewed"):
        cfg = ContextAwareBacktestConfig(
            run_id=run_id,
            lane=cast(
                Literal["strict_context_valid", "reviewed_context_valid", "exploratory_context_unreviewed"],
                lane,
            ),
            starting_cash_usdc=Decimal(str(starting_cash)),
            fee_bps=Decimal(str(fee_bps)),
            slippage_bps=Decimal(str(slippage_bps)),
            min_net_edge=min_net_edge,
        )
        lane_result = run_context_aware_backtest(data_root, cfg)
        lane_results[lane] = lane_result["metrics"]
        click.echo(
            f"    ✓ {lane}: trades={lane_result['metrics']['trades_executed']} "
            f"pnl={float(lane_result['metrics']['net_pnl_usdc']):.2f} "
            f"credibility={lane_result['metrics']['credibility_label']}"
        )

    click.echo("[9/9] Null baseline, sensitivity, final report ...")
    null_result = run_context_null_baseline(data_root, run_id=run_id)
    sens_cfg = ContextAwareBacktestConfig(run_id=run_id, lane="strict_context_valid")
    sensitivity = run_context_sensitivity_grid(data_root, sens_cfg, grid=sensitivity_grid)
    final_report = generate_context_strategy_backtest_report(data_root, run_id=run_id)
    positive_cells = sum(1 for row in sensitivity if float(row.get("net_pnl_usdc", -1) or -1) > 0)
    click.echo(f"    ✓ null={null_result['output_dir']}")
    click.echo(f"    ✓ sensitivity_cells={len(sensitivity)} positive={positive_cells}")
    click.echo(f"    ✓ final_report={final_report}")

    strict_metrics = lane_results.get("strict_context_valid", {})
    reviewed_metrics = lane_results.get("reviewed_context_valid", {})
    click.echo("\n" + "=" * 60)
    click.echo("CONTEXT PIPELINE COMPLETE")
    click.echo("=" * 60)
    click.echo(f"run_id:                  {run_id}")
    click.echo(f"strict_trades:           {strict_metrics.get('trades_executed', 0)}")
    click.echo(f"reviewed_trades:         {reviewed_metrics.get('trades_executed', 0)}")
    click.echo(f"null_net_pnl_usdc:       {float(null_result['metrics']['net_pnl_usdc']):.2f}")
    click.echo(f"final_report:            {final_report}")
