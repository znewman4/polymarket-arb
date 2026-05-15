"""Backtest DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal


@dataclass(frozen=True)
class BacktestRunConfig:
    run_id: str
    dataset_path: str | None
    strategy_name: str
    initial_cash: Decimal
    max_position_per_market: Decimal
    latency_ms: int
    fee_bps: Decimal
    slippage_bps: Decimal | None
    quote_staleness_limit_ms: int
    start_ts_ms: int | None
    end_ts_ms: int | None
    threshold: float = 0.8
    execute_signals: bool = True


@dataclass(frozen=True)
class BacktestEvent:
    ts_ms: int
    event_type: str
    market_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class BacktestSignal:
    signal_id: str
    ts_ms: int
    market_id: str
    token_id: str | None
    side: Literal["buy", "sell", "watch"]
    target_outcome: str | None
    estimated_probability: Decimal | None
    market_price: Decimal | None
    edge: Decimal | None
    confidence: float
    reason: str
    source: str
    metadata_json: str


@dataclass(frozen=True)
class SimulatedOrder:
    order_id: str
    ts_ms: int
    market_id: str
    token_id: str
    side: Literal["buy", "sell"]
    requested_shares: Decimal
    limit_price: Decimal | None
    source_signal_id: str


@dataclass(frozen=True)
class SimulatedFill:
    fill_id: str
    order_id: str
    ts_ms: int
    market_id: str
    token_id: str
    side: Literal["buy", "sell"]
    filled_shares: Decimal
    avg_fill_price: Decimal | None
    notional: Decimal
    fees: Decimal
    partial: bool
    reason: str


@dataclass
class BacktestPosition:
    market_id: str
    token_id: str
    shares: Decimal = Decimal("0")
    avg_entry_price: Decimal = Decimal("0")
    realised_pnl: Decimal = Decimal("0")
    mark_price: Decimal | None = None


@dataclass(frozen=True)
class BacktestMetrics:
    total_events: int
    total_signals: int
    simulated_orders: int
    fills: int
    partial_fills: int
    rejected_stale_quotes: int
    rejected_insufficient_depth: int
    total_notional: Decimal
    total_fees: Decimal
    realised_pnl: Decimal
    unrealised_pnl: Decimal
    final_cash: Decimal
    open_positions_count: int
    notes: list[str] = field(default_factory=list)
