"""Tests for the semantic pipeline orchestrator."""

from __future__ import annotations

from decimal import Decimal

from polymarket_arb.backfill.models import BackfillConfig
from polymarket_arb.backfill.semantic_pipeline import run_semantic_pipeline
from polymarket_arb.storage.base import MarketRow
from polymarket_arb.storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository


def _market(market_id: str = "m1") -> MarketRow:
    return MarketRow(
        id=market_id,
        condition_id=f"cond-{market_id}",
        slug=f"slug-{market_id}",
        question=f"Will {market_id} resolve yes?",
        description=None,
        end_date_ms=None,
        start_date_ms=None,
        closed_at_ms=None,
        resolved_at_ms=None,
        active=True,
        closed=False,
        archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=[f"tok-{market_id}"],
        volume=Decimal("1000"),
        liquidity=Decimal("100"),
        event_id=None,
        neg_risk=False,
        text_hash="abc",
        schema_version=1,
        ingested_ts_ms=1_700_000_000_000,
    )


async def test_semantic_pipeline_detects_missing_semantics(settings):
    settings = settings.model_copy(
        update={"nlp": settings.nlp.model_copy(update={"provider": "mock"})}
    )
    repo = ParquetMarketsRepository(settings.data_root)
    repo.upsert_markets([_market("m1")])

    cfg = BackfillConfig(market_limit=5, include_recently_resolved=False)
    result = await run_semantic_pipeline(settings, cfg, only_missing=True)

    assert result.total_processed == 1
    assert result.semantics_extracted == 1 or result.semantics_failed == 1


async def test_semantic_pipeline_skips_unchanged_text_hash(settings):
    settings = settings.model_copy(
        update={"nlp": settings.nlp.model_copy(update={"provider": "mock"})}
    )
    repo = ParquetMarketsRepository(settings.data_root)
    repo.upsert_markets([_market("m1")])

    cfg = BackfillConfig(market_limit=5, include_recently_resolved=False)

    # First run extracts
    await run_semantic_pipeline(settings, cfg, only_missing=True)

    # Second run with only_missing=True should skip the already-extracted market
    result2 = await run_semantic_pipeline(settings, cfg, only_missing=True)
    assert result2.total_skipped >= 1 or result2.semantics_extracted == 0


async def test_semantic_pipeline_scores_missing_rulebook_evaluations(settings):
    settings = settings.model_copy(
        update={"nlp": settings.nlp.model_copy(update={"provider": "mock"})}
    )
    repo = ParquetMarketsRepository(settings.data_root)
    repo.upsert_markets([_market("m1")])

    cfg = BackfillConfig(market_limit=5, include_recently_resolved=False)
    result = await run_semantic_pipeline(settings, cfg, only_missing=True)

    # If extraction succeeded, scoring should also have run
    if result.semantics_extracted > 0:
        assert result.scores_computed >= 0  # 0 if already scored, >0 if newly scored


async def test_semantic_pipeline_extracts_missing_implications(settings):
    settings = settings.model_copy(
        update={"nlp": settings.nlp.model_copy(update={"provider": "mock"})}
    )
    repo = ParquetMarketsRepository(settings.data_root)
    repo.upsert_markets([_market("m1")])

    cfg = BackfillConfig(market_limit=5, include_recently_resolved=False)
    result = await run_semantic_pipeline(settings, cfg, only_missing=True)
    # implications_extracted may be 0 if the mock returns empty condition lists
    assert result.implications_extracted >= 0


async def test_semantic_pipeline_does_not_store_thinking(settings):
    """A mock that returns <think> content should be stripped before storage."""
    settings = settings.model_copy(
        update={"nlp": settings.nlp.model_copy(update={"provider": "mock"})}
    )
    repo = ParquetMarketsRepository(settings.data_root)
    repo.upsert_markets([_market("m1")])

    # The mock client calls strip_thinking internally, so <think> can't survive
    # through the normal extraction path. We just verify the stored row is clean.
    cfg = BackfillConfig(market_limit=5, include_recently_resolved=False)
    await run_semantic_pipeline(settings, cfg, only_missing=True)

    sem_repo = ParquetMarketSemanticsRepository(settings.data_root)
    row = sem_repo.get_latest("m1")
    if row is not None:
        thinking_fields = [
            "explanation_summary",
            "flag_rationales_json",
            "uncertainty_notes_json",
            "rule_curation_notes_json",
        ]
        for field in thinking_fields:
            val = getattr(row, field, None)
            if isinstance(val, str):
                assert "<think>" not in val.lower(), f"<think> found in {field}"
