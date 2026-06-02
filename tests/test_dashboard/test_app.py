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
from polymarket_arb.storage.base import (
    MarketRow,
    OrderbookLevel,
    OrderbookSnapshot,
    RelationshipCandidateRow,
)
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository
from polymarket_arb.storage.parquet.orderbook_repo import ParquetOrderbookRepository
from polymarket_arb.storage.parquet.orders_log_repo import ParquetOrdersLogRepository
from polymarket_arb.storage.parquet.positions_repo import ParquetPositionsRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)


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


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/orders",
        "/positions",
        "/live",
        "/arb",
        "/relationships",
        "/signals",
        "/markets",
        "/health",
    ],
)
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


def _relationship_candidate(**overrides) -> RelationshipCandidateRow:
    base = dict(
        relationship_id="rel_001",
        market_id_a="m_a",
        market_id_b="m_b",
        condition_id_a="c_a",
        condition_id_b="c_b",
        token_id_a_yes="a_yes",
        token_id_a_no="a_no",
        token_id_b_yes="b_yes",
        token_id_b_no="b_no",
        question_a="Will team A win?",
        question_b="Will team B win?",
        relationship_type="nested_a_implies_b",
        entity_match_score=0.9,
        time_scope_match_score=0.9,
        resolution_criteria_match_score=0.9,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.8,
        model_confidence=1.0,
        final_confidence=0.9,
        validation_status="accepted",
        rejection_reasons_json="[]",
        rationale_summary="test relationship",
        evidence_json="{}",
        rulebook_id="relationship_v1",
        rulebook_version=1,
        rulebook_content_hash="abc123",
        schema_version=1,
        ingested_ts_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
    )
    base.update(overrides)
    return RelationshipCandidateRow(**base)


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

    # / - operational strategy overview
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Limitless Arb" in body
    assert "Relationship Aggressive" in body
    assert "Open arb monitor" in body

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
    assert 'http-equiv="refresh" content="60"' in body


def test_open_positions_excludes_snapshot_rows(app, tmp_data_root: Path) -> None:
    positions_repo = ParquetPositionsRepository(tmp_data_root)
    book_repo = ParquetOrderbookRepository(tmp_data_root)
    base_ts = int(datetime.now(timezone.utc).timestamp() * 1000)

    positions_repo.append(
        _position_row(
            position_id="p-snapshot",
            side="buy",
            status="open",
            open_ts_ms=base_ts,
            ingested_ts_ms=base_ts,
        )
    )
    positions_repo.append(
        _position_row(
            position_id="p-snapshot",
            side="snapshot",
            status="open",
            open_ts_ms=base_ts,
            ingested_ts_ms=base_ts + 60_000,
        )
    )
    book_repo.append_snapshot(_orderbook_snapshot("t1", bid="0.48", ask="0.52"))

    queries_mod.clear_query_cache()
    rows = app.extensions["dashboard_db"].open_positions_with_mtm()

    assert len(rows) == 1
    assert rows[0]["side"] == "buy"


def test_open_positions_excludes_closed(app, tmp_data_root: Path) -> None:
    positions_repo = ParquetPositionsRepository(tmp_data_root)
    book_repo = ParquetOrderbookRepository(tmp_data_root)
    base_ts = int(datetime.now(timezone.utc).timestamp() * 1000)

    positions_repo.append(
        _position_row(
            position_id="p-closed",
            side="buy",
            status="open",
            open_ts_ms=base_ts,
            ingested_ts_ms=base_ts,
        )
    )
    positions_repo.append(
        _position_row(
            position_id="p-closed",
            side="sell",
            status="closed",
            open_ts_ms=base_ts,
            ingested_ts_ms=base_ts + 60_000,
        )
    )
    book_repo.append_snapshot(_orderbook_snapshot("t1", bid="0.48", ask="0.52"))

    queries_mod.clear_query_cache()
    rows = app.extensions["dashboard_db"].open_positions_with_mtm()

    assert rows == []


