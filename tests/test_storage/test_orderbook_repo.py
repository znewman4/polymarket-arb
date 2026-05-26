from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from polymarket_arb.storage.base import OrderbookLevel, OrderbookSnapshot
from polymarket_arb.storage.parquet._writer import write_table_part
from polymarket_arb.storage.parquet.orderbook_repo import ParquetOrderbookRepository
from polymarket_arb.storage.parquet.schemas import ORDERBOOK_SNAPSHOTS_SCHEMA_V1


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


def test_latest_books_bulk_reads_previous_day_when_today_is_empty(tmp_data_root):
    from dataclasses import asdict

    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    snap = _book("yesterday_tok", timestamp_ms=99)
    write_table_part(
        tmp_data_root,
        "orderbook_snapshots",
        ORDERBOOK_SNAPSHOTS_SCHEMA_V1,
        [asdict(snap)],
        ts=yesterday,
    )

    latest = ParquetOrderbookRepository(tmp_data_root).latest_books_bulk(["yesterday_tok"])

    assert latest["yesterday_tok"].timestamp_ms == 99


def test_latest_books_bulk_ignores_old_partitions(tmp_data_root):
    """Data written outside the recent read window must not appear."""
    from dataclasses import asdict

    yesterday = datetime(2000, 1, 1, tzinfo=timezone.utc)
    snap = _book("stale_tok", timestamp_ms=99)
    write_table_part(
        tmp_data_root,
        "orderbook_snapshots",
        ORDERBOOK_SNAPSHOTS_SCHEMA_V1,
        [asdict(snap)],
        ts=yesterday,
    )
    repo = ParquetOrderbookRepository(tmp_data_root)
    assert repo.latest_books_bulk(["stale_tok"]) == {}
