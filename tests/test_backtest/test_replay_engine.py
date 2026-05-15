from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from click.testing import CliRunner

from polymarket_arb.backtest.event_stream import build_event_stream
from polymarket_arb.backtest.replay_engine import default_config, run_backtest
from polymarket_arb.cli import cli
from polymarket_arb.storage.base import BestQuote, MarketRow, OrderbookLevel, OrderbookSnapshot
from polymarket_arb.storage.parquet.best_quotes_repo import ParquetBestQuotesRepository
from polymarket_arb.storage.parquet.market_scores_repo import ParquetMarketScoresRepository
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository
from polymarket_arb.storage.parquet.orderbook_repo import ParquetOrderbookRepository
from tests.test_storage.test_market_scores_repo import _row as score_row


def _env_for(tmp_path) -> dict[str, str]:
    return {
        "POLYMARKET_ARB_STORAGE__DATA_ROOT": str(tmp_path / "data"),
        "POLYMARKET_ARB_LOGGING__JSON_LOG_PATH": str(tmp_path / "logs" / "test.jsonl"),
    }


def _market() -> MarketRow:
    return MarketRow(
        id="m1", condition_id="0xc", slug="slug", question="Q?", description=None,
        end_date_ms=None, start_date_ms=None, closed_at_ms=None, resolved_at_ms=None,
        active=True, closed=False, archived=False, outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=["tok-yes", "tok-no"], volume=None, liquidity=None,
        event_id=None, neg_risk=False, text_hash="h", schema_version=1, ingested_ts_ms=1,
    )


def _seed(data_root):
    ParquetMarketsRepository(data_root).upsert_markets([_market()])
    ParquetBestQuotesRepository(data_root).append(BestQuote(
        token_id="tok-yes", timestamp_ms=10, best_bid=Decimal("0.48"),
        best_bid_size=Decimal("10"), best_ask=Decimal("0.52"), best_ask_size=Decimal("10"),
        midpoint=Decimal("0.50"), spread=Decimal("0.04"), schema_version=1, ingested_ts_ms=10,
    ))
    ParquetOrderbookRepository(data_root).append_snapshot(OrderbookSnapshot(
        token_id="tok-yes", condition_id="0xc", market_slug=None, timestamp_ms=20,
        bids=[OrderbookLevel(Decimal("0.48"), Decimal("10"))],
        asks=[OrderbookLevel(Decimal("0.52"), Decimal("10"))],
        book_hash=None, source="rest", schema_version=1, ingested_ts_ms=20,
    ))
    ParquetMarketScoresRepository(data_root).append(
        replace(score_row(), final_signal_score=0.9, ingested_ts_ms=30)
    )


def test_event_stream_sorted(tmp_path):
    data_root = tmp_path / "data"
    _seed(data_root)
    events = build_event_stream(data_root)
    assert [e.ts_ms for e in events] == sorted(e.ts_ms for e in events)


def test_replay_writes_outputs(tmp_path):
    data_root = tmp_path / "data"
    _seed(data_root)
    result = run_backtest(data_root, default_config(strategy_name="score-threshold", threshold=0.8))
    out = result["output_dir"]
    assert (out / "config.json").exists()
    assert (out / "signals.parquet").exists()
    assert (out / "simulated_orders.parquet").exists()
    assert (out / "fills.parquet").exists()
    assert (out / "positions.parquet").exists()
    assert (out / "equity_curve.parquet").exists()
    assert (out / "metrics.json").exists()


def test_backtest_cli_and_missing_dataset(tmp_path):
    runner = CliRunner()
    missing = runner.invoke(cli, ["backtest", "run"], env=_env_for(tmp_path))
    assert missing.exit_code != 0
    assert "run the recorder" in missing.output or "ingest command" in missing.output

    data_root = tmp_path / "data"
    _seed(data_root)
    result = runner.invoke(
        cli,
        ["backtest", "run", "--strategy", "score-threshold", "--threshold", "0.8"],
        env=_env_for(tmp_path),
    )
    assert result.exit_code == 0, result.output
    assert "backtest completed" in result.output