def test_relationship_open_trades_returns_empty_list_when_no_data(app) -> None:
    assert app.extensions["dashboard_db"].relationship_open_trades() == []


def test_relationship_open_trades_falls_back_to_orders_log(app, tmp_data_root: Path) -> None:
    rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
    orders_repo = ParquetOrdersLogRepository(tmp_data_root)
    book_repo = ParquetOrderbookRepository(tmp_data_root)
    rel_repo.append(_relationship_candidate())
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    orders_repo.append_many([
        _orders_log_row(
            intent_id="rel-a",
            ts_ms=now_ms - 10_000,
            strategy_id="relationship_aggressive",
            market_id="m_a",
            token_id="a_yes",
            side="buy",
            filled_size="10",
            avg_fill_price="0.50",
            notional_usdc="5",
            status="paper_filled",
            source_relationship_id="rel_001",
            notes="gross_edge=0.0700",
        ),
        _orders_log_row(
            intent_id="rel-b",
            ts_ms=now_ms - 9_000,
            strategy_id="relationship_aggressive",
            market_id="m_b",
            token_id="b_no",
            side="buy",
            filled_size="10",
            avg_fill_price="0.20",
            notional_usdc="2",
            status="paper_filled",
            source_relationship_id="rel_001",
            notes="gross_edge=0.0700",
        ),
    ])
    book_repo.append_snapshot(_orderbook_snapshot("a_yes", bid="0.59", ask="0.61"))
    book_repo.append_snapshot(_orderbook_snapshot("b_no", bid="0.24", ask="0.26"))

    rows = app.extensions["dashboard_db"].relationship_open_trades()

    assert len(rows) == 1
    assert rows[0]["relationship_id"] == "rel_001"
    assert rows[0]["side_a"] == "A YES"
    assert rows[0]["side_b"] == "B NO"
    assert rows[0]["entry_price_a"] == pytest.approx(0.50)
    assert rows[0]["entry_price_b"] == pytest.approx(0.20)
    assert rows[0]["gross_edge"] == pytest.approx(0.07)
    assert rows[0]["current_mtm"] == pytest.approx(1.5)


def test_relationship_candidates_summary_counts_by_type(app, tmp_data_root: Path) -> None:
    repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
    repo.append_many([
        _relationship_candidate(
            relationship_id="rel-inverse",
            relationship_type="inverse",
            final_confidence=0.96,
        ),
        _relationship_candidate(
            relationship_id="rel-nested",
            relationship_type="nested_a_implies_b",
            final_confidence=0.91,
        ),
        _relationship_candidate(
            relationship_id="rel-exclusive",
            relationship_type="mutually_exclusive_category",
            final_confidence=0.88,
        ),
        _relationship_candidate(
            relationship_id="rel-clock",
            relationship_type="same_reference_clock",
            final_confidence=0.86,
        ),
        _relationship_candidate(
            relationship_id="rel-low",
            relationship_type="inverse",
            final_confidence=0.80,
        ),
        _relationship_candidate(
            relationship_id="rel-rejected",
            relationship_type="nested_a_implies_b",
            final_confidence=0.99,
            validation_status="rejected",
        ),
    ])

    summary = app.extensions["dashboard_db"].relationship_candidates_summary()

    assert summary["total_accepted"] == 4
    assert summary["by_type"] == {
        "inverse": 1,
        "nested": 1,
        "mutually_exclusive": 1,
        "same_reference_clock": 1,
        "other": 0,
    }
    assert summary["confidence_buckets"] == {
        "0.95+": 1,
        "0.90-0.95": 1,
        "0.85-0.90": 2,
    }


def test_overview_summary_both_strategies_present(app) -> None:
    summary = app.extensions["dashboard_db"].overview_summary()

    assert "limitless_arb" in summary
    assert "relationship_agent" in summary
    assert summary["limitless_arb"]["display_name"] == "Limitless Arb"
    assert summary["relationship_agent"]["display_name"] == "Relationship Aggressive"
    assert summary["limitless_arb"]["mode"] == "PAPER"
    assert summary["relationship_agent"]["mode"] == "PAPER"


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
    assert "Limitless Arb" in body
    assert "Relationship Aggressive" in body

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


