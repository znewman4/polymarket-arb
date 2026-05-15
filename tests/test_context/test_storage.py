"""Storage coverage for evidence-backed context rows."""

from __future__ import annotations

import json

from polymarket_arb.storage.base import (
    ContextDocumentRow,
    ContextRelationshipDecisionRow,
    ContextRuleRow,
    ContextSourceRow,
)
from polymarket_arb.storage.parquet.context_documents_repo import (
    ParquetContextDocumentsRepository,
)
from polymarket_arb.storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from polymarket_arb.storage.parquet.context_rules_repo import ParquetContextRulesRepository
from polymarket_arb.storage.parquet.context_sources_repo import (
    ParquetContextSourcesRepository,
)


def test_context_rows_roundtrip_and_latest(tmp_data_root):
    ts = 1_800_000_000_000
    source = ContextSourceRow(
        context_source_id="src_nba",
        context_space_id="nba_championship_conference_progression",
        source_type="manual",
        source_tier=1,
        title="NBA progression",
        url="manual://nba",
        domain="manual",
        publisher="curated",
        retrieved_at_ms=ts,
        effective_start_ms=ts,
        effective_end_ms=None,
        raw_path="data/context/raw/nba.txt",
        content_hash="abc",
        status="active",
        schema_version=1,
        ingested_ts_ms=ts,
    )
    doc = ContextDocumentRow(
        context_document_id="doc_nba",
        context_source_id=source.context_source_id,
        context_space_id=source.context_space_id,
        url=source.url,
        title=source.title,
        retrieved_at_ms=ts,
        raw_path=source.raw_path,
        cleaned_text_path=None,
        content_excerpt="NBA Finals winner comes through a conference.",
        content_hash="abc",
        extraction_status="manual_curated",
        error_message=None,
        schema_version=1,
        ingested_ts_ms=ts,
    )
    rule = ContextRuleRow(
        context_rule_id="rule_nba",
        context_space_id=source.context_space_id,
        context_type="sports_progression",
        rule_type="championship_implies_conference",
        rule_json=json.dumps({"rule_family": "world_context"}),
        source_document_ids_json=json.dumps([doc.context_document_id]),
        quoted_evidence_json="[]",
        confidence=0.95,
        needs_manual_review=False,
        human_review_status="approved",
        human_review_notes="fixture",
        valid_from_ms=None,
        valid_to_ms=None,
        extraction_model="manual",
        extraction_prompt_version="manual_rules_v1",
        schema_version=1,
        ingested_ts_ms=ts,
    )
    decision_old = ContextRelationshipDecisionRow(
        decision_id="dec_old",
        relationship_id="rel_1",
        context_space_id=source.context_space_id,
        context_rule_ids_json=json.dumps([rule.context_rule_id]),
        previous_validation_status="needs_manual_review",
        new_validation_status="accepted",
        previous_strategy_eligibility="manual_review",
        new_strategy_eligibility="eligible",
        strategy_lane="strict_context_valid",
        decision_reason="old",
        evidence_summary="old",
        schema_version=1,
        ingested_ts_ms=ts,
    )
    decision_new = ContextRelationshipDecisionRow(
        **{**decision_old.__dict__, "decision_id": "dec_new", "decision_reason": "new", "ingested_ts_ms": ts + 1}
    )

    ParquetContextSourcesRepository(tmp_data_root).append(source)
    ParquetContextDocumentsRepository(tmp_data_root).append(doc)
    ParquetContextRulesRepository(tmp_data_root).append(rule)
    ParquetContextRelationshipDecisionsRepository(tmp_data_root).append_many([decision_old, decision_new])

    assert ParquetContextSourcesRepository(tmp_data_root).get_latest("src_nba") == source
    assert ParquetContextDocumentsRepository(tmp_data_root).get_latest("doc_nba") == doc
    assert ParquetContextRulesRepository(tmp_data_root).get_latest("rule_nba") == rule

    decisions = list(ParquetContextRelationshipDecisionsRepository(tmp_data_root).iter_latest())
    assert len(decisions) == 1
    assert decisions[0].decision_id == "dec_new"
