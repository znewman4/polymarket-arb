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


# ─── space-sweep and optimise-spaces commands ─────────────────────────────────


@research_cmd.command(name="space-sweep")
@click.option("--run-id", required=True, help="Existing backtest run_id to aggregate.")
@click.option(
    "--include-pairwise/--no-include-pairwise",
    default=True,
    show_default=True,
    help="Include pairwise context/research/diagnostic data.",
)
@click.option(
    "--include-bundles/--no-include-bundles",
    default=True,
    show_default=True,
    help="Include bundle scanner data.",
)
@click.option(
    "--include-exploratory/--no-include-exploratory",
    default=True,
    show_default=True,
    help="Include exploratory lane data.",
)
@click.option(
    "--out",
    "output_dir",
    default=None,
    type=click.Path(),
    help="Override output directory (default: data/../reports/space_sweep/<run_id>).",
)
@click.pass_context
def space_sweep(
    ctx: click.Context,
    run_id: str,
    include_pairwise: bool,
    include_bundles: bool,
    include_exploratory: bool,
    output_dir: str | None,
) -> None:
    """Aggregate an existing backtest run by space and emit the leaderboard.

    Reads data/backtests/<run_id>/ and produces a per-space classified report
    under data/../reports/space_sweep/<run_id>/.

    RESEARCH-ONLY. Diagnostic-only subtypes are NEVER counted in trade or PnL totals.
    """
    from ..reports.space_research_report import generate_space_research_report
    from ..research.space_sweep import run_space_sweep

    settings: Settings = ctx.obj["settings"]
    out = Path(output_dir) if output_dir else None
    result = run_space_sweep(
        data_root=settings.data_root,
        run_id=run_id,
        include_pairwise=include_pairwise,
        include_bundles=include_bundles,
        include_exploratory=include_exploratory,
        output_dir=out,
    )
    out_dir = generate_space_research_report(result)

    click.echo(
        f"✓ space sweep run_id={result.run_id}\n"
        f"  spaces={len(result.summaries)}\n"
        f"  accepted_trades={len(result.accepted_trades)}\n"
        f"  blocked={len(result.blocked)}\n"
        f"  diagnostic_only={len(result.diagnostic_only)}\n"
        f"  report_integrity={result.report_integrity}\n"
        f"  credibility={result.credibility}\n"
        f"  output: {out_dir}"
    )


@research_cmd.command(name="optimise-spaces")
@click.option(
    "--input",
    "leaderboard_csv",
    required=True,
    type=click.Path(exists=True),
    help="space_leaderboard.csv produced by `research space-sweep`.",
)
@click.option(
    "--grid",
    "grid_path",
    default="configs/research_presets/space_optimisation_grid_v1.yaml",
    show_default=True,
    type=click.Path(exists=True),
    help="Parameter grid YAML.",
)
@click.option(
    "--top-n",
    type=int,
    default=10,
    show_default=True,
    help="Number of top spaces from the leaderboard to optimise.",
)
@click.option(
    "--run-id",
    required=True,
    help="Output run_id for the optimisation results.",
)
@click.option(
    "--sampling",
    default=None,
    type=click.Choice(["exhaustive", "slim", "lhs"]),
    help="Override grid sampling strategy.",
)
@click.option(
    "--sample-size",
    default=None,
    type=int,
    help="Override LHS sample size.",
)
@click.option(
    "--out",
    "output_dir",
    default=None,
    type=click.Path(),
    help="Override output directory (default: data/../reports/space_optimisation/<run_id>).",
)
@click.pass_context
def optimise_spaces(
    ctx: click.Context,
    leaderboard_csv: str,
    grid_path: str,
    top_n: int,
    run_id: str,
    sampling: str | None,
    sample_size: int | None,
    output_dir: str | None,
) -> None:
    """Run per-space parameter grid + robustness scoring on top-N spaces.

    Reads the leaderboard CSV, selects the top-N spaces (excluding D/E/F grades),
    enumerates the parameter grid (or samples it), evaluates each cell, scores
    robustness, and writes the optimisation report.

    RESEARCH-ONLY. Credibility never exceeds exploratory_only_not_credible.
    """
    from ..reports.space_optimisation_report import generate_space_optimisation_report
    from ..research.space_optimisation import run_space_optimisation

    settings: Settings = ctx.obj["settings"]
    out = Path(output_dir) if output_dir else None
    result = run_space_optimisation(
        data_root=settings.data_root,
        leaderboard_csv=Path(leaderboard_csv),
        grid_path=Path(grid_path),
        run_id=run_id,
        top_n=top_n,
        sampling=sampling,
        sample_size=sample_size,
        output_dir=out,
    )
    out_dir = generate_space_optimisation_report(result)

    click.echo(
        f"✓ space optimisation run_id={result.run_id}\n"
        f"  spaces_optimised={len(result.best_by_space)}\n"
        f"  cells_evaluated={len(result.rows)}\n"
        f"  skipped_spaces={len(result.skipped_spaces)}\n"
        f"  output: {out_dir}"
    )


