"""Tests for the cost model."""

from __future__ import annotations

from decimal import Decimal

from polymarket_arb.backtest.cost_model import estimate_costs


def test_zero_costs_with_zero_bps():
    result = estimate_costs(
        notional_usdc=Decimal("100"),
        mid_price=Decimal("0.5"),
        execution_model="price_history_only",
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    assert result.fee_usdc == Decimal("0")
    assert result.slippage_usdc == Decimal("0")
    assert result.fill_price == Decimal("0.5")


def test_fee_applied():
    result = estimate_costs(
        notional_usdc=Decimal("100"),
        mid_price=Decimal("0.5"),
        execution_model="price_history_only",
        fee_bps=Decimal("100"),  # 1%
        slippage_bps=Decimal("0"),
    )
    assert result.fee_usdc == Decimal("1")  # 1% of 100


def test_slippage_pushes_fill_price_up():
    result = estimate_costs(
        notional_usdc=Decimal("100"),
        mid_price=Decimal("0.5"),
        execution_model="price_history_only",
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("100"),  # 1%
    )
    # fill_price = 0.5 * (1 + 0.01) = 0.505
    assert result.fill_price > Decimal("0.5")


def test_fill_price_capped_at_one():
    result = estimate_costs(
        notional_usdc=Decimal("100"),
        mid_price=Decimal("0.99"),
        execution_model="price_history_only",
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("1000"),  # 10%
    )
    assert result.fill_price <= Decimal("1")


def test_mark_only_zero_costs():
    result = estimate_costs(
        notional_usdc=Decimal("100"),
        mid_price=Decimal("0.6"),
        execution_model="mark_only",
        fee_bps=Decimal("200"),
        slippage_bps=Decimal("200"),
    )
    assert result.fee_usdc == Decimal("0")
    assert result.slippage_usdc == Decimal("0")
    assert result.fill_price == Decimal("0.6")


def test_execution_model_confidence():
    r_depth = estimate_costs(
        notional_usdc=Decimal("100"),
        mid_price=Decimal("0.5"),
        execution_model="recorded_depth",
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("50"),
    )
    r_hist = estimate_costs(
        notional_usdc=Decimal("100"),
        mid_price=Decimal("0.5"),
        execution_model="price_history_only",
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("50"),
    )
    assert r_depth.execution_model_confidence > r_hist.execution_model_confidence
