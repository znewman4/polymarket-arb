"""``polymarket-arb ingest ...`` — public/read-only discovery workflows."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import click

from ..http.client import AsyncHttpClient
from ..ingest.gamma.client import GammaClient
from ..ingest.market_universe_discovery import run_universe_discovery
from ..ingest.space_discovery import run_space_discovery
from ..settings import Settings
from ..storage.parquet.raw_writer import RawWriter


@click.group(name="ingest")
def ingest_cmd() -> None:
    """Discovery ingestion commands (public/read-only)."""


@ingest_cmd.command(name="discover-market-universe")
@click.option("--active/--no-active", default=True, show_default=True)
@click.option("--closed/--no-closed", default=True, show_default=True)
@click.option("--lookback-days", type=int, default=365, show_default=True)
@click.option("--include-tags/--no-include-tags", default=True, show_default=True)
@click.option("--include-related-tags/--no-include-related-tags", default=True, show_default=True)
@click.option("--include-series/--no-include-series", default=True, show_default=True)
@click.option("--include-sports/--no-include-sports", default=True, show_default=True)
@click.option("--include-teams/--no-include-teams", default=True, show_default=True)
@click.option("--max-pages", type=int, default=None, help="Maximum pages per paginated endpoint.")
@click.option("--run-id", default=None, help="Discovery run id.")
@click.pass_context
def discover_market_universe(
    ctx: click.Context,
    active: bool,
    closed: bool,
    lookback_days: int,
    include_tags: bool,
    include_related_tags: bool,
    include_series: bool,
    include_sports: bool,
    include_teams: bool,
    max_pages: int | None,
    run_id: str | None,
) -> None:
    """Discover a broad public Gamma market universe into JSONL artifacts."""

    settings: Settings = ctx.obj["settings"]
    rid = run_id or datetime.now(timezone.utc).strftime("universe_%Y%m%d_%H%M%S")
    result = asyncio.run(_run_discovery(
        settings,
        run_id=rid,
        active=active,
        closed=closed,
        lookback_days=lookback_days,
        include_tags=include_tags,
        include_related_tags=include_related_tags,
        include_series=include_series,
        include_sports=include_sports,
        include_teams=include_teams,
        max_pages=max_pages,
    ))
    click.echo(
        f"✓ universe discovery run_id={result.run_id}\n"
        f"  output={result.output_dir}\n"
        f"  counts={result.counts}\n"
        "  label=RESEARCH-ONLY public/read-only"
    )


async def _run_discovery(settings: Settings, **kwargs) -> object:
    async with AsyncHttpClient(settings.http) as http:
        client = GammaClient(
            gamma_host=settings.gamma_host,
            http=http,
            raw_writer=RawWriter(settings.data_root),
        )
        return await run_universe_discovery(client, **kwargs)


@ingest_cmd.command(name="discover-spaces")
@click.option("--discovery-run-id", required=True)
@click.option("--out", "output_dir", default=None, type=click.Path())
@click.pass_context
def discover_spaces(
    ctx: click.Context,
    discovery_run_id: str,
    output_dir: str | None,
) -> None:
    """Turn market-universe JSONL into candidate space parquet."""

    settings: Settings = ctx.obj["settings"]
    result = run_space_discovery(
        discovery_run_id=discovery_run_id,
        data_root=settings.data_root,
        output_dir=Path(output_dir) if output_dir else None,
    )
    click.echo(
        f"✓ discovered spaces run_id={result.discovery_run_id}\n"
        f"  spaces={len(result.rows)}\n"
        f"  output={result.output_path}"
    )
