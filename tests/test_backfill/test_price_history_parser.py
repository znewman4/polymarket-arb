"""Tests for the CLOB /prices-history parser."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_arb.ingest.clob.price_history_parser import (
    PriceHistoryParseError,
    parse_prices_history,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "clob"


def _parse(fixture_name: str, **kwargs):
    payload = json.loads((_FIXTURES / fixture_name).read_text())
    return parse_prices_history(
        payload,
        token_id="tok1",
        interval="1h",
        market_id="mkt1",
        condition_id=None,
        outcome="Yes",
        **kwargs,
    )


def test_price_history_client_parses_valid_history():
    result = _parse("prices_history_btc_token.json")
    assert len(result.rows) == 10
    assert result.duplicate_timestamp_count == 0
    assert result.price_out_of_bounds_count == 0
    assert result.malformed_point_count == 0
    for row in result.rows:
        assert Decimal("0") <= row.price <= Decimal("1")
        assert row.token_id == "tok1"
        assert row.market_id == "mkt1"
        assert row.interval == "1h"


def test_price_history_rejects_price_outside_bounds():
    result = _parse("prices_history_out_of_bounds.json")
    # 2 out-of-bounds points dropped, 2 valid remain
    assert result.price_out_of_bounds_count == 2
    assert len(result.rows) == 2
    for row in result.rows:
        assert Decimal("0") <= row.price <= Decimal("1")


def test_price_history_strict_mode_raises_on_out_of_bounds():
    payload = json.loads((_FIXTURES / "prices_history_out_of_bounds.json").read_text())
    with pytest.raises(PriceHistoryParseError, match="out of bounds"):
        parse_prices_history(
            payload,
            token_id="tok",
            interval="1h",
            market_id="m",
            condition_id=None,
            outcome=None,
            strict=True,
        )


def test_price_history_handles_empty_history():
    result = _parse("prices_history_empty.json")
    assert result.rows == []
    assert result.duplicate_timestamp_count == 0
    assert result.price_out_of_bounds_count == 0


def test_price_history_counts_duplicate_timestamps():
    result = _parse("prices_history_with_duplicates.json")
    # 2 pairs of duplicates = 2 duplicate count, 3 unique timestamps
    assert result.duplicate_timestamp_count == 2
    assert len(result.rows) == 3  # deduplicated


def test_price_history_gap_detection():
    # Fixture with 10 hourly points — largest gap is 1h (3600s)
    result = _parse("prices_history_btc_token.json")
    ts_list = [r.ts_ms for r in result.rows]
    gaps = [ts_list[i + 1] - ts_list[i] for i in range(len(ts_list) - 1)]
    assert max(gaps) == 3_600_000  # 1h in ms


def test_price_history_returns_sorted_rows():
    result = _parse("prices_history_btc_token.json")
    ts_list = [r.ts_ms for r in result.rows]
    assert ts_list == sorted(ts_list)


def test_price_history_rejects_malformed_payload():
    with pytest.raises(PriceHistoryParseError):
        parse_prices_history(
            "not-a-dict",  # type: ignore[arg-type]
            token_id="tok",
            interval="1h",
            market_id="m",
            condition_id=None,
            outcome=None,
        )
