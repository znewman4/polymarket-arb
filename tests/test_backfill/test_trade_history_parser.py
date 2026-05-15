"""Tests for trade history backfill parsing."""

from __future__ import annotations

from decimal import Decimal

from polymarket_arb.backfill.trade_history import _parse_trade_payload


def test_trade_history_parser_handles_missing_side():
    payload = {
        "data": [
            {"timestamp": 1700000000, "price": "0.55", "size": "10"},
        ]
    }
    rows = _parse_trade_payload(
        payload, market_id="m1", condition_id="c1", token_id="tok1", outcome="Yes"
    )
    assert len(rows) == 1
    assert rows[0].side is None
    assert rows[0].price == Decimal("0.55")
    assert rows[0].size == Decimal("10")


def test_trade_history_parser_handles_side_field():
    payload = {
        "data": [
            {"timestamp": 1700000000, "price": "0.42", "size": "5", "side": "BUY"},
        ]
    }
    rows = _parse_trade_payload(
        payload, market_id="m1", condition_id=None, token_id="tok1", outcome=None
    )
    assert rows[0].side == "buy"


def test_trade_history_parser_drops_out_of_bounds():
    payload = {
        "data": [
            {"timestamp": 1700000000, "price": "1.50", "size": "5"},
            {"timestamp": 1700003600, "price": "0.42", "size": "5"},
        ]
    }
    rows = _parse_trade_payload(
        payload, market_id="m1", condition_id=None, token_id="tok1", outcome=None
    )
    assert len(rows) == 1
    assert rows[0].price == Decimal("0.42")


def test_trade_history_parser_handles_list_payload():
    payload = [
        {"timestamp": 1700000000, "price": "0.55", "size": "10"},
    ]
    rows = _parse_trade_payload(
        payload, market_id="m1", condition_id=None, token_id="tok1", outcome="Yes"
    )
    assert len(rows) == 1


def test_trade_history_parser_handles_empty():
    rows = _parse_trade_payload(
        {"data": []}, market_id="m1", condition_id=None, token_id="tok1", outcome=None
    )
    assert rows == []
