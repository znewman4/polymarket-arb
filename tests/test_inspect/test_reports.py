from __future__ import annotations

import csv
from dataclasses import replace
from decimal import Decimal

from click.testing import CliRunner

from polymarket_arb.cli import cli
from polymarket_arb.inspect.audit import audit_data
from polymarket_arb.inspect.exports import export_semantics_review
from polymarket_arb.inspect.reports import counts_report, market_pipeline_report, table_report
from polymarket_arb.storage.base import BestQuote, MarketRow, RulebookEvaluationRow
from polymarket_arb.storage.parquet.best_quotes_repo import ParquetBestQuotesRepository
from polymarket_arb.storage.parquet.market_implications_repo import (
    ParquetMarketImplicationsRepository,
)
from polymarket_arb.storage.parquet.market_scores_repo import ParquetMarketScoresRepository
from polymarket_arb.storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository
from polymarket_arb.storage.parquet.rulebook_evaluations_repo import (
    ParquetRulebookEvaluationsRepository,
)
from tests.test_cli.test_nlp_cli import _row as semantics_row
from tests.test_storage.test_market_implications_repo import _row as implication_row
from tests.test_storage.test_market_scores_repo import _row as score_row


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
        question="Will X happen?",
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
        clob_token_ids=["tok-yes", "tok-no"],
        volume=None,
        liquidity=None,
        event_id=None,
        neg_risk=False,
        text_hash="h",
        schema_version=1,
        ingested_ts_ms=10,
    )


def _quote(token: str) -> BestQuote:
    return BestQuote(
        token_id=token,
        timestamp_ms=100,
        best_bid=Decimal("0.48"),
        best_bid_size=Decimal("10"),
        best_ask=Decimal("0.52"),
        best_ask_size=Decimal("10"),
        midpoint=Decimal("0.50"),
        spread=Decimal("0.04"),
        schema_version=1,
        ingested_ts_ms=100,
    )


def _seed(data_root):
    ParquetMarketsRepository(data_root).upsert_markets([_market()])
    sem = replace(semantics_row(), ambiguity_score=0.4, needs_manual_review=True)
    ParquetMarketSemanticsRepository(data_root).upsert(sem)
    ParquetRulebookEvaluationsRepository(data_root).append(RulebookEvaluationRow(
        evaluation_id="e1",
        extraction_id=sem.extraction_id,
        market_id="m1",
        rulebook_id="ambiguity",
        rulebook_version=1,
        rulebook_content_hash="h",
        score=0.4,
        subscores_json="{}",
        flags=["vague_deadline"],
        evaluated_ts_ms=101,
        schema_version=1,
        ingested_ts_ms=101,
    ))
    ParquetMarketImplicationsRepository(data_root).append(implication_row("m1"))
    ParquetBestQuotesRepository(data_root).append_many([_quote("tok-yes"), _quote("tok-no")])
    ParquetMarketScoresRepository(data_root).append(score_row())


def test_inspect_reports_and_export(tmp_path):
    data_root = tmp_path / "data"
    _seed(data_root)

    tables = table_report(data_root)
    assert any(t.name == "markets" and t.file_count == 1 for t in tables)

    counts = counts_report(data_root)
    assert counts["total_markets"] == 1
    assert counts["markets_with_no_semantics"] == 0

    stages = market_pipeline_report(data_root, "m1")
    assert all(stage.present for stage in stages)

    out = tmp_path / "review.csv"
    assert export_semantics_review(data_root, out, sample=10) == 1
    rows = list(csv.DictReader(out.open()))
    assert rows[0]["market_id"] == "m1"
    assert "thinking" not in rows[0]

    checks = audit_data(data_root)
    assert any(c.name == "no <think> found in normalised data" and c.status == "PASS" for c in checks)


def test_inspect_cli_commands(tmp_path):
    data_root = tmp_path / "data"
    _seed(data_root)
    runner = CliRunner()
    for args in [
        ["inspect", "tables"],
        ["inspect", "counts"],
        ["inspect", "market", "m1"],
        ["inspect", "pipeline", "m1"],
        ["inspect", "freshness"],
        ["inspect", "score-distribution"],
        ["inspect", "audit-data"],
    ]:
        result = runner.invoke(cli, args, env=_env_for(tmp_path))
        assert result.exit_code == 0, result.output
    result = runner.invoke(
        cli,
        ["inspect", "export-semantics-review", "--sample", "20", "--out", str(tmp_path / "r.csv")],
        env=_env_for(tmp_path),
    )
    assert result.exit_code == 0, result.output
