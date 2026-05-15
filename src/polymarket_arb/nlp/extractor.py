"""Orchestrate one extraction: LLMClient → JSON → Pydantic → safety net → DTO.

The extractor returns either a ready-to-write ``MarketSemanticsRow`` or
raises ``LLMOutputError``. The caller is responsible for persisting the
``NlpValidationFailureRow`` on error — see ``cli/nlp.py``.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

from pydantic import ValidationError

from ..storage.base import MarketRow, MarketSemanticsRow
from .prompts import PromptTemplate
from .protocols import LLMClient, LLMOutputError
from .responses import LLMResponse
from .schemas import MarketSemantics
from .validators import apply_temporal_safety_net

_SCHEMA_VERSION = 2


def _now_ms() -> int:
    return int(time.time() * 1000)


def _extraction_id(*, market_id: str, prompt_version: str, model: str,
                   raw_response_hash: str) -> str:
    blob = f"{market_id}|{prompt_version}|{model}|{raw_response_hash}".encode()
    return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class ExtractionFailure:
    """Returned by ``extract_market`` on failure. The caller uses this to
    construct an ``NlpValidationFailureRow``. **Never contains raw text.**
    """

    market_id: str
    failure_kind: str        # invalid_json | schema_violation | thinking_filter_failed | http_error
    raw_response_hash: str   # may be empty if we never saw a response
    validation_error_json: str
    prompt_hash: str
    prompt_version: str
    model_name: str
    attempted_ts_ms: int


@dataclass(frozen=True)
class ExtractionResult:
    """Successful path: a writeable row plus the full LLMResponse for
    metrics/logging."""

    row: MarketSemanticsRow
    response: LLMResponse


async def extract_market(
    *,
    market: MarketRow,
    llm: LLMClient,
    prompt: PromptTemplate,
    model_name_hint: str | None = None,
) -> ExtractionResult | ExtractionFailure:
    """Run one extraction. Returns either ``ExtractionResult`` (success)
    or ``ExtractionFailure`` (the caller persists it to
    ``nlp_validation_failures``)."""

    user_text = prompt.render_user(
        market_id=market.id,
        condition_id=market.condition_id,
        question=market.question,
        description=market.description,
    )
    prompt_hash = hashlib.sha256(
        (prompt.system + "\n\n" + user_text).encode("utf-8")
    ).hexdigest()
    attempted_ts_ms = _now_ms()

    # 1. Call the model.
    try:
        resp = await llm.complete_json(
            system=prompt.system, user=user_text, prompt_version=prompt.version,
        )
    except LLMOutputError as exc:
        return ExtractionFailure(
            market_id=market.id, failure_kind="http_error",
            raw_response_hash="", validation_error_json=json.dumps({"error": str(exc)}),
            prompt_hash=prompt_hash, prompt_version=prompt.version,
            model_name=model_name_hint or "unknown",
            attempted_ts_ms=attempted_ts_ms,
        )

    # 2. Parse JSON (post-strip text).
    try:
        parsed = json.loads(resp.text)
    except json.JSONDecodeError as exc:
        return ExtractionFailure(
            market_id=market.id, failure_kind="invalid_json",
            raw_response_hash=resp.raw_response_hash,
            validation_error_json=json.dumps({"error": str(exc)}),
            prompt_hash=prompt_hash, prompt_version=prompt.version,
            model_name=resp.model, attempted_ts_ms=attempted_ts_ms,
        )

    # 3. Pydantic-validate.
    try:
        parsed = _drop_thinking_fields(parsed)
        sem = MarketSemantics.model_validate(parsed)
    except ValidationError as exc:
        return ExtractionFailure(
            market_id=market.id, failure_kind="schema_violation",
            raw_response_hash=resp.raw_response_hash,
            validation_error_json=exc.json(),
            prompt_hash=prompt_hash, prompt_version=prompt.version,
            model_name=resp.model, attempted_ts_ms=attempted_ts_ms,
        )

    # 4. Deterministic temporal safety net (Gamma trumps the LLM).
    sem = apply_temporal_safety_net(sem, gamma_end_date_ms=market.end_date_ms)

    # 5. Build the storage row.
    extraction_id = _extraction_id(
        market_id=market.id, prompt_version=prompt.version,
        model=resp.model, raw_response_hash=resp.raw_response_hash,
    )
    event_atoms_json = json.dumps(
        [a.model_dump() for a in sem.event_atoms], default=str
    ) if sem.event_atoms else None
    proposition_json = json.dumps(
        sem.proposition.model_dump(), default=str
    ) if sem.proposition else None
    outcome_space_json = json.dumps(
        sem.outcome_space.model_dump(), default=str
    ) if sem.outcome_space else None

    row = MarketSemanticsRow(
        source_market_id=sem.source_market_id or market.id,
        source_condition_id=sem.source_condition_id or market.condition_id,
        question=sem.question,
        canonical_question=sem.canonical_question,
        market_type=sem.market_type,
        subject_entities=list(sem.subject_entities),
        event_entities=list(sem.event_entities),
        temporal_phrase=sem.temporal_phrase,
        temporal_phrase_normalized=sem.temporal_phrase_normalized,
        temporal_resolution=sem.temporal_resolution,
        exact_deadline_ms=sem.exact_deadline_ms,
        date_constraints_json=json.dumps(sem.date_constraints, default=str),
        jurisdiction=sem.jurisdiction,
        positive_resolution_condition=sem.positive_resolution_condition,
        negative_resolution_condition=sem.negative_resolution_condition,
        necessary_conditions_for_yes=list(sem.necessary_conditions_for_yes),
        sufficient_conditions_for_yes=list(sem.sufficient_conditions_for_yes),
        necessary_conditions_for_no=list(sem.necessary_conditions_for_no),
        sufficient_conditions_for_no=list(sem.sufficient_conditions_for_no),
        evidence_required=list(sem.evidence_required),
        ambiguity_flags=list(sem.ambiguity_flags),
        ambiguity_score=None,                     # set by Phase 1.6
        semantic_confidence=float(sem.semantic_confidence),
        needs_manual_review=bool(sem.needs_manual_review),
        explanation_summary=sem.explanation_summary,
        flag_rationales_json=json.dumps(sem.flag_rationales) if sem.flag_rationales else None,
        uncertainty_notes_json=json.dumps(sem.uncertainty_notes) if sem.uncertainty_notes else None,
        rule_curation_notes_json=(
            json.dumps(sem.rule_curation_notes) if sem.rule_curation_notes else None
        ),
        raw_response_hash=resp.raw_response_hash,
        model_name=resp.model,
        prompt_version=prompt.version,
        rulebook_id=None,
        rulebook_version=None,
        extraction_id=extraction_id,
        # Phase 5.5 D+ terms-aware fields
        event_atoms_json=event_atoms_json,
        proposition_json=proposition_json,
        outcome_space_json=outcome_space_json,
        tie_rule=sem.tie_rule,
        if_event_never_occurs_rule=sem.if_event_never_occurs_rule,
        resolution_source=sem.resolution_source,
        timezone_or_boundary=sem.timezone_or_boundary,
        terms_confidence=float(sem.terms_confidence),
        long_horizon=bool(sem.long_horizon),
        unresolved_reference_event=bool(sem.unresolved_reference_event),
        schema_version=_SCHEMA_VERSION,
        ingested_ts_ms=_now_ms(),
    )
    return ExtractionResult(row=row, response=resp)


def _drop_thinking_fields(value):
    if isinstance(value, dict):
        return {
            k: _drop_thinking_fields(v)
            for k, v in value.items()
            if k.lower() != "thinking"
        }
    if isinstance(value, list):
        return [_drop_thinking_fields(v) for v in value]
    return value
