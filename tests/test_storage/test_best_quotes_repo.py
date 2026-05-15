from __future__ import annotations

from decimal import Decimal

from polymarket_arb.storage.base import BestQuote
from polymarket_arb.storage.parquet.best_quotes_repo import ParquetBestQuotesRepository


def _quote(token_id: str = "tok") -> BestQuote:
    return BestQuote(
        token_id=token_id,
        timestamp_ms=1,
        best_bid=Decimal("0.48"),
        best_bid_size=Decimal("10"),
        best_ask=Decimal("0.52"),
        best_ask_size=Decimal("11"),
        midpoint=Decimal("0.50"),
        spread=Decimal("0.04"),
        schema_version=1,
        ingested_ts_ms=1,
    )


def test_append_and_latest_quote(tmp_data_root):
    repo = ParquetBestQuotesRepository(tmp_data_root, row_group_size=4)
    assert repo.append_many([_quote("a"), _quote("b")]) == 2
    latest = repo.latest("a")
    assert latest is not None
    assert latest.midpoint == Decimal("0.50000000")
