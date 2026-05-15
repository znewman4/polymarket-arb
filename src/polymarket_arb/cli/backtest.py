"""``polymarket-arb backtest ...`` - offline replay over local data."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..backtest.datasets import DatasetMissingError
from ..backtest.replay_engine import default_config, run_backtest
from ..settings import Settings


@click.group(name="backtest")
def backtest_cmd() -> None:
    """Offline research replay. No live orders, no network calls."""


@backtest_cmd.command(name="run")
@click.option("--strategy", type=click.Choice(["score-threshold"]), default="score-threshold")
@click.option("--threshold", type=float, default=0.8)
@click.option("--from", "from_ts", type=int, default=None)
@click.option("--to", "to_ts", type=int, default=None)
@click.pass_context
def run(ctx: click.Context, strategy: str, threshold: float, from_ts: int | None, to_ts: int | None) -> None:
    settings: Settings = ctx.obj["settings"]
    config = default_config(strategy_name=strategy, threshold=threshold,
                            start_ts_ms=from_ts, end_ts_ms=to_ts)
    try:
        result = run_backtest(settings.data_root, config)
    except DatasetMissingError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"✓ backtest completed run_id={result['run_id']}")
    click.echo(f"output {result['output_dir']}")


@backtest_cmd.command(name="list")
@click.pass_context
def list_runs(ctx: click.Context) -> None:
    settings: Settings = ctx.obj["settings"]
    root = settings.data_root / "backtests"
    runs = sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []
    if not runs:
        click.echo("(no backtests)")
        return
    for path in runs:
        click.echo(path.name)


@backtest_cmd.command(name="show")
@click.argument("backtest_run_id")
@click.pass_context
def show(ctx: click.Context, backtest_run_id: str) -> None:
    settings: Settings = ctx.obj["settings"]
    path = _run_dir(settings, backtest_run_id) / "config.json"
    if not path.exists():
        raise click.ClickException(f"backtest {backtest_run_id!r} not found")
    click.echo(path.read_text(encoding="utf-8"))


@backtest_cmd.command(name="metrics")
@click.argument("backtest_run_id")
@click.pass_context
def metrics(ctx: click.Context, backtest_run_id: str) -> None:
    settings: Settings = ctx.obj["settings"]
    path = _run_dir(settings, backtest_run_id) / "metrics.json"
    if not path.exists():
        raise click.ClickException(f"metrics for {backtest_run_id!r} not found")
    click.echo(json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=2, sort_keys=True))


def _run_dir(settings: Settings, run_id: str) -> Path:
    return settings.data_root / "backtests" / run_id
