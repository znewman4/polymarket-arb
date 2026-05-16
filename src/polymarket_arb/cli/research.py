"""``polymarket-arb research ...`` — trade-surface expansion and opportunity analysis.

RESEARCH-ONLY. No live trading, wallets, or order placement.
All outputs are labelled exploratory/research-only.
"""

from __future__ import annotations

from pathlib import Path

import click

from ..settings import Settings


@click.group(name="research")
def research_cmd() -> None:
    """Trade-surface expansion and opportunity analysis (research-only, no live execution)."""


@research_cmd.command(name="opportunity-surface-report")
@click.option("--run-id", required=True, help="Backtest run ID to analyse.")
@click.option(
    "--include-exploratory/--no-include-exploratory",
    default=True,
    show_default=True,
    help="Include exploratory lane results.",
)
@click.option(
    "--out",
    "output_dir",
    default=None,
    type=click.Path(),
    help="Override output directory (default: data/../reports/opportunity_surface/<run_id>).",
)
@click.option("--preset-label", default="", help="Preset label to embed in the report.")
@click.pass_context
def opportunity_surface_report(
    ctx: click.Context,
    run_id: str,
    include_exploratory: bool,
    output_dir: str | None,
    preset_label: str,
) -> None:
    """Generate the opportunity surface report for a backtest run.

    Reads existing backtest output (trades, signals, rejected candidates, funnels)
    and produces:
      summary.md, opportunity_surface.csv, trade_candidates.csv,
      accepted_simulated_trades.csv, blocked_opportunities.csv,
      expansion_family_summary.csv, suspicious_matches.csv, before_after_counts.csv,
      master_report.md, suspicious_match_audit.csv, suspicious_match_audit.md

    Ranked by TRADE COUNT — PnL is reported but is not the headline metric.

    RESEARCH-ONLY / EXPLORATORY — not trading advice.
    """
    from ..reports.opportunity_surface_report import generate_opportunity_surface_report

    settings: Settings = ctx.obj["settings"]
    out = Path(output_dir) if output_dir else None
    out_dir = generate_opportunity_surface_report(
        data_root=settings.data_root,
        run_id=run_id,
        include_exploratory=include_exploratory,
        output_dir=out,
        preset_label=preset_label,
    )
    click.echo(
        f"✓ opportunity surface report written to: {out_dir}\n"
        f"  files: summary.md, opportunity_surface.csv, trade_candidates.csv,\n"
        f"         accepted_simulated_trades.csv, blocked_opportunities.csv,\n"
        f"         expansion_family_summary.csv, suspicious_matches.csv,\n"
        f"         before_after_counts.csv, master_report.md,\n"
        f"         suspicious_match_audit.csv, suspicious_match_audit.md"
    )


@research_cmd.command(name="list-presets")
@click.option(
    "--preset-file",
    default=None,
    type=click.Path(exists=True),
    help="Override preset YAML path.",
)
def list_presets(preset_file: str | None) -> None:
    """List all available research presets."""
    from pathlib import Path as _Path

    from ..research_presets import list_presets as _list

    path = _Path(preset_file) if preset_file else None
    names = _list(path)
    click.echo("Available research presets:")
    for name in names:
        click.echo(f"  {name}")


