"""``MarketSemantics`` Pydantic schema strictness."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from polymarket_arb.nlp.schemas import MarketSemantics


def _payload() -> dict:
    return {
        "source_market_id": "m",
        "question": "q?",
        "canonical_question": "q?",
        "market_type": "binary",
        "temporal_resolution": "vague",
        "positive_resolution_condition": "y",
        "negative_resolution_condition": "n",
        "semantic_confidence": 0.5,
    }


def test_minimal_valid_payload():
    sem = MarketSemantics.model_validate(_payload())
    assert sem.market_type == "binary"
    assert sem.semantic_confidence == 0.5


def test_extra_field_rejected():
    bad = _payload() | {"unexpected_field": "nope"}
    with pytest.raises(ValidationError):
        MarketSemantics.model_validate(bad)


def test_unknown_market_type_rejected():
    bad = _payload() | {"market_type": "futures"}
    with pytest.raises(ValidationError):
        MarketSemantics.model_validate(bad)


def test_confidence_out_of_range_rejected():
    bad = _payload() | {"semantic_confidence": 1.5}
    with pytest.raises(ValidationError):
        MarketSemantics.model_validate(bad)


def test_unknown_temporal_resolution_rejected():
    bad = _payload() | {"temporal_resolution": "next-tuesday"}
    with pytest.raises(ValidationError):
        MarketSemantics.model_validate(bad)


def test_frozen_immutability():
    sem = MarketSemantics.model_validate(_payload())
    with pytest.raises(ValidationError):
        sem.semantic_confidence = 0.9  # type: ignore[misc]
