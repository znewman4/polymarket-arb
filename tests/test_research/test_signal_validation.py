"""Tests for the DeepSeek signal-validation cycle.

RESEARCH-ONLY / SIMULATED. No live trading paths exercised.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from polymarket_arb.research.signal_validation import (
    SignalValidationConfig,
    _align,
    _is_stale_relative_to,
    _MarketView,
    _slug_jaccard,
    _slug_tokens,
    build_candidates,
    independence_diagnostics,
)
from polymarket_arb.storage.base import MarketRow


def _market(mid: str, slug: str) -> MarketRow:
    return MarketRow(
        id=mid,
        condition_id=f"cond-{mid}",
        slug=slug,
        question=slug.replace("-", " "),
        description=None,
        end_date_ms=None,
        start_date_ms=None,
        closed_at_ms=None,
        resolved_at_ms=None,
        active=True,
        closed=False,
        archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=[f"tok-{mid}", f"tok-{mid}-no"],
        volume=None,
        liquidity=None,
        event_id=None,
        neg_risk=False,
        text_hash="",
        schema_version=1,
        ingested_ts_ms=0,
    )


def _flat_then_jump(start_ts: int, base_price: float, n: int, step_ms: int = 60_000) -> list[tuple[int, float]]:
    return [(start_ts + i * step_ms, base_price) for i in range(n)]


def _ticks_with_movement(start_ts: int, prices: list[float], step_ms: int = 60_000) -> list[tuple[int, float]]:
    return [(start_ts + i * step_ms, p) for i, p in enumerate(prices)]


def test_slug_tokens_drops_stopwords():
    toks = _slug_tokens("will-the-lakers-win-the-2026-nba-finals")
    assert "lakers" in toks
    assert "2026" in toks
    assert "nba" in toks
    assert "finals" in toks
    assert "will" not in toks
    assert "the" not in toks


def test_slug_jaccard_basic():
    a = _slug_tokens("will-the-lakers-win-the-2026-nba-finals")
    b = _slug_tokens("will-the-celtics-win-the-2026-nba-finals")
    j = _slug_jaccard(a, b)
    assert 0.5 < j < 1.0


def test_align_forward_fills():
    a = [(1, 0.1), (3, 0.2)]
    b = [(2, 0.5), (4, 0.6)]
    aligned = _align(a, b)
    # First valid alignment is at ts=2 (both have a prior tick).
    assert aligned[0][0] == 2
    # Last alignment at ts=4 forward-fills A's last price.
    assert aligned[-1][0] == 4
    assert aligned[-1][1] == 0.2
    assert aligned[-1][2] == 0.6


def test_is_stale_relative_to_requires_window():
    # Build two views with different last_move timestamps.
    day = 24 * 60 * 60 * 1000
    stale = _MarketView(
        market=_market("A", "alpha-bravo-charlie"),
        token_id="tok-A",
        slug_tokens={"alpha", "bravo", "charlie"},
        ticks=[(0, 0.5), (day, 0.5)],
        last_move_ts_ms=day,
        last_move_price=0.5,
        first_tick_ts_ms=0,
        last_tick_ts_ms=10 * day,
    )
    fresh = _MarketView(
        market=_market("B", "alpha-bravo-charlie-2"),
        token_id="tok-B",
        slug_tokens={"alpha", "bravo", "charlie"},
        ticks=[(0, 0.4), (10 * day, 0.6)],
        last_move_ts_ms=10 * day,
        last_move_price=0.6,
        first_tick_ts_ms=0,
        last_tick_ts_ms=15 * day,
    )
    assert _is_stale_relative_to(stale, fresh, 7 * day) is True
    # Reverse should NOT be stale.
    assert _is_stale_relative_to(fresh, stale, 7 * day) is False


def test_aggregate_independence_diagnostics_counts_unique_pairs():
    from polymarket_arb.research.signal_validation import SimulatedTrade
    trades = [
        SimulatedTrade(
            hypothesis_id=f"h{i}",
            simulator="stale_market_convergence_v2",
            relationship_type="stale_related_market",
            family_label="stale_related_market",
            market_id_a="A1",
            market_id_b=f"B{i}",
            token_id_a="tA",
            token_id_b=f"tB{i}",
            entry_ts_ms=1_000_000 + i,
            exit_ts_ms=2_000_000 + i,
            entry_price_a=0.5, entry_price_b=0.4,
            exit_price_a=0.45, exit_price_b=0.45,
            entry_cost_per_dollar=0.45,
            edge_implied_return_pct=0.1,
            realised_return_pct=0.05,
            holding_period_ms=1_000_000,
            slippage_haircut_pct=0.005,
            cluster_key="alpha+bravo",
            is_control=False,
            control_kind="",
            notes="",
        )
        for i in range(5)
    ]
    indep = independence_diagnostics(trades)
    assert indep["trade_count"] == 5
    assert indep["unique_market_id_a"] == 1
    assert indep["unique_market_id_b"] == 5
    # Single-market dominance flag — matches the prior-run GTA VI case.
    assert indep["max_share_single_market_id_a"] == pytest.approx(1.0)


def test_build_candidates_flags_stale_and_near_duplicate():
    day = 24 * 60 * 60 * 1000
    views = [
        _MarketView(
            market=_market("A", "will-lakers-win-2026-nba-finals"),
            token_id="tok-A",
            slug_tokens=_slug_tokens("will-lakers-win-2026-nba-finals"),
            ticks=[(0, 0.5), (day, 0.5)],
            last_move_ts_ms=day,
            last_move_price=0.5,
            first_tick_ts_ms=0,
            last_tick_ts_ms=2 * day,
        ),
        _MarketView(
            market=_market("B", "will-lakers-win-2026-nba-finals-mvp"),
            token_id="tok-B",
            slug_tokens=_slug_tokens("will-lakers-win-2026-nba-finals-mvp"),
            ticks=[(0, 0.4), (15 * day, 0.6)],
            last_move_ts_ms=15 * day,
            last_move_price=0.6,
            first_tick_ts_ms=0,
            last_tick_ts_ms=25 * day,
        ),
        _MarketView(
            market=_market("C", "will-lakers-win-2026-nba-finals-duplicate"),
            token_id="tok-C",
            slug_tokens=_slug_tokens("will-lakers-win-2026-nba-finals-duplicate"),
            ticks=[(0, 0.4), (5 * day, 0.5)],
            last_move_ts_ms=5 * day,
            last_move_price=0.5,
            first_tick_ts_ms=0,
            last_tick_ts_ms=5 * day,
        ),
    ]
    cfg = SignalValidationConfig(run_id="test", near_duplicate_jaccard=0.5, stale_window_ms=7 * day)
    cands = build_candidates(views, cfg)
    # At least one stale pair flagged (A is stale relative to B).
    assert len(cands["stale_related_market"]) >= 1
    # And some near-duplicates among the high-jaccard ones.
    assert len(cands["near_duplicate"]) >= 1
