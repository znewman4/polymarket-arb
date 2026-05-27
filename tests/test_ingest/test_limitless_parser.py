"""Unit tests for the Limitless market parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from polymarket_arb.ingest.limitless.parser import parse_limitless_market
from polymarket_arb.limitless.models import LimitlessMarketEntry

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "limitless" / "sample_markets.json"


@pytest.fixture(scope="module")
def sample_markets() -> list[dict]:
    return json.loads(_FIXTURES.read_text())


def test_valid_binary_market_yes_price(sample_markets):
    raw = sample_markets[0]  # btc-above-65000-sep1, prices=[42.8, 57.2]
    result = parse_limitless_market(raw)
    assert isinstance(result, LimitlessMarketEntry)
    assert result.slug == "btc-above-65000-sep1"
    assert abs(result.yes_price - 0.428) < 1e-9


def test_valid_binary_market_clob(sample_markets):
    raw = sample_markets[1]  # eth-above-3000-oct1, prices=[55.0, 45.0]
    result = parse_limitless_market(raw)
    assert result is not None
    assert abs(result.yes_price - 0.55) < 1e-9


def test_price_exactly_zero_returns_none(sample_markets):
    raw = sample_markets[2]  # prices=[0.0, 100.0]
    assert parse_limitless_market(raw) is None


def test_group_market_returns_none(sample_markets):
    raw = sample_markets[3]  # marketType=group
    assert parse_limitless_market(raw) is None


def test_prices_not_summing_to_100_returns_none(sample_markets):
    raw = sample_markets[4]  # prices=[40.0, 40.0] — sum=80, not ≈100
    assert parse_limitless_market(raw) is None


def test_missing_prices_returns_none():
    raw = {"slug": "no-prices", "title": "No prices", "address": "0x0", "marketType": "single"}
    assert parse_limitless_market(raw) is None


def test_non_numeric_prices_returns_none():
    raw = {
        "slug": "bad",
        "title": "Bad",
        "address": "0x0",
        "marketType": "single",
        "prices": ["not", "numbers"],
    }
    assert parse_limitless_market(raw) is None


def test_result_has_correct_address(sample_markets):
    raw = sample_markets[0]
    result = parse_limitless_market(raw)
    assert result is not None
    assert result.address == "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1"


def test_result_address_falls_back_to_venue_exchange():
    raw = {
        "slug": "clob-no-top-level-address",
        "title": "CLOB address fallback",
        "venue": {"exchange": "0xEXCHANGE"},
        "marketType": "single",
        "prices": [0.5, 0.5],
    }
    result = parse_limitless_market(raw)
    assert result is not None
    assert result.address == "0xEXCHANGE"


def test_percentage_to_fraction_conversion():
    raw = {
        "slug": "test",
        "title": "Test market",
        "address": "0xTEST",
        "marketType": "single",
        "prices": [75.0, 25.0],
    }
    result = parse_limitless_market(raw)
    assert result is not None
    assert abs(result.yes_price - 0.75) < 1e-9


def test_decimal_prices_are_stored_without_rescaling():
    raw = {
        "slug": "decimal-api-response",
        "title": "Decimal API response",
        "address": "0xTEST",
        "marketType": "single",
        "prices": [0.428, 0.572],
    }
    result = parse_limitless_market(raw)
    assert result is not None
    assert abs(result.yes_price - 0.428) < 1e-9


def test_rejects_price_total_in_neither_supported_format():
    raw = {
        "slug": "mixed-api-response",
        "title": "Mixed API response",
        "address": "0xTEST",
        "marketType": "single",
        "prices": [0.42, 58.0],
    }
    assert parse_limitless_market(raw) is None


def test_rejects_normalized_yes_price_above_one():
    raw = {
        "slug": "over-one",
        "title": "Over one",
        "address": "0xTEST",
        "marketType": "single",
        "prices": [1.05, 0.01],
    }
    assert parse_limitless_market(raw) is None


def test_prices_exactly_100_boundary_ok():
    raw = {
        "slug": "boundary",
        "title": "Boundary",
        "address": "0x0",
        "marketType": "single",
        "prices": [99.9, 0.1],
    }
    result = parse_limitless_market(raw)
    assert result is not None
    assert abs(result.yes_price - 0.999) < 1e-9


# ─── token IDs ───────────────────────────────────────────────────────────────


def test_token_ids_populated_from_tokens_field(sample_markets):
    raw = sample_markets[1]  # eth-above-3000-oct1, has tokens.yes/no
    result = parse_limitless_market(raw)
    assert result is not None
    assert result.token_id_yes == "33333333333333333333333333333333333333333333333333333333333333333"
    assert result.token_id_no == "44444444444444444444444444444444444444444444444444444444444444444"


def test_token_ids_empty_string_when_tokens_absent():
    raw = {
        "slug": "no-tokens",
        "title": "No tokens field",
        "address": "0xTEST",
        "marketType": "single",
        "prices": [0.6, 0.4],
    }
    result = parse_limitless_market(raw)
    assert result is not None
    assert result.token_id_yes == ""
    assert result.token_id_no == ""


def test_token_ids_empty_string_when_tokens_is_none():
    raw = {
        "slug": "null-tokens",
        "title": "Null tokens",
        "address": "0xTEST",
        "marketType": "single",
        "prices": [0.5, 0.5],
        "tokens": None,
    }
    result = parse_limitless_market(raw)
    assert result is not None
    assert result.token_id_yes == ""
    assert result.token_id_no == ""
