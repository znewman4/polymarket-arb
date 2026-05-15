"""Compatibility shim for semantic ambiguity scoring.

New code should use ``polymarket_arb.semantics`` directly. This module remains
so older imports route through the YAML-rulebook scorer instead of hardcoded
prompt-adjacent weights.
"""

from __future__ import annotations

import time
from dataclasses import replace

from ..semantics.ambiguity_scorer import score_ambiguity
from ..semantics.rulebook_models import AmbiguityRulebook
from ..storage.base import MarketSemanticsRow

RULEBOOK_ID = "ambiguity"
RULEBOOK_VERSION = 1

DEFAULT_AMBIGUITY_RULEBOOK = AmbiguityRulebook(
    rulebook_id=RULEBOOK_ID,
    rulebook_version=RULEBOOK_VERSION,
    flag_severities={
        "vague_deadline": 0.7,
        "subjective_wording": 0.5,
        "unclear_resolution_source": 0.6,
        "multiple_possible_entities": 0.5,
        "conditional_market": 0.4,
        "missing_resolution_source": 0.7,
        "non_binary_resolution": 0.4,
        "unclear_time_window": 0.6,
        "ambiguous_entity_alias": 0.4,
        "unclear_event_scope": 0.5,
        "low_semantic_confidence": 0.6,
        "missing_resolution_conditions": 0.7,
        "complex_market_type": 0.5,
    },
    combination_rule="max_then_average",
    review_threshold=0.5,
)


def score_semantics_row(
    row: MarketSemanticsRow,
    rulebook: AmbiguityRulebook = DEFAULT_AMBIGUITY_RULEBOOK,
) -> MarketSemanticsRow:
    scored = score_ambiguity(row, rulebook)
    return replace(
        row,
        ambiguity_flags=scored.flags,
        ambiguity_score=scored.score,
        needs_manual_review=scored.needs_manual_review,
        rulebook_id=rulebook.rulebook_id,
        rulebook_version=rulebook.rulebook_version,
        ingested_ts_ms=max(int(time.time() * 1000), row.ingested_ts_ms + 1),
    )
