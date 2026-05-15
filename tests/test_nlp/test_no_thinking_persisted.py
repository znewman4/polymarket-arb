"""Audit test: scan every parquet file produced under the test ``data_root``
and assert the literal substring ``<think>`` never appears.

This is a paranoia check on the persistence discipline. If a future PR
accidentally writes pre-strip text to disk, this test fails loudly.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pyarrow.parquet as pq

from polymarket_arb.cli.nlp import _failure_to_row, _failures_repo, _semantics_repo
from polymarket_arb.nlp.extractor import ExtractionFailure, extract_market
from polymarket_arb.nlp.mock_client import MockLLMClient
from polymarket_arb.nlp.prompts import get_prompt
from polymarket_arb.settings import NlpSettings, Settings
from polymarket_arb.storage.base import MarketRow


def _market() -> MarketRow:
    return MarketRow(
        id="m1", condition_id="0xc", slug="s", question="Will X happen?",
        description="d", end_date_ms=None, start_date_ms=None,
        closed_at_ms=None, resolved_at_ms=None,
        active=True, closed=False, archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=["a", "b"], volume=None, liquidity=None,
        event_id=None, neg_risk=False,
        text_hash="dead", schema_version=1, ingested_ts_ms=0,
    )


def _settings_with(tmp_data_root):
    s = Settings(orders_allowed=False)
    s.storage.data_root = tmp_data_root
    s.nlp = NlpSettings(provider="mock", llm_model="mock-llm")
    return s


def test_no_thinking_substring_anywhere_in_lake(tmp_data_root):
    """Force the extractor through both success + invalid_json paths and
    assert no parquet file contains ``<think>``."""

    settings = _settings_with(tmp_data_root)
    sem_repo = _semantics_repo(settings)
    fail_repo = _failures_repo(settings)
    market = _market()
    prompt = get_prompt("market_semantics_v1")

    # 1. Success path (mock LLM returns valid JSON; <think> is in the
    #    pre-strip output but should never reach disk).
    payload = {
        "source_market_id": market.id, "source_condition_id": market.condition_id,
        "question": market.question, "canonical_question": market.question,
        "market_type": "binary",
        "subject_entities": [], "event_entities": [],
        "temporal_phrase": None, "temporal_phrase_normalized": None,
        "temporal_resolution": "vague", "exact_deadline_ms": None,
        "date_constraints": {}, "jurisdiction": None,
        "positive_resolution_condition": "y",
        "negative_resolution_condition": "n",
        "necessary_conditions_for_yes": [], "sufficient_conditions_for_yes": [],
        "necessary_conditions_for_no": [], "sufficient_conditions_for_no": [],
        "evidence_required": [],
        "ambiguity_flags": [], "semantic_confidence": 0.5,
        "needs_manual_review": False, "explanation_summary": None,
        "flag_rationales": {}, "uncertainty_notes": [],
        "rule_curation_notes": [],
    }
    text_with_thinking = (
        "<think>I am secretly reasoning about the market...</think>"
        + json.dumps(payload)
    )
    llm = MockLLMClient(responder=lambda s, u, v: text_with_thinking)
    res = asyncio.run(extract_market(market=market, llm=llm, prompt=prompt,
                                     model_name_hint="mock-llm"))
    assert hasattr(res, "row"), "extraction should succeed"
    sem_repo.upsert(res.row)  # type: ignore[union-attr]

    # 2. Failure path (invalid JSON with <think> still in pre-strip).
    text_invalid = "<think>more reasoning</think>not json"
    llm2 = MockLLMClient(responder=lambda s, u, v: text_invalid)
    res2 = asyncio.run(extract_market(market=market, llm=llm2, prompt=prompt,
                                      model_name_hint="mock-llm"))
    assert isinstance(res2, ExtractionFailure)
    fail_repo.append(_failure_to_row(res2))

    # 3. Audit every parquet file under the lake.
    for parquet in (tmp_data_root / "normalised").rglob("*.parquet"):
        # Read in chunks; check both column-by-column AND the raw bytes.
        table = pq.read_table(parquet)
        for col_name in table.column_names:
            col = table[col_name]
            # Materialise to Python; assertion is over string columns only.
            for v in col.to_pylist():
                if isinstance(v, str) and "<think>" in v.lower():
                    raise AssertionError(
                        f"<think> substring found in {parquet}::{col_name} → {v!r}"
                    )
        # Belt + braces: scan the file bytes too.
        with parquet.open("rb") as fh:
            blob = fh.read()
        # Note: parquet compresses string columns, so this won't catch every
        # case — but if zstd happens to leave a recognisable substring, we
        # catch it cheaply.
        assert b"<think>" not in blob.lower(), f"<think> bytes found in {parquet}"
