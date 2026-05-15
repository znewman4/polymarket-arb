"""Backtest metric aggregation."""

from __future__ import annotations

from decimal import Decimal

from .models import BacktestMetrics, SimulatedFill, SimulatedOrder


def compute_metrics(
    *,
    total_events: int,
    total_signals: int,
    orders: list[SimulatedOrder],
    fills: list[SimulatedFill],
    final_cash: Decimal,
    open_positions_count: int,
) -> BacktestMetrics:
    filled = [f for f in fills if f.filled_shares > 0]
    return BacktestMetrics(
        total_events=total_events,
        total_signals=total_signals,
        simulated_orders=len(orders),
        fills=len(filled),
        partial_fills=sum(1 for f in filled if f.partial),
        rejected_stale_quotes=0,
        rejected_insufficient_depth=sum(1 for f in fills if f.reason in {"no_available_book", "limit_price_or_no_depth"}),
        total_notional=sum((f.notional for f in filled), Decimal("0")),
        total_fees=sum((f.fees for f in filled), Decimal("0")),
        realised_pnl=Decimal("0"),
        unrealised_pnl=Decimal("0"),
        final_cash=final_cash,
        open_positions_count=open_positions_count,
        notes=["Final outcome PnL is not computed until resolutions are available."],
    )
