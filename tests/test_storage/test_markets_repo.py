from __future__ import annotations

import time
from decimal import Decimal

from polymarket_arb.storage.base import MarketRow
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository


def _now_ms() -> int:
    return int(time.time() * 1000)


def _row(id_: str, *, question: str = "Will X happen?", active: bool = True,
         closed: bool = False, end_offset_ms: int = 86_400_000,
         text_suffix: str = "") -> MarketRow:
    return MarketRow(
        id=id_,
        condition_id=f"0xcond{id_}",
        slug=f"slug-{id_}",
        question=question,
        description="desc",
        end_date_ms=_now_ms() + end_offset_ms,
        start_date_ms=_now_ms() - 86_400_000,
        closed_at_ms=None,
        resolved_at_ms=None,
        active=active,
        closed=closed,
        archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=[f"tok_yes_{id_}", f"tok_no_{id_}"],
        volume=Decimal("100"),
        liquidity=Decimal("50"),
        event_id=f"evt_{id_}",
        neg_risk=False,
        text_hash=f"hash_{id_}{text_suffix}",
        schema_version=1,
        ingested_ts_ms=_now_ms(),
    )


def test_upsert_then_get(tmp_data_root):
    repo = ParquetMarketsRepository(tmp_data_root, row_group_size=4)
    n = repo.upsert_markets([_row("m1"), _row("m2")])
    assert n == 2
    fetched = repo.get_market("m1")
    assert fetched is not None
    assert fetched.id == "m1"
    assert fetched.condition_id == "0xcondm1"
    assert fetched.gamma_outcome_prices_snapshot == [Decimal("0.5"), Decimal("0.5")]


def test_get_returns_latest_after_two_writes(tmp_data_root):
    repo = ParquetMarketsRepository(tmp_data_root, row_group_size=4)
    older = _row("m1", text_suffix="_v1")
    repo.upsert_markets([older])
    # second write with newer ingested_ts_ms
    newer = _row("m1", text_suffix="_v2")
    repo.upsert_markets([newer])
    fetched = repo.get_market("m1")
    assert fetched is not None
    assert fetched.text_hash == "hash_m1_v2"


def test_iter_active_filters_inactive_and_past_end(tmp_data_root):
    repo = ParquetMarketsRepository(tmp_data_root, row_group_size=4)
    repo.upsert_markets([
        _row("active1"),
        _row("inactive", active=False),
        _row("closed", closed=True),
        _row("ended", end_offset_ms=-86_400_000),  # ended yesterday
    ])
    ids = sorted(r.id for r in repo.iter_active_markets())
    assert ids == ["active1"]


def test_search_filters_by_question(tmp_data_root):
    repo = ParquetMarketsRepository(tmp_data_root, row_group_size=4)
    repo.upsert_markets([
        _row("m1", question="Will Bitcoin close above 100k?"),
        _row("m2", question="Will the Eagles win Super Bowl?"),
        _row("m3", question="Will BTC double again?"),
    ])
    hits = repo.search("bitcoin", limit=10)
    ids = sorted(r.id for r in hits)
    assert ids == ["m1"]
    hits = repo.search("btc", limit=10)
    assert sorted(r.id for r in hits) == ["m3"]


def test_returns_empty_on_fresh_lake(tmp_data_root):
    repo = ParquetMarketsRepository(tmp_data_root)
    assert repo.get_market("anything") is None
    assert list(repo.iter_active_markets()) == []
    assert repo.search("bitcoin") == []
    assert repo.latest_count() == 0


def test_latest_count_reflects_dedup(tmp_data_root):
    repo = ParquetMarketsRepository(tmp_data_root, row_group_size=4)
    repo.upsert_markets([_row("m1"), _row("m2")])
    repo.upsert_markets([_row("m1")])  # m1 again
    assert repo.latest_count() == 2
