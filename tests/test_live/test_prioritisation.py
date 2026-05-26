"""Tests for trade prioritisation, deduplication, liquidity guard, and cooldown."""

from __future__ import annotations

from decimal import Decimal

from polymarket_arb.cli import live
from polymarket_arb.live.agent_loop import AgentState
from polymarket_arb.storage.base import (
    OrderbookLevel,
    OrderbookSnapshot,
    RelationshipCandidateRow,
    StrategyCandidateRow,
)
from polymarket_arb.storage.parquet.orderbook_repo import ParquetOrderbookRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)
from polymarket_arb.strategies.nesting_contradiction import AlignedPricePoint

_TS = 1_700_000_000_000


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _rel(
    relationship_id: str = "rel_001",
    *,
    final_confidence: float = 0.8,
    token_id_a_yes: str = "a_yes",
    token_id_b_yes: str = "b_yes",
    market_id_a: str = "market_a",
    market_id_b: str = "market_b",
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=relationship_id,
        market_id_a=market_id_a,
        market_id_b=market_id_b,
        condition_id_a=None,
        condition_id_b=None,
        token_id_a_yes=token_id_a_yes,
        token_id_a_no=token_id_a_yes.replace("yes", "no"),
        token_id_b_yes=token_id_b_yes,
        token_id_b_no=token_id_b_yes.replace("yes", "no"),
        question_a="Q A",
        question_b="Q B",
        relationship_type="nested_a_implies_b",
        entity_match_score=0.9,
        time_scope_match_score=0.9,
        resolution_criteria_match_score=0.9,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.8,
        model_confidence=1.0,
        final_confidence=final_confidence,
        validation_status="accepted",
        rejection_reasons_json="[]",
        rationale_summary="",
        evidence_json="{}",
        rulebook_id="test",
        rulebook_version=1,
        rulebook_content_hash="abc",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _candidate(
    relationship_id: str = "rel_001",
    *,
    gross_edge: str = "0.10",
    token_id_a: str = "a_no",
    token_id_b: str = "b_yes",
) -> StrategyCandidateRow:
    return StrategyCandidateRow(
        candidate_id=f"{relationship_id}_cand",
        run_id="test_run",
        relationship_id=relationship_id,
        market_id_a="market_a",
        market_id_b="market_b",
        token_id_a=token_id_a,
        token_id_b=token_id_b,
        relationship_type="nested_a_implies_b",
        signal_ts_ms=_TS,
        price_a=Decimal("0.55"),
        price_b=Decimal("0.40"),
        price_a_ts_ms=_TS,
        price_b_ts_ms=_TS,
        inequality_violated="a_implies_b",
        theoretical_edge=Decimal(gross_edge),
        gross_edge=Decimal(gross_edge),
        estimated_fee=Decimal("0"),
        estimated_slippage=Decimal("0.005"),
        net_edge_after_costs=Decimal(gross_edge) - Decimal("0.005"),
        execution_model="price_history_only",
        execution_model_confidence=0.4,
        accepted_for_simulation=True,
        rejection_reason=None,
        simulated_position_json="{}",
        stake_usdc=Decimal("50"),
        expected_payout_json="{}",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _point(
    *,
    price_a: str = "0.55",
    price_b: str = "0.40",
    staleness_a_ms: int = 100,
    staleness_b_ms: int = 100,
) -> AlignedPricePoint:
    return AlignedPricePoint(
        ts_ms=_TS,
        price_a=Decimal(price_a),
        price_b=Decimal(price_b),
        price_a_ts_ms=_TS - staleness_a_ms,
        price_b_ts_ms=_TS - staleness_b_ms,
        staleness_a_ms=staleness_a_ms,
        staleness_b_ms=staleness_b_ms,
        alignment_quality="fresh",
    )


def _book(
    token_id: str,
    *,
    bid: str = "0.49",
    ask: str = "0.51",
    size: str = "100",
    ts_ms: int = _TS - 100,
) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        token_id=token_id,
        condition_id=None,
        market_slug=None,
        timestamp_ms=ts_ms,
        bids=[OrderbookLevel(price=Decimal(bid), size=Decimal(size))],
        asks=[OrderbookLevel(price=Decimal(ask), size=Decimal(size))],
        book_hash=None,
        source="rest",
        schema_version=1,
        ingested_ts_ms=ts_ms,
    )


def _state(*books: OrderbookSnapshot, ts_ms: int = _TS) -> AgentState:
    return AgentState(
        ts_ms=ts_ms,
        watched_tokens=[b.token_id for b in books],
        latest_book_by_token={b.token_id: b for b in books},
    )


def _seed(tmp_data_root, *token_ids: str) -> None:
    book_repo = ParquetOrderbookRepository(tmp_data_root)
    for tid in token_ids:
        book_repo.append_snapshot(_book(tid, bid="0.69", ask="0.71", size="100"))


# ─── Task 1: _score_candidate ────────────────────────────────────────────────


def test_score_candidate_returns_float_in_unit_interval() -> None:
    score = live._score_candidate(_candidate(gross_edge="0.10"), _rel(), _point())
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_score_candidate_higher_gross_edge_wins() -> None:
    rel = _rel()
    point = _point()
    low = live._score_candidate(_candidate(gross_edge="0.05"), rel, point)
    high = live._score_candidate(_candidate(gross_edge="0.50"), rel, point)
    assert high > low


def test_score_candidate_stale_prices_score_lower() -> None:
    rel = _rel()
    cand = _candidate()
    fresh = live._score_candidate(cand, rel, _point(staleness_a_ms=100, staleness_b_ms=100))
    stale = live._score_candidate(cand, rel, _point(staleness_a_ms=35_000, staleness_b_ms=35_000))
    assert fresh > stale


def test_score_candidate_higher_confidence_wins() -> None:
    cand = _candidate()
    point = _point()
    low = live._score_candidate(cand, _rel(final_confidence=0.3), point)
    high = live._score_candidate(cand, _rel(final_confidence=0.9), point)
    assert high > low


# ─── Task 1: top-N enforcement ───────────────────────────────────────────────


def test_strategy_returns_at_most_max_pairs_per_tick_pairs(tmp_data_root) -> None:
    rels = [_rel(
        f"rel_{i:03d}",
        token_id_a_yes=f"a_yes_{i}",
        token_id_b_yes=f"b_yes_{i}",
        market_id_a=f"market_a_{i}",
        market_id_b=f"market_b_{i}",
    ) for i in range(10)]

    ParquetRelationshipCandidatesRepository(tmp_data_root).append_many(rels)
    for i in range(10):
        _seed(tmp_data_root, f"a_yes_{i}", f"b_yes_{i}")

    books = {}
    for i in range(10):
        books[f"a_yes_{i}"] = _book(f"a_yes_{i}", bid="0.69", ask="0.71", size="100")
        books[f"b_yes_{i}"] = _book(f"b_yes_{i}", bid="0.39", ask="0.41", size="100")
    state = AgentState(ts_ms=_TS, watched_tokens=list(books), latest_book_by_token=books)

    config = live._RelationshipStrategyConfig(
        strategy_id="relationship_diagnostic",
        min_gross_edge=0.05,
        min_net_edge=0.02,
        max_pairs_per_tick=3,
    )
    intents = live._make_relationship_strategy(config, data_root=tmp_data_root, run_id="t")(state)
    # max 3 pairs = 6 legs
    assert 0 < len(intents) <= 6


# ─── Task 2: per-market deduplication ────────────────────────────────────────


def test_shared_market_only_traded_once_per_tick(tmp_data_root) -> None:
    rel_a = _rel("rel_001", token_id_a_yes="a_yes", token_id_b_yes="b_yes",
                 market_id_a="shared_market", market_id_b="market_b1")
    rel_b = _rel("rel_002", token_id_a_yes="a_yes", token_id_b_yes="c_yes",
                 market_id_a="shared_market", market_id_b="market_c1")

    ParquetRelationshipCandidatesRepository(tmp_data_root).append_many([rel_a, rel_b])
    _seed(tmp_data_root, "a_yes", "b_yes", "c_yes")

    books = {
        "a_yes": _book("a_yes", bid="0.69", ask="0.71", size="100"),
        "b_yes": _book("b_yes", bid="0.39", ask="0.41", size="100"),
        "c_yes": _book("c_yes", bid="0.39", ask="0.41", size="100"),
    }
    state = AgentState(ts_ms=_TS, watched_tokens=list(books), latest_book_by_token=books)

    config = live._RelationshipStrategyConfig(
        strategy_id="relationship_diagnostic",
        min_gross_edge=0.05,
        min_net_edge=0.02,
        max_pairs_per_tick=5,
    )
    intents = live._make_relationship_strategy(config, data_root=tmp_data_root, run_id="t")(state)

    # Only one pair fires; shared_market appears exactly once
    assert "shared_market" in {i.market_id for i in intents}
    assert len(intents) == 2


# ─── Task 3: liquidity guard ─────────────────────────────────────────────────


def test_low_liquidity_market_skipped_despite_high_edge(tmp_data_root) -> None:
    ParquetRelationshipCandidatesRepository(tmp_data_root).append_many([_rel()])
    # Size=0.01 -> ask_depth = 0.71*0.01 = 0.0071 USDC << min 10
    _seed_tiny = lambda: [  # noqa: E731
        ParquetOrderbookRepository(tmp_data_root).append_snapshot(
            _book(tid, bid="0.69", ask="0.71", size="0.01")
        )
        for tid in ("a_yes", "b_yes")
    ]
    _seed_tiny()

    books = {
        "a_yes": _book("a_yes", bid="0.69", ask="0.71", size="0.01"),
        "b_yes": _book("b_yes", bid="0.39", ask="0.41", size="0.01"),
    }
    state = AgentState(ts_ms=_TS, watched_tokens=list(books), latest_book_by_token=books)

    config = live._RelationshipStrategyConfig(
        strategy_id="relationship_diagnostic",
        min_gross_edge=0.05,
        min_net_edge=0.02,
        min_liquidity_usdc=Decimal("10"),
    )
    intents = live._make_relationship_strategy(config, data_root=tmp_data_root, run_id="t")(state)
    assert intents == []


def test_check_liquidity_passes_when_depth_sufficient() -> None:
    rel = _rel()
    books = {
        "a_yes": _book("a_yes", bid="0.49", ask="0.51", size="100"),
        "b_yes": _book("b_yes", bid="0.49", ask="0.51", size="100"),
    }
    state = AgentState(ts_ms=_TS, watched_tokens=list(books), latest_book_by_token=books)
    assert live._check_liquidity(rel, state, Decimal("10")) is True


def test_check_liquidity_fails_when_book_missing() -> None:
    rel = _rel()
    state = AgentState(ts_ms=_TS, watched_tokens=[], latest_book_by_token={})
    assert live._check_liquidity(rel, state, Decimal("10")) is False


# ─── Task 4: cooldown ────────────────────────────────────────────────────────


def _make_strategy_with_liquid_books(tmp_data_root):
    ParquetRelationshipCandidatesRepository(tmp_data_root).append_many([_rel()])
    _seed(tmp_data_root, "a_yes", "b_yes")
    books = {
        "a_yes": _book("a_yes", bid="0.69", ask="0.71", size="100"),
        "b_yes": _book("b_yes", bid="0.39", ask="0.41", size="100"),
    }
    config = live._RelationshipStrategyConfig(
        strategy_id="relationship_diagnostic",
        min_gross_edge=0.05,
        min_net_edge=0.02,
    )
    return live._make_relationship_strategy(config, data_root=tmp_data_root, run_id="t"), books


def test_cooldown_prevents_same_pair_within_window(tmp_data_root) -> None:
    strategy, books = _make_strategy_with_liquid_books(tmp_data_root)

    state1 = AgentState(ts_ms=_TS, watched_tokens=list(books), latest_book_by_token=books)
    assert len(strategy(state1)) == 2  # fires

    # 60 s later — still in 5-minute window
    state2 = AgentState(ts_ms=_TS + 60_000, watched_tokens=list(books), latest_book_by_token=books)
    assert strategy(state2) == []  # suppressed


def test_cooldown_allows_pair_after_window_expires(tmp_data_root) -> None:
    strategy, books = _make_strategy_with_liquid_books(tmp_data_root)

    state1 = AgentState(ts_ms=_TS, watched_tokens=list(books), latest_book_by_token=books)
    assert len(strategy(state1)) == 2  # fires

    # 300_001 ms later — past the 5-minute window
    state3 = AgentState(ts_ms=_TS + 300_001, watched_tokens=list(books), latest_book_by_token=books)
    assert len(strategy(state3)) == 2  # fires again
