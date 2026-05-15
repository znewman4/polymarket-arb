"""``polymarket-arb backfill ...`` — historical price/trade data backfill."""

from __future__ import annotations

import asyncio

import click

from ..backfill.coverage import compute_all_coverage, compute_relationship_coverage, verify_dataset
from ..backfill.models import BackfillConfig
from ..backfill.price_history import run_price_history_backfill, run_relationship_price_backfill
from ..backfill.semantic_pipeline import run_semantic_pipeline
from ..backfill.trade_history import run_trade_history_backfill
from ..settings import Settings
from ..storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository


@click.group(name="backfill")
def backfill_cmd() -> None:
    """Historical price/trade data backfill and dataset verification."""


@backfill_cmd.command(name="prices")
@click.option("--days", type=int, default=180, show_default=True)
@click.option("--limit", type=int, default=200, show_default=True)
@click.option("--interval", default="1h", show_default=True)
@click.option("--fidelity", type=int, default=None, help="Fidelity in minutes (optional).")
@click.pass_context
def backfill_prices(
    ctx: click.Context,
    days: int,
    limit: int,
    interval: str,
    fidelity: int | None,
) -> None:
    """Backfill CLOB price history for the market universe."""
    settings: Settings = ctx.obj["settings"]
    cfg = BackfillConfig(
        requested_days=days,
        market_limit=limit,
        interval=interval,
        fidelity_minutes=fidelity,
    )
    result = asyncio.run(run_price_history_backfill(settings, cfg))
    click.echo(
        f"✓ price history: {result.markets_succeeded} markets ok, "
        f"{result.markets_failed} failed, "
        f"{result.total_rows_written} rows written"
    )
    if result.markets_attempted == 0:
        click.echo("(no markets found — run `gamma fetch-markets` first)")


@backfill_cmd.command(name="relationship-prices")
@click.option("--days", type=int, default=180, show_default=True)
@click.option("--interval", default="1h", show_default=True)
@click.option("--fidelity", type=int, default=None, help="Fidelity in minutes (optional).")
@click.pass_context
def backfill_relationship_prices(
    ctx: click.Context,
    days: int,
    interval: str,
    fidelity: int | None,
) -> None:
    """Backfill CLOB price history for every token referenced by relationship candidates.

    Collects YES/NO token IDs from both legs of all stored relationships, plus any
    additional clob_token_ids from the corresponding market rows, then runs the
    same batch/retry/fallback pipeline as `backfill prices`.

    Prints a per-token post-backfill coverage summary.
    """
    settings: Settings = ctx.obj["settings"]
    cfg = BackfillConfig(requested_days=days, interval=interval, fidelity_minutes=fidelity)
    result, coverage = asyncio.run(run_relationship_price_backfill(settings, cfg))

    n_expected = len(coverage)
    n_ok = sum(1 for v in coverage.values() if v["has_price_history"])
    n_missing = n_expected - n_ok

    click.echo(
        f"✓ relationship prices: tokens_expected={n_expected} "
        f"tokens_with_price_history={n_ok} tokens_missing={n_missing} "
        f"rows_written={result.total_rows_written} "
        f"markets_failed={result.markets_failed}"
    )
    if n_missing > 0:
        click.echo("  missing tokens:")
        for tok, info in coverage.items():
            if not info["has_price_history"]:
                click.echo(f"    {tok[:24]}... market={info['market_id']}")
    if result.markets_attempted == 0:
        click.echo("(no relationships found — run `relationships generate` first)")


@backfill_cmd.command(name="trades")
@click.option("--days", type=int, default=180, show_default=True)
@click.option("--limit", type=int, default=200, show_default=True)
@click.pass_context
def backfill_trades(ctx: click.Context, days: int, limit: int) -> None:
    """Backfill public trade history (best-effort)."""
    settings: Settings = ctx.obj["settings"]
    cfg = BackfillConfig(requested_days=days, market_limit=limit)
    result = asyncio.run(run_trade_history_backfill(settings, cfg))
    click.echo(
        f"✓ trade history: {result.markets_succeeded} markets ok, "
        f"{result.markets_failed} failed/unavailable, "
        f"{result.total_rows_written} rows written"
    )


@backfill_cmd.command(name="coverage")
@click.option("--days", type=int, default=180, show_default=True)
@click.option("--limit", type=int, default=200, show_default=True)
@click.pass_context
def backfill_coverage(ctx: click.Context, days: int, limit: int) -> None:
    """Compute and persist BackfillCoverageRow for each market."""
    settings: Settings = ctx.obj["settings"]
    cfg = BackfillConfig(requested_days=days, market_limit=limit)
    repo = ParquetBackfillCoverageRepository(
        settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )
    count = 0
    for row in compute_all_coverage(settings.data_root, cfg):
        repo.append(row)
        count += 1
    click.echo(f"✓ computed coverage for {count} markets")
    if count == 0:
        click.echo("(no markets in universe — run `gamma fetch-markets` first)")


