"""Orchestrate the full semantic pipeline over the backfill universe.

Steps per market:
1. Extract semantics (if missing or stale text_hash)
2. Score with ambiguity rulebook (if semantics present but unscored)
3. Extract implications (if semantics scored but implications missing)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import replace
from pathlib import Path

from ..nlp.extractor import ExtractionResult, extract_market
from ..nlp.mock_client import MockLLMClient
from ..nlp.prompts import get_prompt
from ..semantics.ambiguity_scorer import score_ambiguity
from ..semantics.implication_extractor import extract_implications_from_semantics
from ..semantics.rulebook import load_rulebook, rulebook_content_hash, rulebook_path
from ..semantics.rulebook_models import AmbiguityRulebook, ImplicationRulebook
from ..settings import REPO_ROOT, Settings
from ..storage.base import NlpValidationFailureRow, RulebookEvaluationRow
from ..storage.parquet.market_implications_repo import ParquetMarketImplicationsRepository
from ..storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from ..storage.parquet.nlp_validation_failures_repo import ParquetNlpValidationFailuresRepository
from ..storage.parquet.rulebook_evaluations_repo import ParquetRulebookEvaluationsRepository
from .market_backfill import select_universe
from .models import BackfillConfig, SemanticPipelineResult

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


async def run_semantic_pipeline(
    settings: Settings,
    cfg: BackfillConfig | None = None,
    *,
    only_missing: bool = True,
    allow_rerun_stale: bool = False,
    force: bool = False,
    target_market_ids: set[str] | None = None,
) -> SemanticPipelineResult:
    """Drive extract → score → implications over the backfill universe.

    Uses ``settings.nlp.provider`` for LLM selection — set to ``"mock"`` in
    tests to avoid requiring Ollama.
    """
    if cfg is None:
        cfg = BackfillConfig()

    result = SemanticPipelineResult()
    data_root: Path = settings.data_root

    sem_repo = ParquetMarketSemanticsRepository(
        data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )
    impl_repo = ParquetMarketImplicationsRepository(
        data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )
    eval_repo = ParquetRulebookEvaluationsRepository(
        data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )
    fail_repo = ParquetNlpValidationFailuresRepository(
        data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )

    markets = select_universe(data_root, cfg)
    if target_market_ids is not None:
        markets = [m for m in markets if m.id in target_market_ids]
    if not markets:
        logger.warning("semantic_pipeline: universe is empty")
        return result
    market_ids = [m.id for m in markets]
    semantics_by_market = sem_repo.latest_for_market_ids(market_ids)
    implication_market_ids = impl_repo.market_ids_with_implications(market_ids)
    pending_semantics = []
    pending_evaluations = []
    pending_failures = []
    pending_implications = []

    prompt = get_prompt(settings.nlp.prompt_version)

    # Load rulebooks
    amb_file = settings.nlp.rulebooks.get("ambiguity", "ambiguity_v1.yaml")
    amb_path = rulebook_path(REPO_ROOT / "configs", amb_file)
    amb_rulebook = load_rulebook(amb_path, kind="ambiguity")
    if not isinstance(amb_rulebook, AmbiguityRulebook):
        raise ValueError("ambiguity rulebook has wrong type")
    amb_content_hash = rulebook_content_hash(amb_path)

    impl_file = settings.nlp.rulebooks.get("implication", "implication_v1.yaml")
    impl_path = rulebook_path(REPO_ROOT / "configs", impl_file)
    impl_rulebook = load_rulebook(impl_path, kind="implication")
    if not isinstance(impl_rulebook, ImplicationRulebook):
        raise ValueError("implication rulebook has wrong type")

    # Set up LLM
    if settings.nlp.provider == "mock":
        llm = MockLLMClient(
            responder=lambda system, user, version: _mock_response(user),
            model="mock-llm",
        )
    else:
        from ..http.client import AsyncHttpClient
        from ..nlp.ollama_client import OllamaLLMClient
        # For non-mock we can't keep the client open across the whole loop easily;
        # use per-market context. Tests always use mock.
        llm = None

    for market in markets:
        result.total_processed += 1
        existing_sem = semantics_by_market.get(market.id)

        # Step 1: semantics extraction
        need_extract = existing_sem is None or force
        if not need_extract and allow_rerun_stale:
            from ..nlp.embeddings import text_for_embedding, text_hash
            current_hash = text_hash(text_for_embedding(market))
            if existing_sem is not None and (
                existing_sem.raw_response_hash != current_hash
                or existing_sem.prompt_version != settings.nlp.prompt_version
                or not existing_sem.event_atoms_json
                or not existing_sem.proposition_json
                or not existing_sem.outcome_space_json
            ):
                need_extract = True

        if need_extract:
            try:
                if llm is None:
                    # Live Ollama path
                    from ..http.client import AsyncHttpClient
                    from ..nlp.ollama_client import OllamaLLMClient
                    async with AsyncHttpClient(settings.http) as http:
                        live_llm = OllamaLLMClient(
                            http=http,
                            nlp=settings.nlp,
                            debug_dir=(
                                settings.nlp_thinking_debug_dir
                                if settings.nlp.debug_capture_thinking
                                else None
                            ),
                        )
                        res = await extract_market(
                            market=market,
                            llm=live_llm,
                            prompt=prompt,
                            model_name_hint=settings.nlp.llm_model,
                        )
                else:
                    res = await extract_market(
                        market=market,
                        llm=llm,
                        prompt=prompt,
                        model_name_hint="mock-llm",
                    )
                if isinstance(res, ExtractionResult):
                    existing_sem = res.row
                    semantics_by_market[market.id] = res.row
                    pending_semantics.append(res.row)
                    result.semantics_extracted += 1
                else:
                    failure_row = NlpValidationFailureRow(
                        failure_id=uuid.uuid4().hex,
                        market_id=res.market_id,
                        model_name=res.model_name,
                        prompt_version=res.prompt_version,
                        prompt_hash=res.prompt_hash,
                        raw_response_hash=res.raw_response_hash,
                        failure_kind=res.failure_kind,
                        validation_error_json=res.validation_error_json,
                        attempted_ts_ms=res.attempted_ts_ms,
                        schema_version=1,
                        ingested_ts_ms=_now_ms(),
                    )
                    pending_failures.append(failure_row)
                    result.semantics_failed += 1
                    result.errors.append(
                        {"extraction_id": res.raw_response_hash, "reason": res.failure_kind}
                    )
                    continue
            except Exception as exc:
                logger.warning("extraction failed for market %s: %s", market.id, exc)
                result.semantics_failed += 1
                result.errors.append({"extraction_id": market.id, "reason": str(exc)})
                continue
        else:
            result.total_skipped += 1

        if existing_sem is None:
            continue

        # Step 2: score with ambiguity rulebook (update row if not yet scored)
        if existing_sem.ambiguity_score is None:
            now = _now_ms()
            ambiguity_score = score_ambiguity(existing_sem, amb_rulebook)
            scored_row = replace(
                existing_sem,
                ambiguity_flags=ambiguity_score.flags,
                ambiguity_score=ambiguity_score.score,
                needs_manual_review=ambiguity_score.needs_manual_review,
                rulebook_id=amb_rulebook.rulebook_id,
                rulebook_version=amb_rulebook.rulebook_version,
                ingested_ts_ms=max(now, existing_sem.ingested_ts_ms + 1),
            )
            eval_row = RulebookEvaluationRow(
                evaluation_id=uuid.uuid4().hex,
                extraction_id=existing_sem.extraction_id,
                market_id=existing_sem.source_market_id,
                rulebook_id=amb_rulebook.rulebook_id,
                rulebook_version=amb_rulebook.rulebook_version,
                rulebook_content_hash=amb_content_hash,
                score=ambiguity_score.score,
                subscores_json=json.dumps(ambiguity_score.subscores, sort_keys=True),
                flags=ambiguity_score.flags,
                evaluated_ts_ms=now,
                schema_version=1,
                ingested_ts_ms=now,
            )
            existing_sem = scored_row
            semantics_by_market[market.id] = scored_row
            pending_semantics.append(scored_row)
            pending_evaluations.append(eval_row)
            result.scores_computed += 1

        # Step 3: implications (only if market has no implications yet)
        if market.id not in implication_market_ids:
            implications = extract_implications_from_semantics(existing_sem, impl_rulebook)
            pending_implications.extend(implications)
            implication_market_ids.add(market.id)
            result.implications_extracted += len(implications)

    sem_repo.upsert_many(pending_semantics)
    eval_repo.append_many(pending_evaluations)
    fail_repo.append_many(pending_failures)
    impl_repo.append_many(pending_implications)
    return result


def _mock_response(user_text: str) -> str:
    market_id = "unknown"
    question = ""
    for line in user_text.splitlines():
        if line.startswith("Market id: "):
            market_id = line.removeprefix("Market id: ").strip()
        elif line.startswith("Question:"):
            idx = user_text.index(line) + len(line)
            tail = user_text[idx:].lstrip("\n")
            question = tail.split("\n", 1)[0].strip()
            break
    return json.dumps({
        "source_market_id": market_id,
        "source_condition_id": None,
        "question": question or "(unknown)",
        "canonical_question": question or "(unknown)",
        "market_type": "binary",
        "subject_entities": [],
        "event_entities": [],
        "temporal_phrase": None,
        "temporal_phrase_normalized": None,
        "temporal_resolution": "vague",
        "exact_deadline_ms": None,
        "date_constraints": {},
        "jurisdiction": None,
        "positive_resolution_condition": "(stub)",
        "negative_resolution_condition": "(stub)",
        "necessary_conditions_for_yes": [],
        "sufficient_conditions_for_yes": [],
        "necessary_conditions_for_no": [],
        "sufficient_conditions_for_no": [],
        "evidence_required": [],
        "ambiguity_flags": [],
        "semantic_confidence": 0.5,
        "needs_manual_review": True,
        "explanation_summary": "Mocked semantics.",
        "flag_rationales": {},
        "uncertainty_notes": [],
        "rule_curation_notes": [],
        "event_atoms": [
            {
                "event_id": "market_event",
                "subject": question or "(unknown)",
                "event_type": "binary_event",
                "definition": "Mocked terms-aware event extracted from the market question.",
                "source_of_truth": None,
                "ambiguity_flags": ["mock_terms"],
            }
        ],
        "proposition": {
            "type": "other",
            "left_event": "market_event",
            "relation": "unknown",
            "right_event": None,
            "strictness": None,
        },
        "outcome_space": {
            "kind": "other",
            "competition_id": None,
            "candidate": None,
            "winner_predicate": None,
        },
        "tie_rule": None,
        "if_event_never_occurs_rule": None,
        "resolution_source": None,
        "timezone_or_boundary": None,
        "terms_confidence": 0.5,
        "long_horizon": False,
        "unresolved_reference_event": False,
    })
