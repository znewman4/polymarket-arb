"""Tests for the depth-aware execution post-pass.

The pass re-fills each trade leg against recorded orderbook depth when
available within a tolerance window, and labels every leg with what model
was actually used so the data-coverage gap is explicit on each row.
"""

from __future__ import annotations

from decimal import Decimal

from polymarket_arb.backtest.standardised.contract import StandardisedTradeRow
from polymarket_arb.backtest.standardised.depth_aware import apply_depth_aware_execution
from polymarket_arb.storage.base import OrderbookLevel, OrderbookSnapshot


def _trade(**overrides) -> StandardisedTradeRow:
    base = {
        "trade_id": "t",
        "trade_group_id": "g",
        "run_id": "r",
        "subrun_id": "s",
        "source_lane": "rulebook_aggressive_deterministic",
        "source_agent": "deterministic_template_aggressive",
        "reason": "test",
        "token_id": "tok-a",
        "market_id": "mkt-a",
        "leg": "a",
        "side": "buy",
        "entry_ts_ms": 2_000_000,
        "entry_price": 0.40,
        "shares": 100.0,
        "stake_usdc": 40.0,
        "notional_usdc": 40.0,
        "fees_usdc": 0.0,
        "slippage_cost_usdc": 0.20,
        "entry_cost_usdc": 40.0,
        "execution_model": "price_history_only",
    }
    base.update(overrides)
    return StandardisedTradeRow(**base)


def _book(ts_ms: int, asks: list[tuple[float, float]]) -> OrderbookSnapshot:
    """Helper: build a synthetic OrderbookSnapshot with the given (price, size) asks."""
    return OrderbookSnapshot(
        token_id="tok-a",
        condition_id=None,
        market_slug=None,
        timestamp_ms=ts_ms,
        bids=[],
        asks=[OrderbookLevel(price=Decimal(str(p)), size=Decimal(str(s))) for p, s in asks],
        book_hash=None,
        source="rest",
        schema_version=1,
        ingested_ts_ms=ts_ms,
    )


def test_no_orderbook_data_marks_fallback() -> None:
    """Empty lookup → every leg labelled price_history_only_fallback."""
    trade = _trade()
    summary = apply_depth_aware_execution([trade], orderbook_lookup={})
    assert trade.execution_model_used == "price_history_only_fallback"
    assert trade.execution_model_confidence == 0.4
    assert trade.depth_lookup_nearest_age_ms is None
    assert summary["legs_filled_from_depth"] == 0
    assert summary["legs_fallback_price_history_only"] == 1
    assert summary["depth_coverage_pct"] == 0.0


def test_snapshot_within_tolerance_fills_from_depth() -> None:
    """A snapshot within the tolerance window → real depth-aware fill."""
    trade = _trade(entry_ts_ms=2_000_000, entry_price=0.40, shares=100.0)
    # Asks: 60 shares at $0.42, 50 shares at $0.45 → 100 shares fill cost is
    # 60*0.42 + 40*0.45 = 25.20 + 18.00 = 43.20 → avg fill = 0.432.
    snap = _book(ts_ms=2_000_500, asks=[(0.42, 60.0), (0.45, 50.0)])
    summary = apply_depth_aware_execution(
        [trade], orderbook_lookup={"tok-a": [snap]}, max_snapshot_age_ms=1_000_000,
    )
    assert trade.execution_model_used == "recorded_depth"
    assert trade.execution_model_confidence == 1.0
    assert abs(trade.entry_price - 0.432) < 1e-6
    assert trade.notional_usdc == 43.2
    # slippage = (0.432 - 0.40) * 100 = 3.2
    assert abs(trade.slippage_cost_usdc - 3.2) < 1e-6
    assert summary["legs_filled_from_depth"] == 1
    assert summary["legs_fallback_price_history_only"] == 0


def test_snapshot_outside_tolerance_falls_back() -> None:
    trade = _trade(entry_ts_ms=2_000_000)
    # Snapshot is 5 days old → outside the default 1-day tolerance.
    snap = _book(ts_ms=2_000_000 - 5 * 24 * 3600 * 1000, asks=[(0.42, 100.0)])
    summary = apply_depth_aware_execution(
        [trade], orderbook_lookup={"tok-a": [snap]}, max_snapshot_age_ms=24 * 3600 * 1000,
    )
    assert trade.execution_model_used == "price_history_only_fallback"
    assert trade.depth_lookup_nearest_age_ms == 5 * 24 * 3600 * 1000
    assert summary["legs_filled_from_depth"] == 0
    assert summary["legs_fallback_price_history_only"] == 1


def test_thin_book_falls_back() -> None:
    """A book with zero ask depth on the buy side → fall back, don't crash."""
    trade = _trade(shares=100.0)
    snap = _book(ts_ms=2_000_000, asks=[])  # no asks
    summary = apply_depth_aware_execution(
        [trade], orderbook_lookup={"tok-a": [snap]}, max_snapshot_age_ms=1_000_000,
    )
    assert trade.execution_model_used == "price_history_only_fallback"
    assert summary["legs_filled_from_depth"] == 0


def test_partial_fill_is_labelled_partial() -> None:
    """If the book has SOME depth but not enough for the full size, label as
    recorded_depth_partial."""
    trade = _trade(shares=100.0, entry_price=0.40)
    snap = _book(ts_ms=2_000_500, asks=[(0.41, 20.0)])  # only 20 shares available
    summary = apply_depth_aware_execution(
        [trade], orderbook_lookup={"tok-a": [snap]}, max_snapshot_age_ms=1_000_000,
    )
    assert trade.execution_model_used == "recorded_depth_partial"
    assert trade.execution_model_confidence == 1.0
    assert trade.shares == 20.0
    assert summary["legs_filled_from_depth"] == 1


def test_closed_form_trades_are_skipped() -> None:
    """Closed-form simulator trades have their own pricing — depth pass leaves them."""
    trade = _trade(trade_kind="closed_form", source_lane="closed_form_simulator")
    snap = _book(ts_ms=2_000_500, asks=[(0.42, 100.0)])
    summary = apply_depth_aware_execution(
        [trade], orderbook_lookup={"tok-a": [snap]}, max_snapshot_age_ms=1_000_000,
    )
    # No re-fill happened
    assert trade.entry_price == 0.40
    assert trade.execution_model_used in ("price_history_only", "closed_form")
    assert summary["legs_filled_from_depth"] == 0
    assert summary["legs_fallback_price_history_only"] == 0


def test_per_lane_coverage_summary() -> None:
    trades = [
        _trade(trade_id="a", trade_group_id="ga", source_lane="rulebook_aggressive_deterministic", token_id="tok-a"),
        _trade(trade_id="b", trade_group_id="gb", source_lane="rulebook_aggressive_deterministic", token_id="tok-NO-DATA"),
        _trade(trade_id="c", trade_group_id="gc", source_lane="control_random_pairs", token_id="tok-NO-DATA"),
    ]
    snap = _book(ts_ms=2_000_500, asks=[(0.41, 1000.0)])
    summary = apply_depth_aware_execution(
        trades, orderbook_lookup={"tok-a": [snap]}, max_snapshot_age_ms=1_000_000,
    )
    assert summary["legs_filled_from_depth"] == 1
    assert summary["legs_fallback_price_history_only"] == 2
    per_lane = summary["per_lane"]
    assert per_lane["rulebook_aggressive_deterministic"]["filled"] == 1
    assert per_lane["rulebook_aggressive_deterministic"]["fallback"] == 1
    assert per_lane["control_random_pairs"]["fallback"] == 1
    assert per_lane["control_random_pairs"]["no_snapshots"] == 1
