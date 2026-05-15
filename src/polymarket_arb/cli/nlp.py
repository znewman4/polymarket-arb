"""``polymarket-arb nlp ...`` — local AI semantic extraction.

``extract-market-semantics`` and ``embed-markets`` make HTTP calls to
Ollama (or use the in-process MockClient when ``settings.nlp.provider ==
"mock"``). ``show-semantics`` and ``review-semantics`` read from local
DuckDB views and never touch the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
import uuid
from dataclasses import replace

import click

from ..http.client import AsyncHttpClient
from ..nlp.embeddings import embed_market, text_for_embedding, text_hash
from ..nlp.extractor import ExtractionFailure, ExtractionResult, extract_market
from ..nlp.mock_client import MockEmbeddingClient, MockLLMClient
from ..nlp.ollama_client import OllamaEmbeddingClient, OllamaLLMClient
from ..nlp.prompts import get_prompt
from ..nlp.protocols import EmbeddingClient, LLMClient
from ..semantics.ambiguity_scorer import score_ambiguity
from ..semantics.implication_extractor import extract_implications_from_semantics
from ..semantics.rulebook import load_rulebook, rulebook_content_hash, rulebook_path
from ..semantics.rulebook_models import AmbiguityRulebook, ImplicationRulebook
from ..settings import REPO_ROOT, Settings
from ..storage.base import (
    MarketRow,
    MarketSemanticsRow,
    NlpValidationFailureRow,
    RulebookEvaluationRow,
)
from ..storage.parquet.market_embeddings_repo import (
    ParquetMarketEmbeddingsRepository,
)
from ..storage.parquet.market_implications_repo import (
    ParquetMarketImplicationsRepository,
)
from ..storage.parquet.market_semantics_repo import (
    ParquetMarketSemanticsRepository,
)
from ..storage.parquet.markets_repo import ParquetMarketsRepository
from ..storage.parquet.nlp_validation_failures_repo import (
    ParquetNlpValidationFailuresRepository,
)
from ..storage.parquet.raw_writer import RawWriter
from ..storage.parquet.rulebook_evaluations_repo import (
    ParquetRulebookEvaluationsRepository,
)


@click.group()
def nlp() -> None:
    """Local AI semantics: extract structured market interpretations."""


# ─── helpers ────────────────────────────────────────────────────────────


def _markets_repo(settings: Settings) -> ParquetMarketsRepository:
    return ParquetMarketsRepository(
        data_root=settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )


def _semantics_repo(settings: Settings) -> ParquetMarketSemanticsRepository:
    return ParquetMarketSemanticsRepository(
        data_root=settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )


def _embeddings_repo(settings: Settings) -> ParquetMarketEmbeddingsRepository:
    return ParquetMarketEmbeddingsRepository(
        data_root=settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )


def _implications_repo(settings: Settings) -> ParquetMarketImplicationsRepository:
    return ParquetMarketImplicationsRepository(
        data_root=settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )


def _failures_repo(settings: Settings) -> ParquetNlpValidationFailuresRepository:
    return ParquetNlpValidationFailuresRepository(
        data_root=settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )


def _evaluations_repo(settings: Settings) -> ParquetRulebookEvaluationsRepository:
    return ParquetRulebookEvaluationsRepository(
        data_root=settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


def _failure_to_row(f: ExtractionFailure) -> NlpValidationFailureRow:
    return NlpValidationFailureRow(
        failure_id=uuid.uuid4().hex,
        market_id=f.market_id,
        model_name=f.model_name,
        prompt_version=f.prompt_version,
        prompt_hash=f.prompt_hash,
        raw_response_hash=f.raw_response_hash,
        failure_kind=f.failure_kind,
        validation_error_json=f.validation_error_json,
        attempted_ts_ms=f.attempted_ts_ms,
        schema_version=1,
        ingested_ts_ms=_now_ms(),
    )


# ─── extract-market-semantics ───────────────────────────────────────────


@nlp.command(name="extract-market-semantics")
@click.option("--limit", type=int, default=10,
              help="Max number of markets to process this run.")
@click.option("--rerun-stale", is_flag=True, default=False,
              help="Re-extract markets whose text_hash differs from the latest semantics row.")
@click.option("--provider", type=click.Choice(["mock", "ollama"]), default=None,
              help="Override configured NLP provider for this run.")
@click.pass_context
def extract_market_semantics(
    ctx: click.Context,
    limit: int,
    rerun_stale: bool,
    provider: str | None,
) -> None:
    """Run the configured LLM provider over markets and write structured
    semantics rows. ``MockLLMClient`` is used when ``nlp.provider=mock``."""

    settings: Settings = ctx.obj["settings"]
    if provider is not None:
        settings = settings.model_copy(
            update={"nlp": settings.nlp.model_copy(update={"provider": provider})}
        )
    summary = asyncio.run(_run_extract(settings, limit=limit, rerun_stale=rerun_stale))
    click.echo(
        f"✓ extracted {summary['ok']} / attempted {summary['attempted']}; "
        f"{summary['failed']} validation failures (audited in nlp_validation_failures)"
    )
    if summary["attempted"] == 0:
        click.echo("(no eligible markets — run `gamma fetch-markets` first)")


async def _run_extract(settings: Settings, *, limit: int, rerun_stale: bool) -> dict:
    markets_repo = _markets_repo(settings)
    sem_repo = _semantics_repo(settings)
    fail_repo = _failures_repo(settings)
    prompt = get_prompt(settings.nlp.prompt_version)

    candidates = list(markets_repo.iter_active_markets())[:limit]
    eligible: list[MarketRow] = []
    expected_model = "mock-llm" if settings.nlp.provider == "mock" else settings.nlp.llm_model
    for m in candidates:
        existing = sem_repo.get_latest(m.id)
        if existing is None:
            eligible.append(m)
            continue
        if rerun_stale and existing.prompt_version != prompt.version:
            eligible.append(m)
            continue
        if rerun_stale and existing.model_name != expected_model:
            eligible.append(m)
    candidates = eligible

    ok_rows: list[MarketSemanticsRow] = []
    failures: list[NlpValidationFailureRow] = []
    raw_writer = RawWriter(settings.data_root)
    attempted = 0

    if settings.nlp.provider == "mock":
        # Used in dev/tests without Ollama running.
        mock_llm: LLMClient = MockLLMClient(
            responder=lambda system, user, version: _mock_response_for(user),
            model="mock-llm",
        )
        for m in candidates:
            attempted += 1
            res = await extract_market(market=m, llm=mock_llm, prompt=prompt,
                                       model_name_hint="mock-llm")
            if isinstance(res, ExtractionResult):
                ok_rows.append(res.row)
                _write_nlp_raw_success(raw_writer, market=m, prompt=prompt, result=res)
            else:
                failures.append(_failure_to_row(res))
                _write_nlp_raw_failure(raw_writer, market=m, failure=res)
    else:
        async with AsyncHttpClient(settings.http) as http:
            live_llm: LLMClient = OllamaLLMClient(
                http=http, nlp=settings.nlp,
                debug_dir=settings.nlp_thinking_debug_dir
                          if settings.nlp.debug_capture_thinking else None,
            )
            for m in candidates:
                attempted += 1
                res = await extract_market(
                    market=m, llm=live_llm, prompt=prompt,
                    model_name_hint=settings.nlp.llm_model,
                )
                if isinstance(res, ExtractionResult):
                    ok_rows.append(res.row)
                    _write_nlp_raw_success(raw_writer, market=m, prompt=prompt, result=res)
                else:
                    failures.append(_failure_to_row(res))
                    _write_nlp_raw_failure(raw_writer, market=m, failure=res)

    sem_repo.upsert_many(ok_rows)
    fail_repo.append_many(failures)
    return {"attempted": attempted, "ok": len(ok_rows), "failed": len(failures)}


def _mock_response_for(user_text: str) -> str:
    """Minimal valid MarketSemantics JSON. Keeps the CLI testable without
    running Ollama. The mock pretends every market is binary, vague, and
    self-flags ``vague_deadline``."""

    # Extract market_id and question from the rendered prompt so the
    # mock's JSON echos them — keeps the round-trip realistic.
    market_id = "unknown"
    question = ""
    for line in user_text.splitlines():
        if line.startswith("Market id: "):
            market_id = line.removeprefix("Market id: ").strip()
        elif line.startswith("Question:"):
            # next non-empty line
            idx = user_text.index(line) + len(line)
            tail = user_text[idx:].lstrip("\n")
            question = tail.split("\n", 1)[0].strip()
            break

    payload: dict[str, object] = {
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
        "explanation_summary": "Mocked semantics for development.",
        "flag_rationales": {},
        "uncertainty_notes": [],
        "rule_curation_notes": [],
    }
    return json.dumps(payload)


def _prompt_hash_for(market: MarketRow, prompt) -> tuple[str, str]:
    user_text = prompt.render_user(
        market_id=market.id,
        condition_id=market.condition_id,
        question=market.question,
        description=market.description,
    )
    prompt_hash = hashlib.sha256((prompt.system + "\n\n" + user_text).encode()).hexdigest()
    return prompt_hash, user_text


def _write_nlp_raw_success(
    raw_writer: RawWriter,
    *,
    market: MarketRow,
    prompt,
    result: ExtractionResult,
) -> None:
    prompt_hash, _ = _prompt_hash_for(market, prompt)
    try:
        response_cleaned = json.loads(result.response.text)
    except json.JSONDecodeError:
        response_cleaned = None
    raw_writer.write_json(
        "nlp",
        {
            "market_id": market.id,
            "prompt_hash": prompt_hash,
            "prompt_version": prompt.version,
            "model_name": result.response.model,
            "request_cleaned": {
                "market_id": market.id,
                "condition_id": market.condition_id,
                "question": market.question,
                "description": market.description,
            },
            "response_cleaned": response_cleaned,
            "raw_response_hash": result.response.raw_response_hash,
            "validation_error_json": None,
            "request_ts_ms": result.row.ingested_ts_ms - (result.response.duration_ms or 0),
            "response_ts_ms": result.row.ingested_ts_ms,
        },
    )


def _write_nlp_raw_failure(
    raw_writer: RawWriter,
    *,
    market: MarketRow,
    failure: ExtractionFailure,
) -> None:
    raw_writer.write_json(
        "nlp",
        {
            "market_id": market.id,
            "prompt_hash": failure.prompt_hash,
            "prompt_version": failure.prompt_version,
            "model_name": failure.model_name,
            "request_cleaned": {
                "market_id": market.id,
                "condition_id": market.condition_id,
                "question": market.question,
                "description": market.description,
            },
            "response_cleaned": None,
            "raw_response_hash": failure.raw_response_hash,
            "validation_error_json": failure.validation_error_json,
            "request_ts_ms": failure.attempted_ts_ms,
            "response_ts_ms": _now_ms(),
        },
    )


# ─── show-semantics ─────────────────────────────────────────────────────


@nlp.command(name="show-semantics")
@click.argument("market_id")
@click.pass_context
def show_semantics(ctx: click.Context, market_id: str) -> None:
    """Pretty-print structured semantic annotations for one market."""

    settings: Settings = ctx.obj["settings"]
    row = _semantics_repo(settings).get_latest(market_id)
    if row is None:
        click.echo(f"(no semantics row for {market_id!r})")
        sys.exit(1)
    click.echo(f"market_id            {row.source_market_id}")
    click.echo(f"prompt_version       {row.prompt_version}")
    click.echo(f"model                {row.model_name}")
    click.echo(f"market_type          {row.market_type}")
    click.echo(f"semantic_confidence  {row.semantic_confidence:.2f}    "
               f"needs_manual_review={row.needs_manual_review}")
    click.echo(f"temporal             {row.temporal_resolution}    "
               f"deadline_ms={row.exact_deadline_ms}    "
               f"phrase={row.temporal_phrase!r}")
    click.echo(f"YES if               {row.positive_resolution_condition}")
    click.echo(f"NO if                {row.negative_resolution_condition}")
    if row.necessary_conditions_for_yes:
        click.echo(f"necessary YES        {row.necessary_conditions_for_yes}")
    if row.sufficient_conditions_for_yes:
        click.echo(f"sufficient YES       {row.sufficient_conditions_for_yes}")
    if row.evidence_required:
        click.echo(f"evidence_required    {row.evidence_required}")
    if row.ambiguity_flags:
        click.echo(f"ambiguity_flags      {row.ambiguity_flags}")
    if row.explanation_summary:
        click.echo(f"summary              {row.explanation_summary}")
    click.echo(f"raw_response_hash    {row.raw_response_hash[:16]}...")
    click.echo(f"extraction_id        {row.extraction_id[:16]}...")


# ─── review-semantics ───────────────────────────────────────────────────


@nlp.command(name="review-semantics")
@click.option("--sample", type=int, default=20)
@click.pass_context
def review_semantics(ctx: click.Context, sample: int) -> None:
    """List markets where the model self-flagged ``needs_manual_review``."""

    settings: Settings = ctx.obj["settings"]
    rows = _semantics_repo(settings).needs_review(limit=sample)
    if not rows:
        click.echo("(no markets flagged for manual review)")
        return
    click.echo(f"{len(rows)} flagged for manual review:")
    for r in rows:
        click.echo(f"  {r.source_market_id:<16} | conf={r.semantic_confidence:.2f} "
                   f"| flags={r.ambiguity_flags}")
        click.echo(f"     {r.canonical_question[:90]}")


# ─── embed-markets ──────────────────────────────────────────────────────


@nlp.command(name="embed-markets")
@click.option("--limit", type=int, default=10)
@click.pass_context
def embed_markets_cmd(ctx: click.Context, limit: int) -> None:
    """Compute embedding vectors for markets and write to ``market_embeddings``."""

    settings: Settings = ctx.obj["settings"]
    summary = asyncio.run(_run_embed(settings, limit=limit))
    click.echo(
        f"✓ embedded {summary['written']} markets "
        f"(skipped {summary['cached']} already-known text hashes)"
    )


async def _run_embed(settings: Settings, *, limit: int) -> dict:
    markets_repo = _markets_repo(settings)
    embed_repo = _embeddings_repo(settings)
    space = f"{settings.nlp.embedding_model}@v1"
    known = embed_repo.known_text_hashes(space)

    candidates = list(markets_repo.iter_active_markets())[:limit]
    pending: list[MarketRow] = [
        m for m in candidates if text_hash(text_for_embedding(m)) not in known
    ]
    skipped = len(candidates) - len(pending)
    written = 0

    if not pending:
        return {"written": 0, "cached": skipped}

    if settings.nlp.provider == "mock":
        mock_client: EmbeddingClient = MockEmbeddingClient(dimensions=8, model="mock-embed")
        for m in pending:
            row = await embed_market(market=m, client=mock_client, embedding_space=space)
            embed_repo.upsert(row)
            written += 1
    else:
        async with AsyncHttpClient(settings.http) as http:
            live_client: EmbeddingClient = OllamaEmbeddingClient(http=http, nlp=settings.nlp)
            for m in pending:
                row = await embed_market(market=m, client=live_client, embedding_space=space)
                embed_repo.upsert(row)
                written += 1
    return {"written": written, "cached": skipped}


# ─── score-semantics ───────────────────────────────────────────────────


@nlp.command(name="score-semantics")
@click.option("--limit", type=int, default=100)
@click.option("--all", "include_scored", is_flag=True, default=False,
              help="Re-score rows that already have rulebook metadata.")
@click.pass_context
def score_semantics(ctx: click.Context, limit: int, include_scored: bool) -> None:
    """Apply versioned rulebooks to MarketSemantics rows."""

    settings: Settings = ctx.obj["settings"]
    repo = _semantics_repo(settings)
    eval_repo = _evaluations_repo(settings)
    rulebook_file = settings.nlp.rulebooks.get("ambiguity", "ambiguity_v1.yaml")
    path = rulebook_path(REPO_ROOT / "configs", rulebook_file)
    rulebook = load_rulebook(path, kind="ambiguity")
    if not isinstance(rulebook, AmbiguityRulebook):
        raise click.ClickException("configured ambiguity rulebook has wrong type")
    content_hash = rulebook_content_hash(path)
    rows = repo.iter_latest(limit=limit, unscored_only=not include_scored)
    scored: list[MarketSemanticsRow] = []
    evaluations: list[RulebookEvaluationRow] = []
    now = _now_ms()
    for row in rows:
        score = score_ambiguity(row, rulebook)
        scored.append(
            replace(
                row,
                ambiguity_flags=score.flags,
                ambiguity_score=score.score,
                needs_manual_review=score.needs_manual_review,
                rulebook_id=rulebook.rulebook_id,
                rulebook_version=rulebook.rulebook_version,
                ingested_ts_ms=max(now, row.ingested_ts_ms + 1),
            )
        )
        evaluations.append(
            RulebookEvaluationRow(
                evaluation_id=uuid.uuid4().hex,
                extraction_id=row.extraction_id,
                market_id=row.source_market_id,
                rulebook_id=rulebook.rulebook_id,
                rulebook_version=rulebook.rulebook_version,
                rulebook_content_hash=content_hash,
                score=score.score,
                subscores_json=json.dumps(score.subscores, sort_keys=True),
                flags=score.flags,
                evaluated_ts_ms=now,
                schema_version=1,
                ingested_ts_ms=now,
            )
        )
    written = repo.upsert_many(scored)
    eval_repo.append_many(evaluations)
    click.echo(
        f"✓ scored {written} semantics rows "
        f"(rulebook={rulebook.rulebook_id} v{rulebook.rulebook_version})"
    )
    if written == 0:
        click.echo("(no eligible semantics rows — run `nlp extract-market-semantics` first)")


# ─── market implications ────────────────────────────────────────────────


@nlp.command(name="extract-implications")
@click.option("--limit", type=int, default=100)
@click.option("--include-unscored", is_flag=True, default=False,
              help="Also process semantics rows that have not been score-semantics'd.")
@click.pass_context
def extract_implications(
    ctx: click.Context,
    limit: int,
    include_unscored: bool,
) -> None:
    """Extract single-market implications from semantics."""

    settings: Settings = ctx.obj["settings"]
    sem_repo = _semantics_repo(settings)
    impl_repo = _implications_repo(settings)
    rulebook_file = settings.nlp.rulebooks.get("implication", "implication_v1.yaml")
    path = rulebook_path(REPO_ROOT / "configs", rulebook_file)
    rulebook = load_rulebook(path, kind="implication")
    if not isinstance(rulebook, ImplicationRulebook):
        raise click.ClickException("configured implication rulebook has wrong type")
    rows = sem_repo.iter_latest(limit=limit, unscored_only=False)
    if not include_unscored:
        rows = [r for r in rows if r.ambiguity_score is not None]
    implications = [
        implication
        for row in rows
        for implication in extract_implications_from_semantics(row, rulebook)
    ]
    written = impl_repo.append_many(implications)
    click.echo(f"✓ extracted {written} market implications from {len(rows)} semantics rows")
    if written == 0:
        click.echo("(no implications found — check semantics condition lists)")


@nlp.command(name="show-implications")
@click.argument("market_id")
@click.pass_context
def show_implications(ctx: click.Context, market_id: str) -> None:
    """Print extracted implications for one market."""

    settings: Settings = ctx.obj["settings"]
    rows = _implications_repo(settings).for_market(market_id)
    if not rows:
        click.echo(f"(no implications for {market_id!r})")
        sys.exit(1)
    click.echo(f"{len(rows)} implication(s) for {market_id}:")
    for r in rows:
        review = " review" if r.needs_manual_review else ""
        click.echo(f"  {r.implication_type:<26} conf={r.final_confidence:.2f}{review}")
        click.echo(f"     {r.statement}")


@nlp.command(name="review-implications")
@click.option("--sample", type=int, default=20)
@click.pass_context
def review_implications(ctx: click.Context, sample: int) -> None:
    """Sample implications flagged for manual review."""

    settings: Settings = ctx.obj["settings"]
    rows = _implications_repo(settings).needs_review(limit=sample)
    if not rows:
        click.echo("(no implications flagged for manual review)")
        return
    click.echo(f"{len(rows)} implication(s) flagged for manual review:")
    for r in rows:
        click.echo(f"  {r.market_id:<16} | {r.implication_type} | conf={r.final_confidence:.2f}")
        click.echo(f"     {r.statement}")
