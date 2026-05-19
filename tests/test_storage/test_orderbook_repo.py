from __future__ import annotations

from decimal import Decimal

from polymarket_arb.storage.base import OrderbookLevel, OrderbookSnapshot
from polymarket_arb.storage.parquet.orderbook_repo import ParquetOrderbookRepository


def _book(
    token_id: str = "tok",
    *,
    timestamp_ms: int = 1,
    bid: str = "0.48",
    ask: str = "0.52",
) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        token_id=token_id,
        condition_id="0xc",
        market_slug="slug",
        timestamp_ms=timestamp_ms,
        bids=[OrderbookLevel(Decimal(bid), Decimal("10"))],
        asks=[OrderbookLevel(Decimal(ask), Decimal("11"))],
        book_hash="h",
        source="rest",
        schema_version=1,
        ingested_ts_ms=timestamp_ms,
    )


def test_append_and_latest_book(tmp_data_root):
    repo = ParquetOrderbookRepository(tmp_data_root, row_group_size=4)
    assert repo.append_snapshots([_book("a"), _book("b")]) == 2
    latest = repo.latest_book("a")
    assert latest is not None
    assert latest.token_id == "a"
    assert latest.bids[0].price == Decimal("0.48")


def test_latest_books_bulk_returns_latest_for_each_token(tmp_data_root):
    repo = ParquetOrderbookRepository(tmp_data_root, row_group_size=4)
    repo.append_snapshots([
        _book("a", timestamp_ms=1, bid="0.40", ask="0.50"),
        _book("a", timestamp_ms=2, bid="0.60", ask="0.70"),
        _book("b", timestamp_ms=3, bid="0.20", ask="0.30"),
    ])

    latest = repo.latest_books_bulk(["a", "b", "missing", "a"])

    assert set(latest) == {"a", "b"}
    assert latest["a"].timestamp_ms == 2
    assert latest["a"].bids[0].price == Decimal("0.60")
    assert latest["b"].timestamp_ms == 3


def test_latest_books_bulk_empty_inputs_return_empty(tmp_data_root):
    repo = ParquetOrderbookRepository(tmp_data_root, row_group_size=4)
    assert repo.latest_books_bulk([]) == {}
    assert repo.latest_books_bulk(["missing"]) == {}
