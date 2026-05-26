"""Live agent loop.

Polls the lake on a fixed interval, evaluates a strategy callable against the
latest orderbook snapshots, and routes any resulting ``OrderIntent`` through
``OrderClient`` (which honours paper_mode + kill switch + compliance gate).

The loop:
    1. Check kill switch → halt if active.
    2. Bulk-load the latest orderbook snapshots for watched token_ids.
    3. Call the strategy callable with the current state → list[OrderIntent].
    4. For each intent, OrderClient.place_order() → OrdersLogRow.
    5. Sleep ``agent_poll_interval_s``.
    6. Repeat until ``max_iterations`` (None = forever) or kill-switch.

The strategy callable is a function:
    ``def strategy(state: AgentState) -> list[OrderIntent]``
where AgentState exposes the latest snapshots and a clock.  Real strategy
implementations plug in via the CLI; tests pass a stub.

This module does NOT call the network; the strategy reads from the lake.  The
``record snapshots`` CLI loop is expected to be running in parallel on the VPS
populating the lake.
"""

from __future__ import annotations

import resource
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
from loguru import logger

from ..monitoring import kill_switch
from ..risk.models import OrderIntent
from ..storage.base import OrderbookSnapshot
from ..storage.parquet.orderbook_repo import ParquetOrderbookRepository
from .models import OrderResult
from .order_client import OrderClient

if TYPE_CHECKING:
    from ..settings import Settings


@dataclass
class AgentState:
    """Inputs visible to a strategy callable at each tick."""

    ts_ms: int
    watched_tokens: list[str]
    latest_book_by_token: dict[str, OrderbookSnapshot]


@dataclass
class AgentLoopStats:
    """Per-run counters surfaced when the loop exits."""

    iterations: int = 0
    intents_emitted: int = 0
    orders_placed: int = 0
    orders_rejected: int = 0
    halted_by_kill_switch: bool = False
    rejected_by_status: dict[str, int] = field(default_factory=dict)


StrategyFn = Callable[[AgentState], list[OrderIntent]]


def run_agent_loop(
    settings: Settings,
    *,
    watched_tokens: Iterable[str],
    strategy: StrategyFn,
    order_client: OrderClient | None = None,
    orderbook_repo: ParquetOrderbookRepository | None = None,
    data_root: Path | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.time,
    watched_tokens_fn: Callable[[], list[str]] | None = None,
) -> AgentLoopStats:
    """Run the agent loop until kill switch or ``agent_max_iterations`` is hit.

    Honours ``settings.agent_poll_interval_s`` and ``settings.agent_max_iterations``.
    Test harness passes a stub ``sleep_fn`` / ``now_fn`` to drive deterministically.
    """
    resource.setrlimit(resource.RLIMIT_NOFILE, (65536, 65536))

    root = data_root or settings.data_root
    orderbook_con: duckdb.DuckDBPyConnection | None = None
    if orderbook_repo is None:
        orderbook_con = duckdb.connect()
        book_repo = ParquetOrderbookRepository(root, duckdb_connection=orderbook_con)
    else:
        book_repo = orderbook_repo
    client = order_client or OrderClient(settings)
    watched = list(dict.fromkeys(watched_tokens))  # dedupe, preserve order
    max_iter = settings.agent_max_iterations
    interval_s = max(1, int(settings.agent_poll_interval_s))
    stats = AgentLoopStats()

    logger.info(
        "agent_loop starting paper_mode={} orders_allowed={} watched_tokens={} interval_s={}",
        settings.paper_mode, settings.orders_allowed, len(watched), interval_s,
    )

    try:
        while True:
            if kill_switch.is_active(settings.killswitch_path):
                stats.halted_by_kill_switch = True
                logger.warning("agent_loop halted: kill switch active")
                return stats

            ts_ms = int(now_fn() * 1000)
            if watched_tokens_fn is not None:
                watched = list(dict.fromkeys(watched_tokens_fn()))
            # Build the per-tick snapshot in one DuckDB query. Missing books are
            # fine; the strategy decides whether to fire on partial state.
            snapshots = book_repo.latest_books_bulk(watched)

            state = AgentState(ts_ms=ts_ms, watched_tokens=watched, latest_book_by_token=snapshots)
            intents = strategy(state) or []
            stats.intents_emitted += len(intents)

            for intent in intents:
                result = client.place_order(
                    intent,
                    strategy_id=intent.strategy_id,
                    market_id=intent.market_id,
                    source_relationship_id=intent.detail.get("relationship_id", ""),
                    notes=(
                        f"gross_edge={intent.detail.get('gross_edge', '')} "
                        f"rel_type={intent.detail.get('relationship_type', '')} "
                        f"priority_score={intent.detail.get('priority_score', '')}"
                    ),
                )
                _record_result(stats, result)

            stats.iterations += 1
            if max_iter is not None and stats.iterations >= max_iter:
                logger.info("agent_loop completed {} iterations; exiting (max_iterations)", stats.iterations)
                return stats

            sleep_fn(interval_s)
    finally:
        if orderbook_con is not None:
            orderbook_con.close()


def _record_result(stats: AgentLoopStats, result: OrderResult) -> None:
    if result.status.startswith("paper_filled") or result.status.startswith("paper_partial"):
        stats.orders_placed += 1
    else:
        stats.orders_rejected += 1
    stats.rejected_by_status[result.status] = stats.rejected_by_status.get(result.status, 0) + 1
