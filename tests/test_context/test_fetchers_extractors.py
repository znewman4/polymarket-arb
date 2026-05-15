"""Fixture-only context fetch/extraction helpers."""

from __future__ import annotations

import pytest

from polymarket_arb.context.evidence_store import content_hash, excerpt
from polymarket_arb.context.extractors import compact_rule_payload, rule_matches_text
from polymarket_arb.context.fetchers import validate_public_context_url
from polymarket_arb.storage.base import ContextRuleRow


def test_fetcher_rejects_forbidden_endpoint():
    with pytest.raises(ValueError, match="disallowed"):
        validate_public_context_url("https://clob.polymarket.com/orders")


def test_evidence_helpers_hash_and_truncate_excerpt():
    text = "NBA Finals winner implies conference winner. " * 20
    assert content_hash(text) == content_hash(text)
    assert len(excerpt(text, max_chars=80)) <= 80


def test_fixture_extractor_matches_rule_terms():
    text = "The exact finish implies top n rule covers whether the team finished in the top 4."
    rule = ContextRuleRow(
        context_rule_id="rule_epl",
        context_space_id="premier_league_finish_position",
        context_type="ranking_finish",
        rule_type="exact_finish_implies_top_n",
        rule_json='{"rule_family": "world_context", "source": "fixture"}',
        source_document_ids_json="[]",
        quoted_evidence_json="[]",
        confidence=0.9,
        needs_manual_review=False,
        human_review_status="approved",
        human_review_notes="",
        valid_from_ms=None,
        valid_to_ms=None,
        extraction_model="manual",
        extraction_prompt_version="manual_rules_v1",
        schema_version=1,
        ingested_ts_ms=1,
    )
    assert rule_matches_text(rule, text)
    payload = compact_rule_payload(rule)
    assert payload["rule_family"] == "world_context"
    assert payload["rule_type"] == "exact_finish_implies_top_n"
