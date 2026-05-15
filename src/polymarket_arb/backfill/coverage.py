"""Compute BackfillCoverageRow for each market and run the verify suite."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

from ..storage.base import (
    BackfillCoverageRow,
    MarketRow,
    PriceHistoryRow,
    TradeHistoryRow,
)
from ..storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from ..storage.parquet.market_implications_repo import ParquetMarketImplicationsRepository
from ..storage.parquet.market_scores_repo import ParquetMarketScoresRepository
from ..storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from ..storage.parquet.markets_repo import ParquetMarketsRepository
from ..storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from ..storage.parquet.trade_history_repo import ParquetTradeHistoryRepository
from .market_backfill import select_universe
from .models import BackfillConfig
from .validators import (
    ValidationResult,
    validate_coverage_rows,
    validate_markets_table,
    validate_no_thinking,
    validate_price_history,
    validate_semantics_coverage,
    validate_trade_history,
)

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_ONE_WEEK_MS = 7 * 24 * 3600 * 1000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _compute_price_gap_stats(
    rows: list[PriceHistoryRow],
) -> tuple[int, int]:
    """Return (missing_gap_count, largest_gap_ms) for sorted rows."""
    if len(rows) < 2:
        return 0, 0
    sorted_rows = sorted(rows, key=lambda r: r.ts_ms)
    gaps = [
        sorted_rows[i + 1].ts_ms - sorted_rows[i].ts_ms
        for i in range(len(sorted_rows) - 1)
    ]
    largest = max(gaps)
    # A "missing gap" is any gap > 2x the median expected interval
    if not gaps:
        return 0, 0
    median_gap = sorted(gaps)[len(gaps) // 2]
    threshold = max(median_gap * 3, _ONE_WEEK_MS)
    missing_count = sum(1 for g in gaps if g > threshold)
    return missing_count, largest


def compute_market_coverage(
    market: MarketRow,
    price_rows: list[PriceHistoryRow],
    trade_rows: list[TradeHistoryRow],
    sem_present: bool,
    score_present: bool,
    impl_present: bool,
    cfg: BackfillConfig,
) -> BackfillCoverageRow:
    now_ms = _now_ms()
    start_ts_ms = now_ms - cfg.requested_days * 24 * 3600 * 1000
    end_ts_ms = now_ms

    has_gamma = bool(market.id)
    has_price_history = bool(price_rows)
    has_trade_history = bool(trade_rows)
    has_semantics = sem_present
    has_rulebook_score = score_present
    has_implications = impl_present
    has_embeddings = False  # embeddings not tracked in this pipeline phase
    has_backfill_coverage = True  # this row is itself the coverage

    price_points_count = len(price_rows)
    trade_points_count = len(trade_rows)

    prices_sorted = sorted(price_rows, key=lambda r: r.ts_ms) if price_rows else []
    first_price_ts_ms = prices_sorted[0].ts_ms if prices_sorted else None
    last_price_ts_ms = prices_sorted[-1].ts_ms if prices_sorted else None

    missing_price_gap_count, largest_price_gap_ms = _compute_price_gap_stats(price_rows)

    price_values = [r.price for r in price_rows]
    price_min = min(price_values) if price_values else None
    price_max = max(price_values) if price_values else None

    # Out-of-bounds count from the actual rows stored (already filtered by parser)
    price_out_of_bounds_count = sum(
        1 for r in price_rows if r.price < Decimal("0") or r.price > Decimal("1")
    )
    # Duplicate timestamps per token
    seen: dict[tuple, int] = {}
    for r in price_rows:
        key = (r.token_id, r.ts_ms)
        seen[key] = seen.get(key, 0) + 1
    duplicate_timestamp_count = sum(v - 1 for v in seen.values() if v > 1)

    # Coverage score formula (deterministic)
    score = 0.0
    exclusion_reasons: list[str] = []

    if has_gamma:
        score += 0.05
    else:
        exclusion_reasons.append("no_gamma_data")

    if has_price_history:
        price_weight = min(price_points_count / max(cfg.min_price_points, 1), 1.0)
        score += 0.45 * price_weight
    else:
        exclusion_reasons.append("no_price_history")

    if has_semantics:
        score += 0.15
    if has_rulebook_score:
        score += 0.10
    if has_implications:
        score += 0.10

    # Gap penalty
    if largest_price_gap_ms > 0:
        gap_factor = max(0.0, 1.0 - largest_price_gap_ms / _ONE_WEEK_MS)
    else:
        gap_factor = 1.0
    score += 0.10 * gap_factor

    # Quality penalties
    if price_out_of_bounds_count > 0:
        score -= 0.05
        exclusion_reasons.append(f"price_out_of_bounds:{price_out_of_bounds_count}")
    if duplicate_timestamp_count > 0:
        score -= 0.05
        exclusion_reasons.append(f"duplicate_timestamps:{duplicate_timestamp_count}")

    if not market.clob_token_ids:
        exclusion_reasons.append("no_token_ids")

    coverage_score = round(max(0.0, min(1.0, score)), 4)
    recommended_for_backtest = (
        coverage_score >= cfg.min_coverage_score
        and price_points_count >= cfg.min_price_points
        and bool(market.clob_token_ids)
    )

    return BackfillCoverageRow(
        market_id=market.id,
        condition_id=market.condition_id or "",
        question=market.question,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        requested_days=cfg.requested_days,
        has_gamma=has_gamma,
        has_price_history=has_price_history,
        has_trade_history=has_trade_history,
        has_semantics=has_semantics,
        has_rulebook_score=has_rulebook_score,
        has_implications=has_implications,
        has_embeddings=has_embeddings,
        has_backfill_coverage=has_backfill_coverage,
        price_points_count=price_points_count,
        trade_points_count=trade_points_count,
        first_price_ts_ms=first_price_ts_ms,
        last_price_ts_ms=last_price_ts_ms,
        missing_price_gap_count=missing_price_gap_count,
        largest_price_gap_ms=largest_price_gap_ms,
        price_min=price_min,
        price_max=price_max,
        price_out_of_bounds_count=price_out_of_bounds_count,
        duplicate_timestamp_count=duplicate_timestamp_count,
        coverage_score=coverage_score,
        recommended_for_backtest=recommended_for_backtest,
        exclusion_reasons_json=json.dumps(exclusion_reasons),
        schema_version=_SCHEMA_VERSION,
        ingested_ts_ms=_now_ms(),
    )


def compute_all_coverage(data_root: Path, cfg: BackfillConfig) -> Iterator[BackfillCoverageRow]:
    price_repo = ParquetPriceHistoryRepository(data_root)
    trade_repo = ParquetTradeHistoryRepository(data_root)
    sem_repo = ParquetMarketSemanticsRepository(data_root)
    score_repo = ParquetMarketScoresRepository(data_root)
    impl_repo = ParquetMarketImplicationsRepository(data_root)

    markets = select_universe(data_root, cfg)
    for market in markets:
        price_rows = list(price_repo.iter_for_market(market.id))
        trade_rows = list(trade_repo.iter_for_token(
            market.clob_token_ids[0] if market.clob_token_ids else ""
        ))
        sem_present = sem_repo.get_latest(market.id) is not None
        score_present = score_repo.latest(market.id) is not None
        impl_present = len(impl_repo.for_market(market.id)) > 0
        yield compute_market_coverage(
            market, price_rows, trade_rows, sem_present, score_present, impl_present, cfg
        )


def compute_relationship_coverage(data_root: Path, cfg: BackfillConfig) -> Iterator[BackfillCoverageRow]:
    """Compute coverage for every market referenced by relationship candidates."""
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    market_repo = ParquetMarketsRepository(data_root)
    price_repo = ParquetPriceHistoryRepository(data_root)
    trade_repo = ParquetTradeHistoryRepository(data_root)
    sem_repo = ParquetMarketSemanticsRepository(data_root)
    score_repo = ParquetMarketScoresRepository(data_root)
    impl_repo = ParquetMarketImplicationsRepository(data_root)

    market_ids: list[str] = []
    for rel in rel_repo.iter_latest():
        market_ids.append(rel.market_id_a)
        market_ids.append(rel.market_id_b)

    markets = market_repo.get_many_markets(market_ids)
    unique_market_ids = list(dict.fromkeys(mid for mid in market_ids if mid in markets))
    price_by_market: dict[str, list[PriceHistoryRow]] = {}
    for row in price_repo.iter_for_markets(unique_market_ids):
        price_by_market.setdefault(row.market_id, []).append(row)
    first_tokens = [
        market.clob_token_ids[0]
        for market in markets.values()
        if market.clob_token_ids
    ]
    trades_by_token: dict[str, list[TradeHistoryRow]] = {}
    for trade_row in trade_repo.iter_for_tokens(first_tokens):
        trades_by_token.setdefault(trade_row.token_id, []).append(trade_row)
    sem_by_market = sem_repo.latest_for_market_ids(unique_market_ids)
    score_by_market = score_repo.latest_for_market_ids(unique_market_ids)
    impl_market_ids = impl_repo.market_ids_with_implications(unique_market_ids)

    for market_id in dict.fromkeys(market_ids):
        market = markets.get(market_id)
        if market is None:
            continue
        price_rows = price_by_market.get(market.id, [])
        trade_rows = trades_by_token.get(market.clob_token_ids[0], []) if market.clob_token_ids else []
        sem_present = market.id in sem_by_market
        score_present = market.id in score_by_market
        impl_present = market.id in impl_market_ids
        yield compute_market_coverage(
            market, price_rows, trade_rows, sem_present, score_present, impl_present, cfg
        )


def verify_dataset(data_root: Path, cfg: BackfillConfig) -> list[ValidationResult]:
    """Run all validators; return PASS/WARN/FAIL array."""
    results: list[ValidationResult] = []

    markets = list(select_universe(data_root, cfg))
    results.append(validate_markets_table(list(markets)))

    # Collect all price/trade rows for validation
    price_repo = ParquetPriceHistoryRepository(data_root)
    trade_repo = ParquetTradeHistoryRepository(data_root)
    market_ids = [m.id for m in markets]
    token_ids = [
        token_id
        for market in markets
        for token_id in (market.clob_token_ids or [])
    ]
    price_rows = list(price_repo.iter_for_markets(market_ids))
    trade_rows = list(trade_repo.iter_for_tokens(token_ids))

    results.append(validate_price_history(price_rows))
    results.append(validate_trade_history(trade_rows))

    # Semantics coverage
    sem_repo = ParquetMarketSemanticsRepository(data_root)
    score_repo = ParquetMarketScoresRepository(data_root)
    impl_repo = ParquetMarketImplicationsRepository(data_root)
    sem_latest = sem_repo.latest_for_market_ids(market_ids)
    score_latest = score_repo.latest_for_market_ids(market_ids)
    sem_ids = set(sem_latest)
    score_ids = set(score_latest)
    impl_ids = impl_repo.market_ids_with_implications(market_ids)
    results.append(validate_semantics_coverage(market_ids, sem_ids, score_ids, impl_ids))

    # Check for chain-of-thought leakage in semantics rows
    sem_rows = list(sem_latest.values())
    results.append(
        validate_no_thinking(
            sem_rows,
            [
                "explanation_summary",
                "flag_rationales_json",
                "uncertainty_notes_json",
                "rule_curation_notes_json",
                "canonical_question",
            ],
        )
    )

    # Coverage rows
    cov_repo = ParquetBackfillCoverageRepository(data_root)
    cov_rows = list(cov_repo.iter_latest())
    results.append(validate_coverage_rows(cov_rows))

    return results
