"""Extractor: success path + every failure path.

Crucially, every failure path persists ``raw_response_hash`` only — no raw
text. That's enforced by the ``ExtractionFailure`` dataclass shape (no text
field) and re-asserted here for paranoia.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from polymarket_arb.nlp.extractor import (
    ExtractionFailure,
    ExtractionResult,
    extract_market,
)
from polymarket_arb.nlp.mock_client import MockLLMClient
from polymarket_arb.nlp.prompts import get_prompt
from polymarket_arb.storage.base import MarketRow


def _market(question: str = "Will X happen by 2028?",
            description: str = "stub", end_date_ms: int | None = None) -> MarketRow:
    return MarketRow(
        id="mtest", condition_id="0xc", slug="s", question=question,
        description=description, end_date_ms=end_date_ms, start_date_ms=None,
        closed_at_ms=None, resolved_at_ms=None,
        active=True, closed=False, archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=["a", "b"], volume=None, liquidity=None,
        event_id=None, neg_risk=False,
        text_hash="dead", schema_version=1, ingested_ts_ms=0,
    )


def _valid_payload(question: str) -> dict:
    return {
        "source_market_id": "mtest",
        "source_condition_id": "0xc",
        "question": question,
        "canonical_question": question,
        "market_type": "binary",
        "subject_entities": [],
        "event_entities": [],
        "temporal_phrase": None,
        "temporal_phrase_normalized": None,
        "temporal_resolution": "vague",
        "exact_deadline_ms": None,
        "date_constraints": {},
        "jurisdiction": None,
        "positive_resolution_condition": "Yes if X happens",
        "negative_resolution_condition": "No otherwise",
        "necessary_conditions_for_yes": [],
        "sufficient_conditions_for_yes": [],
        "necessary_conditions_for_no": [],
        "sufficient_conditions_for_no": [],
        "evidence_required": [],
        "ambiguity_flags": [],
        "semantic_confidence": 0.7,
        "needs_manual_review": False,
        "explanation_summary": "ok",
        "flag_rationales": {},
        "uncertainty_notes": [],
        "rule_curation_notes": [],
    }


@pytest.mark.asyncio
async def test_success_path_produces_row():
    market = _market()
    llm = MockLLMClient(
        responder=lambda s, u, v: json.dumps(_valid_payload(market.question)),
    )
    res = await extract_market(market=market, llm=llm,
                               prompt=get_prompt("market_semantics_v1"),
                               model_name_hint="mock-llm")
    assert isinstance(res, ExtractionResult)
    row = res.row
    assert row.source_market_id == "mtest"
    assert row.prompt_version == "market_semantics_v1"
    assert row.model_name == "mock-llm"
    assert row.extraction_id and len(row.extraction_id) == 64
    assert row.raw_response_hash and len(row.raw_response_hash) == 64
    # Schema/version stamps present
    assert row.schema_version == 2  # bumped in Phase 5.5 D+
    # Temporal safety net normalised the vague question.
    assert row.temporal_resolution == "vague"
    assert row.exact_deadline_ms is None


@pytest.mark.asyncio
async def test_invalid_json_returns_failure_no_raw_text():
    market = _market()
    llm = MockLLMClient(responder=lambda s, u, v: "not-valid-json {")
    res = await extract_market(market=market, llm=llm,
                               prompt=get_prompt("market_semantics_v1"))
    assert isinstance(res, ExtractionFailure)
    assert res.failure_kind == "invalid_json"
    # No raw text field exists on the failure dataclass:
    assert not hasattr(res, "raw_text")
    assert not hasattr(res, "response_text")
    # validation_error_json captures the parse error structurally:
    err = json.loads(res.validation_error_json)
    assert "error" in err
    assert res.raw_response_hash and len(res.raw_response_hash) == 64


@pytest.mark.asyncio
async def test_schema_violation_returns_failure():
    market = _market()
    bad = _valid_payload(market.question)
    bad["semantic_confidence"] = 1.5  # out of [0,1]
    llm = MockLLMClient(responder=lambda s, u, v: json.dumps(bad))
    res = await extract_market(market=market, llm=llm,
                               prompt=get_prompt("market_semantics_v1"))
    assert isinstance(res, ExtractionFailure)
    assert res.failure_kind == "schema_violation"
    err = json.loads(res.validation_error_json)
    assert isinstance(err, list) and any("semantic_confidence" in str(e) for e in err)


@pytest.mark.asyncio
async def test_temporal_safety_net_overrides_lying_model():
    """Model claims an exact_deadline_ms despite vague phrasing → validator
    forces it back to None and adds vague_deadline flag."""

    market = _market(question="Will it happen by June 2028?")
    payload = _valid_payload(market.question)
    payload["temporal_resolution"] = "exact_date"
    payload["exact_deadline_ms"] = 1_700_000_000_000  # wrong: market is vague
    llm = MockLLMClient(responder=lambda s, u, v: json.dumps(payload))
    res = await extract_market(market=market, llm=llm,
                               prompt=get_prompt("market_semantics_v1"))
    assert isinstance(res, ExtractionResult)
    row = res.row
    assert row.temporal_resolution in ("month", "quarter", "year", "vague")
    assert row.exact_deadline_ms is None
    assert "vague_deadline" in row.ambiguity_flags


@pytest.mark.asyncio
async def test_gamma_end_date_promotes_to_exact_deadline():
    """If Gamma supplies an endDate, the validator uses it — never the LLM."""

    market = _market(question="Will it happen by Q3 2028?",
                     end_date_ms=1_700_000_000_000)
    payload = _valid_payload(market.question)
    llm = MockLLMClient(responder=lambda s, u, v: json.dumps(payload))
    res = await extract_market(market=market, llm=llm,
                               prompt=get_prompt("market_semantics_v1"))
    assert isinstance(res, ExtractionResult)
    assert res.row.exact_deadline_ms == 1_700_000_000_000
