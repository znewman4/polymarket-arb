"""``polymarket-arb context ...`` commands."""

from __future__ import annotations

from pathlib import Path

import click

from ..settings import REPO_ROOT, Settings


@click.group(name="context")
def context_cmd() -> None:
    """Evidence-backed context rules and review workflow."""


@context_cmd.group(name="registry")
def registry_group() -> None:
    """Context registry commands."""


@context_cmd.group(name="manual-rules")
def manual_rules_group() -> None:
    """Manual curated context rule commands."""


@context_cmd.group(name="review")
def review_group() -> None:
    """Manual review import/export commands."""


@registry_group.command(name="audit")
@click.option("--registry", default="configs/context_spaces/context_spaces_v1.yaml", show_default=True)
def registry_audit_cmd(registry: str) -> None:
    """Validate and summarize the context registry."""
    from ..context.source_registry import registry_audit

    result = registry_audit(Path(registry))
    click.echo(f"✓ context registry spaces={result['context_spaces']}")
    click.echo(f"  completeness={result['completeness_counts']}")


@manual_rules_group.command(name="import")
@click.argument("path", type=click.Path(exists=True))
@click.pass_context
def manual_rules_import(ctx: click.Context, path: str) -> None:
    """Import curated manual context rules."""
    from ..context.manual_rules import import_manual_rules

    settings: Settings = ctx.obj["settings"]
    result = import_manual_rules(settings.data_root, Path(path))
    click.echo(
        f"✓ manual rules imported: sources={result['sources']} "
        f"documents={result['documents']} rules={result['rules']}"
    )


@review_group.command(name="export")
@click.option("--output", "output_path", required=True, type=click.Path())
@click.pass_context
def review_export(ctx: click.Context, output_path: str) -> None:
    """Export pending context rules for manual review."""
    from ..context.manual_rules import export_review_queue

    settings: Settings = ctx.obj["settings"]
    result = export_review_queue(settings.data_root, Path(output_path))
    click.echo(f"✓ review queue exported: {result['exported']} rows to {output_path}")


@review_group.command(name="import")
@click.argument("path", type=click.Path(exists=True))
@click.pass_context
def review_import(ctx: click.Context, path: str) -> None:
    """Import approved/rejected context rule review rows."""
    from ..context.manual_rules import import_review_queue

    settings: Settings = ctx.obj["settings"]
    result = import_review_queue(settings.data_root, Path(path))
    click.echo(f"✓ review queue imported: {result['imported']} rule versions")


@context_cmd.command(name="fetch")
@click.option("--space", "space_id", required=True)
def context_fetch(space_id: str) -> None:
    """Placeholder for optional public source snapshots."""
    click.echo(
        "context fetch is optional in this slice; "
        f"curated/manual rules should be used first for {space_id}"
    )


@context_cmd.command(name="extract-rules")
@click.option("--space", "space_id", required=True)
def context_extract_rules(space_id: str) -> None:
    """Placeholder for fixture/manual extraction."""
    click.echo(
        "context extract-rules is fixture/manual-first in this slice; "
        f"no live extraction run for {space_id}"
    )


@context_cmd.command(name="validate-rules")
@click.option("--registry", default="configs/context_spaces/context_spaces_v1.yaml", show_default=True)
@click.pass_context
def context_validate_rules(ctx: click.Context, registry: str) -> None:
    """Validate latest context rules."""
    from ..context.validators import validate_context_rules
    from ..storage.parquet.context_rules_repo import ParquetContextRulesRepository

    settings: Settings = ctx.obj["settings"]
    rules = list(ParquetContextRulesRepository(settings.data_root).iter_latest())
    results = validate_context_rules(Path(registry), rules)
    invalid = [r for r in results if not r.valid]
    click.echo(f"✓ context rules validated: total={len(results)} invalid={len(invalid)}")
    for row in invalid[:10]:
        click.echo(f"  invalid {row.context_rule_id}: {row.reason}")


@context_cmd.command(name="report")
@click.option("--output", "output_path", default=None, type=click.Path())
@click.pass_context
def context_report(ctx: click.Context, output_path: str | None) -> None:
    """Generate context rules report."""
    from ..reports.context_rules_report import generate_context_rules_report

    settings: Settings = ctx.obj["settings"]
    path = generate_context_rules_report(
        settings.data_root,
        output_dir=Path(output_path) if output_path else None,
    )
    click.echo(f"✓ context rules report written to: {path}")


def default_registry_path() -> Path:
    return REPO_ROOT / "configs" / "context_spaces" / "context_spaces_v1.yaml"
