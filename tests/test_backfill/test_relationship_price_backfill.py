"""Tests for run_relationship_price_backfill()."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from polymarket_arb.backfill.models import BackfillConfig
from polymarket_arb.backfill.price_history import run_relationship_price_backfill
from polymarket_arb.storage.base import PriceHistoryRow, RelationshipCandidateRow
from polymarket_arb.storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _rel(
    rel_id: str = "rel_01",
    tok_a_yes: str = "tok_a_yes",
    tok_a_no: str = "tok_a_no",
    tok_b_yes: str = "tok_b_yes",
    tok_b_no: str = "tok_b_no",
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=rel_id,
        market_id_a="market_a",
        market_id_b="market_b",
        condition_id_a="cond_a",
        condition_id_b="cond_b",
        token_id_a_yes=tok_a_yes,
        token_id_a_no=tok_a_no,
        token_id_b_yes=tok_b_yes,
        token_id_b_no=tok_b_no,
        question_a="Q A?",
        question_b="Q B?",
        relationship_type="nested_a_implies_b",
        entity_match_score=1.0,
        time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.8,
        model_confidence=0.9,
        final_confidence=0.8,
        validation_status="accepted",
        rejection_reasons_json="[]",
        rationale_summary="test",
        evidence_json="{}",
        rulebook_id="v2",
        rulebook_version=2,
        rulebook_content_hash="hash",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _mock_settings(tmp_data_root: Path) -> MagicMock:
    settings = MagicMock()
    settings.data_root = tmp_data_root
    settings.storage.parquet.compression = "snappy"
    settings.storage.parquet.row_group_size = 10_000
    settings.clob_host = "https://clob.example.com"
    settings.http = MagicMock()
    return settings


class TestCollectsTokensFromRelationships:
    def test_collects_all_four_token_slots(self, tmp_data_root: Path) -> None:
        """All YES/NO tokens from both legs should be collected."""
        ParquetRelationshipCandidatesRepository(tmp_data_root).append(
            _rel("r1", "a_yes", "a_no", "b_yes", "b_no")
        )

        async def _run():
            with patch(
                "polymarket_arb.backfill.price_history.AsyncHttpClient"
            ) as mock_http_cls:
                mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
                mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_client = MagicMock()
                mock_client.fetch_batch_prices_history = AsyncMock(return_value=[])

                with patch(
                    "polymarket_arb.backfill.price_history.ClobClient",
                    return_value=mock_client,
                ):
                    settings = _mock_settings(tmp_data_root)
                    _, coverage = await run_relationship_price_backfill(
                        settings, BackfillConfig(requested_days=1)
                    )
                return coverage

        coverage = asyncio.run(_run())
        assert "a_yes" in coverage
        assert "a_no" in coverage
        assert "b_yes" in coverage
        assert "b_no" in coverage

    def test_two_relationships_deduplicates_shared_token(self, tmp_data_root: Path) -> None:
        """Shared token across two relationships counted once."""
        repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        repo.append(_rel("r1", "shared_tok", "a_no", "b_yes", "b_no"))
        repo.append(_rel("r2", "shared_tok", "a_no2", "c_yes", "c_no"))

        async def _run():
            with patch("polymarket_arb.backfill.price_history.AsyncHttpClient") as mh:
                mh.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
                mh.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_client = MagicMock()
                mock_client.fetch_batch_prices_history = AsyncMock(return_value=[])
                with patch("polymarket_arb.backfill.price_history.ClobClient", return_value=mock_client):
                    settings = _mock_settings(tmp_data_root)
                    _, coverage = await run_relationship_price_backfill(settings, BackfillConfig(requested_days=1))
                return coverage

        coverage = asyncio.run(_run())
        assert list(coverage.keys()).count("shared_tok") == 1


class TestPostBackfillCoverageReport:
    def test_report_structure_present(self, tmp_data_root: Path) -> None:
        """Coverage report has expected keys for each token."""
        ParquetRelationshipCandidatesRepository(tmp_data_root).append(
            _rel("r1", "tok_a_yes", "tok_a_no", "tok_b_yes", "tok_b_no")
        )
        # Seed price history for one token so it shows has_price_history=True
        ParquetPriceHistoryRepository(tmp_data_root).append(
            PriceHistoryRow(
                market_id="market_a",
                condition_id="cond_a",
                token_id="tok_a_yes",
                outcome="Yes",
                ts_ms=TS,
                price=Decimal("0.6"),
                source="test",
                fidelity="hourly",
                interval="1h",
                schema_version=1,
                ingested_ts_ms=TS,
            )
        )

        async def _run():
            with patch("polymarket_arb.backfill.price_history.AsyncHttpClient") as mh:
                mh.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
                mh.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_client = MagicMock()
                mock_client.fetch_batch_prices_history = AsyncMock(return_value=[])
                with patch("polymarket_arb.backfill.price_history.ClobClient", return_value=mock_client):
                    settings = _mock_settings(tmp_data_root)
                    result, coverage = await run_relationship_price_backfill(settings, BackfillConfig(requested_days=1))
                return result, coverage

        _, coverage = asyncio.run(_run())

        assert "tokens_expected" not in coverage  # not a summary dict; keyed by token_id
        entry = coverage["tok_a_yes"]
        assert "has_price_history" in entry
        assert "tick_count" in entry
        assert "first_ts_ms" in entry
        assert "last_ts_ms" in entry
        assert "market_id" in entry
        assert entry["has_price_history"] is True
        assert entry["tick_count"] == 1

        # tok_b_yes has no price history seeded
        assert coverage["tok_b_yes"]["has_price_history"] is False
        assert coverage["tok_b_yes"]["tick_count"] == 0

    def test_empty_relationships_returns_empty_coverage(self, tmp_data_root: Path) -> None:
        async def _run():
            settings = _mock_settings(tmp_data_root)
            return await run_relationship_price_backfill(settings, BackfillConfig(requested_days=1))

        result, coverage = asyncio.run(_run())
        assert coverage == {}
        assert result.markets_attempted == 0
