from __future__ import annotations

from decimal import Decimal

import pytest

from polymarket_arb.nlp.embeddings import (
    embed_market,
    text_for_embedding,
    text_hash,
)
from polymarket_arb.nlp.mock_client import MockEmbeddingClient
from polymarket_arb.storage.base import MarketRow


def _market(question: str, description: str | None = None) -> MarketRow:
    return MarketRow(
        id="m1", condition_id="0xc", slug="s", question=question,
        description=description, end_date_ms=None, start_date_ms=None,
        closed_at_ms=None, resolved_at_ms=None,
        active=True, closed=False, archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=["a", "b"], volume=None, liquidity=None,
        event_id=None, neg_risk=False,
        text_hash="x", schema_version=1, ingested_ts_ms=0,
    )


def test_text_for_embedding_concatenates():
    m = _market("Q?", "Description goes here.")
    assert text_for_embedding(m) == "Q?\n\nDescription goes here."


def test_text_hash_deterministic():
    a = text_hash("hello")
    b = text_hash("hello")
    c = text_hash("world")
    assert a == b
    assert a != c
    assert len(a) == 64


@pytest.mark.asyncio
async def test_embed_market_round_trip():
    client = MockEmbeddingClient(dimensions=8)
    m = _market("Will X happen?")
    row = await embed_market(market=m, client=client,
                             embedding_space="mock-embed@v1")
    assert row.market_id == "m1"
    assert row.embedding_space == "mock-embed@v1"
    assert row.dimensions == 8 and len(row.vector) == 8
    assert row.text_hash == text_hash(text_for_embedding(m))
