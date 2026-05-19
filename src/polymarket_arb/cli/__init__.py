"""Click CLI entrypoint.

The CLI is structured as subgroups (``gamma``, ``nlp``, ``clob``, ``score``)
which are the canonical command surface. The flat command names from the
user-facing spec are kept as aliases via ``cli/_aliases.py`` — each alias
delegates to the same underlying callback as its canonical form.
"""

from __future__ import annotations

import click

from ..logging_setup import configure_logging
from ..monitoring import kill_switch
from ..settings import load_settings
from . import (
    _aliases,
    backfill,
    backtest,
    clob,
    context,
    diagnostic,
    gamma,
    healthcheck,
    ingest,
    inspect,
    live,
    nlp,
    pipeline,
    record,
    relationships,
    report,
    research,
    score,
    strategy,
)


@click.group()
@click.option("--env", default=None, help="Override settings env (default: POLYMARKET_ARB_ENV).")
@click.pass_context
def cli(ctx: click.Context, env: str | None) -> None:
    """polymarket-arb — research, replay, and (later) execution platform."""

    settings = load_settings(env=env)
    configure_logging(settings)
    kill_switch.install_signal_handler()
    ctx.ensure_object(dict)
    ctx.obj["settings"] = settings


# Subgroups (canonical surface)
cli.add_command(healthcheck.healthcheck)
cli.add_command(gamma.gamma)
cli.add_command(ingest.ingest_cmd)
cli.add_command(nlp.nlp)
cli.add_command(clob.clob)
cli.add_command(context.context_cmd)
cli.add_command(score.score)
cli.add_command(inspect.inspect_cmd)
cli.add_command(record.record_cmd)
cli.add_command(backtest.backtest_cmd)
cli.add_command(backfill.backfill_cmd)
cli.add_command(report.report_cmd)
cli.add_command(relationships.relationships_cmd)
cli.add_command(strategy.strategy_cmd)
cli.add_command(pipeline.pipeline_cmd)
cli.add_command(diagnostic.diagnostic_cmd)
cli.add_command(research.research_cmd)
cli.add_command(live.live_cmd)


# Flat aliases for compatibility with the user's spec; same callbacks.
_aliases.register(cli)


def main() -> None:  # pragma: no cover
    cli(prog_name="polymarket-arb")


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["cli", "main"]
