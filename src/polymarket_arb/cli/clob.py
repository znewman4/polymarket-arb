"""``polymarket-arb clob ...`` - public read-only CLOB ingestion."""

from __future__ import annotations

import asyncio
import contextlib
import sys

import click

from ..http.client import AsyncHttpClient
from ..ingest.clob.client import ClobClient
from ..ingest.clob.parser import best_quote_from_book, parse_orderbook
from ..settings import Settings
from ..storage.parquet.best_quotes_repo import ParquetBestQuotesRepository
from ..storage.parquet.markets_repo import ParquetMarketsRepository
from ..storage.parquet.orderbook_repo import ParquetOrderbookRepository
from ..storage.parquet.raw_writer import RawWriter
from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository


@click.group()
def clob() -> None:
    """Public Polymarket CLOB read-only ingestion."""


def _markets_repo(settings: Settings) -> ParquetMarketsRepository:
    return ParquetMarketsRepository(
        data_root=settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )


def _orderbook_repo(settings: Settings) -> ParquetOrderbookRepository:
    return ParquetOrderbookRepository(
        data_root=settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )


def _quotes_repo(settings: Settings) -> ParquetBestQuotesRepository:
    return ParquetBestQuotesRepository(
        data_root=settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )


@clob.command(name="fetch-quotes")
@click.option("--limit", type=int, default=100)
@click.pass_context
def fetch_quotes(ctx: click.Context, limit: int) -> None:
    """Snapshot best bid/ask for active market tokens."""

    settings: Settings = ctx.obj["settings"]
    summary = asyncio.run(_run_snapshot_active(settings, limit=limit, depth=False))
    click.echo(f"✓ wrote {summary['quotes']} best quotes from {summary['markets']} markets")


@clob.command(name="fetch-orderbook")
@click.argument("market_id")
@click.pass_context
def fetch_orderbook(ctx: click.Context, market_id: str) -> None:
    """Fetch + persist one market's outcome-token orderbooks."""

    settings: Settings = ctx.obj["settings"]
    market = _markets_repo(settings).get_market(market_id)
    if market is None:
        click.echo(f"(market {market_id!r} not found in local lake)")
        sys.exit(1)
    summary = asyncio.run(_run_fetch_tokens(settings, market.clob_token_ids, write_books=True))
    click.echo(
        f"✓ wrote {summary['books']} orderbooks and {summary['quotes']} best quotes "
        f"for market {market_id}"
    )


@clob.command(name="snapshot-active-markets")
@click.option("--limit", type=int, default=100)
@click.pass_context
def snapshot_active_markets(ctx: click.Context, limit: int) -> None:
    """Bulk orderbook snapshot for active markets."""

    settings: Settings = ctx.obj["settings"]
    summary = asyncio.run(_run_snapshot_active(settings, limit=limit, depth=True))
    click.echo(
        f"✓ wrote {summary['books']} orderbooks and {summary['quotes']} best quotes "
        f"from {summary['markets']} markets"
    )


@clob.command(name="snapshot-candidate-tokens")
@click.option("--limit", type=int, default=3000, help="Max tokens to snapshot per run.")
@click.pass_context
def snapshot_candidate_tokens(ctx: click.Context, limit: int) -> None:
    """Snapshot orderbooks for all tokens referenced in relationship_candidates lake.

    Prioritises the tokens the agent actually watches rather than whatever
    markets happen to be first in the active-markets list.
    """
    settings: Settings = ctx.obj["settings"]
    repo = ParquetRelationshipCandidatesRepository(settings.data_root)

    token_ids: list[str] = []
    seen: set[str] = set()
    for rel in repo.iter_latest():
        if rel.validation_status not in ("accepted", "needs_manual_review"):
            continue
        for tid in (rel.token_id_a_yes, rel.token_id_a_no, rel.token_id_b_yes, rel.token_id_b_no):
            if tid and tid not in seen:
                seen.add(tid)
                token_ids.append(tid)

    token_ids = token_ids[:limit]
    if not token_ids:
        click.echo("No candidate tokens found — is the relationship_candidates lake populated?")
        return

    summary = asyncio.run(_run_fetch_tokens(settings, token_ids, write_books=True))
    click.echo(
        f"✓ wrote {summary['books']} orderbooks for {len(token_ids)} candidate tokens"
    )


async def _run_snapshot_active(settings: Settings, *, limit: int, depth: bool) -> dict[str, int]:
    markets = list(_markets_repo(settings).iter_active_markets())[:limit]
    tokens = [token for market in markets for token in market.clob_token_ids]
    summary = await _run_fetch_tokens(settings, tokens, write_books=depth)
    summary["markets"] = len(markets)
    return summary


_BATCH_SIZE = 50  # CLOB /books accepts up to ~100; 50 is safe


async def _run_fetch_tokens(
    settings: Settings,
    token_ids: list[str],
    *,
    write_books: bool,
) -> dict[str, int]:
    orderbooks = _orderbook_repo(settings)
    quotes = _quotes_repo(settings)
    raw_writer = RawWriter(settings.data_root)
    books_written = 0
    quotes_written = 0
    async with AsyncHttpClient(settings.http) as http:
        client = ClobClient(clob_host=settings.clob_host, http=http, raw_writer=raw_writer)
        for i in range(0, len(token_ids), _BATCH_SIZE):
            batch = token_ids[i : i + _BATCH_SIZE]
            try:
                payloads = await client.fetch_books(batch)
                if not isinstance(payloads, list):
                    payloads = []
            except Exception:
                payloads = []
                for tid in batch:
                    with contextlib.suppress(Exception):
                        payloads.append(await client.fetch_book(tid))
            for payload in payloads:
                try:
                    book = parse_orderbook(payload)
                    quote = best_quote_from_book(book)
                    if write_books:
                        orderbooks.append_snapshot(book)
                        books_written += 1
                    quotes.append(quote)
                    quotes_written += 1
                except Exception:
                    pass
    return {"books": books_written, "quotes": quotes_written}
