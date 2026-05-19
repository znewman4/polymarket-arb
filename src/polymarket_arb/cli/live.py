"""CLI: ``polymarket-arb live`` — paper / live agent commands.

Subcommands:
    live healthcheck — print agent readiness (kill switch, paper_mode, etc).
    live agent       — run the agent loop, polling the lake for orderbooks
                       and routing strategy intents through OrderClient.
"""

from __future__ import annotations

import json

import click

from ..live.agent_loop import AgentState, run_agent_loop
from ..live.order_client import healthcheck as agent_healthcheck
from ..risk.models import OrderIntent
from ..settings import Settings


@click.group(name="live")
def live_cmd() -> None:
    """Paper / live agent commands.  Paper mode is the safe default."""


@live_cmd.command(name="healthcheck")
@click.pass_context
def live_healthcheck(ctx: click.Context) -> None:
    """Print agent readiness flags.  Does not place any order."""
    settings: Settings = ctx.obj["settings"]
    info = agent_healthcheck(settings)
    click.echo(json.dumps(info, indent=2, sort_keys=True))


@live_cmd.command(name="agent")
@click.option(
    "--watched-tokens", "watched_tokens_str", required=True,
    help="Comma-separated token_id list to poll the lake for.",
)
@click.option(
    "--max-iterations", type=int, default=None,
    help="Stop after N iterations (default: run until kill switch).",
)
@click.option(
    "--poll-interval-s", type=int, default=None,
    help="Override settings.agent_poll_interval_s for this run.",
)
@click.option(
    "--strategy",
    type=click.Choice(["noop"]),
    default="noop",
    show_default=True,
    help="Built-in strategy to evaluate each tick.  'noop' fires no intents — "
         "use it to verify the loop, kill switch, and book-lookup paths.",
)
@click.pass_context
def live_agent(
    ctx: click.Context,
    watched_tokens_str: str,
    max_iterations: int | None,
    poll_interval_s: int | None,
    strategy: str,
) -> None:
    """Run the paper/live agent loop.

    The agent polls the lake for the latest orderbook of each watched token,
    evaluates the chosen strategy, and routes any resulting OrderIntents
    through OrderClient (which honours paper_mode + kill switch + compliance
    gate).  Every intent — including rejections — is written to ``orders_log``.

    Paper mode is the default.  Flipping to live trading requires BOTH
    ``settings.paper_mode=False`` AND ``settings.orders_allowed=True`` AND a
    configured Polymarket signing key.  The signing path is a stub today.
    """
    settings: Settings = ctx.obj["settings"]
    if max_iterations is not None:
        settings = settings.model_copy(update={"agent_max_iterations": max_iterations})
    if poll_interval_s is not None:
        settings = settings.model_copy(update={"agent_poll_interval_s": poll_interval_s})

    tokens = [t.strip() for t in watched_tokens_str.split(",") if t.strip()]
    strategy_fn = _STRATEGIES[strategy]

    stats = run_agent_loop(
        settings,
        watched_tokens=tokens,
        strategy=strategy_fn,
    )
    click.echo(
        json.dumps(
            {
                "iterations": stats.iterations,
                "intents_emitted": stats.intents_emitted,
                "orders_placed": stats.orders_placed,
                "orders_rejected": stats.orders_rejected,
                "halted_by_kill_switch": stats.halted_by_kill_switch,
                "rejected_by_status": stats.rejected_by_status,
                "paper_mode": settings.paper_mode,
                "orders_allowed": settings.orders_allowed,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _noop_strategy(_state: AgentState) -> list[OrderIntent]:
    """Built-in strategy: fire no intents.  Exists so the loop and kill switch
    can be exercised end-to-end before any real strategy is plugged in."""
    return []


_STRATEGIES = {"noop": _noop_strategy}