@research_cmd.command(name="final-report")
@click.option("--sweep-run-id", required=True, help="space-sweep run_id.")
@click.option("--optimisation-run-id", default=None, help="optional optimisation run_id.")
@click.option(
    "--out",
    "output_dir",
    default=None,
    type=click.Path(),
    help="Override output dir (default: data/../reports/final_strategy_research/<sweep_run_id>).",
)
@click.pass_context
def final_report(
    ctx: click.Context,
    sweep_run_id: str,
    optimisation_run_id: str | None,
    output_dir: str | None,
) -> None:
    """Generate the final narrative research report (all 19 sections).

    Combines a space-sweep result with an optional optimisation result and
    produces ``final_report.md`` under
    ``data/../reports/final_strategy_research/<sweep_run_id>/``.

    RESEARCH-ONLY.
    """
    from ..reports.final_strategy_research_report import generate_final_report

    settings: Settings = ctx.obj["settings"]
    out = Path(output_dir) if output_dir else None
    path = generate_final_report(
        data_root=settings.data_root,
        sweep_run_id=sweep_run_id,
        optimisation_run_id=optimisation_run_id,
        output_dir=out,
    )
    click.echo(f"✓ final strategy research report written to: {path}")


@research_cmd.command(name="expanded-universe-run")
@click.option("--discovery-run-id", required=True)
@click.option("--starting-cash", type=float, default=10000.0, show_default=True)
@click.option(
    "--preset",
    default="exploratory_trade_surface",
    type=click.Choice([
        "strict_research",
        "exploratory_trade_surface",
        "gross_violation_scan",
        "net_after_cost_scan",
        "replay_many_entries",
    ]),
    show_default=True,
)
@click.option("--run-id", required=True)
@click.option(
    "--skip-discovery/--run-discovery",
    default=False,
    show_default=True,
    help="Skip live universe discovery when the JSONL run already exists.",
)
@click.pass_context
def expanded_universe_run(
    ctx: click.Context,
    discovery_run_id: str,
    starting_cash: float,
    preset: str,
    run_id: str,
    skip_discovery: bool,
) -> None:
    """Run the expanded-universe research chain.

    RESEARCH-ONLY.  The chained commands use public/read-only ingestion and
    simulated backtests only.
    """

    settings: Settings = ctx.obj["settings"]
    final_path = _run_expanded_universe_subcommands(
        settings=settings,
        discovery_run_id=discovery_run_id,
        starting_cash=starting_cash,
        preset=preset,
        run_id=run_id,
        skip_discovery=skip_discovery,
    )
    click.echo(
        f"✓ expanded universe run complete\n"
        f"  discovery_run_id={discovery_run_id}\n"
        f"  run_id={run_id}\n"
        f"  final_report={final_path}\n"
        "  label=RESEARCH-ONLY simulated/backtested"
    )


