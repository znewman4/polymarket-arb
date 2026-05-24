"""End-to-end tests for the read-only Flask dashboard.

Covers:
  * All five routes return 200 against an empty lake.
  * /health returns valid JSON with the expected top-level keys.
  * /orders.csv returns the right Content-Type + Content-Disposition.
  * Seeded-lake test: counters, /orders rows, and the markets-join all work
    against a small synthetic data lake written via the existing parquet
    repositories.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_arb.dashboard.app import create_app
from polymarket_arb.live.models import OrdersLogRow
from polymarket_arb.settings import Settings
from polymarket_arb.storage.base import MarketRow, OrderbookLevel, OrderbookSnapshot
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository
from polymarket_arb.storage.parquet.orderbook_repo import ParquetOrderbookRepository
from polymarket_arb.storage.parquet.orders_log_repo import ParquetOrdersLogRepository


def _settings_with_root(base: Settings, data_root: Path) -> Settings:
    return base.model_copy(
        update={"storage": base.storage.model_copy(update={"data_root": data_root})}
    )


@pytest.fixture
def app(settings: Settings, tmp_data_root: Path):
    s = _settings_with_root(settings, tmp_data_root)
    flask_app = create_app(s)
    flask_app.config.update({"TESTING": True})
    yield flask_app
    flask_app.extensions["dashboard_db"].close()


@pytest.fixture
def client(app):
    return app.test_client()


# ─── Empty lake: every route renders ──────────────────────────────────────────


@pytest.mark.parametrize("path", ["/", "/orders", "/signals", "/markets", "/health"])
def test_routes_200_on_empty_lake(client, path: str) -> None:
    resp = client.get(path)
    assert resp.status_code == 200, resp.data


def test_health_returns_valid_json(client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    payload = json.loads(resp.data)
    expected_keys = {
        "today_utc",
        "recorder_last_cycle_ts_ms",
        "agent_last_tick_ts_ms",
        "orderbook_snapshots_today",
        "orders_log_writable",
        "services",
    }
    assert expected_keys.issubset(payload.keys())
    assert payload["orders_log_writable"] is True  # tmp_data_root is writable
    assert payload["recorder_last_cycle_ts_ms"] is None
    assert payload["agent_last_tick_ts_ms"] is None


def test_orders_csv_has_correct_headers(client) -> None:
    resp = client.get("/orders.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    disp = resp.headers.get("Content-Disposition", "")
    assert disp.startswith("attachment;")
    assert ".csv" in disp


# ─── Seeded lake: counters, join, pagination ──────────────────────────────────


def _orders_log_row(**overrides) -> OrdersLogRow:
    base = dict(
        intent_id="i1",
        ts_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
        strategy_id="relationship_aggressive",
        token_id="t1",
        market_id="m1",
        side="buy",
        requested_size="10",
        filled_size="0",
        avg_fill_price=None,
        notional_usdc="5",
        fees_usdc="0",
        status="paper_no_fill",
        reason="",
        paper_mode=True,
        kill_switch_active=False,
        orders_allowed=False,
        preflight_passed=True,
        preflight_token_id="t1",
        http_status=None,
        source_lane="",
        source_relationship_id="r1",
        source_hypothesis_id="",
        schema_version=1,
        ingested_ts_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
        notes="",
        detail_json="{}",
    )
    base.update(overrides)
    return OrdersLogRow(**base)


def _market_row(market_id: str, question: str) -> MarketRow:
    return MarketRow(
        id=market_id,
        condition_id=f"c-{market_id}",
        slug=f"slug-{market_id}",
        question=question,
        description="",
        end_date_ms=None,
        start_date_ms=None,
        closed_at_ms=None,
        resolved_at_ms=None,
        active=True,
        closed=False,
        archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=["t1", "t2"],
        volume=Decimal("0"),
        liquidity=Decimal("0"),
        event_id=None,
        neg_risk=False,
        text_hash="hash",
        schema_version=1,
        ingested_ts_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
    )


def _orderbook_snapshot(token_id: str) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        token_id=token_id,
        condition_id="c-m1",
        market_slug="slug-m1",
        timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
        bids=[OrderbookLevel(price=Decimal("0.4"), size=Decimal("100"))],
        asks=[OrderbookLevel(price=Decimal("0.6"), size=Decimal("100"))],
        book_hash="bh",
        source="rest",
        schema_version=1,
        ingested_ts_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
    )


def test_seeded_lake_renders_counters_and_joins(
    client, tmp_data_root: Path
) -> None:
    orders_repo = ParquetOrdersLogRepository(tmp_data_root)
    markets_repo = ParquetMarketsRepository(tmp_data_root)
    book_repo = ParquetOrderbookRepository(tmp_data_root)

    orders_repo.append_many(
        [
            _orders_log_row(intent_id="i-filled", status="paper_filled", notional_usdc="50"),
            _orders_log_row(intent_id="i-nofill", status="paper_no_fill"),
            _orders_log_row(intent_id="i-reject", status="rejected_kill_switch",
                            kill_switch_active=True),
        ]
    )
    markets_repo.upsert_markets([_market_row("m1", "Will Foo happen?")])
    book_repo.append_snapshot(_orderbook_snapshot("t1"))

    # /  — counters
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Will Foo happen?" in body
    # Three signals total, one filled, fill rate ~33.3%
    assert ">3<" in body  # total
    assert ">1<" in body  # filled

    # /orders — first page joins the market question
    resp = client.get("/orders?page=1")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Will Foo happen?" in body
    assert "paper_filled" in body
    assert "rejected_kill_switch" in body

    # /orders.csv — header includes "question" column
    resp = client.get("/orders.csv")
    assert resp.status_code == 200
    assert b"question" in resp.data
    assert b"Will Foo happen?" in resp.data


def test_orders_filter_by_status(client, tmp_data_root: Path) -> None:
    orders_repo = ParquetOrdersLogRepository(tmp_data_root)
    orders_repo.append_many(
        [
            _orders_log_row(intent_id="i-1", status="paper_filled"),
            _orders_log_row(intent_id="i-2", status="paper_no_fill"),
        ]
    )
    resp = client.get("/orders?status=paper_filled")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "1 total rows" in body
