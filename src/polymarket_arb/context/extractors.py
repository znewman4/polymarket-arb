"""Fixture/manual extraction helpers for context rules."""

from __future__ import annotations

import json

from ..storage.base import ContextRuleRow


def rule_matches_text(rule: ContextRuleRow, text: str) -> bool:
    """Small fixture helper used by tests before live extraction exists."""
    haystack = text.lower()
    return rule.rule_type.replace("_", " ") in haystack or rule.context_type in haystack


def compact_rule_payload(rule: ContextRuleRow) -> dict:
    try:
        payload = json.loads(rule.rule_json or "{}")
    except json.JSONDecodeError:
        return {}
    return {
        "context_rule_id": rule.context_rule_id,
        "context_space_id": rule.context_space_id,
        "context_type": rule.context_type,
        "rule_type": rule.rule_type,
        "rule_family": payload.get("rule_family", ""),
        "confidence": rule.confidence,
        "human_review_status": rule.human_review_status,
    }
