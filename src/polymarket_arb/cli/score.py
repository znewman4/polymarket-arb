"""``polymarket-arb score ...`` - weighted research-signal scoring."""

from __future__ import annotations

import json
import time

import click

from ..fusion.models import FusionInputs
from ..fusion.scoring import score_market
from ..semantics.rulebook import load_rulebook, rulebook_path
from ..semantics.rulebook_models import EvidenceFusionRulebook
from ..settings import REPO_ROOT, Settings
from ..storage.parquet.best_quotes_repo import ParquetBestQuotesRepository
from ..storage.parquet.market_implications_repo import ParquetMarketImplicationsRepository
from ..storage.parquet.market_scores_repo import ParquetMarketScoresRepository
from ..storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from ..storage.parquet.markets_repo import ParquetMarketsRepository


@click.group()
def score() -> None:
    """Deterministic non-trading research signal scores."""


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


def _quotes_repo(settings: Settings) -> ParquetBestQuotesRepository:
    return ParquetBestQuotesRepository(
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


def _scores_repo(settings: Settings) -> ParquetMarketScoresRepository:
    return ParquetMarketScoresRepository(
        data_root=settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )


@score.command(name="score-markets")
@click.option("--limit", type=int, default=100)
@click.pass_context
def score_markets(ctx: click.Context, limit: int) -> None:
    """Compute research-only fusion scores over active markets."""

    settings: Settings = ctx.obj["settings"]
    written = run_score_markets(settings, limit=limit)
    click.echo(f"✓ scored {written} markets (research-only)")


def run_score_markets(settings: Settings, *, limit: int = 100) -> int:
    """Compute and persist research-only fusion scores.

    Kept as a plain function so the recorder can reuse the exact scoring path
    without routing through Click.
    """

    rulebook_file = settings.nlp.rulebooks.get("evidence_fusion", "evidence_fusion_v1.yaml")
    rb = load_rulebook(rulebook_path(REPO_ROOT / "configs", rulebook_file), kind="evidence_fusion")
    if not isinstance(rb, EvidenceFusionRulebook):
        raise ValueError("configured evidence_fusion rulebook has wrong type")

    markets = list(_markets_repo(settings).iter_active_markets())[:limit]
    sem_repo = _semantics_repo(settings)
    quotes_repo = _quotes_repo(settings)
    impl_repo = _implications_repo(settings)
    rows = []
    for market in markets:
        sem = sem_repo.get_latest(market.id)
        if sem is None:
            continue
        quotes = [q for token in market.clob_token_ids if (q := quotes_repo.latest(token)) is not None]
        if not quotes:
            continue
        midpoint_values = [float(q.midpoint) for q in quotes if q.midpoint is not None]
        spread_values = [float(q.spread) for q in quotes if q.spread is not None]
        size_values = [
            float(x)
            for q in quotes
            for x in (q.best_bid_size, q.best_ask_size)
            if x is not None
        ]
        now_ms = int(time.time() * 1000)
        freshest = max((q.timestamp_ms for q in quotes), default=0)
        implications = impl_repo.for_market(market.id)
        inputs = FusionInputs(
            market_id=market.id,
            market_midpoint=_avg(midpoint_values),
            spread=_avg(spread_values),
            liquidity_score=min(1.0, sum(size_values) / 1000.0) if size_values else 0.0,
            semantic_confidence=sem.semantic_confidence,
            ambiguity_score=sem.ambiguity_score if sem.ambiguity_score is not None else 1.0,
            implication_quality_score=_avg([i.final_confidence for i in implications]) or 0.0,
            resolution_risk_score=1.0 - (sem.ambiguity_score if sem.ambiguity_score is not None else 1.0),
            evidence_quality_score=1.0 if sem.evidence_required else 0.25,
            freshness_score=max(0.0, min(1.0, 1.0 - ((now_ms - freshest) / 30_000))),
        )
        rows.append(score_market(inputs, rb))
    return _scores_repo(settings).append_many(rows)


@score.command(name="show-score")
@click.argument("market_id")
@click.pass_context
def show_score(ctx: click.Context, market_id: str) -> None:
    """Print the latest fusion score + explanation for a market."""

    settings: Settings = ctx.obj["settings"]
    row = _scores_repo(settings).latest(market_id)
    if row is None:
        click.echo(f"(no score for {market_id!r})")
        raise SystemExit(1)
    click.echo(f"market_id       {row.market_id}")
    click.echo(f"score           {row.final_signal_score:.3f}")
    click.echo(f"recommendation  {row.recommendation}")
    click.echo(f"midpoint        {row.market_midpoint}")
    click.echo(f"spread          {row.spread}")
    click.echo(f"rulebook        {row.rulebook_id} v{row.rulebook_version}")
    click.echo(f"explanation     {json.loads(row.explanation_json)}")


def _avg(values: list[float]) -> float | None:
    return None if not values else sum(values) / len(values)
