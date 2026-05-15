"""``polymarket-arb report ...`` — generate HTML research reports."""

from __future__ import annotations

from pathlib import Path

import click

from ..reports.historical_dataset_report import generate_historical_dataset_report
from ..reports.master_audit_report import generate_master_audit_report
from ..reports.semantic_quality_report import generate_semantic_quality_report
from ..settings import Settings


@click.group(name="report")
def report_cmd() -> None:
    """Generate HTML research reports from the local data lake."""


@report_cmd.command(name="historical-dataset")
@click.option("--output", default=None, help="Output directory (default: reports/historical_dataset/...).")
@click.pass_context
def historical_dataset(ctx: click.Context, output: str | None) -> None:
    """Generate the Historical Dataset HTML report."""
    settings: Settings = ctx.obj["settings"]
    output_dir = Path(output) if output else None
    path = generate_historical_dataset_report(settings.data_root, output_dir)
    click.echo(f"✓ report written: {path}")


@report_cmd.command(name="semantic-quality")
@click.option("--output", default=None, help="Output directory (default: reports/semantic_quality/...).")
@click.pass_context
def semantic_quality(ctx: click.Context, output: str | None) -> None:
    """Generate the Semantic Quality HTML report."""
    settings: Settings = ctx.obj["settings"]
    output_dir = Path(output) if output else None
    path = generate_semantic_quality_report(settings.data_root, output_dir)
    click.echo(f"✓ report written: {path}")


@report_cmd.command(name="master-audit")
@click.option("--run-id", required=True, help="Reviewed run id to headline.")
@click.option("--strict-run-id", default=None, help="Strict run id to include.")
@click.option("--diagnostic-run-id", default=None, help="Diagnostic-only comparison run id.")
@click.option("--output", default=None, help="Output directory (default: reports/master_audit/<timestamp>).")
@click.pass_context
def master_audit(
    ctx: click.Context,
    run_id: str,
    strict_run_id: str | None,
    diagnostic_run_id: str | None,
    output: str | None,
) -> None:
    """Generate the single master audit report."""
    settings: Settings = ctx.obj["settings"]
    output_dir = Path(output) if output else None
    path = generate_master_audit_report(
        settings.data_root,
        run_id=run_id,
        strict_run_id=strict_run_id,
        diagnostic_run_id=diagnostic_run_id,
        output_dir=output_dir,
    )
    click.echo(f"✓ master audit report written: {path}")
