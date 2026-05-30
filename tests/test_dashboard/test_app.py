"""End-to-end tests for the read-only Flask dashboard.

Covers:
  * All dashboard routes return 200 against an empty lake.
  * /health returns valid JSON with the expected top-level keys.
  * /orders.csv returns the right Content-Type + Content-Disposition.
  * Seeded-lake test: counters, /orders rows, and the markets-join all work
    against a small synthetic data lake written via the existing parquet
    repositories.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_arb.dashboard import queries as queries_mod
from polymarket_arb.dashboard.app import create_app
from polymarket_arb.dashboard.queries import DuckDBQueryService
from polymarket_arb.live.models import OrdersLogRow, PositionRow
from polymarket_arb.settings import Settings
from polymarket_arb.storage.base import MarketRow, OrderbookLevel, OrderbookSnapshot
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository
from polymarket_arb.storage.parquet.orderbook_repo import ParquetOrderbookRepository
from polymarket_arb.storage.parquet.orders_log_repo import ParquetOrdersLogRepository
from polymarket_arb.storage.parquet.positions_repo import ParquetPositionsRepository


def _settings_with_root(base: Settings, data_root: Path) -> Settings:
    return base.model_copy(
        update={"storage": base.storage.model_copy(update={"data_root": data_root})}
    )


@pytest.fixture
def app(settings: Settings, tmp_data_root: Path):
    s = _settings_with_root(settings, tmp_data_root)
    flask_app = create_app(s)
    flask_app.config.update({"TESTING": True})
    # Force a synchronous cache refresh so the overview route doesn't return 202
    # racing the daemon refresh thread on an empty lake.
    flask_app.extensions["dashboard_cache"].refresh()
    yield flask_app
    flask_app.extensions["dashboard_db"].close()


@pytest.fixture
def client(app):
    return app.test_client()


# ─── Empty lake: every route renders ──────────────────────────────────────────


@pytest.mark.parametrize("path", ["/", "/orders", "/positions", "/live", "/signals", "/markets", "/health"])
def test_routes_200_on_empty_lake(client, path: str) -> None:
    resp = client.get(path)
    assert resp.status_code == 200, resp.data


def test_health_returns_ok(client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.data == b"ok"


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


def _position_row(**overrides) -> PositionRow:
    base = dict(
        position_id="p1",
        strategy_id="limitless_arb",
        market_id="m1",
        token_id="t1",
        side="buy",
        open_ts_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
        entry_price="0.40",
        size="10",
        notional_usdc="4.00",
        gross_edge="",
        relationship_id="r1",
        relationship_type="",
        notes="arb_gap=0.0250 similarity=0.900",
        status="open",
        ingested_ts_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
    )
    base.update(overrides)
    return PositionRow(**base)


def _orderbook_snapshot(
    token_id: str,
    *,
    bid: str = "0.4",
    ask: str = "0.6",
    timestamp_ms: int | None = None,
) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        token_id=token_id,
        condition_id="c-m1",
        market_slug="slug-m1",
        timestamp_ms=timestamp_ms or int(datetime.now(timezone.utc).timestamp() * 1000),
        bids=[OrderbookLevel(price=Decimal(bid), size=Decimal("100"))],
        asks=[OrderbookLevel(price=Decimal(ask), size=Decimal("100"))],
        book_hash="bh",
        source="rest",
        schema_version=1,
        ingested_ts_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
    )


def test_seeded_lake_renders_counters_and_joins(
    client, app, tmp_data_root: Path
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

    # Force the cache to reload now that the lake is seeded.
    app.extensions["dashboard_cache"].refresh()

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


def test_positions_page_renders_mtm_and_locked_profit(
    client, app, tmp_data_root: Path
) -> None:
    positions_repo = ParquetPositionsRepository(tmp_data_root)
    book_repo = ParquetOrderbookRepository(tmp_data_root)
    positions_repo.append(_position_row())
    book_repo.append_snapshot(_orderbook_snapshot("t1", bid="0.48", ask="0.52"))

    rows = app.extensions["dashboard_db"].open_positions_with_mtm()
    assert len(rows) == 1
    assert rows[0]["current_mid"] == pytest.approx(0.50)
    assert rows[0]["mtm_pnl"] == pytest.approx(1.00)
    assert rows[0]["locked_profit"] == pytest.approx(0.25)
    assert rows[0]["token_id"] == "t1"

    app.extensions["dashboard_cache"].refresh()
    resp = client.get("/positions")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Open Positions" in body
    assert "limitless_arb" in body
    assert "t1" in body
    assert "+1.00" in body
    assert "0.25" in body
    assert 'http-equiv="refresh" content="30"' in body


def test_query_methods_use_method_name_ttl_cache(tmp_data_root: Path) -> None:
    qs = DuckDBQueryService(tmp_data_root)
    try:
        queries_mod.clear_query_cache()
        first = qs.overview_counters()
        ParquetOrdersLogRepository(tmp_data_root).append(
            _orders_log_row(intent_id="after-cache", status="paper_filled")
        )
        second = qs.overview_counters()

        assert second == first
        result, timestamp = queries_mod._QUERY_CACHE["overview_counters"]
        assert result == first
        assert isinstance(timestamp, float)
    finally:
        qs.close()


def test_orderbook_snapshot_queries_filter_to_recent_dt(
    tmp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qs = DuckDBQueryService(tmp_data_root)
    captured: dict[str, str] = {}

    try:
        queries_mod.clear_query_cache()
        monkeypatch.setattr(qs, "_has_data", lambda table: True)
        monkeypatch.setattr(qs, "_glob_recent", lambda table, days=7: "'dummy.parquet'")

        def fake_fetchall_dict(sql, params=None):
            captured["sql"] = sql
            return []

        monkeypatch.setattr(qs, "_fetchall_dict", fake_fetchall_dict)
        qs.open_positions_with_mtm()

        assert "dt >= current_date - INTERVAL 1 DAY" in captured["sql"]
    finally:
        qs.close()


def test_overview_and_tradebook_label_filled_notional_as_deployed_capital(
    client, app, tmp_data_root: Path
) -> None:
    orders_repo = ParquetOrdersLogRepository(tmp_data_root)
    orders_repo.append_many([
        _orders_log_row(
            intent_id="i-relationship",
            status="paper_filled",
            notional_usdc="100",
            notes="gross_edge=0.05 rel_type=nested_a_implies_b",
        ),
        _orders_log_row(
            intent_id="i-limitless",
            strategy_id="limitless_arb",
            status="paper_filled",
            notional_usdc="50",
        ),
    ])

    series = app.extensions["dashboard_db"].cumulative_notional_by_hour()
    assert series[-1]["cumulative_notional"] == pytest.approx(150.0)
    expected = app.extensions["dashboard_db"].expected_pnl_stats()
    assert expected["total_expected_pnl"] == pytest.approx(5.0)
    assert expected["total_cost_basis"] == pytest.approx(150.0)
    assert expected["expected_return_pct"] == pytest.approx(3.3333)
    assert expected["trade_count"] == 2

    app.extensions["dashboard_cache"].refresh()
    body = client.get("/").data.decode()
    assert "Cumulative notional deployed (USDC)" in body
    assert "capital deployed, not profit" in body
    assert "Expected PnL" in body
    assert "+5.00 USDC" in body
    assert "+3.3333%" in body
    assert "Requires gross_edge in trade notes" in body

    tradebook = client.get("/trades").data.decode()
    assert "Total Notional (USDC)" in tradebook
    assert "Cumulative Notional (USDC)" in tradebook
    assert "Running PnL" not in tradebook


def test_live_monitor_page_renders_today_activity(
    client, app, tmp_data_root: Path
) -> None:
    ParquetOrdersLogRepository(tmp_data_root).append_many([
        _orders_log_row(
            intent_id="i-live",
            strategy_id="relationship_aggressive",
            status="live_submitted",
            notional_usdc="25",
        ),
        _orders_log_row(
            intent_id="i-paper",
            strategy_id="limitless_arb",
            status="paper_filled",
            notional_usdc="40",
        ),
        _orders_log_row(
            intent_id="i-rejected",
            strategy_id="relationship_aggressive",
            status="rejected_kill_switch",
        ),
    ])

    data = app.extensions["dashboard_db"].live_monitor_data()
    assert data["live_submitted_today"] == 1
    assert data["paper_filled_today"] == 1
    assert data["rejected_today"] == 1
    assert data["total_live_notional_today"] == pytest.approx(25.0)
    assert set(data["strategies_active"]) == {
        "relationship_aggressive",
        "limitless_arb",
    }
    assert len(data["recent_live_orders"]) == 1
    assert data["recent_live_orders"][0]["status"] == "live_submitted"
    assert len(data["recent_all_orders"]) == 3

    body = client.get("/live").data.decode()
    assert "Live Monitor" in body
    assert "Live Orders Today" in body
    assert "Live Notional (USDC)" in body
    assert "relationship_aggressive" in body
    assert "limitless_arb" in body
    assert "live_submitted" in body
    assert "touch ~/polymarket-arb/data/.killswitch" in body
