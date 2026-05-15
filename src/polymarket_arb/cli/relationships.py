"""``polymarket-arb relationships ...`` — relationship mining CLI."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..settings import Settings


@click.group(name="relationships")
def relationships_cmd() -> None:
    """Market relationship mining: generate, validate, and report."""


@relationships_cmd.command(name="generate")
@click.option("--limit", type=int, default=None, help="Cap number of markets to process.")
@click.option("--embeddings/--no-embeddings", default=False, show_default=True)
@click.option("--rulebook", "rulebook_path", default=None, type=click.Path(), help="Path to relationship rulebook YAML.")
@click.pass_context
def relationships_generate(
    ctx: click.Context,
    limit: int | None,
    embeddings: bool,
    rulebook_path: str | None,
) -> None:
    """Generate and validate relationship candidates, write to lake."""
    from ..relationships import generate_relationships

    settings: Settings = ctx.obj["settings"]
    rb_path = Path(rulebook_path) if rulebook_path else None
    result = generate_relationships(
        settings,
        rulebook_path_override=rb_path,
        use_embeddings=embeddings,
        market_limit=limit,
    )
    click.echo(
        f"✓ relationships: considered={result.total_candidates_considered} "
        f"accepted={result.accepted} rejected={result.rejected} "
        f"manual_review={result.needs_manual_review} "
        f"({result.wall_clock_ms}ms)"
    )
    if result.by_type:
        click.echo("  by type: " + ", ".join(f"{t}={c}" for t, c in sorted(result.by_type.items())))
    if result.top_rejection_reasons:
        click.echo("  top rejections: " + ", ".join(f"{k}={v}" for k, v in list(result.top_rejection_reasons.items())[:5]))


@relationships_cmd.command(name="validate")
@click.option("--rulebook", "rulebook_path", default=None, type=click.Path())
@click.pass_context
def relationships_validate(ctx: click.Context, rulebook_path: str | None) -> None:
    """Re-validate existing candidates from semantics + rulebook."""
    settings: Settings = ctx.obj["settings"]
    from ..relationships import generate_relationships
    rb_path = Path(rulebook_path) if rulebook_path else None
    result = generate_relationships(settings, rulebook_path_override=rb_path)
    click.echo(f"✓ re-validated: {result.total_candidates_considered} candidates")


@relationships_cmd.command(name="report")
@click.option("--output", "output_path", default=None, type=click.Path(), help="Output directory for the report.")
@click.pass_context
def relationships_report(ctx: click.Context, output_path: str | None) -> None:
    """Generate the Relationship Candidate HTML report."""
    from ..reports.relationship_candidates_report import generate_relationship_candidates_report

    settings: Settings = ctx.obj["settings"]
    out = Path(output_path) if output_path else None
    path = generate_relationship_candidates_report(settings.data_root, output_dir=out)
    click.echo(f"✓ report written to: {path}")


@relationships_cmd.command(name="classification-audit")
@click.option("--output", "output_path", default=None, type=click.Path(), help="Output directory for the audit report.")
@click.pass_context
def relationships_classification_audit(ctx: click.Context, output_path: str | None) -> None:
    """Generate the strict relationship classification audit report."""
    from ..reports.classification_audit_report import generate_classification_audit_report

    settings: Settings = ctx.obj["settings"]
    out = Path(output_path) if output_path else None
    path = generate_classification_audit_report(settings.data_root, output_dir=out)
    click.echo(f"✓ classification audit written to: {path}")


@relationships_cmd.command(name="apply-context")
@click.option("--registry", default="configs/context_spaces/context_spaces_v1.yaml", show_default=True)
@click.option("--template-registry", "template_registry", default=None, type=click.Path(),
              help="Path to deterministic templates YAML (e.g. configs/deterministic_templates/templates_v1.yaml).")
@click.option("--write-upgraded-relationships/--decisions-only", default=False, show_default=True)
@click.option("--all", "apply_all", is_flag=True, default=True, show_default=True,
              help="Process every relationship in the store (default). Always True.")
@click.option("--overwrite-reviewed/--keep-reviewed", "overwrite_reviewed", default=False, show_default=True,
              help="Overwrite existing human-reviewed or auto-approved decisions.")
@click.option("--audit-output", "audit_output", default=None, type=click.Path(),
              help="Write a CSV audit of auto-applied template matches to this path.")
@click.pass_context
def relationships_apply_context(
    ctx: click.Context,
    registry: str,
    template_registry: str | None,
    write_upgraded_relationships: bool,
    apply_all: bool,
    overwrite_reviewed: bool,
    audit_output: str | None,
) -> None:
    """Apply context rules to ALL relationship candidates, writing a lane decision for every one.

    Relationships without a configured context space receive research_only/context_missing.
    With --template-registry, approved deterministic templates are also checked, expanding
    the set of relationships that reach reviewed_context_valid without individual pair review.
    Protected lanes (exploratory_context_auto_approved) are preserved by default.
    """
    from ..context.decision_engine import apply_context_decisions

    settings: Settings = ctx.obj["settings"]
    tmpl_path = Path(template_registry) if template_registry else None
    default_audit = (
        settings.data_root / "reports" / "template_audit" / "auto_applied_templates.csv"
        if tmpl_path and not audit_output
        else None
    )
    audit_path = Path(audit_output) if audit_output else default_audit
    result = apply_context_decisions(
        settings.data_root,
        Path(registry),
        template_registry_path=tmpl_path,
        append_upgraded_relationships=write_upgraded_relationships,
        skip_human_reviewed=not overwrite_reviewed,
        audit_output_path=audit_path,
    )
    click.echo(
        f"✓ context decisions: relationships={result['relationships_loaded']} "
        f"decisions_written={result['decisions_written']} "
        f"skipped_protected={result.get('skipped_protected', 0)} "
        f"upgraded={result['upgraded']} "
        f"template_matched={result.get('template_matched', 0)} "
        f"context_missing={result['context_missing']} "
        f"analysis_only={result['analysis_only']}"
    )
    if result.get("lane_counts"):
        click.echo("  lanes: " + ", ".join(f"{k}={v}" for k, v in sorted(result["lane_counts"].items())))
    if result.get("template_counts"):
        click.echo("  by template: " + ", ".join(
            f"{tid}={cnt}" for tid, cnt in sorted(result["template_counts"].items())
        ))
    if result.get("audit_path"):
        click.echo(f"  audit CSV: {result['audit_path']} ({result.get('audit_rows_written', 0)} rows)")


@relationships_cmd.command(name="context-audit")
@click.option("--output", "output_path", default=None, type=click.Path(), help="Output directory for the audit report.")
@click.pass_context
def relationships_context_audit(ctx: click.Context, output_path: str | None) -> None:
    """Generate context-aware relationship classification audit report."""
    from ..reports.context_classification_audit_report import (
        generate_context_classification_audit_report,
    )

    settings: Settings = ctx.obj["settings"]
    out = Path(output_path) if output_path else None
    path = generate_context_classification_audit_report(settings.data_root, output_dir=out)
    click.echo(f"✓ context classification audit written to: {path}")


@relationships_cmd.group(name="review")
def review_group() -> None:
    """Relationship-level manual review and auto-approve workflow."""


@review_group.command(name="export")
@click.option(
    "--output",
    "output_path",
    default="data/context/relationship_review_queue_latest.csv",
    show_default=True,
    type=click.Path(),
)
@click.pass_context
def review_export(ctx: click.Context, output_path: str) -> None:
    """Export needs-manual-review relationships to CSV for human inspection."""
    from ..context.relationship_review import export_relationship_review_queue

    settings: Settings = ctx.obj["settings"]
    result = export_relationship_review_queue(settings.data_root, Path(output_path))
    click.echo(f"✓ relationship review queue: {result['exported']} rows → {output_path}")


@review_group.command(name="import")
@click.argument("csv_path", type=click.Path(exists=True))
@click.pass_context
def review_import(ctx: click.Context, csv_path: str) -> None:
    """Append-only import of human-reviewed relationship decisions from CSV."""
    from ..context.relationship_review import import_relationship_review_queue

    settings: Settings = ctx.obj["settings"]
    result = import_relationship_review_queue(settings.data_root, Path(csv_path))
    click.echo(
        f"✓ relationship review import: {result['imported']} decisions imported, "
        f"{result['skipped']} rows skipped"
    )


@review_group.command(name="auto-approve")
@click.pass_context
def review_auto_approve(ctx: click.Context) -> None:
    """Auto-approve all needs_manual_review relationships as exploratory_context_auto_approved.

    Results from this lane are research-only and are NEVER counted toward
    the headline credibility label.  Use --include-auto-approved in the
    context-aware backtest to see their contribution separately.
    """
    from ..context.relationship_review import auto_approve_relationships

    settings: Settings = ctx.obj["settings"]
    result = auto_approve_relationships(settings.data_root)
    click.echo(
        f"✓ auto-approve: {result['auto_approved']} relationships marked "
        f"exploratory_context_auto_approved "
        f"(skipped_superior={result['skipped_already_eligible']} "
        f"skipped_not_review={result['skipped_not_eligible_for_review']})"
    )
    click.echo("  WARNING: auto-approved results are EXPLORATORY ONLY — never headline credible.")


@relationships_cmd.command(name="coverage-audit")
@click.option("--output", "output_dir", default=None, type=click.Path(),
              help="Output directory for CSV + markdown (default: data/reports/coverage_audit/).")
@click.pass_context
def relationships_coverage_audit(ctx: click.Context, output_dir: str | None) -> None:
    """Audit every relationship for data coverage and write CSV + markdown report.

    Shows Gamma metadata, CLOB token IDs, price history, backfill coverage score,
    and context decision per relationship. Reports the first/worst blocker for each.
    """
    from collections import Counter

    from ..backtest.coverage_audit import run_coverage_audit
    from ..reports.coverage_audit_report import generate_coverage_audit_report

    settings: Settings = ctx.obj["settings"]
    rows = run_coverage_audit(settings.data_root)
    click.echo(f"[coverage-audit] Audited {len(rows)} relationships.")

    out = Path(output_dir) if output_dir else settings.data_root / "reports" / "coverage_audit"
    csv_path, md_path = generate_coverage_audit_report(rows, out)

    blockers = Counter(r.final_blocker for r in rows)
    both_ph = sum(1 for r in rows if r.both_have_price_history)
    no_blocker = sum(1 for r in rows if r.final_blocker == "none")

    click.echo(f"  total: {len(rows)}")
    click.echo(f"  both have price history: {both_ph}")
    click.echo(f"  fully covered (no blocker): {no_blocker}")
    click.echo("  top blockers:")
    for blocker, count in blockers.most_common(8):
        click.echo(f"    {count:5d}  {blocker}")
    click.echo(f"  CSV: {csv_path}")
    click.echo(f"  MD:  {md_path}")


@relationships_cmd.command(name="funnel-report")
@click.option("--output", "output_dir", default=None, type=click.Path(),
              help="Output directory (default: data/reports/relationship_funnel/).")
@click.option("--include-violations/--no-include-violations", default=False, show_default=True,
              help="Evaluate gross violations per tick (slow - O(Nxticks)).")
@click.option("--min-gross-edge", type=float, default=0.001, show_default=True,
              help="Minimum gross edge for violation detection (only used with --include-violations).")
@click.pass_context
def relationships_funnel_report(
    ctx: click.Context,
    output_dir: str | None,
    include_violations: bool,
    min_gross_edge: float,
) -> None:
    """Generate a full per-relationship data funnel report (CSV + markdown).

    One row per relationship showing all pipeline layers: Gamma metadata,
    CLOB tokens, price history, coverage score, context decision, aligned tick
    count, and optionally gross strategy violations.
    """
    from ..reports.relationship_funnel_report import generate_relationship_funnel_report

    settings: Settings = ctx.obj["settings"]
    out = Path(output_dir) if output_dir else settings.data_root / "reports" / "relationship_funnel"
    click.echo(f"[funnel-report] Building funnel report (include_violations={include_violations}) ...")
    csv_path, md_path = generate_relationship_funnel_report(
        settings.data_root,
        out,
        include_violations=include_violations,
        min_gross_edge_for_violations=min_gross_edge,
    )
    click.echo(f"  CSV: {csv_path}")
    click.echo(f"  MD:  {md_path}")


@relationships_cmd.command(name="show")
@click.argument("relationship_id")
@click.pass_context
def relationships_show(ctx: click.Context, relationship_id: str) -> None:
    """Print a single relationship candidate as JSON."""
    from dataclasses import asdict

    from ..storage.parquet.relationship_candidates_repo import (
        ParquetRelationshipCandidatesRepository,
    )

    settings: Settings = ctx.obj["settings"]
    repo = ParquetRelationshipCandidatesRepository(settings.data_root)
    row = repo.get_latest(relationship_id)
    if row is None:
        click.echo(f"No relationship found with id={relationship_id!r}", err=True)
        raise SystemExit(1)
    click.echo(json.dumps(asdict(row), indent=2, default=str))
