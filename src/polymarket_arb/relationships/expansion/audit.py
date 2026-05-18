"""Shared audit helpers for deterministic expansion passes."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

GENERATED_BY = "deterministic_expansion"


def make_relationship_id() -> str:
    return uuid.uuid4().hex


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def build_evidence_json(
    pass_id: str,
    guard_results: dict[str, Any],
    parse_evidence: dict[str, Any],
    notes: str = "",
) -> str:
    payload = {
        "generated_by": GENERATED_BY,
        "expansion_pass_id": pass_id,
        "expansion_version": 1,
        "guard_results": guard_results,
        "parse_evidence": parse_evidence,
    }
    if notes:
        payload["notes"] = notes
    return json.dumps(payload, sort_keys=True)


def build_classification_reason_json(
    pass_id: str,
    subtype_reason: str,
) -> str:
    return json.dumps([
        {"side": "expansion", "reason": f"{pass_id}: {subtype_reason}"}
    ], sort_keys=True)


def extract_year(question: str) -> str | None:
    """Return the 4-digit year in the question, or None if absent."""
    m = re.search(r"\b(20\d{2})\b", question)
    return m.group(1) if m else None


def slug(value: str) -> str:
    """Normalise a string for guard comparisons."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