@research_cmd.command(name="validate-deepseek-signals")
@click.option("--run-id", required=True, help="Run id; output goes to data/reports/deepseek_signal_validation/<run-id>/")
@click.option("--seed", type=int, default=7, show_default=True)
@click.option(
    "--max-candidate-pairs", type=int, default=4000, show_default=True,
    help="Cap on structural candidate pairs (top by shared-token count).",
)
@click.option(
    "--near-duplicate-jaccard", type=float, default=0.7, show_default=True,
    help="Slug-Jaccard threshold above which a pair is flagged near-duplicate.",
)
@click.option(
    "--stale-window-days", type=int, default=7, show_default=True,
    help="Days of last-movement gap needed to call one side stale.",
)
@click.option(
    "--control-sample-size", type=int, default=200, show_default=True,
    help="Cap on random same-cluster control pairs.",
)
@click.option(
    "--deepseek-jsonl", default=None, type=click.Path(exists=False, dir_okay=False),
    help="Optional path to a prior deepseek_hypotheses.jsonl to re-audit.",
)
@click.pass_context
def validate_deepseek_signals(
    ctx: click.Context,
    run_id: str,
    seed: int,
    max_candidate_pairs: int,
    near_duplicate_jaccard: float,
    stale_window_days: int,
    control_sample_size: int,
    deepseek_jsonl: str | None,
) -> None:
    """Run the DeepSeek signal-validation cycle (research-only, no live trading)."""
    from ..research.signal_validation import SignalValidationConfig, run_workflow

    settings: Settings = ctx.obj["settings"]
    cfg = SignalValidationConfig(
        run_id=run_id,
        seed=seed,
        max_candidate_pairs=max_candidate_pairs,
        near_duplicate_jaccard=near_duplicate_jaccard,
        stale_window_ms=stale_window_days * 24 * 60 * 60 * 1000,
        control_sample_size=control_sample_size,
        deepseek_jsonl=Path(deepseek_jsonl) if deepseek_jsonl else None,
    )
    report_dir = run_workflow(settings.data_root, cfg)
    click.echo(
        f"✓ deepseek signal validation complete\n"
        f"  run_id={run_id}\n"
        f"  report_dir={report_dir}\n"
        "  label=RESEARCH-ONLY simulated/backtested"
    )


def _run_expanded_universe_subcommands(
    *,
    settings: Settings,
    discovery_run_id: str,
    starting_cash: float,
    preset: str,
    run_id: str,
    skip_discovery: bool,
) -> Path:
    from ._subprocess import run_cli_subcommand

    discovery_dir = settings.data_root / "raw" / "market_universe" / discovery_run_id
    should_discover = not skip_discovery and not discovery_dir.exists()
    if should_discover:
        run_cli_subcommand([
            "ingest",
            "discover-market-universe",
            "--active",
            "--closed",
            "--lookback-days",
            "365",
            "--include-tags",
            "--include-related-tags",
            "--include-series",
            "--include-sports",
            "--include-teams",
            "--run-id",
            discovery_run_id,
        ], settings)

    steps = [
        ["ingest", "discover-spaces", "--discovery-run-id", discovery_run_id],
        [
            "backfill",
            "discovered-universe",
            "--discovery-run-id",
            discovery_run_id,
            "--semantic",
            "--prices",
        ],
        [
            "strategy",
            "context-aware",
            "backtest",
            "--preset",
            preset,
            "--starting-cash",
            str(starting_cash),
            "--run-id",
            run_id,
        ],
        [
            "strategy",
            "template-bundle",
            "backtest",
            "--preset",
            preset,
            "--starting-cash",
            str(starting_cash),
            "--run-id",
            run_id,
        ],
        ["research", "space-sweep", "--run-id", run_id],
        ["research", "final-report", "--sweep-run-id", run_id],
    ]
    for step in steps:
        run_cli_subcommand(step, settings)

    return settings.data_root.parent / "reports" / "final_strategy_research" / run_id / "final_report.md"


