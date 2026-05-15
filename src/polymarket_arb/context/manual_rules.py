"""Manual context-rule import/export workflow."""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..storage.base import ContextDocumentRow, ContextRuleRow, ContextSourceRow
from ..storage.parquet.context_documents_repo import ParquetContextDocumentsRepository
from ..storage.parquet.context_rules_repo import ParquetContextRulesRepository
from ..storage.parquet.context_sources_repo import ParquetContextSourcesRepository
from .evidence_store import content_hash, excerpt

REVIEW_FIELDS = [
    "context_rule_id",
    "context_space_id",
    "rule_type",
    "rule_json",
    "evidence_summary",
    "confidence",
    "needs_manual_review",
    "human_review_status",
    "human_review_notes",
]


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def import_manual_rules(data_root: Path, path: Path | str) -> dict[str, int]:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("manual_rules") or []
    source_repo = ParquetContextSourcesRepository(data_root)
    doc_repo = ParquetContextDocumentsRepository(data_root)
    rule_repo = ParquetContextRulesRepository(data_root)

    sources: list[ContextSourceRow] = []
    docs: list[ContextDocumentRow] = []
    rules: list[ContextRuleRow] = []
    now = _now_ms()
    for item in entries:
        source = dict(item.get("source") or {})
        rule_id = str(item["context_rule_id"])
        space_id = str(item["context_space_id"])
        evidence_text = " ".join(
            str(ev.get("quote", "")) for ev in item.get("quoted_evidence") or []
            if isinstance(ev, dict)
        )
        source_id = f"{rule_id}_source"
        doc_id = f"{rule_id}_document"
        raw_text = evidence_text or str(item.get("rule_json") or {})
        digest = content_hash(raw_text)
        sources.append(ContextSourceRow(
            context_source_id=source_id,
            context_space_id=space_id,
            source_type=str(source.get("source_type") or "manual_context"),
            source_tier=int(source.get("source_tier") or 1),
            title=str(source.get("title") or rule_id),
            url=str(source.get("url") or f"manual://{rule_id}"),
            domain=str(source.get("domain") or "manual"),
            publisher=str(source.get("publisher") or "local_curated"),
            retrieved_at_ms=now,
            effective_start_ms=0,
            effective_end_ms=None,
            raw_path=str(source.get("url") or f"manual://{rule_id}"),
            content_hash=digest,
            status="registered",
            schema_version=1,
            ingested_ts_ms=now,
        ))
        docs.append(ContextDocumentRow(
            context_document_id=doc_id,
            context_source_id=source_id,
            context_space_id=space_id,
            url=str(source.get("url") or f"manual://{rule_id}"),
            title=str(source.get("title") or rule_id),
            retrieved_at_ms=now,
            raw_path=str(source.get("url") or f"manual://{rule_id}"),
            cleaned_text_path=None,
            content_excerpt=excerpt(raw_text),
            content_hash=digest,
            extraction_status="cleaned",
            error_message=None,
            schema_version=1,
            ingested_ts_ms=now,
        ))
        rule_payload = dict(item.get("rule_json") or {})
        rule_payload.setdefault("rule_family", str(item.get("rule_family") or "world_context"))
        rules.append(ContextRuleRow(
            context_rule_id=rule_id,
            context_space_id=space_id,
            context_type=str(item["context_type"]),
            rule_type=str(item["rule_type"]),
            rule_json=json.dumps(rule_payload, sort_keys=True),
            source_document_ids_json=json.dumps([doc_id]),
            quoted_evidence_json=json.dumps(item.get("quoted_evidence") or []),
            confidence=float(item.get("confidence") or 0.0),
            needs_manual_review=bool(item.get("needs_manual_review", True)),
            human_review_status=str(item.get("human_review_status") or "pending"),
            human_review_notes=str(item.get("human_review_notes") or ""),
            valid_from_ms=item.get("valid_from_ms"),
            valid_to_ms=item.get("valid_to_ms"),
            extraction_model="manual",
            extraction_prompt_version="manual_rules_v1",
            schema_version=1,
            ingested_ts_ms=now,
        ))

    source_repo.append_many(sources)
    doc_repo.append_many(docs)
    rule_repo.append_many(rules)
    return {"sources": len(sources), "documents": len(docs), "rules": len(rules)}


def export_review_queue(data_root: Path, output_path: Path | str) -> dict[str, int]:
    output_path = Path(output_path)
    repo = ParquetContextRulesRepository(data_root)
    rows = [
        row for row in repo.iter_latest()
        if row.needs_manual_review and row.human_review_status == "pending"
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "context_rule_id": row.context_rule_id,
                "context_space_id": row.context_space_id,
                "rule_type": row.rule_type,
                "rule_json": row.rule_json,
                "evidence_summary": _evidence_summary(row),
                "confidence": row.confidence,
                "needs_manual_review": row.needs_manual_review,
                "human_review_status": row.human_review_status,
                "human_review_notes": row.human_review_notes,
            })
    return {"exported": len(rows)}


def import_review_queue(data_root: Path, input_path: Path | str) -> dict[str, int]:
    input_path = Path(input_path)
    repo = ParquetContextRulesRepository(data_root)
    updates: list[ContextRuleRow] = []
    now = _now_ms()
    with input_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            status = (row.get("human_review_status") or "").strip()
            if status not in {"approved", "rejected"}:
                continue
            current = repo.get_latest(str(row["context_rule_id"]))
            if current is None:
                continue
            updates.append(replace(
                current,
                human_review_status=status,
                human_review_notes=str(row.get("human_review_notes") or ""),
                ingested_ts_ms=now,
            ))
    repo.append_many(updates)
    return {"imported": len(updates)}


def _evidence_summary(row: ContextRuleRow) -> str:
    try:
        quoted: Any = json.loads(row.quoted_evidence_json or "[]")
    except json.JSONDecodeError:
        return ""
    if not isinstance(quoted, list) or not quoted:
        return ""
    first = quoted[0]
    if isinstance(first, dict):
        return str(first.get("quote") or "")[:240]
    return str(first)[:240]