@backfill_cmd.command(name="relationship-coverage")
@click.option("--days", type=int, default=180, show_default=True)
@click.pass_context
def backfill_relationship_coverage(ctx: click.Context, days: int) -> None:
    """Compute BackfillCoverageRow for every market referenced by relationships."""
    settings: Settings = ctx.obj["settings"]
    cfg = BackfillConfig(requested_days=days, market_limit=1_000_000)
    repo = ParquetBackfillCoverageRepository(
        settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )
    count = 0
    for row in compute_relationship_coverage(settings.data_root, cfg):
        repo.append(row)
        count += 1
    click.echo(f"✓ computed relationship coverage for {count} markets")
    if count == 0:
        click.echo("(no relationship markets found — run `relationships generate` first)")


@backfill_cmd.command(name="verify")
@click.option("--days", type=int, default=180, show_default=True)
@click.option("--limit", type=int, default=200, show_default=True)
@click.pass_context
def backfill_verify(ctx: click.Context, days: int, limit: int) -> None:
    """Run dataset validators and print PASS/WARN/FAIL summary."""
    settings: Settings = ctx.obj["settings"]
    cfg = BackfillConfig(requested_days=days, market_limit=limit)
    results = verify_dataset(settings.data_root, cfg)
    for r in results:
        marker = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}.get(r.status, "?")
        click.echo(f"  {marker} [{r.status}] {r.name}: {r.details}")
    fails = sum(1 for r in results if r.status == "FAIL")
    warns = sum(1 for r in results if r.status == "WARN")
    click.echo(f"\n{len(results)} checks: {fails} FAIL, {warns} WARN")


@backfill_cmd.command(name="semantic-pipeline")
@click.option("--days", type=int, default=180, show_default=True)
@click.option("--limit", type=int, default=200, show_default=True)
@click.option("--queue-csv", default=None, type=click.Path(exists=True), help="Only process market IDs from a targeted queue CSV.")
@click.option(
    "--only-missing/--allow-rerun-stale",
    default=True,
    show_default=True,
    help="Process only markets without semantics, or allow reprocessing stale rows.",
)
@click.option("--force", is_flag=True, help="Rerun all selected queue/universe markets.")
@click.pass_context
def backfill_semantic_pipeline(
    ctx: click.Context,
    days: int,
    limit: int,
    queue_csv: str | None,
    only_missing: bool,
    force: bool,
) -> None:
    """Drive NLP extraction → scoring → implications over the backfill universe."""
    settings: Settings = ctx.obj["settings"]
    cfg = BackfillConfig(requested_days=days, market_limit=limit)
    target_market_ids = None
    if queue_csv:
        from pathlib import Path

        from ..backfill.targeted_semantics_queue import read_target_market_ids

        target_market_ids = read_target_market_ids(Path(queue_csv))
    result = asyncio.run(
        run_semantic_pipeline(
            settings,
            cfg,
            only_missing=only_missing,
            allow_rerun_stale=not only_missing,
            force=force,
            target_market_ids=target_market_ids,
        )
    )
    click.echo(
        f"✓ semantic pipeline: processed={result.total_processed}, "
        f"skipped={result.total_skipped}, "
        f"extracted={result.semantics_extracted}, "
        f"failed={result.semantics_failed}, "
        f"scored={result.scores_computed}, "
        f"implications={result.implications_extracted}"
    )
    if queue_csv:
        click.echo(f"  queue_csv={queue_csv} queue_rows={len(target_market_ids or [])}")
    if result.total_processed == 0:
        click.echo("(no markets — run `gamma fetch-markets` first)")


@backfill_cmd.command(name="targeted-semantic-queue")
@click.option("--output", "output_path", default=None, type=click.Path(), help="Output CSV path.")
@click.pass_context
def backfill_targeted_semantic_queue(ctx: click.Context, output_path: str | None) -> None:
    """Generate a targeted queue for terms-aware semantic extraction."""
    from pathlib import Path

    from ..backfill.targeted_semantics_queue import build_targeted_semantics_queue

    settings: Settings = ctx.obj["settings"]
    path = build_targeted_semantics_queue(
        settings.data_root,
        output_path=Path(output_path) if output_path else None,
    )
    click.echo(f"✓ targeted semantics queue written to: {path}")
    click.echo("  Use: polymarket-arb backfill semantic-pipeline --queue-csv " + str(path))
