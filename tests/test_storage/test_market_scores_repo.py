from __future__ import annotations

from polymarket_arb.storage.base import MarketScoreRow
from polymarket_arb.storage.parquet.market_scores_repo import ParquetMarketScoresRepository


def _row() -> MarketScoreRow:
    return MarketScoreRow(
        market_id="m1",
        model_probability_placeholder=None,
        market_midpoint=0.5,
        spread=0.04,
        liquidity_score=0.8,
        semantic_confidence=0.7,
        ambiguity_score=0.2,
        implication_quality_score=0.6,
        resolution_risk_score=0.8,
        evidence_quality_score=0.5,
        freshness_score=1.0,
        final_signal_score=0.55,
        recommendation="research",
        explanation_json="{}",
        rulebook_id="evidence_fusion",
        rulebook_version=1,
        schema_version=1,
        ingested_ts_ms=1,
    )


def test_append_and_latest(tmp_data_root):
    repo = ParquetMarketScoresRepository(tmp_data_root, row_group_size=4)
    repo.append(_row())
    row = repo.latest("m1")
    assert row is not None
    assert row.recommendation == "research"
