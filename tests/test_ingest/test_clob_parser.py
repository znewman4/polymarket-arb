from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_arb.ingest.clob.parser import ClobParseError, best_quote_from_book, parse_orderbook


def _fixture(name: str) -> dict:
    return json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "clob" / name).read_text())


def test_parse_orderbook_and_best_quote():
    book = parse_orderbook(_fixture("book_single.json"))
    assert book.token_id == "token-yes"
    assert book.condition_id == "0xcondition"
    assert book.bids[0].price == Decimal("0.48")
    assert book.asks[0].size == Decimal("800")

    quote = best_quote_from_book(book)
    assert quote.best_bid == Decimal("0.48")
    assert quote.best_ask == Decimal("0.52")
    assert quote.midpoint == Decimal("0.50")
    assert quote.spread == Decimal("0.04")


def test_malformed_price_raises():
    payload = _fixture("book_single.json")
    payload["bids"][0]["price"] = "not-a-price"
    with pytest.raises(ClobParseError):
        parse_orderbook(payload)
