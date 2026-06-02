"""Tests for OrderClient (paper-mode + kill switch + compliance gate)."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest

from polymarket_arb.live.order_client import OrderClient
from polymarket_arb.monitoring import kill_switch
from polymarket_arb.risk.models import OrderIntent
from polymarket_arb.storage.base import OrderbookLevel, OrderbookSnapshot
from polymarket_arb.storage.parquet.orderbook_repo import ParquetOrderbookRepository
from polymarket_arb.storage.parquet.orders_log_repo import ParquetOrdersLogRepository
from polymarket_arb.storage.parquet.positions_repo import ParquetPositionsRepository


def _intent(
    token_id: str = "tok-a",
    size: float = 100.0,
    price: float = 1.0,
    side: str = "buy",
    detail: dict[str, str] | None = None,
) -> OrderIntent:
    return OrderIntent(
        id="intent-1",
        strategy_id="strict_research",
        token_id=token_id,
        side=side,
        price=Decimal(str(price)),
        size=Decimal(str(size)),
        detail=detail or {},
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


def _seed_book_bids(data_root, token_id: str, bids: list[tuple[float, float]]) -> None:
    repo = ParquetOrderbookRepository(data_root)
    snap = OrderbookSnapshot(
        token_id=token_id,
        condition_id=None,
        market_slug=None,
        timestamp_ms=1_700_000_000_000,
        bids=[OrderbookLevel(price=Decimal(str(p)), size=Decimal(str(s))) for p, s in bids],
        asks=[],
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
    result = client.place_order(
        _intent(
            size=80.0,
            detail={
                "gross_edge": "0.05",
                "relationship_id": "rel-a",
                "relationship_type": "nested_a_implies_b",
            },
        ),
        market_id="market-a",
        source_relationship_id="rel-a",
        notes="signal notes",
    )
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
    assert json.loads(row.detail_json)["gross_edge"] == "0.05"
    # Filled paper orders create an open tracked position.
    positions = list(ParquetPositionsRepository(tmp_data_root).iter_recent())
    assert len(positions) == 1
    position = positions[0]
    key = f"market-atok-astrict_research{result.ts_ms}".encode()
    assert position.position_id == hashlib.sha256(key).hexdigest()[:16]
    assert Decimal(position.entry_price) == Decimal("0.52")
    assert Decimal(position.size) == Decimal("80")
    assert Decimal(position.notional_usdc) == Decimal("41.60")
    assert position.gross_edge == "0.05"
    assert position.relationship_id == "rel-a"
    assert position.relationship_type == "nested_a_implies_b"
    assert position.notes == "signal notes"
    assert position.status == "open"
    assert position.ingested_ts_ms == result.ts_ms


def test_paper_mode_no_book_falls_through_with_audit(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    client = OrderClient(s)
    result = client.place_order(_intent(token_id="no-such-token"))
    assert result.status == "paper_no_book"
    assert result.filled_size == Decimal("0")
    rows = list(ParquetOrdersLogRepository(tmp_data_root).iter_recent())
    assert len(rows) == 1
    assert rows[0].status == "paper_no_book"
    assert list(ParquetPositionsRepository(tmp_data_root).iter_recent()) == []


def test_live_preflight_quote_fills_without_recorded_book(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    client = OrderClient(s)
    result = client.place_order(
        _intent(size=1.0, price=0.57),
        preflight_book={"best_ask": 0.57},
    )

    assert result.status == "paper_filled"
    assert result.avg_fill_price == Decimal("0.57")
    assert result.preflight_passed is True
    assert result.preflight_token_id == "tok-a"


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


def test_live_mode_with_orders_allowed_and_no_signer_url_rejected(
    settings, tmp_data_root
) -> None:
    """paper_mode=False AND orders_allowed=True but no signer URL →
    rejected_credentials_missing (no network attempt)."""
    s = settings.model_copy(update={
        "paper_mode": False,
        "orders_allowed": True,
        "polymarket_signer_url": "",
    })
    client = OrderClient(s)
    result = client.place_order(_intent())
    assert result.status == "rejected_credentials_missing"
    assert result.submitted is False
    rows = list(ParquetOrdersLogRepository(tmp_data_root).iter_recent())
    assert rows[0].status == "rejected_credentials_missing"


def test_live_mode_submits_via_signer(
    settings, tmp_data_root, monkeypatch
) -> None:
    """With signer service mocked, status → live_submitted."""
    s = settings.model_copy(update={
        "paper_mode": False,
        "orders_allowed": True,
    })
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **kw: type(
            "Response",
            (),
            {
                "raise_for_status": lambda self: None,
                "json": lambda self: {"neg_risk": True, "minimum_tick_size": "0.01"},
            },
        )(),
    )
    monkeypatch.setattr(
        "polymarket_arb.live.order_client.post_order_via_signer",
        lambda *a, **kw: {"orderID": "abc123"},
    )
    client = OrderClient(s)
    result = client.place_order(_intent(size=10.0, price=0.5))
    assert result.status == "live_submitted"
    assert result.submitted is True
    assert result.http_status == 200
    assert result.filled_size == Decimal("10")
    assert result.notional_usdc == Decimal("5.0")


def test_live_mode_submits_sell_via_signer(
    settings, tmp_data_root, monkeypatch
) -> None:
    s = settings.model_copy(update={
        "paper_mode": False,
        "orders_allowed": True,
    })
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **kw: type(
            "Response",
            (),
            {
                "raise_for_status": lambda self: None,
                "json": lambda self: {"neg_risk": False, "minimum_tick_size": "0.01"},
            },
        )(),
    )
    signer_calls: list[dict] = []

    def _post_order_via_signer(*args, **kwargs):
        signer_calls.append(kwargs)
        return {"orderID": "sell-abc"}

    monkeypatch.setattr(
        "polymarket_arb.live.order_client.post_order_via_signer",
        _post_order_via_signer,
    )
    client = OrderClient(s)
    result = client.place_order(_intent(side="sell", size=10.0, price=0.5))

    assert result.status == "live_submitted"
    assert result.submitted is True
    assert signer_calls[0]["side"] == "sell"
    assert signer_calls[0]["token_id"] == "tok-a"


def test_live_mode_accepts_order_hash_response(
    settings, tmp_data_root, monkeypatch
) -> None:
    """The Node signer may return orderHashes rather than orderID."""
    s = settings.model_copy(update={
        "paper_mode": False,
        "orders_allowed": True,
    })
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **kw: type(
            "Response",
            (),
            {
                "raise_for_status": lambda self: None,
                "json": lambda self: {"neg_risk": False, "minimum_tick_size": "0.01"},
            },
        )(),
    )
    monkeypatch.setattr(
        "polymarket_arb.live.order_client.post_order_via_signer",
        lambda *a, **kw: {"success": True, "orderHashes": ["hash-123"]},
    )
    client = OrderClient(s)
    result = client.place_order(_intent(size=1.0, price=0.5))
    assert result.status == "live_submitted"
    assert result.submitted is True
    rows = list(ParquetOrdersLogRepository(tmp_data_root).iter_recent())
    assert json.loads(rows[0].detail_json)["order_id"] == "hash-123"


def test_live_mode_failed_when_signer_raises(
    settings, tmp_data_root, monkeypatch
) -> None:
    """Exception inside the live path → live_failed, no re-raise."""
    s = settings.model_copy(update={
        "paper_mode": False,
        "orders_allowed": True,
    })

    def _boom(*a, **kw):
        raise RuntimeError("CLOB unreachable")

    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **kw: type(
            "Response",
            (),
            {
                "raise_for_status": lambda self: None,
                "json": lambda self: {"neg_risk": False, "minimum_tick_size": "0.01"},
            },
        )(),
    )
    monkeypatch.setattr(
        "polymarket_arb.live.order_client.post_order_via_signer",
        _boom,
    )
    client = OrderClient(s)
    result = client.place_order(_intent())
    assert result.status == "live_failed"
    assert result.submitted is False
    assert "CLOB unreachable" in result.reason


def test_unsupported_side_rejected(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    _seed_book(tmp_data_root, "tok-a", [(0.51, 100.0)])
    intent = OrderIntent(
        id="i", strategy_id="s", token_id="tok-a",
        side="short", price=Decimal("0.49"), size=Decimal("50"),
    )
    client = OrderClient(s)
    result = client.place_order(intent)
    assert result.status == "rejected_unsupported_side"


def test_paper_sell_full_fill(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    _seed_book_bids(tmp_data_root, "tok-a", [(0.50, 30.0), (0.48, 30.0)])
    client = OrderClient(s)

    result = client.place_order(_intent(side="sell", size=50.0, price=0.48))

    assert result.status == "paper_filled"
    assert result.filled_size == Decimal("50.0")
    assert result.avg_fill_price == pytest.approx(Decimal("0.492"))
    rows = list(ParquetOrdersLogRepository(tmp_data_root).iter_recent())
    assert len(rows) == 1
    assert rows[0].side == "sell"


def test_paper_sell_partial_fill(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    _seed_book_bids(tmp_data_root, "tok-a", [(0.50, 20.0)])
    client = OrderClient(s)

    result = client.place_order(_intent(side="sell", size=50.0, price=0.48))

    assert result.status == "paper_partial"
    assert result.filled_size == Decimal("20.0")


def test_paper_sell_no_bids(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    _seed_book(tmp_data_root, "tok-a", [(0.51, 100.0)])
    client = OrderClient(s)

    result = client.place_order(_intent(side="sell", size=50.0, price=0.48))

    assert result.status == "paper_no_fill"


def test_paper_sell_no_book(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    client = OrderClient(s)

    result = client.place_order(_intent(token_id="unknown", side="sell", size=50.0, price=0.48))

    assert result.status == "paper_no_book"


def test_paper_invalid_side_rejected(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    _seed_book(tmp_data_root, "tok-a", [(0.51, 100.0)])
    client = OrderClient(s)

    result = client.place_order(_intent(side="short", size=50.0, price=0.48))

    assert result.status == "rejected_unsupported_side"


def test_paper_sell_kill_switch_blocks(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    s.killswitch_path.parent.mkdir(parents=True, exist_ok=True)
    s.killswitch_path.write_text("halt\n")
    try:
        _seed_book_bids(tmp_data_root, "tok-a", [(0.50, 100.0)])
        client = OrderClient(s)
        result = client.place_order(_intent(side="sell", size=50.0, price=0.48))
        assert result.status == "rejected_kill_switch"
    finally:
        s.killswitch_path.unlink(missing_ok=True)
        kill_switch.reset_for_tests()


def test_paper_sell_fee_applied(settings, tmp_data_root) -> None:
    s = settings.model_copy(update={"paper_mode": True})
    _seed_book_bids(tmp_data_root, "tok-a", [(0.50, 100.0)])
    client = OrderClient(s)

    result = client.place_order(_intent(side="sell", size=10.0, price=0.48))

    assert result.status == "paper_filled"
    assert result.fees_usdc == pytest.approx(Decimal("0.10"))
