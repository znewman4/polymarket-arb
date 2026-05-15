from __future__ import annotations

from polymarket_arb.storage.base import RulebookEvaluationRow
from polymarket_arb.storage.parquet.rulebook_evaluations_repo import (
    ParquetRulebookEvaluationsRepository,
)


def _row(market_id: str = "m1") -> RulebookEvaluationRow:
    return RulebookEvaluationRow(
        evaluation_id=f"eval-{market_id}",
        extraction_id=f"ext-{market_id}",
        market_id=market_id,
        rulebook_id="ambiguity",
        rulebook_version=1,
        rulebook_content_hash="h" * 64,
        score=0.7,
        subscores_json='{"vague_deadline":0.7}',
        flags=["vague_deadline"],
        evaluated_ts_ms=1,
        schema_version=1,
        ingested_ts_ms=1,
    )


def test_append_and_recent(tmp_data_root):
    repo = ParquetRulebookEvaluationsRepository(tmp_data_root, row_group_size=4)
    assert repo.append_many([_row("m1"), _row("m2")]) == 2
    rows = repo.recent(limit=10)
    assert len(rows) == 2
    assert {r.market_id for r in rows} == {"m1", "m2"}


def test_empty_lake(tmp_data_root):
    repo = ParquetRulebookEvaluationsRepository(tmp_data_root)
    assert repo.recent() == []
