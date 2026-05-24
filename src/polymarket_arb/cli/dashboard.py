"""CLI: ``polymarket-arb dashboard`` — start the read-only Flask dashboard."""

from __future__ import annotations

import click

from ..settings import Settings


@click.group(name="dashboard")
def dashboard_cmd() -> None:
    """Read-only paper-trading dashboard."""


@dashboard_cmd.command(name="serve")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Bind address. Use 0.0.0.0 inside a container.")
@click.option("--port", default=5000, show_default=True, type=int)
@click.pass_context
def dashboard_serve(ctx: click.Context, host: str, port: int) -> None:
    """Start the Flask dashboard on the given host/port."""
    settings: Settings = ctx.obj["settings"]
    from ..dashboard.app import create_app
    app = create_app(settings)
    app.run(host=host, port=port, debug=False, use_reloader=False)
