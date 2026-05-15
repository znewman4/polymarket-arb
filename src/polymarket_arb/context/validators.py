"""Deterministic validation for context rules and lanes."""

from __future__ import annotations

import json

from ..storage.base import ContextRuleRow
from .models import RuleValidationResult
from .source_registry import load_context_registry

VALID_REVIEW_STATUSES = {"not_required", "pending", "approved", "rejected"}
VALID_RULE_FAMILIES = {"world_context", "market_terms", "completeness", "invalidating"}


def validate_context_rules(registry_path, rules: list[ContextRuleRow]) -> list[RuleValidationResult]:
    registry = load_context_registry(registry_path)
    results: list[RuleValidationResult] = []
    for rule in rules:
        valid = True
        status = "context_reviewed_valid"
        reason = "valid"
        if rule.context_space_id not in registry.context_spaces:
            valid = False
            status = "context_reviewed_invalid"
            reason = "unknown_context_space"
        elif rule.human_review_status not in VALID_REVIEW_STATUSES:
            valid = False
            status = "context_reviewed_invalid"
            reason = "invalid_review_status"
        else:
            try:
                payload = json.loads(rule.rule_json or "{}")
            except json.JSONDecodeError:
                payload = {}
                valid = False
                status = "context_reviewed_invalid"
                reason = "invalid_rule_json"
            family = str(payload.get("rule_family") or payload.get("family") or "")
            if family and family not in VALID_RULE_FAMILIES:
                valid = False
                status = "context_reviewed_invalid"
                reason = "invalid_rule_family"
        if valid and rule.human_review_status == "pending":
            status = "context_extracted_unreviewed"
            reason = "pending_review"
        if valid and rule.human_review_status == "rejected":
            valid = False
            status = "context_reviewed_invalid"
            reason = "review_rejected"
        results.append(RuleValidationResult(
            context_rule_id=rule.context_rule_id,
            valid=valid,
            status=status,  # type: ignore[arg-type]
            reason=reason,
        ))
    return results


def is_rule_usable(rule: ContextRuleRow, *, allow_unreviewed: bool) -> bool:
    if rule.confidence <= 0:
        return False
    if rule.human_review_status in {"approved", "not_required"}:
        return True
    return allow_unreviewed and rule.human_review_status == "pending"


def is_reviewed(rule: ContextRuleRow) -> bool:
    return rule.human_review_status in {"approved", "not_required"}


def rule_family(rule: ContextRuleRow) -> str:
    try:
        payload = json.loads(rule.rule_json or "{}")
    except json.JSONDecodeError:
        return ""
    return str(payload.get("rule_family") or payload.get("family") or "")
