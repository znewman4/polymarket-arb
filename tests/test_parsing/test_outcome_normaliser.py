from __future__ import annotations

from polymarket_arb.parsing.outcome_normaliser import parse_stringified_json


def test_none_returns_empty():
    assert parse_stringified_json(None) == []


def test_raw_list_passthrough():
    v = ["Yes", "No"]
    assert parse_stringified_json(v) == ["Yes", "No"]


def test_stringified_list_parsed():
    assert parse_stringified_json('["Yes","No"]') == ["Yes", "No"]


def test_invalid_json_returns_empty():
    assert parse_stringified_json("not-json") == []


def test_non_list_json_returns_empty():
    # JSON object inside a string — we don't try to coerce.
    assert parse_stringified_json('{"x": 1}') == []
