"""Tests for OrderClient (paper-mode + kill switch + compliance gate)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from polymarket_arb.live.order_client import OrderClient
from polymarket_arb.monitoring import kill_switch
from polymarket_arb.risk.models import OrderIntent
from polymarket_arb.storage.base import OrderbookLevel, OrderbookSnapshot
from polymarket_arb.storage.parquet.orderbook_repo import ParquetOrderbookRepository
from polymarket_arb.storage.parquet.orders_log_repo import ParquetOrdersLogRepository


def _intent(token_id: str = "tok-a", size: float = 100.0, price: float = 1.0) -> OrderIntent:
    return OrderIntent(
        id="intent-1",
        strategy_id="strict_research",
        token_id=token_id,
        side="buy",
        price=Decimal(str(price)),
        size=Decimal(str(size)),
    )


def _seed_book(data_root, token_id: str, asks: list[tuple[float, float]]) -> None:
    repo = ParquetOrderbookRepository(data_root)
    snap = OrderbookSnapshot(
        token_id=token_id,
        condition_id=None,
        market_slug=None,
        timestamp_ms=1_700_000_000_000,
        bids=[],
        asks=[OrderbookLevel(price=Decimal(str(p)), size=Decimal(str(s))) for p, s in asks],
        book_hash=None,
        source="rest",
        schema_version=1,
        ingested_ts_ms=1_700_000_000_000,
    )
    repo.append_snapshot(snap)


def test_paper_mode_simulated_fill_writes_orders_log(settings, tmp_data_root) -> None:
    """Paper-mode happy path: book exists → simulated fill → orders_log written."""
    s = settings.model_copy(update={"paper_mode": True, "orders_allowed": False})
    _seed_book(tmp_data_root, "tok-a", [(0.51, 40.0), (0.53, 60.0)])
    client = OrderClient(s)
    result = client.place_order(_intent(size=80.0))
    # 40 @ 0.51 + 40 @ 0.53 = 20.4 + 21.2 = 41.6 over 80 shares → avg = 0.52
    assert result.status == "paper_filled"
    assert result.submitted is False
    assert result.paper_mode is True
    assert result.filled_size == Decimal("80")
    assert result.avg_fill_price == pytest.approx(Decimal("0.52"))
    # orders_log row was appended
    rows = list(ParquetOrdersLogRepository(tmp_data_root).iter_recent())
    assert len(rows) == 1
    row = rows[0]
    assert row.intent_id == "intent-1"
    assert row.paper_mode is True
    assert row.status == "paper_filled"


def test_paper_mode_no_book_falls_through_with_audit(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    client = OrderClient(s)
    result = client.place_order(_intent(token_id="no-such-token"))
    assert result.status == "paper_no_book"
    assert result.filled_size == Decimal("0")
    rows = list(ParquetOrdersLogRepository(tmp_data_root).iter_recent())
    assert len(rows) == 1
    assert rows[0].status == "paper_no_book"


def test_kill_switch_blocks_paper_order(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    # Drop the killswitch file in place.
    s.killswitch_path.parent.mkdir(parents=True, exist_ok=True)
    s.killswitch_path.write_text("halt\n")
    try:
        _seed_book(tmp_data_root, "tok-a", [(0.51, 100.0)])
        client = OrderClient(s)
        result = client.place_order(_intent())
        assert result.status == "rejected_kill_switch"
        assert result.submitted is False
        assert result.kill_switch_active is True
        rows = list(ParquetOrdersLogRepository(tmp_data_root).iter_recent())
        assert rows[0].status == "rejected_kill_switch"
    finally:
        s.killswitch_path.unlink(missing_ok=True)
        kill_switch.reset_for_tests()


def test_preflight_failed_rejects(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    _seed_book(tmp_data_root, "tok-a", [(0.51, 100.0)])
    client = OrderClient(s)
    result = client.place_order(_intent(), preflight_passed=False)
    assert result.status == "rejected_preflight"
    assert result.preflight_passed is False
    rows = list(ParquetOrdersLogRepository(tmp_data_root).iter_recent())
    assert rows[0].status == "rejected_preflight"


def test_live_mode_with_orders_disallowed_rejects(settings, tmp_data_root) -> None:
    """paper_mode=False AND orders_allowed=False → rejected_orders_disallowed."""
    s = settings.model_copy(update={"paper_mode": False, "orders_allowed": False})
    client = OrderClient(s)
    result = client.place_order(_intent())
    assert result.status == "rejected_orders_disallowed"
    assert result.submitted is False
    rows = list(ParquetOrdersLogRepository(tmp_data_root).iter_recent())
    assert rows[0].status == "rejected_orders_disallowed"


def test_live_mode_with_orders_allowed_falls_through_signing_stub(settings, tmp_data_root) -> None:
    """paper_mode=False AND orders_allowed=True → signing stub raises →
    rejected_live_signing_not_ready (so we never actually submit)."""
    s = settings.model_copy(update={"paper_mode": False, "orders_allowed": True})
    client = OrderClient(s, signing_key_hex=None)
    result = client.place_order(_intent())
    assert result.status == "rejected_live_signing_not_ready"
    assert result.submitted is False


def test_unsupported_side_rejected(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    _seed_book(tmp_data_root, "tok-a", [(0.51, 100.0)])
    intent = OrderIntent(
        id="i", strategy_id="s", token_id="tok-a",
        side="sell", price=Decimal("0.49"), size=Decimal("50"),
    )
    client = OrderClient(s)
    result = client.place_order(intent)
    assert result.status == "rejected_unsupported_side"
