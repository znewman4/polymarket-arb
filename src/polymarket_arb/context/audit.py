"""Context audit helpers."""

from __future__ import annotations

import json
from collections import Counter
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from ..storage.parquet.context_rules_repo import ParquetContextRulesRepository


def context_rule_summary(data_root: Path) -> dict[str, Any]:
    rules = list(ParquetContextRulesRepository(data_root).iter_latest())
    return {
        "total_rules": len(rules),
        "by_context_space": dict(Counter(r.context_space_id for r in rules)),
        "by_rule_type": dict(Counter(r.rule_type for r in rules)),
        "by_review_status": dict(Counter(r.human_review_status for r in rules)),
        "needs_review": sum(1 for r in rules if r.needs_manual_review),
    }


def context_decision_summary(data_root: Path) -> dict[str, Any]:
    decisions = list(ParquetContextRelationshipDecisionsRepository(data_root).iter_latest())
    return {
        "total_decisions": len(decisions),
        "by_context_space": dict(Counter(d.context_space_id for d in decisions)),
        "by_strategy_lane": dict(Counter(d.strategy_lane for d in decisions)),
        "by_new_validation_status": dict(Counter(d.new_validation_status for d in decisions)),
        "by_new_strategy_eligibility": dict(
            Counter(d.new_strategy_eligibility for d in decisions)
        ),
    }


def rows_as_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        data = asdict(row)
        for key, value in list(data.items()):
            if isinstance(value, str) and key.endswith("_json"):
                with suppress(json.JSONDecodeError):
                    data[key] = json.dumps(json.loads(value), sort_keys=True)
        out.append(data)
    return out