def test_arb_monitor_page_renders_positions_exits_and_kill_switches(
    client, app, tmp_data_root: Path
) -> None:
    positions_repo = ParquetPositionsRepository(tmp_data_root)
    orders_repo = ParquetOrdersLogRepository(tmp_data_root)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rel_id = "arb-rel-1"
    slug = "very-long-limitless-market-slug-for-dashboard-truncation-check"

    positions_repo.append_many([
        _position_row(
            position_id=f"{rel_id}_lim",
            relationship_id=rel_id,
            market_id=slug,
            token_id="0xLIMITLESS",
            entry_price="0.35",
            size="1",
            notional_usdc="1",
            gross_edge="0.25",
            notes=(
                f"arb_gap=0.2500 slug={slug} lim_entry=0.3500 "
                "poly_yes_entry=0.4000 similarity=0.900"
            ),
            open_ts_ms=now_ms - 3_600_000,
            ingested_ts_ms=now_ms - 3_600_000,
        ),
        _position_row(
            position_id=f"{rel_id}_poly",
            relationship_id=rel_id,
            market_id="0xCOND",
            token_id="tok_no",
            entry_price="0.60",
            size="1",
            notional_usdc="1",
            gross_edge="0.25",
            notes=(
                f"arb_gap=0.2500 slug={slug} lim_entry=0.3500 "
                "poly_yes_entry=0.4000 similarity=0.900"
            ),
            open_ts_ms=now_ms - 3_600_000,
            ingested_ts_ms=now_ms - 3_600_000,
        ),
        _position_row(
            position_id=rel_id,
            relationship_id=rel_id,
            side="snapshot",
            market_id=slug,
            token_id="tok_no",
            gross_edge="0.1000",
            notes="snap lim_now=0.4800 poly_yes_now=0.4800 unrealised=0.1000",
            open_ts_ms=now_ms - 3_600_000,
            ingested_ts_ms=now_ms - 600_000,
        ),
    ])
    csv_dir = tmp_data_root / "cross_market_arb"
    csv_dir.mkdir(parents=True, exist_ok=True)
    (csv_dir / "arb_20260602_120000.csv").write_text(
        "limitless_slug,limitless_title,poly_condition_id,poly_question,"
        "limitless_yes,poly_yes,total,arb_gap,similarity,status\n"
        f"{slug},Question,0xCOND,Question,0.4800,0.4800,0.9600,0.0400,0.9000,ARB_OPPORTUNITY\n",
        encoding="utf-8",
    )
    orders_repo.append_many([
        _orders_log_row(
            intent_id="lim-exit",
            ts_ms=now_ms - 300_000,
            strategy_id="limitless_arb_exit",
            market_id=slug,
            side="SELL_YES",
            avg_fill_price="0.4800",
            status="paper_filled",
            source_relationship_id=rel_id,
            notes=(
                "exit_leg=limitless position_id=arb-rel-1 lim_entry=0.3500 "
                "lim_exit=0.4800 gross_profit=0.0500 fees_usdc=0.0400 "
                "realised_profit=0.0100"
            ),
        ),
        _orders_log_row(
            intent_id="poly-exit",
            ts_ms=now_ms - 299_000,
            strategy_id="limitless_arb_exit",
            market_id="0xCOND",
            side="sell",
            avg_fill_price="0.5200",
            status="paper_filled",
            source_relationship_id=rel_id,
            notes=(
                "exit_leg=polymarket position_id=arb-rel-1 poly_entry=0.4000 "
                "poly_yes_current=0.4800 gross_profit=0.0500 fees_usdc=0.0400 "
                "realised_profit=0.0100"
            ),
        ),
    ])
    (tmp_data_root / ".killswitch_limitless_arb").write_text("halt\n")

    qs = app.extensions["dashboard_db"]
    open_rows = qs.open_arb_positions()
    closed_rows = qs.closed_arb_positions()
    assert len(open_rows) == 1
    assert open_rows[0]["market_slug"] == slug
    assert open_rows[0]["entry_arb_gap"] == pytest.approx(0.25)
    assert open_rows[0]["lim_entry_price"] == pytest.approx(0.35)
    assert open_rows[0]["poly_yes_entry_price"] == pytest.approx(0.40)
    assert open_rows[0]["stake_usdc"] == pytest.approx(1.0)
    assert open_rows[0]["current_lim_yes"] == pytest.approx(0.48)
    assert open_rows[0]["current_poly_yes"] == pytest.approx(0.48)
    assert open_rows[0]["current_gap"] == pytest.approx(0.04)
    assert open_rows[0]["current_mtm"] == pytest.approx(0.05)
    assert len(closed_rows) == 1
    assert closed_rows[0]["realised_profit"] == pytest.approx(0.01)
    assert closed_rows[0]["lim_exit_price"] == pytest.approx(0.48)
    assert closed_rows[0]["poly_exit_price"] == pytest.approx(0.52)

    body = client.get("/arb").data.decode()
    assert "Limitless Arb Monitor" in body
    assert "Realised PnL" in body
    assert "+0.0100" in body
    assert "Open Positions" in body
    assert "Closed Positions" in body
    assert "Limitless arb" in body
    assert "Active" in body
    assert "0.2500" in body
    assert "Current Lim" in body
    assert "Current Poly YES" in body
    assert "Current Gap" in body
    assert "0.0400" in body
    assert "was 0.2500" in body
    assert "+0.0500" in body
    assert 'href="/arb" class="active"' in body
    assert 'http-equiv="refresh" content="60"' in body


