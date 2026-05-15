from __future__ import annotations

from decimal import Decimal

from polymarket_arb.storage.base import OrderbookLevel, OrderbookSnapshot
from polymarket_arb.storage.parquet.orderbook_repo import ParquetOrderbookRepository


def _book(token_id: str = "tok") -> OrderbookSnapshot:
    return OrderbookSnapshot(
        token_id=token_id,
        condition_id="0xc",
        market_slug="slug",
        timestamp_ms=1,
        bids=[OrderbookLevel(Decimal("0.48"), Decimal("10"))],
        asks=[OrderbookLevel(Decimal("0.52"), Decimal("11"))],
        book_hash="h",
        source="rest",
        schema_version=1,
        ingested_ts_ms=1,
    )


def test_append_and_latest_book(tmp_data_root):
    repo = ParquetOrderbookRepository(tmp_data_root, row_group_size=4)
    assert repo.append_snapshots([_book("a"), _book("b")]) == 2
    latest = repo.latest_book("a")
    assert latest is not None
    assert latest.token_id == "a"
    assert latest.bids[0].price == Decimal("0.48")
