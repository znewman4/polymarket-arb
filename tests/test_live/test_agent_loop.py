"""Tests for the agent loop (paper-mode end-to-end, kill switch halt)."""

from __future__ import annotations

from decimal import Decimal

from polymarket_arb.live.agent_loop import AgentState, run_agent_loop
from polymarket_arb.monitoring import kill_switch
from polymarket_arb.risk.models import OrderIntent
from polymarket_arb.storage.base import OrderbookLevel, OrderbookSnapshot
from polymarket_arb.storage.parquet.orderbook_repo import ParquetOrderbookRepository
from polymarket_arb.storage.parquet.orders_log_repo import ParquetOrdersLogRepository


def _seed_book(data_root, token_id: str) -> None:
    repo = ParquetOrderbookRepository(data_root)
    snap = OrderbookSnapshot(
        token_id=token_id, condition_id=None, market_slug=None,
        timestamp_ms=1_700_000_000_000,
        bids=[],
        asks=[OrderbookLevel(price=Decimal("0.51"), size=Decimal("100"))],
        book_hash=None, source="rest",
        schema_version=1, ingested_ts_ms=1_700_000_000_000,
    )
    repo.append_snapshot(snap)


def _strategy_fires_one_intent(state: AgentState) -> list[OrderIntent]:
    """A test strategy that emits one buy intent per tick for the first watched token."""
    if not state.watched_tokens:
        return []
    return [OrderIntent(
        id=f"intent-{state.ts_ms}",
        strategy_id="test_strategy",
        token_id=state.watched_tokens[0],
        side="buy",
        price=Decimal("1.0"),  # permissive limit — book asks are at 0.51
        size=Decimal("10"),
    )]


def _noop_strategy(_state: AgentState) -> list[OrderIntent]:
    return []


class _BulkOnlyOrderbookRepo:
    def __init__(self, snapshots: dict[str, OrderbookSnapshot]) -> None:
        self.snapshots = snapshots
        self.bulk_calls: list[list[str]] = []

    def latest_books_bulk(self, token_ids: list[str]) -> dict[str, OrderbookSnapshot]:
        self.bulk_calls.append(list(token_ids))
        return {token_id: self.snapshots[token_id] for token_id in token_ids if token_id in self.snapshots}

    def latest_book(self, _token_id: str) -> OrderbookSnapshot | None:
        raise AssertionError("agent loop should use latest_books_bulk")


def test_agent_loop_runs_max_iterations_and_emits_orders(settings, tmp_data_root) -> None:
    _seed_book(tmp_data_root, "tok-a")
    s = settings.model_copy(update={
        "paper_mode": True, "agent_poll_interval_s": 1, "agent_max_iterations": 3,
    })
    sleeps: list[float] = []
    times = iter([1_700_000_001.0, 1_700_000_002.0, 1_700_000_003.0, 1_700_000_004.0])
    stats = run_agent_loop(
        s,
        watched_tokens=["tok-a"],
        strategy=_strategy_fires_one_intent,
        sleep_fn=sleeps.append,
        now_fn=lambda: next(times),
    )
    assert stats.iterations == 3
    assert stats.intents_emitted == 3
    assert stats.orders_placed == 3
    assert stats.halted_by_kill_switch is False
    # Loop sleeps BETWEEN iterations, not after the last one → 2 sleeps for 3 iters.
    assert sleeps == [1, 1]
    # 3 rows in orders_log
    rows = list(ParquetOrdersLogRepository(tmp_data_root).iter_recent())
    assert len(rows) == 3
    assert all(r.paper_mode is True for r in rows)
    assert all(r.status == "paper_filled" for r in rows)


def test_agent_loop_halts_when_kill_switch_set(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={
        "paper_mode": True, "agent_poll_interval_s": 1, "agent_max_iterations": 10,
    })
    # Drop the killswitch file BEFORE the loop starts.
    s.killswitch_path.parent.mkdir(parents=True, exist_ok=True)
    s.killswitch_path.write_text("halt\n")
    try:
        sleeps: list[float] = []
        stats = run_agent_loop(
            s,
            watched_tokens=["tok-a"],
            strategy=_strategy_fires_one_intent,
            sleep_fn=sleeps.append,
            now_fn=lambda: 1_700_000_000.0,
        )
        assert stats.halted_by_kill_switch is True
        assert stats.iterations == 0
        assert stats.intents_emitted == 0
        assert sleeps == []  # never slept — halted before first sleep
    finally:
        s.killswitch_path.unlink(missing_ok=True)
        kill_switch.reset_for_tests()


def test_agent_loop_noop_strategy_writes_no_orders(settings, tmp_data_root) -> None:
    """Loop runs but emits 0 intents → orders_log untouched."""
    s = settings.model_copy(update={
        "paper_mode": True, "agent_poll_interval_s": 1, "agent_max_iterations": 5,
    })
    sleeps: list[float] = []
    times = iter([1_700_000_000.0 + i for i in range(10)])
    stats = run_agent_loop(
        s,
        watched_tokens=["tok-a"],
        strategy=_noop_strategy,
        sleep_fn=sleeps.append,
        now_fn=lambda: next(times),
    )
    assert stats.iterations == 5
    assert stats.intents_emitted == 0
    assert stats.orders_placed == 0
    rows = list(ParquetOrdersLogRepository(tmp_data_root).iter_recent())
    assert len(rows) == 0


def test_agent_loop_loads_orderbooks_in_bulk(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={
        "paper_mode": True, "agent_poll_interval_s": 1, "agent_max_iterations": 2,
    })
    snap = OrderbookSnapshot(
        token_id="tok-a", condition_id=None, market_slug=None,
        timestamp_ms=1_700_000_000_000,
        bids=[],
        asks=[OrderbookLevel(price=Decimal("0.51"), size=Decimal("100"))],
        book_hash=None, source="rest",
        schema_version=1, ingested_ts_ms=1_700_000_000_000,
    )
    repo = _BulkOnlyOrderbookRepo({"tok-a": snap})
    seen_snapshots: list[dict[str, OrderbookSnapshot]] = []

    def strategy(state: AgentState) -> list[OrderIntent]:
        seen_snapshots.append(state.latest_book_by_token)
        return []

    stats = run_agent_loop(
        s,
        watched_tokens=["tok-a", "tok-b", "tok-a"],
        strategy=strategy,
        orderbook_repo=repo,  # type: ignore[arg-type]
        sleep_fn=lambda _seconds: None,
        now_fn=lambda: 1_700_000_000.0,
    )

    assert stats.iterations == 2
    assert repo.bulk_calls == [["tok-a", "tok-b"], ["tok-a", "tok-b"]]
    assert seen_snapshots == [{"tok-a": snap}, {"tok-a": snap}]
