"""CLI: ``polymarket-arb live`` — paper / live agent commands.

Subcommands:
    live healthcheck — print agent readiness (kill switch, paper_mode, etc).
    live agent       — run the agent loop, polling the lake for orderbooks
                       and routing strategy intents through OrderClient.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import click

from ..live.agent_loop import AgentState, StrategyFn, run_agent_loop
from ..live.order_client import healthcheck as agent_healthcheck
from ..risk.models import OrderIntent
from ..settings import Settings
from ..storage.base import (
    OrderbookSnapshot,
    RelationshipCandidateRow,
    StrategyCandidateRow,
)
from ..storage.parquet.orderbook_repo import ParquetOrderbookRepository
from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from ..strategies.nesting_contradiction import AlignedPricePoint, evaluate_relationship_at_tick

_ELIGIBLE_RELATIONSHIP_STATUSES = frozenset({"accepted", "needs_manual_review"})
_RELATIONSHIP_ORDER_SIZE = Decimal("50")


@dataclass(frozen=True)
class _RelationshipStrategyConfig:
    strategy_id: str
    min_gross_edge: float
    min_net_edge: float
    fee_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("50")


def _noop_strategy(_state: AgentState) -> list[OrderIntent]:
    """Built-in strategy: fire no intents.  Exists so the loop and kill switch
    can be exercised end-to-end before any real strategy is plugged in."""
    return []


_STRATEGIES: dict[str, StrategyFn | _RelationshipStrategyConfig] = {
    "noop": _noop_strategy,
    "relationship_diagnostic": _RelationshipStrategyConfig(
        strategy_id="relationship_diagnostic",
        min_gross_edge=0.05,
        min_net_edge=0.02,
    ),
    "relationship_aggressive": _RelationshipStrategyConfig(
        strategy_id="relationship_aggressive",
        min_gross_edge=0.03,
        min_net_edge=0.01,
    ),
}


@click.group(name="live")
def live_cmd() -> None:
    """Paper / live agent commands.  Paper mode is the safe default."""


@live_cmd.command(name="healthcheck")
@click.pass_context
def live_healthcheck(ctx: click.Context) -> None:
    """Print agent readiness flags.  Does not place any order."""
    settings: Settings = ctx.obj["settings"]
    info = agent_healthcheck(settings)
    click.echo(json.dumps(info, indent=2, sort_keys=True))


@live_cmd.command(name="agent")
@click.option(
    "--watched-tokens", "watched_tokens_str", default="", show_default=False,
    help="Comma-separated token_id list to poll the lake for.",
)
@click.option(
    "--strategy-auto-tokens", is_flag=True,
    help="Add all token IDs from eligible relationship candidates to watched tokens.",
)
@click.option(
    "--max-iterations", type=int, default=None,
    help="Stop after N iterations (default: run until kill switch).",
)
@click.option(
    "--poll-interval-s", type=int, default=None,
    help="Override settings.agent_poll_interval_s for this run.",
)
@click.option(
    "--strategy",
    type=click.Choice(list(_STRATEGIES)),
    default="noop",
    show_default=True,
    help="Built-in strategy to evaluate each tick.",
)
@click.pass_context
def live_agent(
    ctx: click.Context,
    watched_tokens_str: str,
    strategy_auto_tokens: bool,
    max_iterations: int | None,
    poll_interval_s: int | None,
    strategy: str,
) -> None:
    """Run the paper/live agent loop.

    The agent polls the lake for the latest orderbook of each watched token,
    evaluates the chosen strategy, and routes any resulting OrderIntents
    through OrderClient (which honours paper_mode + kill switch + compliance
    gate).  Every intent — including rejections — is written to ``orders_log``.

    Paper mode is the default.  Flipping to live trading requires BOTH
    ``settings.paper_mode=False`` AND ``settings.orders_allowed=True`` AND a
    configured Polymarket signing key.  The signing path is a stub today.
    """
    settings: Settings = ctx.obj["settings"]
    if max_iterations is not None:
        settings = settings.model_copy(update={"agent_max_iterations": max_iterations})
    if poll_interval_s is not None:
        settings = settings.model_copy(update={"agent_poll_interval_s": poll_interval_s})

    # Load relationships once at startup to resolve initial watched tokens and
    # to fail fast if the lake is empty.
    relationships = (
        _load_live_relationships(settings.data_root)
        if strategy_auto_tokens or _is_relationship_strategy(strategy)
        else []
    )
    tokens = _resolve_watched_tokens(
        watched_tokens_str,
        relationships,
        strategy_auto_tokens=strategy_auto_tokens,
    )
    strategy_fn = _build_strategy(
        strategy,
        data_root=settings.data_root,
        run_id=_new_live_run_id(strategy),
    )
    # For relationship strategies with auto-tokens, refresh watched token list
    # each tick so newly generated relationship candidates are picked up.
    watched_tokens_fn = (
        _make_watched_tokens_fn(settings.data_root, _parse_watched_tokens(watched_tokens_str))
        if (strategy_auto_tokens and _is_relationship_strategy(strategy))
        else None
    )

    stats = run_agent_loop(
        settings,
        watched_tokens=tokens,
        strategy=strategy_fn,
        watched_tokens_fn=watched_tokens_fn,
    )
    click.echo(
        json.dumps(
            {
                "iterations": stats.iterations,
                "intents_emitted": stats.intents_emitted,
                "orders_placed": stats.orders_placed,
                "orders_rejected": stats.orders_rejected,
                "halted_by_kill_switch": stats.halted_by_kill_switch,
                "rejected_by_status": stats.rejected_by_status,
                "paper_mode": settings.paper_mode,
                "orders_allowed": settings.orders_allowed,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _new_live_run_id(strategy_id: str) -> str:
    return f"live_{strategy_id}_{int(time.time() * 1000)}"


def _is_relationship_strategy(strategy_id: str) -> bool:
    return isinstance(_STRATEGIES[strategy_id], _RelationshipStrategyConfig)


def _load_live_relationships(data_root: Path) -> list[RelationshipCandidateRow]:
    repo = ParquetRelationshipCandidatesRepository(data_root)
    candidates = [
        rel for rel in repo.iter_latest()
        if rel.validation_status in _ELIGIBLE_RELATIONSHIP_STATUSES
    ]
    if not candidates:
        return []
    all_tokens = list({
        t for rel in candidates
        for t in (rel.token_id_a_yes, rel.token_id_b_yes)
        if t
    })
    book_repo = ParquetOrderbookRepository(data_root)
    books = book_repo.latest_books_bulk(all_tokens)
    tokens_with_books = {t for t, b in books.items() if b is not None}
    return [
        rel for rel in candidates
        if rel.token_id_a_yes in tokens_with_books
        and rel.token_id_b_yes in tokens_with_books
    ]


def _resolve_watched_tokens(
    watched_tokens_str: str,
    relationships: Iterable[RelationshipCandidateRow],
    *,
    strategy_auto_tokens: bool,
) -> list[str]:
    manual_tokens = _parse_watched_tokens(watched_tokens_str)
    auto_tokens = _relationship_token_ids(relationships) if strategy_auto_tokens else []
    tokens = _dedupe([*manual_tokens, *auto_tokens])
    if tokens:
        return tokens
    if strategy_auto_tokens:
        raise click.UsageError(
            "--strategy-auto-tokens did not find any eligible relationship token IDs; "
            "provide --watched-tokens or populate relationship_candidates."
        )
    raise click.UsageError("--watched-tokens is required unless --strategy-auto-tokens is set.")


def _parse_watched_tokens(watched_tokens_str: str) -> list[str]:
    return [t.strip() for t in watched_tokens_str.split(",") if t.strip()]


def _relationship_token_ids(relationships: Iterable[RelationshipCandidateRow]) -> list[str]:
    tokens: list[str] = []
    for rel in relationships:
        for token_id in (
            rel.token_id_a_yes,
            rel.token_id_a_no,
            rel.token_id_b_yes,
            rel.token_id_b_no,
        ):
            if token_id:
                tokens.append(token_id)
    return _dedupe(tokens)


def _dedupe(tokens: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(tokens))


def _build_strategy(
    strategy_id: str,
    *,
    data_root: Path,
    run_id: str,
) -> StrategyFn:
    entry = _STRATEGIES[strategy_id]
    if not isinstance(entry, _RelationshipStrategyConfig):
        return entry
    return _make_relationship_strategy(entry, data_root=data_root, run_id=run_id)


def _make_watched_tokens_fn(
    data_root: Path,
    manual_tokens: list[str],
) -> Callable[[], list[str]]:
    _cache: list[str] = []
    _tick: list[int] = [0]  # mutable counter in closure

    def fn() -> list[str]:
        if _tick[0] % 60 == 0:
            rels = _load_live_relationships(data_root)
            _cache.clear()
            _cache.extend(_dedupe([*manual_tokens, *_relationship_token_ids(rels)]))
        _tick[0] += 1
        return list(_cache)
    return fn


_RELATIONSHIP_RELOAD_INTERVAL = 60


def _make_relationship_strategy(
    config: _RelationshipStrategyConfig,
    *,
    data_root: Path,
    run_id: str,
) -> StrategyFn:
    cached_relationships: list[RelationshipCandidateRow] = []
    tick_count = 0

    def strategy(state: AgentState) -> list[OrderIntent]:
        nonlocal cached_relationships, tick_count
        if tick_count % _RELATIONSHIP_RELOAD_INTERVAL == 0:
            cached_relationships = _load_live_relationships(data_root)
        tick_count += 1
        intents: list[OrderIntent] = []
        for rel in cached_relationships:
            point = _aligned_point_from_state(rel, state)
            if point is None:
                continue
            candidate = evaluate_relationship_at_tick(
                rel=rel,
                point=point,
                run_id=run_id,
                min_gross_edge=config.min_gross_edge,
                fee_bps=config.fee_bps,
                slippage_bps=config.slippage_bps,
                min_net_edge=config.min_net_edge,
            )
            if candidate is None or not candidate.accepted_for_simulation:
                continue
            leg_intents = _order_intents_from_candidate(config.strategy_id, rel, point, candidate)
            if leg_intents is not None:
                intents.extend(leg_intents)
        return intents

    return strategy


def _aligned_point_from_state(
    rel: RelationshipCandidateRow,
    state: AgentState,
) -> AlignedPricePoint | None:
    if not rel.token_id_a_yes or not rel.token_id_b_yes:
        return None
    book_a = state.latest_book_by_token.get(rel.token_id_a_yes)
    book_b = state.latest_book_by_token.get(rel.token_id_b_yes)
    if book_a is None or book_b is None:
        return None
    mid_a = _book_midpoint(book_a)
    mid_b = _book_midpoint(book_b)
    if mid_a is None or mid_b is None:
        return None
    return AlignedPricePoint(
        ts_ms=state.ts_ms,
        price_a=mid_a,
        price_b=mid_b,
        price_a_ts_ms=book_a.timestamp_ms,
        price_b_ts_ms=book_b.timestamp_ms,
        staleness_a_ms=max(0, state.ts_ms - book_a.timestamp_ms),
        staleness_b_ms=max(0, state.ts_ms - book_b.timestamp_ms),
        alignment_quality="fresh",
    )


def _book_midpoint(book: OrderbookSnapshot) -> Decimal | None:
    best_bid = max((level.price for level in book.bids), default=None)
    best_ask = min((level.price for level in book.asks), default=None)
    if best_bid is None or best_ask is None:
        return None
    return (best_bid + best_ask) / Decimal("2")


def _order_intents_from_candidate(
    strategy_id: str,
    rel: RelationshipCandidateRow,
    point: AlignedPricePoint,
    candidate: StrategyCandidateRow,
) -> list[OrderIntent] | None:
    price_a = _price_for_token(candidate.token_id_a, rel, point)
    price_b = _price_for_token(candidate.token_id_b, rel, point)
    if price_a is None or price_b is None:
        return None
    return [
        OrderIntent(
            id=f"{candidate.candidate_id}:a",
            strategy_id=strategy_id,
            token_id=candidate.token_id_a,
            side="buy",
            price=price_a,
            size=_RELATIONSHIP_ORDER_SIZE,
            limit_price=None,
            detail={"gross_edge": str(candidate.gross_edge)},
        ),
        OrderIntent(
            id=f"{candidate.candidate_id}:b",
            strategy_id=strategy_id,
            token_id=candidate.token_id_b,
            side="buy",
            price=price_b,
            size=_RELATIONSHIP_ORDER_SIZE,
            limit_price=None,
            detail={"gross_edge": str(candidate.gross_edge)},
        ),
    ]


def _price_for_token(
    token_id: str,
    rel: RelationshipCandidateRow,
    point: AlignedPricePoint,
) -> Decimal | None:
    if token_id == rel.token_id_a_yes:
        return point.price_a
    if token_id == rel.token_id_a_no:
        return Decimal("1") - point.price_a
    if token_id == rel.token_id_b_yes:
        return point.price_b
    if token_id == rel.token_id_b_no:
        return Decimal("1") - point.price_b
    return None
