from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal

from click.testing import CliRunner

from polymarket_arb.cli import cli
from polymarket_arb.storage.base import BestQuote, MarketRow
from polymarket_arb.storage.parquet.best_quotes_repo import ParquetBestQuotesRepository
from polymarket_arb.storage.parquet.market_implications_repo import (
    ParquetMarketImplicationsRepository,
)
from polymarket_arb.storage.parquet.market_scores_repo import ParquetMarketScoresRepository
from polymarket_arb.storage.parquet.market_semantics_repo import (
    ParquetMarketSemanticsRepository,
)
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository
from tests.test_cli.test_nlp_cli import _row as semantics_row
from tests.test_storage.test_market_implications_repo import _row as implication_row


def _env_for(tmp_path) -> dict[str, str]:
    return {
        "POLYMARKET_ARB_STORAGE__DATA_ROOT": str(tmp_path / "data"),
        "POLYMARKET_ARB_LOGGING__JSON_LOG_PATH": str(tmp_path / "logs" / "test.jsonl"),
    }


def _market() -> MarketRow:
    return MarketRow(
        id="m1",
        condition_id="0xc",
        slug="slug",
        question="Q?",
        description=None,
        end_date_ms=None,
        start_date_ms=None,
        closed_at_ms=None,
        resolved_at_ms=None,
        active=True,
        closed=False,
        archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.01"), Decimal("0.99")],
        clob_token_ids=["tok-yes", "tok-no"],
        volume=None,
        liquidity=None,
        event_id=None,
        neg_risk=False,
        text_hash="h",
        schema_version=1,
        ingested_ts_ms=1,
    )


def _quote(token: str) -> BestQuote:
    return BestQuote(
        token_id=token,
        timestamp_ms=int(time.time() * 1000),
        best_bid=Decimal("0.48"),
        best_bid_size=Decimal("100"),
        best_ask=Decimal("0.52"),
        best_ask_size=Decimal("100"),
        midpoint=Decimal("0.50"),
        spread=Decimal("0.04"),
        schema_version=1,
        ingested_ts_ms=1,
    )


def test_score_cli_uses_best_quotes_not_gamma_snapshot(tmp_path):
    data_root = tmp_path / "data"
    ParquetMarketsRepository(data_root).upsert_markets([_market()])
    ParquetMarketSemanticsRepository(data_root).upsert(
        replace(semantics_row(), ambiguity_score=0.2, evidence_required=["official source"])
    )
    ParquetMarketImplicationsRepository(data_root).append(implication_row("m1"))
    ParquetBestQuotesRepository(data_root).append_many([_quote("tok-yes"), _quote("tok-no")])

    runner = CliRunner()
    result = runner.invoke(cli, ["score", "score-markets", "--limit", "10"], env=_env_for(tmp_path))
    assert result.exit_code == 0, result.output
    assert "scored 1 markets" in result.output

    row = ParquetMarketScoresRepository(data_root).latest("m1")
    assert row is not None
    assert row.market_midpoint == 0.5
    assert row.market_midpoint != 0.01

    result = runner.invoke(cli, ["score", "show-score", "m1"], env=_env_for(tmp_path))
    assert result.exit_code == 0, result.output
    assert "research" in result.output or "watch" in result.output or "paper_signal_only" in result.output