@research_cmd.command(name="standardised-backtest")
@click.option(
    "--run-id",
    default=None,
    help="Output directory tag.  Defaults to standardised_<utc-now>_<rand>.",
)
@click.option(
    "--smoke/--full",
    "smoke",
    default=True,
    show_default=True,
    help="Smoke runs use smaller candidate caps and tolerate Ollama being offline.",
)
@click.option(
    "--enable-rulebook-baseline/--no-rulebook-baseline",
    default=True,
    show_default=True,
)
@click.option(
    "--enable-rulebook-aggressive/--no-rulebook-aggressive",
    default=True,
    show_default=True,
)
@click.option(
    "--enable-ai-deepseek/--no-ai-deepseek",
    default=True,
    show_default=True,
)
@click.option(
    "--enable-ai-embedding/--no-ai-embedding",
    default=True,
    show_default=True,
)
@click.option(
    "--enable-closed-form-sim/--no-closed-form-sim",
    default=True,
    show_default=True,
)
@click.option(
    "--enable-strict-validation/--no-strict-validation",
    default=True,
    show_default=True,
)
@click.option(
    "--enable-control-null/--no-control-null",
    default=True,
    show_default=True,
)
@click.option(
    "--enable-diagnostic-ultra-loose/--no-diagnostic-ultra-loose",
    default=False,
    show_default=True,
)
@click.option(
    "--stake-per-leg-usdc",
    type=float,
    default=50.0,
    show_default=True,
    help="Standardised stake per leg across every replay lane (overrides preset). "
         "Set to 0 to keep each preset's own stake.",
)
@click.option(
    "--control-pairs",
    type=int,
    default=100,
    show_default=True,
    help="Random-pair count for the control lane.",
)
@click.option(
    "--infinite-cash/--finite-cash",
    default=True,
    show_default=True,
    help="Pretend cash is unlimited so trade counts aren't capped by starting balance.",
)
@click.option(
    "--reuse-deepseek-responses-from",
    type=click.Path(exists=False, dir_okay=False),
    default=None,
    help="Path to a deepseek_raw_responses.jsonl from a prior run.  When set, "
         "skips the ~20-minute Ollama hypothesis-generation step.",
)
@click.option(
    "--enable-depth-aware/--no-depth-aware",
    default=True,
    show_default=True,
    help="Re-fill each leg against recorded orderbook depth when available; "
         "fall back to price_history_only otherwise (labelled explicitly).",
)
@click.option(
    "--depth-max-snapshot-age-ms",
    type=int,
    default=24 * 60 * 60 * 1000,
    show_default=True,
    help="Max age (ms) between a leg's entry_ts and the nearest orderbook "
         "snapshot before falling back to flat-bps slippage.",
)
@click.pass_context
def standardised_backtest(
    ctx: click.Context,
    run_id: str | None,
    smoke: bool,
    enable_rulebook_baseline: bool,
    enable_rulebook_aggressive: bool,
    enable_ai_deepseek: bool,
    enable_ai_embedding: bool,
    enable_closed_form_sim: bool,
    enable_strict_validation: bool,
    enable_control_null: bool,
    enable_diagnostic_ultra_loose: bool,
    stake_per_leg_usdc: float,
    control_pairs: int,
    infinite_cash: bool,
    reuse_deepseek_responses_from: str | None,
    enable_depth_aware: bool,
    depth_max_snapshot_age_ms: int,
) -> None:
    """Run one standardised backtest across every lane and emit the report pack.

    Evidence-capture only.  No automatic promotion / demotion / kill decisions.
    All accepted trade legs land in ``data/backtests/<run_id>/standardised/standardised_trade_log.parquet``.
    """
    from ..backtest.standardised.orchestrator import (
        StandardisedBacktestConfig,
        new_run_id,
        run_standardised_backtest,
    )

    settings: Settings = ctx.obj["settings"]
    cfg = StandardisedBacktestConfig(
        run_id=run_id or new_run_id(),
        enable_rulebook_baseline=enable_rulebook_baseline,
        enable_rulebook_aggressive=enable_rulebook_aggressive,
        enable_ai_freereign_deepseek=enable_ai_deepseek,
        enable_ai_embedding_only=enable_ai_embedding,
        enable_closed_form_simulators=enable_closed_form_sim,
        enable_strict_validation=enable_strict_validation,
        enable_control_null_baseline=enable_control_null,
        enable_diagnostic_ultra_loose=enable_diagnostic_ultra_loose,
        smoke_mode=smoke,
        stake_per_leg_usdc=(stake_per_leg_usdc if stake_per_leg_usdc > 0 else None),
        control_pairs=control_pairs,
        infinite_cash=infinite_cash,
        reuse_deepseek_responses_from=reuse_deepseek_responses_from,
        enable_depth_aware_execution=enable_depth_aware,
        depth_max_snapshot_age_ms=depth_max_snapshot_age_ms,
    )
    out_dir = run_standardised_backtest(settings, cfg)
    click.echo(
        f"✓ standardised backtest complete\n"
        f"  run_id={cfg.run_id}\n"
        f"  output_dir={out_dir}\n"
        "  evidence capture only — review fields default to `unreviewed`."
    )
