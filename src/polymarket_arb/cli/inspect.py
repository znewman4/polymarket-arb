"""``polymarket-arb inspect ...`` - local lake inspection and audits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from ..inspect.audit import audit_data as run_audit_data
from ..inspect.exports import export_semantics_review
from ..inspect.reports import (
    counts_report,
    freshness_report,
    market_pipeline_report,
    market_report,
    pretty_json,
    score_distribution_report,
    table_report,
)
from ..settings import REPO_ROOT, Settings


@click.group(name="inspect")
def inspect_cmd() -> None:
    """Inspect local data, coverage, freshness, and review exports."""


@inspect_cmd.command(name="tables")
@click.pass_context
def tables(ctx: click.Context) -> None:
    settings: Settings = ctx.obj["settings"]
    click.echo("table/view                 files  rows       latest_ingested_ts_ms  path")
    for row in table_report(settings.data_root):
        click.echo(
            f"{row.name:<26} {row.file_count:>5}  "
            f"{_fmt(row.row_count):>9}  {_fmt(row.latest_ingested_ts_ms):>21}  "
            f"{row.backing_path}"
        )


@inspect_cmd.command(name="counts")
@click.pass_context
def counts(ctx: click.Context) -> None:
    settings: Settings = ctx.obj["settings"]
    report = counts_report(settings.data_root)
    for key, value in report.items():
        if isinstance(value, dict):
            click.echo(f"{key}:")
            for k, v in value.items():
                click.echo(f"  {k}: {v}")
        else:
            click.echo(f"{key}: {value}")


@inspect_cmd.command(name="market")
@click.argument("market_id")
@click.pass_context
def market(ctx: click.Context, market_id: str) -> None:
    settings: Settings = ctx.obj["settings"]
    report = market_report(settings.data_root, market_id)
    if not report.get("present"):
        raise click.ClickException(f"market {market_id!r} not found locally")
    gamma = report["gamma"]
    click.echo(f"market_id       {market_id}")
    click.echo(f"condition_id    {gamma['condition_id']}")
    click.echo(f"question        {gamma['question']}")
    click.echo(f"outcomes        {gamma['outcomes']}")
    click.echo(f"token_ids       {gamma['token_ids']}")
    click.echo(f"flags           active={gamma['active']} closed={gamma['closed']} archived={gamma['archived']}")
    click.echo(f"end_date_ms     {gamma['end_date_ms']}")
    click.echo(f"text_hash       {gamma['text_hash']}")
    if report["semantics"]:
        sem = report["semantics"]
        click.echo(f"semantics       conf={sem['semantic_confidence']} ambiguity={sem['ambiguity_score']}")
        click.echo(f"YES if          {sem['positive_resolution_condition']}")
        click.echo(f"NO if           {sem['negative_resolution_condition']}")
    else:
        click.echo("semantics       missing")
    click.echo(f"implications    {report['implications']['count']}")
    click.echo(f"best_quotes     {len(report['best_quotes'])}")
    if report["market_score"]:
        score = report["market_score"]
        click.echo(f"fusion_score    {score.get('final_signal_score')} ({score.get('recommendation')})")
    else:
        click.echo("fusion_score    missing")


@inspect_cmd.command(name="pipeline")
@click.argument("market_id")
@click.pass_context
def pipeline(ctx: click.Context, market_id: str) -> None:
    settings: Settings = ctx.obj["settings"]
    for stage in market_pipeline_report(settings.data_root, market_id):
        state = "present" if stage.present else "missing"
        click.echo(f"{stage.name}: {state} latest_ts={stage.latest_ts_ms}")
        if stage.summary:
            click.echo(f"  {stage.summary}")
        if stage.next_command:
            click.echo(f"  Next: {stage.next_command}")


@inspect_cmd.command(name="freshness")
@click.pass_context
def freshness(ctx: click.Context) -> None:
    settings: Settings = ctx.obj["settings"]
    click.echo(pretty_json(freshness_report(settings.data_root)))


@inspect_cmd.command(name="audit-data")
@click.pass_context
def audit_data(ctx: click.Context) -> None:
    settings: Settings = ctx.obj["settings"]
    checks = run_audit_data(settings.data_root, repo_root=REPO_ROOT)
    for check in checks:
        click.echo(f"{check.status:<4} {check.name} - {check.detail}")


@inspect_cmd.command(name="export-semantics-review")
@click.option("--sample", type=int, default=100)
@click.option("--out", "out_path", type=click.Path(path_type=Path), required=True)
@click.option("--only-review-needed", is_flag=True, default=False)
@click.option("--sort", "sort_key",
              type=click.Choice(["ambiguity_score_desc", "newest", "random"]),
              default="ambiguity_score_desc")
@click.pass_context
def export_review(
    ctx: click.Context,
    sample: int,
    out_path: Path,
    only_review_needed: bool,
    sort_key: str,
) -> None:
    settings: Settings = ctx.obj["settings"]
    count = export_semantics_review(
        settings.data_root,
        out_path,
        sample=sample,
        only_review_needed=only_review_needed,
        sort=sort_key,
    )
    click.echo(f"✓ wrote {count} review rows to {out_path}")


@inspect_cmd.command(name="score-distribution")
@click.option("--top", "top_n", type=int, default=10)
@click.pass_context
def score_distribution(ctx: click.Context, top_n: int) -> None:
    settings: Settings = ctx.obj["settings"]
    report = score_distribution_report(settings.data_root, top_n=top_n)
    click.echo(pretty_json(report))


def _fmt(value: Any) -> str:
    return "-" if value is None else str(value)