def test_arb_open_positions_includes_current_gap(app, tmp_data_root: Path) -> None:
    positions_repo = ParquetPositionsRepository(tmp_data_root)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rel_id = "arb-rel-current-gap"
    slug = "limitless-current-gap-market"

    positions_repo.append_many([
        _position_row(
            position_id=f"{rel_id}_lim",
            relationship_id=rel_id,
            market_id=slug,
            token_id="lim_yes",
            entry_price="0.35",
            size="1",
            notional_usdc="1",
            gross_edge="0.2000",
            notes=f"arb_gap=0.2000 slug={slug} lim_entry=0.3500 poly_yes_entry=0.4000",
            open_ts_ms=now_ms - 1_000,
            ingested_ts_ms=now_ms - 1_000,
        ),
        _position_row(
            position_id=f"{rel_id}_poly",
            relationship_id=rel_id,
            market_id="poly-cond",
            token_id="poly_no",
            entry_price="0.60",
            size="1",
            notional_usdc="1",
            gross_edge="0.2000",
            notes=f"arb_gap=0.2000 slug={slug} lim_entry=0.3500 poly_yes_entry=0.4000",
            open_ts_ms=now_ms - 1_000,
            ingested_ts_ms=now_ms - 1_000,
        ),
    ])
    csv_dir = tmp_data_root / "cross_market_arb"
    csv_dir.mkdir(parents=True, exist_ok=True)
    (csv_dir / "arb_20260602_123000.csv").write_text(
        "limitless_slug,limitless_title,poly_condition_id,poly_question,"
        "limitless_yes,poly_yes,total,arb_gap,similarity,status\n"
        f"{slug},Question,poly-cond,Question,0.4200,0.5400,0.9600,0.0400,0.9000,ARB_OPPORTUNITY\n",
        encoding="utf-8",
    )

    row = app.extensions["dashboard_db"].open_arb_positions()[0]

    assert row["current_lim_yes"] == pytest.approx(0.42)
    assert row["current_poly_yes"] == pytest.approx(0.54)
    assert row["current_gap"] == pytest.approx(0.04)
    assert row["current_mtm"] == pytest.approx(-0.07)
    assert row["convergence_pct"] == pytest.approx(80.0)
    assert row["convergence_state"] == "good"