@research_cmd.command(name="expand-relationships")
@click.option(
    "--pass",
    "passes",
    multiple=True,
    type=click.Choice([
        "sports_ranking", "sports_progression",
        "threshold_ladders", "date_ladders",
        "election_ladders", "contrapositive", "transitive_closure",
        "all",
    ]),
    default=("all",),
    show_default=True,
    help=(
        "Which expansion pass(es) to run. "
        "'all' runs all passes: sports_ranking, sports_progression, "
        "threshold_ladders, date_ladders, election_ladders, "
        "contrapositive, transitive_closure."
    ),
)
@click.option(
    "--dry-run/--commit",
    default=True,
    show_default=True,
    help=(
        "Dry-run: compute but do NOT write to the store. "
        "--commit writes new relationship candidates to the Parquet lake."
    ),
)
@click.option(
    "--max-pairs-per-group",
    type=int,
    default=500,
    show_default=True,
    help="Safety cap on emitted pairs per (team, competition) group.",
)
@click.option(
    "--closure-mode",
    type=click.Choice(["strict", "exploratory"]),
    default="strict",
    show_default=True,
    help=(
        "Source-lane policy for transitive_closure. Strict closes only over "
        "strict_context_valid edges; exploratory also allows reviewed/exploratory lanes."
    ),
)
@click.option(
    "--max-closure-depth",
    type=int,
    default=None,
    help="Override transitive_closure depth (default: 3 strict / 4 exploratory).",
)
@click.pass_context
def expand_relationships(
    ctx: click.Context,
    passes: tuple[str, ...],
    dry_run: bool,
    max_pairs_per_group: int,
    closure_mode: str,
    max_closure_depth: int | None,
) -> None:
    """Run deterministic relationship expansion passes.

    Reads all ingested market semantics and emits new relationship candidates
    that the standard miner may have missed (ranking ladders, stage ordering).

    All emitted relationships carry generated_by=deterministic_expansion and
    can be picked up by `relationships apply-context` without changes.

    RESEARCH-ONLY. No live trading.

    Example usage:

    \b
    # Dry-run (safe) — see what would be emitted without writing
    polymarket-arb research expand-relationships --dry-run --pass all

    \b
    # Commit — write new candidates to the store
    polymarket-arb research expand-relationships --commit --pass sports_ranking
    polymarket-arb relationships apply-context --all --keep-reviewed
    """
    settings: Settings = ctx.obj["settings"]

    run_passes = set(passes)
    if "all" in run_passes:
        run_passes = {
            "sports_ranking", "sports_progression",
            "threshold_ladders", "date_ladders",
            "election_ladders", "contrapositive", "transitive_closure",
        }

    mode = "DRY RUN" if dry_run else "COMMIT"
    click.echo(f"  {mode} — passes: {sorted(run_passes)}")

    if "sports_ranking" in run_passes:
        from ..relationships.expansion.sports_ranking import run_sports_ranking_expansion

        result = run_sports_ranking_expansion(
            settings.data_root,
            dry_run=dry_run,
            max_pairs_per_group=max_pairs_per_group,
        )
        click.echo(
            f"✓ sports_ranking expansion\n"
            f"  emitted={result.emitted_count}\n"
            f"  skipped_existing={result.skipped_existing}\n"
            f"  skipped_guard_fail={result.skipped_guard_fail}\n"
            f"  needs_review={result.needs_review_count}\n"
            f"  guard_failures={result.guard_failure_counts}"
        )
        if dry_run:
            click.echo("  (dry-run: nothing written — rerun with --commit to save)")

    if "sports_progression" in run_passes:
        from ..relationships.expansion.sports_progression import run_sports_progression_expansion

        result = run_sports_progression_expansion(
            settings.data_root,
            dry_run=dry_run,
        )
        click.echo(
            f"✓ sports_progression expansion\n"
            f"  emitted={result.emitted_count}\n"
            f"  skipped_existing={result.skipped_existing}\n"
            f"  skipped_guard_fail={result.skipped_guard_fail}"
        )
        if dry_run:
            click.echo("  (dry-run: nothing written — rerun with --commit to save)")

    if "threshold_ladders" in run_passes:
        from ..relationships.expansion.threshold_ladders import run_threshold_ladder_expansion

        result = run_threshold_ladder_expansion(
            settings.data_root,
            dry_run=dry_run,
            max_pairs_per_group=max_pairs_per_group,
        )
        click.echo(
            f"✓ threshold_ladders expansion\n"
            f"  emitted={result.emitted_count}\n"
            f"  skipped_existing={result.skipped_existing}\n"
            f"  skipped_guard_fail={result.skipped_guard_fail}\n"
            f"  needs_review={result.needs_review_count}\n"
            f"  guard_failures={result.guard_failure_counts}"
        )
        if dry_run:
            click.echo("  (dry-run: nothing written — rerun with --commit to save)")

    if "date_ladders" in run_passes:
        from ..relationships.expansion.date_ladders import run_date_ladder_expansion

        result = run_date_ladder_expansion(
            settings.data_root,
            dry_run=dry_run,
            max_pairs_per_group=max_pairs_per_group,
        )
        click.echo(
            f"✓ date_ladders expansion\n"
            f"  emitted={result.emitted_count}\n"
            f"  skipped_existing={result.skipped_existing}\n"
            f"  skipped_guard_fail={result.skipped_guard_fail}\n"
            f"  needs_review={result.needs_review_count}"
        )
        if dry_run:
            click.echo("  (dry-run: nothing written — rerun with --commit to save)")

    if "election_ladders" in run_passes:
        from ..relationships.expansion.election_ladders import run_election_ladder_expansion

        result = run_election_ladder_expansion(
            settings.data_root,
            dry_run=dry_run,
            max_pairs_per_group=max_pairs_per_group,
        )
        click.echo(
            f"✓ election_ladders expansion\n"
            f"  emitted={result.emitted_count}\n"
            f"  skipped_existing={result.skipped_existing}\n"
            f"  skipped_guard_fail={result.skipped_guard_fail}\n"
            f"  needs_review={result.needs_review_count}\n"
            f"  guard_failures={result.guard_failure_counts}"
        )
        if dry_run:
            click.echo("  (dry-run: nothing written — rerun with --commit to save)")

    if "contrapositive" in run_passes:
        from ..relationships.expansion.contrapositive import run_contrapositive_expansion

        result = run_contrapositive_expansion(
            settings.data_root,
            dry_run=dry_run,
        )
        click.echo(
            f"✓ contrapositive expansion\n"
            f"  emitted={result.emitted_count}\n"
            f"  skipped_existing={result.skipped_existing}\n"
            f"  skipped_guard_fail={result.skipped_guard_fail}\n"
            f"  guard_failures={result.guard_failure_counts}"
        )
        if dry_run:
            click.echo("  (dry-run: nothing written — rerun with --commit to save)")

    if "transitive_closure" in run_passes:
        from ..relationships.expansion.transitive_closure import run_transitive_closure_expansion

        result = run_transitive_closure_expansion(
            settings.data_root,
            dry_run=dry_run,
            source_mode=closure_mode,
            max_depth=max_closure_depth,
            max_pairs_per_group=max_pairs_per_group,
        )
        click.echo(
            f"✓ transitive_closure expansion\n"
            f"  emitted={result.emitted_count}\n"
            f"  skipped_existing={result.skipped_existing}\n"
            f"  skipped_guard_fail={result.skipped_guard_fail}\n"
            f"  needs_review={result.needs_review_count}\n"
            f"  guard_failures={result.guard_failure_counts}"
        )
        if dry_run:
            click.echo("  (dry-run: nothing written — rerun with --commit to save)")
