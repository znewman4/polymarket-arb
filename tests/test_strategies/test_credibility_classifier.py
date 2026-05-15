"""Tests for backtest credibility classifier."""

from __future__ import annotations

from decimal import Decimal

from polymarket_arb.backtest.relationship_replay import classify_credibility


def _mock_coverage(coverage_score: float = 0.8):
    from dataclasses import dataclass

    @dataclass
    class FakeCov:
        coverage_score: float

    return FakeCov(coverage_score=coverage_score)


class TestClassifyCredibility:
    def test_data_insufficient_few_trades(self):
        label, rationale = classify_credibility(
            trades_executed=5,  # < 30
            net_pnl_usdc=Decimal("500"),
            candidates_accepted=10,
            candidates_rejected=0,
            rejection_counts={},
            max_drawdown_pct=10.0,
            coverage_by_market={"m1": _mock_coverage(0.9)},
        )
        assert label == "data_insufficient"
        assert "30" in rationale

    def test_data_insufficient_low_coverage(self):
        label, _rationale = classify_credibility(
            trades_executed=50,
            net_pnl_usdc=Decimal("500"),
            candidates_accepted=50,
            candidates_rejected=0,
            rejection_counts={},
            max_drawdown_pct=10.0,
            coverage_by_market={f"m{i}": _mock_coverage(0.2) for i in range(5)},
        )
        assert label == "data_insufficient"

    def test_data_insufficient_mostly_unsupported(self):
        label, _rationale = classify_credibility(
            trades_executed=0,
            net_pnl_usdc=Decimal("0"),
            candidates_accepted=0,
            candidates_rejected=100,
            rejection_counts={"unsupported_trade_structure": 85},  # 85%
            max_drawdown_pct=0.0,
            coverage_by_market={"m1": _mock_coverage(0.8)},
        )
        assert label == "data_insufficient"

    def test_not_credible_negative_pnl(self):
        label, _rationale = classify_credibility(
            trades_executed=50,
            net_pnl_usdc=Decimal("-100"),  # negative
            candidates_accepted=50,
            candidates_rejected=10,
            rejection_counts={},
            max_drawdown_pct=10.0,
            coverage_by_market={f"m{i}": _mock_coverage(0.8) for i in range(5)},
        )
        assert label == "not_credible"

    def test_not_credible_high_drawdown(self):
        label, _rationale = classify_credibility(
            trades_executed=50,
            net_pnl_usdc=Decimal("100"),
            candidates_accepted=50,
            candidates_rejected=10,
            rejection_counts={},
            max_drawdown_pct=60.0,  # > 50%
            coverage_by_market={f"m{i}": _mock_coverage(0.8) for i in range(5)},
        )
        assert label == "not_credible"

    def test_not_credible_null_beats_strategy(self):
        label, _rationale = classify_credibility(
            trades_executed=50,
            net_pnl_usdc=Decimal("100"),
            candidates_accepted=50,
            candidates_rejected=10,
            rejection_counts={},
            max_drawdown_pct=10.0,
            coverage_by_market={f"m{i}": _mock_coverage(0.8) for i in range(5)},
            null_pnl=Decimal("200"),  # null > strategy
        )
        assert label == "not_credible"

    def test_credible_positive_all_gates_pass(self):
        label, _rationale = classify_credibility(
            trades_executed=50,
            net_pnl_usdc=Decimal("500"),
            candidates_accepted=50,
            candidates_rejected=10,
            rejection_counts={},
            max_drawdown_pct=15.0,
            coverage_by_market={f"m{i}": _mock_coverage(0.9) for i in range(10)},
        )
        assert label == "credible_positive"

    def test_inconclusive_middleground(self):
        label, _rationale = classify_credibility(
            trades_executed=50,
            net_pnl_usdc=Decimal("50"),
            candidates_accepted=50,
            candidates_rejected=10,
            rejection_counts={},
            max_drawdown_pct=30.0,  # > 25% but ≤ 50%
            coverage_by_market={f"m{i}": _mock_coverage(0.8) for i in range(5)},
        )
        assert label in ("inconclusive", "not_credible")  # either is valid for this borderline
