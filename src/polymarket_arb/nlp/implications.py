"""Backward-compatible import shim for Phase 2 implication extraction."""

from __future__ import annotations

from ..semantics.implication_extractor import (
    extract_implications_from_semantics as _extract_implications_from_semantics,
)
from ..semantics.rulebook_models import ImplicationRulebook
from ..storage.base import MarketImplicationRow, MarketSemanticsRow

IMPLICATION_RULEBOOK_ID = "implication"
IMPLICATION_RULEBOOK_VERSION = 1

DEFAULT_IMPLICATION_RULEBOOK = ImplicationRulebook(
    rulebook_id=IMPLICATION_RULEBOOK_ID,
    rulebook_version=IMPLICATION_RULEBOOK_VERSION,
    type_weights={
        "necessary_for_yes": 0.8,
        "sufficient_for_yes": 0.9,
        "necessary_for_no": 0.75,
        "sufficient_for_no": 0.85,
        "evidence_updates_yes": 0.55,
        "evidence_updates_no": 0.55,
        "ambiguity_warning": 0.25,
    },
    ambiguity_penalty_weight=0.5,
    review_threshold=0.5,
)


def extract_implications_from_semantics(
    row: MarketSemanticsRow,
    rulebook: ImplicationRulebook = DEFAULT_IMPLICATION_RULEBOOK,
) -> list[MarketImplicationRow]:
    return _extract_implications_from_semantics(row, rulebook)


__all__ = [
    "DEFAULT_IMPLICATION_RULEBOOK",
    "IMPLICATION_RULEBOOK_ID",
    "IMPLICATION_RULEBOOK_VERSION",
    "extract_implications_from_semantics",
]
