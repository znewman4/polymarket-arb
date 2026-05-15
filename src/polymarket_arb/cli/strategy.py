"""``polymarket-arb strategy ...`` — simulated nesting/contradiction strategy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import click

from ..settings import Settings

if TYPE_CHECKING:
    from ..strategies.models import ContextAwareBacktestConfig


@click.group(name="strategy")
def strategy_cmd() -> None:
    """Simulated relationship-strategy backtesting (research only, no live execution)."""


@strategy_cmd.group(name="nesting-contradiction")
def nesting_contradiction_group() -> None:
    """Nesting/contradiction/inverse strategy commands."""


@strategy_cmd.group(name="category-bundle")
def category_bundle_group() -> None:
    """N-way category bundle scan/backtest commands."""


@strategy_cmd.group(name="mutual-exclusion")
def mutual_exclusion_group() -> None:
    """Pairwise mutual-exclusion strategy commands."""


@strategy_cmd.group(name="nesting")
def nesting_group() -> None:
    """Nesting strategy commands."""


@strategy_cmd.group(name="context-aware")
def context_aware_group() -> None:
    """Evidence-backed context-aware strategy commands."""


@nesting_contradiction_group.command(name="candidates")
@click.option("--run-id", default=None, help="Override run ID.")
@click.pass_context
def strategy_candidates(ctx: click.Context, run_id: str | None) -> None:
    """Generate StrategyCandidateRows without running the full replay."""
    settings: Settings = ctx.obj["settings"]

    from ..storage.parquet.relationship_candidates_repo import (
        ParquetRelationshipCandidatesRepository,
    )

    repo = ParquetRelationshipCandidatesRepository(settings.data_root)
    accepted = list(repo.iter_accepted())
    click.echo(f"Found {len(accepted)} accepted relationships eligible for strategy.")


@nesting_contradiction_group.command(name="backtest")
@click.option("--starting-cash", type=float, default=10000.0, show_default=True)
@click.option("--fee-bps", type=int, default=0, show_default=True)
@click.option("--slippage-bps", type=int, default=50, show_default=True)
@click.option("--min-net-edge", type=float, default=0.01, show_default=True)
@click.option("--execution-model", default="price_history_only", show_default=True,
              type=click.Choice(["price_history_only", "spread_model", "recorded_depth", "mark_only"]))
@click.option("--start", "start_date", default=None, help="Start date YYYY-MM-DD.")
@click.option("--end", "end_date", default=None, help="End date YYYY-MM-DD.")
@click.option("--include-manual-review/--no-include-manual-review", default=False)
@click.option("--run-id", default=None)
@click.pass_context
def strategy_backtest(
    ctx: click.Context,
    starting_cash: float,
    fee_bps: int,
    slippage_bps: int,
    min_net_edge: float,
    execution_model: str,
    start_date: str | None,
    end_date: str | None,
    include_manual_review: bool,
    run_id: str | None,
) -> None:
    """Run simulated nesting/contradiction strategy backtest."""
    import uuid
    from decimal import Decimal

    from ..backtest.relationship_replay import run_relationship_backtest
    from ..strategies.models import RelationshipBacktestConfig

    execution_model_value = cast(
        Literal["price_history_only", "spread_model", "recorded_depth", "mark_only"],
        execution_model,
    )

    settings: Settings = ctx.obj["settings"]

    start_ts_ms = None
    end_ts_ms = None
    if start_date:
        from datetime import datetime, timezone
        start_ts_ms = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    if end_date:
        from datetime import datetime, timezone
        end_ts_ms = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

    cfg = RelationshipBacktestConfig(
        run_id=run_id or uuid.uuid4().hex,
        starting_cash_usdc=Decimal(str(starting_cash)),
        fee_bps=Decimal(str(fee_bps)),
        slippage_bps=Decimal(str(slippage_bps)),
        min_net_edge=min_net_edge,
        execution_model=execution_model_value,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        allow_manual_review_relationships=include_manual_review,
    )

    result = run_relationship_backtest(settings.data_root, cfg)
    m = result["metrics"]
    click.echo(
        f"✓ backtest run_id={result['run_id']}\n"
        f"  trades_executed={m.trades_executed}\n"
        f"  net_pnl={float(m.net_pnl_usdc):.2f} USDC\n"
        f"  total_return={m.total_return_pct:.2f}%\n"
        f"  max_drawdown={m.max_drawdown_pct:.1f}%\n"
        f"  credibility={m.credibility_label}\n"
        f"  output: {result['output_dir']}"
    )


@nesting_contradiction_group.command(name="null-baseline")
@click.option("--reference-run-id", required=True, help="Run ID of the strategy backtest to compare against.")
@click.option("--seed", type=int, default=0, show_default=True)
@click.pass_context
def strategy_null_baseline(ctx: click.Context, reference_run_id: str, seed: int) -> None:
    """Run random-pair null baseline and compare to strategy run."""
    from ..backtest.null_baseline import run_null_baseline
    from ..strategies.models import RelationshipBacktestConfig

    settings: Settings = ctx.obj["settings"]
    cfg = RelationshipBacktestConfig(run_id=reference_run_id)
    result = run_null_baseline(settings.data_root, cfg, seed=seed)
    m = result.get("metrics")
    if m:
        click.echo(f"✓ null baseline: pnl={float(m.net_pnl_usdc):.2f} USDC trades={m.trades_executed}")
    else:
        click.echo(f"✓ null baseline: {result.get('message', 'done')}")


@nesting_contradiction_group.command(name="sensitivity")
@click.option("--grid", default="full", type=click.Choice(["full", "slim"]), show_default=True)
@click.option("--run-id", default=None)
@click.pass_context
def strategy_sensitivity(ctx: click.Context, grid: str, run_id: str | None) -> None:
    """Run the sensitivity grid (192-cell full or 16-cell slim)."""
    import uuid

    from ..backtest.sensitivity import run_sensitivity_grid
    from ..strategies.models import RelationshipBacktestConfig

    settings: Settings = ctx.obj["settings"]
    cfg = RelationshipBacktestConfig(run_id=run_id or uuid.uuid4().hex)
    results = run_sensitivity_grid(settings.data_root, cfg, slim=(grid == "slim"))
    click.echo(f"✓ sensitivity grid: {len(results)} cells run")
    pos = sum(1 for r in results if r.get("net_pnl_usdc", -1) > 0)
    click.echo(f"  {pos}/{len(results)} cells with positive PnL")


@nesting_contradiction_group.command(name="report")
@click.argument("run_id")
@click.option("--output", "output_path", default=None, type=click.Path())
@click.pass_context
def strategy_report(ctx: click.Context, run_id: str, output_path: str | None) -> None:
    """Generate the strategy backtest HTML report."""
    from ..reports.strategy_backtest_report import generate_strategy_backtest_report

    settings: Settings = ctx.obj["settings"]
    out = Path(output_path) if output_path else None
    path = generate_strategy_backtest_report(settings.data_root, run_id=run_id, output_dir=out)
    click.echo(f"✓ strategy report written to: {path}")


@strategy_cmd.command(name="report")
@click.argument("run_id")
@click.option("--output", "output_path", default=None, type=click.Path())
@click.pass_context
def strategy_family_report(ctx: click.Context, run_id: str, output_path: str | None) -> None:
    """Generate the combined family strategy HTML report."""
    from ..reports.strategy_backtest_report import generate_strategy_backtest_report

    settings: Settings = ctx.obj["settings"]
    out = Path(output_path) if output_path else None
    path = generate_strategy_backtest_report(settings.data_root, run_id=run_id, output_dir=out)
    click.echo(f"✓ strategy report written to: {path}")


def _run_family_backtest(
    ctx: click.Context,
    *,
    strategy_name: str,
    relationship_types: list[str],
    starting_cash: float,
    fee_bps: int,
    slippage_bps: int,
    min_net_edge: float,
    run_id: str | None,
) -> None:
    import uuid
    from decimal import Decimal

    from ..backtest.relationship_replay import run_relationship_backtest
    from ..strategies.models import RelationshipBacktestConfig

    settings: Settings = ctx.obj["settings"]
    cfg = RelationshipBacktestConfig(
        run_id=run_id or uuid.uuid4().hex,
        strategy_name=strategy_name,
        relationship_types=relationship_types,
        starting_cash_usdc=Decimal(str(starting_cash)),
        fee_bps=Decimal(str(fee_bps)),
        slippage_bps=Decimal(str(slippage_bps)),
        min_net_edge=min_net_edge,
    )
    result = run_relationship_backtest(settings.data_root, cfg)
    m = result["metrics"]
    click.echo(
        f"✓ {strategy_name} backtest run_id={result['run_id']}\n"
        f"  trades_executed={m.trades_executed}\n"
        f"  net_pnl={float(m.net_pnl_usdc):.2f} USDC\n"
        f"  credibility={m.credibility_label}\n"
        f"  output: {result['output_dir']}"
    )


@mutual_exclusion_group.command(name="backtest")
@click.option("--starting-cash", type=float, default=10000.0, show_default=True)
@click.option("--fee-bps", type=int, default=0, show_default=True)
@click.option("--slippage-bps", type=int, default=50, show_default=True)
@click.option("--min-net-edge", type=float, default=0.01, show_default=True)
@click.option("--run-id", default=None)
@click.pass_context
def mutual_exclusion_backtest(
    ctx: click.Context,
    starting_cash: float,
    fee_bps: int,
    slippage_bps: int,
    min_net_edge: float,
    run_id: str | None,
) -> None:
    """Run pairwise mutual-exclusion and inverse-pair backtest."""
    _run_family_backtest(
        ctx,
        strategy_name="mutual_exclusion",
        relationship_types=["mutually_exclusive", "mutually_exclusive_category", "inverse"],
        starting_cash=starting_cash,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        min_net_edge=min_net_edge,
        run_id=run_id,
    )


@nesting_group.command(name="backtest")
@click.option("--starting-cash", type=float, default=10000.0, show_default=True)
@click.option("--fee-bps", type=int, default=0, show_default=True)
@click.option("--slippage-bps", type=int, default=50, show_default=True)
@click.option("--min-net-edge", type=float, default=0.01, show_default=True)
@click.option("--run-id", default=None)
@click.pass_context
def nesting_backtest(
    ctx: click.Context,
    starting_cash: float,
    fee_bps: int,
    slippage_bps: int,
    min_net_edge: float,
    run_id: str | None,
) -> None:
    """Run nesting-only backtest."""
    _run_family_backtest(
        ctx,
        strategy_name="nesting",
        relationship_types=["nested_a_implies_b", "nested_b_implies_a"],
        starting_cash=starting_cash,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        min_net_edge=min_net_edge,
        run_id=run_id,
    )


@category_bundle_group.command(name="scan")
@click.option("--metadata", "metadata_path", default="configs/outcome_spaces/outcome_spaces_v1.yaml", show_default=True)
@click.option("--fee-bps", type=int, default=0, show_default=True)
@click.option("--slippage-bps", type=int, default=50, show_default=True)
@click.option("--min-net-edge", type=float, default=0.01, show_default=True)
@click.option("--allow-uncertain-bundles/--no-allow-uncertain-bundles", default=False)
@click.option("--run-id", default=None)
@click.pass_context
def category_bundle_scan(
    ctx: click.Context,
    metadata_path: str,
    fee_bps: int,
    slippage_bps: int,
    min_net_edge: float,
    allow_uncertain_bundles: bool,
    run_id: str | None,
) -> None:
    """Scan latest prices for category bundle opportunities."""
    import uuid
    from decimal import Decimal

    from ..backtest.category_bundle_replay import run_category_bundle_scan
    from ..strategies.models import CategoryBundleBacktestConfig

    settings: Settings = ctx.obj["settings"]
    cfg = CategoryBundleBacktestConfig(
        run_id=run_id or uuid.uuid4().hex,
        metadata_path=metadata_path,
        fee_bps=Decimal(str(fee_bps)),
        slippage_bps=Decimal(str(slippage_bps)),
        min_net_edge=Decimal(str(min_net_edge)),
        allow_uncertain_bundles=allow_uncertain_bundles,
    )
    result = run_category_bundle_scan(settings.data_root, cfg)
    m = result["metrics"]
    click.echo(
        f"✓ category bundle scan run_id={result['run_id']}\n"
        f"  outcome_spaces_scanned={m['outcome_spaces_scanned']}\n"
        f"  complete_outcome_spaces={m['complete_outcome_spaces']}\n"
        f"  opportunities_found={m['opportunities_found']}\n"
        f"  output: {result['output_dir']}"
    )


@category_bundle_group.command(name="backtest")
@click.option("--starting-cash", type=float, default=10000.0, show_default=True)
@click.option("--metadata", "metadata_path", default="configs/outcome_spaces/outcome_spaces_v1.yaml", show_default=True)
@click.option("--fee-bps", type=int, default=0, show_default=True)
@click.option("--slippage-bps", type=int, default=50, show_default=True)
@click.option("--min-net-edge", type=float, default=0.01, show_default=True)
@click.option("--max-stake-per-bundle", type=float, default=1000.0, show_default=True)
@click.option("--start", "start_date", default=None, help="Start date YYYY-MM-DD.")
@click.option("--end", "end_date", default=None, help="End date YYYY-MM-DD.")
@click.option("--allow-uncertain-bundles/--no-allow-uncertain-bundles", default=False)
@click.option("--run-id", default=None)
@click.pass_context
def category_bundle_backtest(
    ctx: click.Context,
    starting_cash: float,
    metadata_path: str,
    fee_bps: int,
    slippage_bps: int,
    min_net_edge: float,
    max_stake_per_bundle: float,
    start_date: str | None,
    end_date: str | None,
    allow_uncertain_bundles: bool,
    run_id: str | None,
) -> None:
    """Run conservative simulated N-way category bundle backtest."""
    import uuid
    from datetime import datetime, timezone
    from decimal import Decimal

    from ..backtest.category_bundle_replay import run_category_bundle_backtest
    from ..strategies.models import CategoryBundleBacktestConfig

    start_ts_ms = None
    end_ts_ms = None
    if start_date:
        start_ts_ms = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    if end_date:
        end_ts_ms = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

    settings: Settings = ctx.obj["settings"]
    cfg = CategoryBundleBacktestConfig(
        run_id=run_id or uuid.uuid4().hex,
        starting_cash_usdc=Decimal(str(starting_cash)),
        metadata_path=metadata_path,
        fee_bps=Decimal(str(fee_bps)),
        slippage_bps=Decimal(str(slippage_bps)),
        min_net_edge=Decimal(str(min_net_edge)),
        max_stake_per_bundle_usdc=Decimal(str(max_stake_per_bundle)),
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        allow_uncertain_bundles=allow_uncertain_bundles,
    )
    result = run_category_bundle_backtest(settings.data_root, cfg)
    m = result["metrics"]
    click.echo(
        f"✓ category bundle backtest run_id={result['run_id']}\n"
        f"  outcome_spaces_scanned={m['outcome_spaces_scanned']}\n"
        f"  complete_outcome_spaces={m['complete_outcome_spaces']}\n"
        f"  trades_executed={m['trades_executed']}\n"
        f"  net_pnl={float(m['net_pnl_usdc']):.2f} USDC\n"
        f"  credibility={m['credibility_label']}\n"
        f"  output: {result['output_dir']}"
    )


@category_bundle_group.command(name="report")
@click.argument("run_id")
@click.option("--output", "output_path", default=None, type=click.Path())
@click.pass_context
def category_bundle_report(ctx: click.Context, run_id: str, output_path: str | None) -> None:
    """Generate the category bundle HTML report."""
    from ..reports.category_bundle_report import generate_category_bundle_report

    settings: Settings = ctx.obj["settings"]
    out = Path(output_path) if output_path else None
    path = generate_category_bundle_report(settings.data_root, run_id=run_id, output_dir=out)
    click.echo(f"✓ category bundle report written to: {path}")


@context_aware_group.command(name="backtest")
@click.option("--starting-cash", type=float, default=10000.0, show_default=True)
@click.option("--fee-bps", type=int, default=0, show_default=True)
@click.option("--slippage-bps", type=int, default=50, show_default=True)
@click.option("--min-net-edge", type=float, default=0.01, show_default=True)
@click.option("--min-relationship-confidence", type=float, default=0.65, show_default=True)
@click.option(
    "--lane",
    default="strict_context_valid",
    type=click.Choice(
        [
            "strict_context_valid",
            "reviewed_context_valid",
            "exploratory_context_unreviewed",
            "exploratory_context_auto_approved",
            "all_context_research",
        ]
    ),
    show_default=True,
)
@click.option(
    "--context-time-mode",
    default="ex_post_research",
    type=click.Choice(["ex_post_research", "historical_replay_safe"]),
    show_default=True,
)
@click.option(
    "--relationship-universe",
    default="reviewed_lanes",
    type=click.Choice(["accepted_only", "all_with_context_decisions", "reviewed_lanes"]),
    show_default=True,
    help="Relationship universe for context-aware replay.",
)
@click.option(
    "--include-auto-approved/--no-include-auto-approved",
    default=False,
    show_default=True,
    help="Also evaluate exploratory_context_auto_approved relationships. "
         "Results are capped at inconclusive; never headline credible.",
)
@click.option("--diagnostic-bypass-review", is_flag=True, default=False,
              help="[DIAGNOSTIC] Bypass lane/eligibility checks. Forces diagnostic_only_not_credible.")
@click.option("--diagnostic-ignore-coverage", is_flag=True, default=False,
              help="[DIAGNOSTIC] Skip coverage score threshold. Forces diagnostic_only_not_credible.")
@click.option("--diagnostic-allow-unresolved", is_flag=True, default=False,
              help="[DIAGNOSTIC] Allow unresolved markets. Forces diagnostic_only_not_credible.")
@click.option("--min-combined-prob", type=float, default=0.0, show_default=True,
              help="Pair viability: min P(A)+P(B) across price history for sum-based types.")
@click.option("--min-single-prob", type=float, default=0.0, show_default=True,
              help="Pair viability: min P(A) or P(B) across price history for sum-based types.")
@click.option("--run-id", default=None)
@click.pass_context
def context_aware_backtest(
    ctx: click.Context,
    starting_cash: float,
    fee_bps: int,
    slippage_bps: int,
    min_net_edge: float,
    min_relationship_confidence: float,
    lane: str,
    context_time_mode: str,
    relationship_universe: str,
    include_auto_approved: bool,
    diagnostic_bypass_review: bool,
    diagnostic_ignore_coverage: bool,
    diagnostic_allow_unresolved: bool,
    min_combined_prob: float,
    min_single_prob: float,
    run_id: str | None,
) -> None:
    """Run a context-aware simulated backtest for one strategy lane.

    When any --diagnostic-* flag is set the run uses DiagnosticBacktestConfig
    and is labelled diagnostic_only_not_credible. Never eligible for credible_positive.
    """
    import uuid
    from decimal import Decimal

    settings: Settings = ctx.obj["settings"]
    _run_id = run_id or uuid.uuid4().hex
    is_diagnostic = diagnostic_bypass_review or diagnostic_ignore_coverage or diagnostic_allow_unresolved

    if is_diagnostic:
        import click as _click
        _click.echo(
            "  WARNING: diagnostic flags active — result labelled diagnostic_only_not_credible.",
            err=True,
        )
        from ..backtest.diagnostic_replay import run_diagnostic_backtest
        from ..strategies.models import DiagnosticBacktestConfig

        diag_cfg = DiagnosticBacktestConfig(
            run_id=_run_id,
            lane=cast(
                Literal[
                    "strict_context_valid",
                    "reviewed_context_valid",
                    "exploratory_context_unreviewed",
                    "exploratory_context_auto_approved",
                    "all_context_research",
                ],
                lane,
            ),
            bypass_review_lane_checks=diagnostic_bypass_review,
            bypass_coverage_threshold=diagnostic_ignore_coverage,
            allow_unresolved_markets=diagnostic_allow_unresolved,
            relationship_universe=cast(
                Literal["accepted_only", "all_with_context_decisions", "reviewed_lanes"],
                relationship_universe,
            ),
            bypass_min_confidence=(min_relationship_confidence == 0.0),
            min_relationship_confidence=min_relationship_confidence,
            starting_cash_usdc=Decimal(str(starting_cash)),
            fee_bps=Decimal(str(fee_bps)),
            slippage_bps=Decimal(str(slippage_bps)),
            min_net_edge=min_net_edge,
            include_auto_approved=include_auto_approved,
            min_combined_prob_for_pairwise=min_combined_prob,
            min_single_prob_for_pairwise=min_single_prob,
        )
        result = run_diagnostic_backtest(settings.data_root, diag_cfg)
        m = result["metrics"]
        click.echo(
            f"✓ diagnostic backtest run_id={result['run_id']}\n"
            f"  credibility={m['credibility_label']}\n"
            f"  positions_opened={m['positions_opened']}\n"
            f"  net_pnl={float(m['net_pnl_usdc']):.2f} USDC\n"
            f"  output: {result['output_dir']}"
        )
    else:
        from ..backtest.context_aware_replay import run_context_aware_backtest
        from ..strategies.models import ContextAwareBacktestConfig

        context_cfg = ContextAwareBacktestConfig(
            run_id=_run_id,
            lane=cast(
                Literal[
                    "strict_context_valid",
                    "reviewed_context_valid",
                    "exploratory_context_unreviewed",
                    "exploratory_context_auto_approved",
                    "all_context_research",
                ],
                lane,
            ),
            context_time_mode=cast(Literal["ex_post_research", "historical_replay_safe"], context_time_mode),
            relationship_universe=cast(
                Literal["accepted_only", "all_with_context_decisions", "reviewed_lanes"],
                relationship_universe,
            ),
            starting_cash_usdc=Decimal(str(starting_cash)),
            fee_bps=Decimal(str(fee_bps)),
            slippage_bps=Decimal(str(slippage_bps)),
            min_net_edge=min_net_edge,
            min_relationship_confidence=min_relationship_confidence,
            include_auto_approved=include_auto_approved,
            min_combined_prob_for_pairwise=min_combined_prob,
            min_single_prob_for_pairwise=min_single_prob,
        )
        result = run_context_aware_backtest(settings.data_root, context_cfg)
        m = result["metrics"]
        if include_auto_approved:
            click.echo("  NOTE: --include-auto-approved active — results are EXPLORATORY ONLY.")
        click.echo(
            f"✓ context-aware backtest run_id={result['run_id']} lane={lane}\n"
            f"  trades_executed={m['trades_executed']}\n"
            f"  net_pnl={float(m['net_pnl_usdc']):.2f} USDC\n"
            f"  credibility={m['credibility_label']}\n"
            f"  output: {result['output_dir']}"
        )


@context_aware_group.command(name="null-baseline")
@click.option("--run-id", required=True)
@click.pass_context
def context_aware_null_baseline(ctx: click.Context, run_id: str) -> None:
    """Write the context-aware null baseline artifact."""
    from ..backtest.context_aware_replay import run_context_null_baseline

    settings: Settings = ctx.obj["settings"]
    result = run_context_null_baseline(settings.data_root, run_id=run_id)
    metrics = result["metrics"]
    click.echo(
        f"✓ context-aware null baseline run_id={run_id}\n"
        f"  net_pnl={float(metrics['net_pnl_usdc']):.2f} USDC\n"
        f"  output: {result['output_dir']}"
    )


@context_aware_group.command(name="sensitivity")
@click.option("--grid", default="full", type=click.Choice(["full", "slim"]), show_default=True)
@click.option("--run-id", required=True)
@click.option(
    "--lane",
    default=None,
    type=click.Choice([
        "strict_context_valid",
        "reviewed_context_valid",
        "exploratory_context_unreviewed",
        "exploratory_context_auto_approved",
    ]),
    show_default=False,
    help="Override the saved run lane. Defaults to the run's saved lane when available.",
)
@click.pass_context
def context_aware_sensitivity(ctx: click.Context, grid: str, run_id: str, lane: str | None) -> None:
    """Run the context-aware sensitivity grid for a lane."""
    from ..backtest.context_aware_replay import run_context_sensitivity_grid

    settings: Settings = ctx.obj["settings"]
    cfg = _load_context_aware_run_config(settings.data_root, run_id, lane)
    results = run_context_sensitivity_grid(settings.data_root, cfg, grid=grid)
    positive = sum(1 for row in results if float(row.get("net_pnl_usdc", -1) or -1) > 0)
    click.echo(f"✓ context-aware sensitivity: {len(results)} cells, {positive} positive")


def _load_context_aware_run_config(
    data_root: Path,
    run_id: str,
    lane: str | None,
) -> ContextAwareBacktestConfig:
    from ..strategies.models import ContextAwareBacktestConfig

    context_dir = data_root / "backtests" / run_id / "context_aware"
    candidate_paths: list[Path] = []
    if lane:
        candidate_paths.append(context_dir / lane / "config.json")
    else:
        preferred = [
            "reviewed_context_valid",
            "strict_context_valid",
            "exploratory_context_unreviewed",
            "exploratory_context_auto_approved",
        ]
        candidate_paths.extend(context_dir / item / "config.json" for item in preferred)
        if context_dir.exists():
            candidate_paths.extend(sorted(context_dir.glob("*/config.json")))

    for path in candidate_paths:
        if not path.exists():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        if lane:
            raw["lane"] = lane
        raw["run_id"] = run_id
        return ContextAwareBacktestConfig.model_validate(raw)

    fallback_lane = lane or "reviewed_context_valid"
    return ContextAwareBacktestConfig(
        run_id=run_id,
        lane=cast(
            Literal[
                "strict_context_valid",
                "reviewed_context_valid",
                "exploratory_context_unreviewed",
                "exploratory_context_auto_approved",
            ],
            fallback_lane,
        ),
    )


@context_aware_group.command(name="report")
@click.argument("run_id")
@click.option("--output", "output_path", default=None, type=click.Path())
@click.pass_context
def context_aware_report(ctx: click.Context, run_id: str, output_path: str | None) -> None:
    """Generate the context-aware strategy HTML report."""
    from ..reports.context_strategy_backtest_report import generate_context_strategy_backtest_report

    settings: Settings = ctx.obj["settings"]
    out = Path(output_path) if output_path else None
    path = generate_context_strategy_backtest_report(settings.data_root, run_id=run_id, output_dir=out)
    click.echo(f"✓ context-aware strategy report written to: {path}")


@strategy_cmd.group(name="template-bundle")
def template_bundle_group() -> None:
    """N-way bundle scanner for template-approved mutual exclusion spaces (research-only)."""


@template_bundle_group.command(name="scan")
@click.option("--fee-bps", type=int, default=0, show_default=True)
@click.option("--slippage-bps", type=int, default=50, show_default=True)
@click.option("--min-net-edge", type=float, default=0.01, show_default=True)
@click.option("--run-id", default=None)
@click.pass_context
def template_bundle_scan(
    ctx: click.Context,
    fee_bps: int,
    slippage_bps: int,
    min_net_edge: float,
    run_id: str | None,
) -> None:
    """Latest-price N-way bundle scan over all template-approved mutual exclusion spaces.

    Groups template-approved pairs by outcome_space_id and checks whether the
    sum of YES prices creates an arbitrage opportunity.

    RESEARCH-ONLY / auto_applied_pending_human_review.
    """
    import uuid
    from decimal import Decimal

    from ..backtest.template_bundle_replay import run_template_bundle_scan
    from ..strategies.models import CategoryBundleBacktestConfig

    settings: Settings = ctx.obj["settings"]
    cfg = CategoryBundleBacktestConfig(
        run_id=run_id or uuid.uuid4().hex,
        strategy_name="template_bundle_scan",
        fee_bps=Decimal(str(fee_bps)),
        slippage_bps=Decimal(str(slippage_bps)),
        min_net_edge=Decimal(str(min_net_edge)),
    )
    result = run_template_bundle_scan(settings.data_root, cfg)
    m = result["metrics"]
    rows = result["scan_rows"]
    click.echo(
        f"✓ template bundle scan run_id={result['run_id']}\n"
        f"  spaces_scanned={m['outcome_spaces_scanned']}\n"
        f"  template_relationships={m['template_relationships_loaded']}\n"
        f"  opportunities_found={m['opportunities_found']}\n"
        f"  label={m['label']}\n"
        f"  output: {result['output_dir']}"
    )
    if rows:
        click.echo("\n  Bundle results:")
        for r in sorted(rows, key=lambda x: str(x.get("sum_yes_prices", "1")))[:10]:
            click.echo(
                f"    {r['outcome_space_id']:<40}"
                f"  markets={r.get('candidate_count_observed','?'):>3}"
                f"  sum_yes={r.get('sum_yes_prices','—')!s:>8}"
                f"  basket={r.get('best_executable_basket','—')}"
                f"  edge={r.get('gross_edge','—')}"
            )


@template_bundle_group.command(name="backtest")
@click.option("--starting-cash", type=float, default=10000.0, show_default=True)
@click.option("--fee-bps", type=int, default=0, show_default=True)
@click.option("--slippage-bps", type=int, default=50, show_default=True)
@click.option("--min-net-edge", type=float, default=0.01, show_default=True)
@click.option("--max-stake-per-bundle", type=float, default=1000.0, show_default=True)
@click.option("--run-id", default=None)
@click.pass_context
def template_bundle_backtest(
    ctx: click.Context,
    starting_cash: float,
    fee_bps: int,
    slippage_bps: int,
    min_net_edge: float,
    max_stake_per_bundle: float,
    run_id: str | None,
) -> None:
    """Replay template-bundle opportunities over historical price data.

    RESEARCH-ONLY / auto_applied_pending_human_review. All results require
    human review of template auto-approvals before any credibility upgrade.
    """
    import uuid
    from decimal import Decimal

    from ..backtest.template_bundle_replay import run_template_bundle_backtest
    from ..strategies.models import CategoryBundleBacktestConfig

    settings: Settings = ctx.obj["settings"]
    cfg = CategoryBundleBacktestConfig(
        run_id=run_id or uuid.uuid4().hex,
        strategy_name="template_bundle_backtest",
        starting_cash_usdc=Decimal(str(starting_cash)),
        fee_bps=Decimal(str(fee_bps)),
        slippage_bps=Decimal(str(slippage_bps)),
        min_net_edge=Decimal(str(min_net_edge)),
        max_stake_per_bundle_usdc=Decimal(str(max_stake_per_bundle)),
    )
    result = run_template_bundle_backtest(settings.data_root, cfg)
    m = result["metrics"]
    f = result["funnel"]
    click.echo(
        f"✓ template bundle backtest run_id={result['run_id']}\n"
        f"  spaces_scanned={m['outcome_spaces_scanned']}\n"
        f"  spaces_with_price_history={m['spaces_with_price_history']}\n"
        f"  ticks_evaluated={m['ticks_evaluated']}\n"
        f"  gross_violations={m['gross_violations']}\n"
        f"  net_violations={m['net_violations']}\n"
        f"  bundles_executed={m['bundles_executed']}\n"
        f"  net_pnl={float(m['net_pnl_usdc']):.2f} USDC\n"
        f"  credibility={m['credibility_label']}\n"
        f"  output: {result['output_dir']}"
    )
