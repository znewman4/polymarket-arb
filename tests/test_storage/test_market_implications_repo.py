from __future__ import annotations

from polymarket_arb.storage.base import MarketImplicationRow
from polymarket_arb.storage.parquet.market_implications_repo import (
    ParquetMarketImplicationsRepository,
)


def _row(market_id: str = "m1", *, needs_review: bool = False) -> MarketImplicationRow:
    return MarketImplicationRow(
        implication_id=f"imp-{market_id}",
        market_id=market_id,
        extraction_id="e1",
        implication_type="sufficient_for_yes",
        statement="X happens",
        extracted_label='{"implication_type":"sufficient_for_yes"}',
        deterministic_score=0.7,
        model_confidence=0.8,
        final_confidence=0.56,
        ambiguity_flags=[],
        needs_manual_review=needs_review,
        source="market_semantics",
        model_name="mock",
        prompt_version="market_semantics_v1",
        rulebook_id="implication",
        rulebook_version=1,
        schema_version=1,
        ingested_ts_ms=1,
    )


def test_append_and_read_for_market(tmp_data_root):
    repo = ParquetMarketImplicationsRepository(tmp_data_root, row_group_size=4)
    assert repo.append_many([_row("m1"), _row("m2")]) == 2
    rows = repo.for_market("m1")
    assert len(rows) == 1
    assert rows[0].market_id == "m1"


def test_needs_review(tmp_data_root):
    repo = ParquetMarketImplicationsRepository(tmp_data_root, row_group_size=4)
    repo.append_many([_row("m1", needs_review=False), _row("m2", needs_review=True)])
    rows = repo.needs_review(limit=10)
    assert [r.market_id for r in rows] == ["m2"]


def test_empty_lake(tmp_data_root):
    repo = ParquetMarketImplicationsRepository(tmp_data_root)
    assert repo.for_market("m1") == []
    assert repo.needs_review() == []
