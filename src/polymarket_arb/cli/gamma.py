"""``polymarket-arb gamma ...`` — Gamma market catalogue.

``fetch-markets`` hits live Gamma (raw lake + normalised parquet).
``list-markets`` / ``search-markets`` / ``show-market`` read from local
DuckDB views and never touch the network.
"""

from __future__ import annotations

import asyncio
import sys
import time

import click

from ..http.client import AsyncHttpClient
from ..ingest.gamma.client import GammaClient
from ..settings import Settings
from ..storage.parquet.events_repo import ParquetEventsRepository
from ..storage.parquet.markets_repo import ParquetMarketsRepository
from ..storage.parquet.raw_writer import RawWriter


@click.group()
def gamma() -> None:
    """Polymarket Gamma market/event catalogue ingestion and search."""


def _markets_repo(settings: Settings) -> ParquetMarketsRepository:
    return ParquetMarketsRepository(
        data_root=settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )


def _events_repo(settings: Settings) -> ParquetEventsRepository:
    return ParquetEventsRepository(
        data_root=settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )


# ─── fetch-markets (network) ──────────────────────────────────────────────


@gamma.command(name="fetch-markets")
@click.option("--limit", type=int, default=500,
              help="Page size to request from Gamma.")
@click.option("--max-pages", type=int, default=1,
              help="Number of pages to fetch (1 = single page; use --all to exhaust).")
@click.option("--all", "fetch_all", is_flag=True, default=False,
              help="Paginate until exhausted (overrides --max-pages).")
@click.option("--include-events/--skip-events", default=True,
              help="Also fetch /events into events/ table.")
@click.pass_context
def fetch_markets(ctx: click.Context, limit: int, max_pages: int,
                  fetch_all: bool, include_events: bool) -> None:
    """Fetch active markets (and events) from Gamma into raw + normalised lake."""

    settings: Settings = ctx.obj["settings"]
    started = time.monotonic()
    counts = asyncio.run(_run_fetch(settings, limit=limit,
                                    max_pages=None if fetch_all else max_pages,
                                    include_events=include_events))
    elapsed = time.monotonic() - started
    click.echo(
        f"✓ {counts['markets']} markets, {counts['events']} events ingested in "
        f"{elapsed:.1f}s "
        f"(latest_count={counts['markets_latest']})"
    )


async def _run_fetch(settings: Settings, *, limit: int, max_pages: int | None,
                     include_events: bool) -> dict[str, int]:
    raw_writer = RawWriter(settings.data_root)
    markets_repo = _markets_repo(settings)
    events_repo = _events_repo(settings)
    n_markets = 0
    n_events = 0

    async with AsyncHttpClient(settings.http) as http:
        client = GammaClient(
            gamma_host=settings.gamma_host, http=http,
            raw_writer=raw_writer, page_size=limit,
        )
        # Buffer rows then bulk-write once; keeps part-file count manageable.
        market_rows = []
        async for row in client.iter_markets(max_pages=max_pages):
            market_rows.append(row)
        n_markets = markets_repo.upsert_markets(market_rows)

        if include_events:
            event_rows = []
            async for ev in client.iter_events(max_pages=max_pages):
                event_rows.append(ev)
            n_events = events_repo.upsert_events(event_rows)

    return {
        "markets": n_markets,
        "events": n_events,
        "markets_latest": markets_repo.latest_count(),
    }


# ─── list-markets (local DuckDB) ─────────────────────────────────────────


@gamma.command(name="list-markets")
@click.option("--active", is_flag=True, default=False,
              help="Only list currently-active markets.")
@click.option("--limit", type=int, default=20)
@click.pass_context
def list_markets(ctx: click.Context, active: bool, limit: int) -> None:
    """List markets from the local DuckDB views."""

    settings: Settings = ctx.obj["settings"]
    repo = _markets_repo(settings)
    rows = list(repo.iter_active_markets()) if active else \
        repo.search("", limit=limit)
    if not rows:
        click.echo("(no markets in local lake — run `gamma fetch-markets` first)")
        sys.exit(0)
    rows = rows[:limit]
    click.echo(f"{len(rows)} markets:")
    for r in rows:
        end = "open" if r.end_date_ms is None else \
            time.strftime("%Y-%m-%d", time.gmtime(r.end_date_ms / 1000))
        click.echo(f"  {r.id:<16} | end={end} | {r.question[:70]}")


# ─── search-markets ──────────────────────────────────────────────────────


@gamma.command(name="search-markets")
@click.argument("query")
@click.option("--limit", type=int, default=20)
@click.pass_context
def search_markets(ctx: click.Context, query: str, limit: int) -> None:
    """Search local markets by question text (case-insensitive substring)."""

    settings: Settings = ctx.obj["settings"]
    rows = _markets_repo(settings).search(query, limit=limit)
    if not rows:
        click.echo(f"(no matches for {query!r})")
        sys.exit(0)
    click.echo(f"{len(rows)} match(es) for {query!r}:")
    for r in rows:
        click.echo(f"  {r.id:<16} | {r.slug:<40} | {r.question[:70]}")


# ─── show-market ─────────────────────────────────────────────────────────


@gamma.command(name="show-market")
@click.argument("market_id")
@click.pass_context
def show_market(ctx: click.Context, market_id: str) -> None:
    """Print the normalised row for one market id."""

    settings: Settings = ctx.obj["settings"]
    row = _markets_repo(settings).get_market(market_id)
    if row is None:
        click.echo(f"(market {market_id!r} not found in local lake)")
        sys.exit(1)
    end = "—" if row.end_date_ms is None else \
        time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(row.end_date_ms / 1000))
    click.echo(f"id           {row.id}")
    click.echo(f"slug         {row.slug}")
    click.echo(f"condition_id {row.condition_id}")
    click.echo(f"event_id     {row.event_id or '—'}")
    click.echo(f"question     {row.question}")
    if row.description:
        click.echo(f"description  {row.description[:200]}")
    click.echo(f"active       {row.active}    closed={row.closed}    archived={row.archived}")
    click.echo(f"end_date     {end}")
    click.echo(f"outcomes     {row.outcomes}")
    click.echo(f"gamma prices {row.gamma_outcome_prices_snapshot}  (stale snapshot)")
    click.echo(f"clob tokens  {row.clob_token_ids}")
    click.echo(f"volume       {row.volume}    liquidity={row.liquidity}")
    click.echo(f"text_hash    {row.text_hash}")
