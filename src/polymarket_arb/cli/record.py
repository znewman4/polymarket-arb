"""``polymarket-arb record ...`` - local public-data recording loops."""

from __future__ import annotations

import asyncio

import click

from ..record.manifest import ManifestWriter
from ..record.recorder import record_combined, record_quotes, record_scores, record_snapshots
from ..record.scheduler import parse_duration_s
from ..settings import Settings


@click.group(name="record")
def record_cmd() -> None:
    """Record public read-only CLOB snapshots and research scores."""


@record_cmd.command(name="quotes")
@click.option("--limit", type=int, default=100)
@click.option("--interval", default="10s")
@click.option("--duration", default="1h")
@click.pass_context
def quotes(ctx: click.Context, limit: int, interval: str, duration: str) -> None:
    settings: Settings = ctx.obj["settings"]
    interval_s = parse_duration_s(interval)
    duration_s = parse_duration_s(duration)
    _run_manifested(
        settings,
        command="record quotes",
        args={"limit": limit, "quote_interval_s": interval_s, "duration_s": duration_s},
        coro=lambda: record_quotes(settings, limit=limit, interval_s=interval_s, duration_s=duration_s),
        table_map={"quotes": "best_quotes"},
    )


@record_cmd.command(name="snapshots")
@click.option("--limit", type=int, default=100)
@click.option("--interval", default="30s")
@click.option("--duration", default="1h")
@click.pass_context
def snapshots(ctx: click.Context, limit: int, interval: str, duration: str) -> None:
    settings: Settings = ctx.obj["settings"]
    interval_s = parse_duration_s(interval)
    duration_s = parse_duration_s(duration)
    _run_manifested(
        settings,
        command="record snapshots",
        args={"limit": limit, "orderbook_interval_s": interval_s, "duration_s": duration_s},
        coro=lambda: record_snapshots(settings, limit=limit, interval_s=interval_s, duration_s=duration_s),
        table_map={"quotes": "best_quotes", "books": "orderbook_snapshots"},
    )


@record_cmd.command(name="scores")
@click.option("--limit", type=int, default=100)
@click.option("--interval", default="60s")
@click.option("--duration", default="1h")
@click.pass_context
def scores(ctx: click.Context, limit: int, interval: str, duration: str) -> None:
    settings: Settings = ctx.obj["settings"]
    interval_s = parse_duration_s(interval)
    duration_s = parse_duration_s(duration)
    _run_manifested(
        settings,
        command="record scores",
        args={"limit": limit, "score_interval_s": interval_s, "duration_s": duration_s},
        coro=lambda: record_scores(settings, limit=limit, interval_s=interval_s, duration_s=duration_s),
        table_map={"scores": "market_scores"},
    )


@record_cmd.command(name="run")
@click.option("--limit", type=int, default=100)
@click.option("--quote-interval", default="10s")
@click.option("--score-interval", default="60s")
@click.option("--duration", default="1h")
@click.pass_context
def run(ctx: click.Context, limit: int, quote_interval: str, score_interval: str, duration: str) -> None:
    settings: Settings = ctx.obj["settings"]
    quote_interval_s = parse_duration_s(quote_interval)
    score_interval_s = parse_duration_s(score_interval)
    duration_s = parse_duration_s(duration)
    _run_manifested(
        settings,
        command="record run",
        args={
            "limit": limit,
            "quote_interval_s": quote_interval_s,
            "score_interval_s": score_interval_s,
            "duration_s": duration_s,
        },
        coro=lambda: record_combined(
            settings,
            limit=limit,
            quote_interval_s=quote_interval_s,
            score_interval_s=score_interval_s,
            duration_s=duration_s,
        ),
        table_map={"quotes": "best_quotes", "scores": "market_scores"},
    )


def _run_manifested(settings: Settings, *, command: str, args: dict, coro, table_map: dict[str, str]) -> None:
    manifest = ManifestWriter(settings.data_root, command=command, args=args)
    try:
        counts = asyncio.run(coro())
        row_counts = {table_map[k]: v for k, v in counts.items() if k in table_map and v}
        manifest.complete(row_counts=row_counts, tables_written=sorted(row_counts))
    except Exception as exc:
        manifest.fail(exc)
        raise
    click.echo(f"✓ {command} completed run_id={manifest.run_id}")
    click.echo(f"manifest {manifest.path}")
