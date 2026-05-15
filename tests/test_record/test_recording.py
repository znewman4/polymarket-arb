from __future__ import annotations

from decimal import Decimal

import respx
from click.testing import CliRunner
from httpx import Response

from polymarket_arb.cli import cli
from polymarket_arb.record.scheduler import parse_duration_s
from polymarket_arb.storage.base import MarketRow
from polymarket_arb.storage.parquet.best_quotes_repo import ParquetBestQuotesRepository
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository


def _env_for(tmp_path) -> dict[str, str]:
    return {
        "POLYMARKET_ARB_CLOB_HOST": "https://clob.example",
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


def _book(token: str) -> dict:
    return {
        "market": "0xc",
        "asset_id": token,
        "timestamp": "2026-05-09T12:00:00Z",
        "bids": [{"price": "0.48", "size": "100"}],
        "asks": [{"price": "0.52", "size": "100"}],
    }


def test_parse_duration():
    assert parse_duration_s("10s") == 10
    assert parse_duration_s("1m") == 60
    assert parse_duration_s("1h") == 3600
    assert parse_duration_s("0") == 0


def test_record_quotes_writes_manifest_and_quotes(tmp_path):
    data_root = tmp_path / "data"
    ParquetMarketsRepository(data_root).upsert_markets([_market()])
    with respx.mock(assert_all_called=False) as router:
        router.get("https://clob.example/book").mock(
            side_effect=lambda request: Response(200, json=_book(request.url.params["token_id"]))
        )
        result = CliRunner().invoke(
            cli,
            ["record", "quotes", "--limit", "1", "--interval", "0s", "--duration", "0s"],
            env=_env_for(tmp_path),
        )
    assert result.exit_code == 0, result.output
    assert ParquetBestQuotesRepository(data_root).latest("tok-yes") is not None
    manifests = list((data_root / "runs").glob("*/manifest.json"))
    assert len(manifests) == 1
    assert '"status": "completed"' in manifests[0].read_text()
