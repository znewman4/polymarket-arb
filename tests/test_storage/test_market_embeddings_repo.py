from __future__ import annotations

import time

from polymarket_arb.storage.base import MarketEmbeddingRow
from polymarket_arb.storage.parquet.market_embeddings_repo import (
    ParquetMarketEmbeddingsRepository,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _row(*, market_id: str = "m1", text_hash: str = "h1",
         space: str = "mock@v1") -> MarketEmbeddingRow:
    return MarketEmbeddingRow(
        market_id=market_id, embedding_space=space, text_hash=text_hash,
        dimensions=4, vector=[0.1, 0.2, 0.3, 0.4],
        model_name="mock", schema_version=1, ingested_ts_ms=_now_ms(),
    )


def test_upsert_then_get_latest(tmp_data_root):
    repo = ParquetMarketEmbeddingsRepository(tmp_data_root, row_group_size=4)
    assert repo.upsert_many([_row(market_id="m1"), _row(market_id="m2")]) == 2
    e = repo.get_latest("m1", "mock@v1")
    assert e is not None
    assert e.market_id == "m1" and e.dimensions == 4


def test_known_text_hashes_dedup(tmp_data_root):
    repo = ParquetMarketEmbeddingsRepository(tmp_data_root, row_group_size=4)
    repo.upsert_many([
        _row(market_id="m1", text_hash="h1"),
        _row(market_id="m2", text_hash="h2"),
        _row(market_id="m3", text_hash="h1"),
    ])
    seen = repo.known_text_hashes("mock@v1")
    assert seen == {"h1", "h2"}
    assert repo.known_text_hashes("nonexistent@v1") == set()
