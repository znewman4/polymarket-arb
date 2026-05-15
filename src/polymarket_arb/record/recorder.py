"""Reusable public-data recording routines."""

from __future__ import annotations

from ..cli.clob import _run_snapshot_active
from ..cli.score import run_score_markets
from ..settings import Settings
from .scheduler import run_interval_loop


async def record_quotes(settings: Settings, *, limit: int, interval_s: int, duration_s: int) -> dict[str, int]:
    return await run_interval_loop(
        lambda: _run_snapshot_active(settings, limit=limit, depth=False),
        interval_s=interval_s,
        duration_s=duration_s,
    )


async def record_snapshots(settings: Settings, *, limit: int, interval_s: int, duration_s: int) -> dict[str, int]:
    return await run_interval_loop(
        lambda: _run_snapshot_active(settings, limit=limit, depth=True),
        interval_s=interval_s,
        duration_s=duration_s,
    )


async def record_scores(settings: Settings, *, limit: int, interval_s: int, duration_s: int) -> dict[str, int]:
    async def action() -> dict[str, int]:
        return {"scores": run_score_markets(settings, limit=limit)}

    return await run_interval_loop(action, interval_s=interval_s, duration_s=duration_s)


async def record_combined(
    settings: Settings,
    *,
    limit: int,
    quote_interval_s: int,
    score_interval_s: int,
    duration_s: int,
) -> dict[str, int]:
    # Conservative first version: run quote+score once per loop at the shorter
    # cadence, only using public read-only CLOB fetches.
    interval = max(1, min(quote_interval_s, score_interval_s))

    async def action() -> dict[str, int]:
        quotes = await _run_snapshot_active(settings, limit=limit, depth=False)
        scores = run_score_markets(settings, limit=limit)
        return {"quotes": quotes.get("quotes", 0), "markets": quotes.get("markets", 0), "scores": scores}

    return await run_interval_loop(action, interval_s=interval, duration_s=duration_s)
