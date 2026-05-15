"""Simulated orderbook-depth fills. No live order placement."""

from __future__ import annotations

import uuid
from decimal import Decimal

from ..storage.base import OrderbookLevel, OrderbookSnapshot
from .models import BacktestRunConfig, BacktestSignal, SimulatedFill, SimulatedOrder


def simulate_buy_from_orderbook(
    book: OrderbookSnapshot,
    shares: Decimal,
    *,
    limit_price: Decimal | None = None,
    fee_bps: Decimal = Decimal("0"),
) -> tuple[Decimal, Decimal, Decimal, bool, str]:
    return _walk(book.asks, shares, ascending=True, limit_price=limit_price, fee_bps=fee_bps)


def simulate_sell_from_orderbook(
    book: OrderbookSnapshot,
    shares: Decimal,
    *,
    limit_price: Decimal | None = None,
    fee_bps: Decimal = Decimal("0"),
) -> tuple[Decimal, Decimal, Decimal, bool, str]:
    return _walk(book.bids, shares, ascending=False, limit_price=limit_price, fee_bps=fee_bps)


def simulate_signal_execution(
    signal: BacktestSignal,
    latest_orderbook: OrderbookSnapshot | None,
    config: BacktestRunConfig,
) -> tuple[SimulatedOrder | None, SimulatedFill | None]:
    if signal.side == "watch" or signal.token_id is None:
        return None, None
    order = SimulatedOrder(
        order_id=uuid.uuid4().hex,
        ts_ms=signal.ts_ms + config.latency_ms,
        market_id=signal.market_id,
        token_id=signal.token_id,
        side=signal.side,
        requested_shares=config.max_position_per_market,
        limit_price=None,
        source_signal_id=signal.signal_id,
    )
    if latest_orderbook is None:
        return order, SimulatedFill(
            fill_id=uuid.uuid4().hex,
            order_id=order.order_id,
            ts_ms=order.ts_ms,
            market_id=order.market_id,
            token_id=order.token_id,
            side=order.side,
            filled_shares=Decimal("0"),
            avg_fill_price=None,
            notional=Decimal("0"),
            fees=Decimal("0"),
            partial=False,
            reason="no_available_book",
        )
    if signal.side == "buy":
        filled, notional, fees, partial, reason = simulate_buy_from_orderbook(
            latest_orderbook, order.requested_shares, limit_price=order.limit_price,
            fee_bps=config.fee_bps,
        )
    else:
        filled, notional, fees, partial, reason = simulate_sell_from_orderbook(
            latest_orderbook, order.requested_shares, limit_price=order.limit_price,
            fee_bps=config.fee_bps,
        )
    avg = None if filled == 0 else notional / filled
    return order, SimulatedFill(
        fill_id=uuid.uuid4().hex,
        order_id=order.order_id,
        ts_ms=order.ts_ms,
        market_id=order.market_id,
        token_id=order.token_id,
        side=order.side,
        filled_shares=filled,
        avg_fill_price=avg,
        notional=notional,
        fees=fees,
        partial=partial,
        reason=reason,
    )


def _walk(
    levels: list[OrderbookLevel],
    shares: Decimal,
    *,
    ascending: bool,
    limit_price: Decimal | None,
    fee_bps: Decimal,
) -> tuple[Decimal, Decimal, Decimal, bool, str]:
    sorted_levels = sorted(levels, key=lambda x: x.price, reverse=not ascending)
    remaining = shares
    filled = Decimal("0")
    notional = Decimal("0")
    for level in sorted_levels:
        if limit_price is not None:
            if ascending and level.price > limit_price:
                break
            if not ascending and level.price < limit_price:
                break
        take = min(remaining, level.size)
        filled += take
        notional += take * level.price
        remaining -= take
        if remaining <= 0:
            break
    fees = notional * fee_bps / Decimal("10000")
    if filled == 0:
        return filled, notional, fees, False, "limit_price_or_no_depth"
    if filled < shares:
        return filled, notional, fees, True, "partial_insufficient_depth"
    return filled, notional, fees, False, "filled"
