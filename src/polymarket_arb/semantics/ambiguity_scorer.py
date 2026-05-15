"""Pure ambiguity scoring from MarketSemantics + YAML rulebook."""

from __future__ import annotations

from dataclasses import dataclass

from ..storage.base import MarketSemanticsRow
from .rulebook_models import AmbiguityRulebook

_NON_EXACT_TEMPORAL = {"month", "quarter", "year", "vague", "open_ended"}


@dataclass(frozen=True)
class AmbiguityScore:
    score: float
    flags: list[str]
    subscores: dict[str, float]
    needs_manual_review: bool


def score_ambiguity(row: MarketSemanticsRow, rulebook: AmbiguityRulebook) -> AmbiguityScore:
    flags = list(dict.fromkeys(row.ambiguity_flags))
    if row.semantic_confidence < 0.55:
        _add_flag(flags, "low_semantic_confidence")
    if row.temporal_resolution in _NON_EXACT_TEMPORAL and row.temporal_resolution != "open_ended":
        _add_flag(flags, "vague_deadline")
    if row.market_type in {"unknown", "scalar", "event_grouped"}:
        _add_flag(flags, "complex_market_type")
    if _looks_missing(row.positive_resolution_condition) or _looks_missing(
        row.negative_resolution_condition
    ):
        _add_flag(flags, "missing_resolution_conditions")

    subscores = {flag: rulebook.flag_severities.get(flag, 0.25) for flag in flags}
    confidence_penalty = max(0.0, min(1.0, 1.0 - row.semantic_confidence))
    if confidence_penalty:
        subscores["semantic_confidence_gap"] = confidence_penalty * 0.5

    if not subscores:
        score = 0.0
    else:
        values = list(subscores.values())
        score = (max(values) + (sum(values) / len(values))) / 2
    score = round(max(0.0, min(1.0, score)), 4)
    return AmbiguityScore(
        score=score,
        flags=flags,
        subscores=subscores,
        needs_manual_review=row.needs_manual_review or score >= rulebook.review_threshold,
    )


def _add_flag(flags: list[str], flag: str) -> None:
    if flag not in flags:
        flags.append(flag)


def _looks_missing(text: str | None) -> bool:
    if text is None:
        return True
    stripped = text.strip().lower()
    return stripped in {"", "(stub)", "stub", "unknown", "n/a", "none"}
