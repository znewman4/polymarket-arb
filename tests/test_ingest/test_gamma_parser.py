"""Fixture-replay tests for the Gamma parser.

Real ``markets_page1.json`` was captured from live Gamma so the
stringified-JSON / ISO-date / extra-fields quirks are exercised against
actual API output, not a hand-crafted approximation.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_arb.ingest.gamma.parser import (
    InvalidMarket,
    market_text_hash,
    parse_event,
    parse_market,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gamma"


def _load(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())


def test_real_gamma_capture_yields_marketrows():
    rows = []
    for raw in _load("markets_page1.json"):
        row = parse_market(raw, ingested_ts_ms=1_700_000_000_000)
        if row is not None:
            rows.append(row)
    assert rows, "expected at least one row from the live Gamma capture"
    for r in rows:
        assert r.id and r.condition_id and r.slug and r.question
        assert isinstance(r.outcomes, list) and len(r.outcomes) >= 2
        assert isinstance(r.gamma_outcome_prices_snapshot, list)
        assert all(isinstance(p, Decimal) for p in r.gamma_outcome_prices_snapshot)
        assert isinstance(r.clob_token_ids, list) and len(r.clob_token_ids) >= 2
        assert r.text_hash and len(r.text_hash) == 64  # sha256 hex


def test_synthetic_stringified_and_raw_list_both_parse():
    raws = _load("markets_synthetic.json")
    rows = [parse_market(r, ingested_ts_ms=1) for r in raws]
    assert all(r is not None for r in rows)
    stringified, raw_list, missing_dates = rows
    # Stringified form
    assert stringified.outcomes == ["Yes", "No"]
    assert stringified.gamma_outcome_prices_snapshot == [Decimal("0.42"), Decimal("0.58")]
    # Raw-list form
    assert raw_list.outcomes == ["Yes", "No"]
    assert raw_list.gamma_outcome_prices_snapshot == [Decimal("0.50"), Decimal("0.50")]
    # Missing-dates: end/start nullable
    assert missing_dates.end_date_ms is None
    assert missing_dates.start_date_ms is None


def test_iso_dates_converted_to_ms():
    [synth] = [r for r in _load("markets_synthetic.json") if r["slug"] == "synthetic-stringified"]
    row = parse_market(synth, ingested_ts_ms=1)
    # 2026-01-01T00:00:00Z → 1767225600000 ms
    assert row.start_date_ms == 1767225600000
    # 2026-12-31T00:00:00Z → 1798675200000 ms
    assert row.end_date_ms == 1798675200000


def test_invalid_markets_dropped():
    raws = _load("markets_invalid.json")
    rows = [parse_market(r, ingested_ts_ms=1) for r in raws]
    assert all(r is None for r in rows), "every invalid market must be dropped"


def test_strict_mode_raises_on_invalid():
    bad = _load("markets_invalid.json")[0]
    with pytest.raises(InvalidMarket):
        parse_market(bad, ingested_ts_ms=1, strict=True)


def test_text_hash_is_deterministic():
    a = market_text_hash("Q?", "D")
    b = market_text_hash("Q?", "D")
    c = market_text_hash("Q?", "different")
    assert a == b
    assert a != c
    assert len(a) == 64


def test_event_parser_handles_nested_markets_and_tags():
    raws = _load("events_synthetic.json")
    rows = [parse_event(r, ingested_ts_ms=1) for r in raws]
    full, minimal = rows
    assert full is not None and minimal is not None
    assert full.market_ids == ["1001", "1002"]
    assert sorted(full.tags) == ["politics", "test"]
    assert full.start_date_ms == 1767225600000
    assert minimal.market_ids == []
    assert minimal.tags == []
    assert minimal.start_date_ms is None
