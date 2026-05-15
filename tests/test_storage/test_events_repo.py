from __future__ import annotations

import time

from polymarket_arb.storage.base import EventRow
from polymarket_arb.storage.parquet.events_repo import ParquetEventsRepository


def _now_ms() -> int:
    return int(time.time() * 1000)


def _evt(id_: str, *, title: str = "Example Event") -> EventRow:
    return EventRow(
        id=id_,
        ticker=None,
        slug=f"slug-{id_}",
        title=title,
        description="desc",
        start_date_ms=_now_ms() - 86_400_000,
        end_date_ms=_now_ms() + 86_400_000,
        market_ids=[f"m_{id_}_a", f"m_{id_}_b"],
        tags=["politics", "test"],
        schema_version=1,
        ingested_ts_ms=_now_ms(),
    )


def test_upsert_get_iter(tmp_data_root):
    repo = ParquetEventsRepository(tmp_data_root, row_group_size=4)
    assert repo.upsert_events([_evt("e1"), _evt("e2")]) == 2
    e1 = repo.get_event("e1")
    assert e1 is not None
    assert e1.id == "e1"
    assert sorted(r.id for r in repo.iter_events()) == ["e1", "e2"]


def test_returns_empty_on_fresh_lake(tmp_data_root):
    repo = ParquetEventsRepository(tmp_data_root)
    assert repo.get_event("anything") is None
    assert list(repo.iter_events()) == []
