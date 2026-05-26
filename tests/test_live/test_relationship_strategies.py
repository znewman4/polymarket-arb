"""Tests for live relationship strategy wiring."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import click
import pytest

from polymarket_arb.cli import live
from polymarket_arb.live.agent_loop import AgentState
from polymarket_arb.storage.base import OrderbookLevel, OrderbookSnapshot, RelationshipCandidateRow
from polymarket_arb.storage.parquet.orderbook_repo import ParquetOrderbookRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

_TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _rel(
    relationship_id: str = "rel_001",
    *,
    validation_status: str = "accepted",
    relationship_type: str = "nested_a_implies_b",
    token_id_a_yes: str | None = "a_yes",
    token_id_a_no: str | None = "a_no",
    token_id_b_yes: str | None = "b_yes",
    token_id_b_no: str | None = "b_no",
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=relationship_id,
        market_id_a=f"{relationship_id}_market_a",
        market_id_b=f"{relationship_id}_market_b",
        condition_id_a=f"{relationship_id}_cond_a",
        condition_id_b=f"{relationship_id}_cond_b",
        token_id_a_yes=token_id_a_yes,
        token_id_a_no=token_id_a_no,
        token_id_b_yes=token_id_b_yes,
        token_id_b_no=token_id_b_no,
        question_a="Will A happen?",
        question_b="Will B happen?",
        relationship_type=relationship_type,
        entity_match_score=0.9,
        time_scope_match_score=0.9,
        resolution_criteria_match_score=0.9,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.8,
        model_confidence=1.0,
        final_confidence=0.8,
        validation_status=validation_status,
        rejection_reasons_json="[]",
        rationale_summary="test relationship",
        evidence_json="{}",
        rulebook_id="relationship_v1",
        rulebook_version=1,
        rulebook_content_hash="abc123",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _book(
    token_id: str,
    *,
    bid: str | None,
    ask: str | None,
    ts_ms: int = _TS - 100,
) -> OrderbookSnapshot:
    bids = [] if bid is None else [OrderbookLevel(price=Decimal(bid), size=Decimal("100"))]
    asks = [] if ask is None else [OrderbookLevel(price=Decimal(ask), size=Decimal("100"))]
    return OrderbookSnapshot(
        token_id=token_id,
        condition_id=None,
        market_slug=None,
        timestamp_ms=ts_ms,
        bids=bids,
        asks=asks,
        book_hash=None,
        source="rest",
        schema_version=1,
        ingested_ts_ms=ts_ms,
    )


def _seed_orderbook_snapshots(data_root, *token_ids: str) -> None:
    """Write one minimal orderbook snapshot per token_id into the orderbook lake."""
    repo = ParquetOrderbookRepository(data_root)
    for tid in token_ids:
        repo.append_snapshot(_book(tid, bid="0.49", ask="0.51"))


def _state(*books: OrderbookSnapshot, ts_ms: int = _TS) -> AgentState:
    return AgentState(
        ts_ms=ts_ms,
        watched_tokens=[book.token_id for book in books],
        latest_book_by_token={book.token_id: book for book in books},
    )


def test_relationship_strategy_registry_exposes_expected_names() -> None:
    assert set(live._STRATEGIES) == {
        "noop",
        "relationship_diagnostic",
        "relationship_aggressive",
    }


def test_load_live_relationships_filters_to_accepted_and_manual_review(tmp_data_root) -> None:
    _seed_orderbook_snapshots(tmp_data_root, "a_yes", "b_yes")
    repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
    repo.append_many([
        _rel("rel_accepted", validation_status="accepted"),
        _rel("rel_review", validation_status="needs_manual_review"),
        _rel("rel_rejected", validation_status="rejected"),
    ])

    loaded = live._load_live_relationships(tmp_data_root)

    assert {rel.relationship_id for rel in loaded} == {"rel_accepted", "rel_review"}


def test_relationship_auto_tokens_include_all_token_fields_and_dedupe() -> None:
    rels = [
        _rel(
            "rel_1",
            token_id_a_yes="a_yes",
            token_id_a_no="a_no",
            token_id_b_yes="b_yes",
            token_id_b_no="b_no",
        ),
        _rel(
            "rel_2",
            token_id_a_yes="b_no",
            token_id_a_no=None,
            token_id_b_yes="c_yes",
            token_id_b_no="a_yes",
        ),
    ]

    assert live._relationship_token_ids(rels) == ["a_yes", "a_no", "b_yes", "b_no", "c_yes"]
    assert live._resolve_watched_tokens(
        "manual,b_yes",
        rels,
        strategy_auto_tokens=True,
    ) == ["manual", "b_yes", "a_yes", "a_no", "b_no", "c_yes"]


def test_watched_tokens_required_without_auto_tokens() -> None:
    with pytest.raises(click.UsageError):
        live._resolve_watched_tokens("", [], strategy_auto_tokens=False)


def test_relationship_strategy_skips_missing_yes_books_or_incomplete_books(tmp_data_root) -> None:
    _seed_orderbook_snapshots(tmp_data_root, "a_yes", "b_yes")
    repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
    repo.append_many([_rel()])
    strategy = live._build_strategy(
        "relationship_diagnostic",
        data_root=tmp_data_root,
        run_id="live_test",
    )

    assert strategy(_state(_book("a_yes", bid="0.69", ask="0.71"))) == []
    assert strategy(_state(
        _book("a_yes", bid="0.69", ask="0.71"),
        _book("b_yes", bid=None, ask="0.41"),
    )) == []


def test_relationship_diagnostic_emits_two_token_mapped_intents(tmp_data_root) -> None:
    _seed_orderbook_snapshots(tmp_data_root, "a_yes", "b_yes")
    repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
    repo.append_many([_rel(relationship_type="nested_a_implies_b")])
    strategy = live._build_strategy(
        "relationship_diagnostic",
        data_root=tmp_data_root,
        run_id="live_test",
    )

    intents = strategy(_state(
        _book("a_yes", bid="0.69", ask="0.71"),
        _book("b_yes", bid="0.39", ask="0.41"),
    ))

    assert len(intents) == 2
    assert [intent.token_id for intent in intents] == ["a_no", "b_yes"]
    assert [intent.market_id for intent in intents] == ["rel_001_market_a", "rel_001_market_b"]
    assert [intent.side for intent in intents] == ["buy", "buy"]
    assert [intent.detail for intent in intents] == [
        {
            "gross_edge": "0.30",
            "relationship_id": "rel_001",
            "relationship_type": "nested_a_implies_b",
        },
        {
            "gross_edge": "0.30",
            "relationship_id": "rel_001",
            "relationship_type": "nested_a_implies_b",
        },
    ]
    assert [intent.size for intent in intents] == [Decimal("50"), Decimal("50")]
    assert [intent.price for intent in intents] == [Decimal("0.30"), Decimal("0.40")]
    assert [intent.strategy_id for intent in intents] == [
        "relationship_diagnostic",
        "relationship_diagnostic",
    ]
    assert intents[0].id.endswith(":a")
    assert intents[1].id.endswith(":b")


def test_relationship_aggressive_accepts_signal_diagnostic_rejects(tmp_data_root) -> None:
    _seed_orderbook_snapshots(tmp_data_root, "a_yes", "b_yes")
    repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
    repo.append_many([_rel(relationship_type="nested_a_implies_b")])
    state = _state(
        _book("a_yes", bid="0.53", ask="0.55"),
        _book("b_yes", bid="0.49", ask="0.51"),
    )

    diagnostic = live._build_strategy(
        "relationship_diagnostic",
        data_root=tmp_data_root,
        run_id="live_test_diag",
    )
    aggressive = live._build_strategy(
        "relationship_aggressive",
        data_root=tmp_data_root,
        run_id="live_test_agg",
    )

    assert diagnostic(state) == []
    assert len(aggressive(state)) == 2
