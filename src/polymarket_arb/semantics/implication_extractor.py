"""Extract deterministic implication candidates from scored semantics rows."""

from __future__ import annotations

import hashlib
import json
import time

from ..storage.base import MarketImplicationRow, MarketSemanticsRow
from .implication_scorer import score_implication
from .rulebook_models import ImplicationRulebook


def extract_implications_from_semantics(
    row: MarketSemanticsRow,
    rulebook: ImplicationRulebook,
) -> list[MarketImplicationRow]:
    ambiguity_score = row.ambiguity_score if row.ambiguity_score is not None else 1.0
    out: list[MarketImplicationRow] = []
    for text in row.necessary_conditions_for_yes:
        out.append(_make_row(row, rulebook, "necessary_for_yes", text, ambiguity_score))
    for text in row.sufficient_conditions_for_yes:
        out.append(_make_row(row, rulebook, "sufficient_for_yes", text, ambiguity_score))
    for text in row.necessary_conditions_for_no:
        out.append(_make_row(row, rulebook, "necessary_for_no", text, ambiguity_score))
    for text in row.sufficient_conditions_for_no:
        out.append(_make_row(row, rulebook, "sufficient_for_no", text, ambiguity_score))
    for text in row.evidence_required:
        out.append(_make_row(row, rulebook, "evidence_updates_yes", text, ambiguity_score))
    for flag in row.ambiguity_flags:
        out.append(_make_row(row, rulebook, "ambiguity_warning", flag, ambiguity_score))
    return out


def _make_row(
    row: MarketSemanticsRow,
    rulebook: ImplicationRulebook,
    implication_type: str,
    statement: str,
    ambiguity_score: float,
) -> MarketImplicationRow:
    model_confidence = row.semantic_confidence
    scored = score_implication(
        implication_type=implication_type,
        model_confidence=model_confidence,
        ambiguity_score=ambiguity_score,
        rulebook=rulebook,
    )
    source = "market_semantics"
    implication_id = hashlib.sha256(
        "|".join([row.source_market_id, row.extraction_id, implication_type, statement]).encode()
    ).hexdigest()
    return MarketImplicationRow(
        implication_id=implication_id,
        market_id=row.source_market_id,
        extraction_id=row.extraction_id,
        implication_type=implication_type,
        statement=statement,
        extracted_label=_label_for(implication_type),
        deterministic_score=scored.deterministic_score,
        model_confidence=model_confidence,
        final_confidence=scored.final_confidence,
        ambiguity_flags=list(row.ambiguity_flags),
        needs_manual_review=row.needs_manual_review or scored.needs_manual_review,
        source=source,
        model_name=row.model_name,
        prompt_version=row.prompt_version,
        rulebook_id=rulebook.rulebook_id,
        rulebook_version=rulebook.rulebook_version,
        schema_version=1,
        ingested_ts_ms=int(time.time() * 1000),
    )


def _label_for(implication_type: str) -> str:
    return json.dumps({"implication_type": implication_type}, sort_keys=True)
