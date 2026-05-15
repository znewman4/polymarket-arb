"""Async price-history backfill over the selected universe."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from ..http.client import AsyncHttpClient
from ..ingest.clob.client import ClobClient
from ..ingest.clob.price_history_parser import parse_prices_history
from ..settings import Settings
from ..storage.base import MarketRow
from ..storage.parquet.markets_repo import ParquetMarketsRepository
from ..storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from ..storage.parquet.raw_writer import RawWriter
from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from .market_backfill import select_universe
from .models import BackfillConfig, BackfillResult

logger = logging.getLogger(__name__)

_BATCH_MAX = 20  # max token IDs per batch call
_COARSE_FALLBACK_INTERVAL = "max"
_COARSE_FALLBACK_FIDELITY_MINUTES = 720


def _now_s() -> int:
    return int(time.time())


def _http_error_details(exc: Exception) -> dict[str, Any]:
    """Extract status/body from wrapped HTTP errors without logging secrets."""
    cause = exc.__cause__
    if isinstance(cause, httpx.HTTPStatusError):
        return {
            "status": cause.response.status_code,
            "body_excerpt": cause.response.text[:300],
        }
    return {"status": None, "body_excerpt": str(exc)[:300]}


def _should_try_coarse_fallback(exc: Exception) -> bool:
    details = _http_error_details(exc)
    body = str(details.get("body_excerpt") or "").lower()
    status = details.get("status")
    return status == 400 and (
        "interval is too long" in body
        or "invalid filters" in body
        or "invalid request body" in body
    )


def _log_prices_history_failure(
    *,
    token_id: str | None,
    endpoint: str,
    interval: str,
    fidelity_minutes: int | None,
    start_ts_s: int | None,
    end_ts_s: int | None,
    exc: Exception,
    level: int = logging.WARNING,
) -> None:
    details = _http_error_details(exc)
    logger.log(
        level,
        (
            "prices-history fetch failed endpoint=%s token_id=%s interval=%s "
            "fidelity=%s start_ts=%s end_ts=%s status=%s body=%s"
        ),
        endpoint,
        token_id,
        interval,
        fidelity_minutes,
        start_ts_s,
        end_ts_s,
        details.get("status"),
        details.get("body_excerpt"),
    )


async def run_price_history_backfill(
    settings: Settings,
    cfg: BackfillConfig | None = None,
) -> BackfillResult:
    if cfg is None:
        cfg = BackfillConfig()

    result = BackfillResult()
    data_root: Path = settings.data_root
    repo = ParquetPriceHistoryRepository(
        data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )
    raw_writer = RawWriter(data_root)

    markets = select_universe(data_root, cfg)
    if not markets:
        logger.warning("price_history_backfill: universe is empty — run gamma fetch-markets first")
        return result

    start_ts_s = _now_s() - cfg.requested_days * 86400
    end_ts_s = _now_s()

    # Build flat list of (market, token_id, outcome, idx) tuples
    tokens: list[tuple] = []
    for market in markets:
        outcomes = market.outcomes or []
        for idx, token_id in enumerate(market.clob_token_ids or []):
            outcome = outcomes[idx] if idx < len(outcomes) else None
            tokens.append((market, token_id, outcome))

    async with AsyncHttpClient(settings.http) as http:
        client = ClobClient(clob_host=settings.clob_host, http=http, raw_writer=raw_writer)

        # Process in batches of up to _BATCH_MAX
        for batch_start in range(0, len(tokens), _BATCH_MAX):
            batch = tokens[batch_start : batch_start + _BATCH_MAX]
            batch_token_ids = [t[1] for t in batch]

            # Try batch endpoint first; fall back to individual on failure
            batch_payloads: list[dict[str, Any]] | None = None
            batch_interval = cfg.interval
            try:
                payloads = await client.fetch_batch_prices_history(
                    batch_token_ids,
                    interval=cfg.interval,
                    start_ts_s=start_ts_s,
                    end_ts_s=end_ts_s,
                    fidelity_minutes=cfg.fidelity_minutes,
                )
                if payloads and len(payloads) == len(batch):
                    batch_payloads = list(payloads)
            except Exception as exc:
                _log_prices_history_failure(
                    token_id=None,
                    endpoint="/batch-prices-history",
                    interval=cfg.interval,
                    fidelity_minutes=cfg.fidelity_minutes,
                    start_ts_s=start_ts_s,
                    end_ts_s=end_ts_s,
                    exc=exc,
                    level=logging.DEBUG,
                )
                if _should_try_coarse_fallback(exc):
                    try:
                        payloads = await client.fetch_batch_prices_history(
                            batch_token_ids,
                            interval=_COARSE_FALLBACK_INTERVAL,
                            start_ts_s=None,
                            end_ts_s=None,
                            fidelity_minutes=_COARSE_FALLBACK_FIDELITY_MINUTES,
                        )
                        if payloads and len(payloads) == len(batch):
                            batch_payloads = list(payloads)
                            batch_interval = _COARSE_FALLBACK_INTERVAL
                    except Exception as fallback_exc:
                        _log_prices_history_failure(
                            token_id=None,
                            endpoint="/batch-prices-history",
                            interval=_COARSE_FALLBACK_INTERVAL,
                            fidelity_minutes=_COARSE_FALLBACK_FIDELITY_MINUTES,
                            start_ts_s=None,
                            end_ts_s=None,
                            exc=fallback_exc,
                            level=logging.DEBUG,
                        )

            for i, (market, token_id, outcome) in enumerate(batch):
                result.markets_attempted += 1
                payload: dict | None = None
                interval_used = batch_interval

                if batch_payloads is not None:
                    payload = batch_payloads[i]
                else:
                    try:
                        payload = await client.fetch_prices_history(
                            token_id,
                            interval=cfg.interval,
                            start_ts_s=start_ts_s,
                            end_ts_s=end_ts_s,
                            fidelity_minutes=cfg.fidelity_minutes,
                        )
                    except Exception as exc:
                        _log_prices_history_failure(
                            token_id=token_id,
                            endpoint="/prices-history",
                            interval=cfg.interval,
                            fidelity_minutes=cfg.fidelity_minutes,
                            start_ts_s=start_ts_s,
                            end_ts_s=end_ts_s,
                            exc=exc,
                        )
                        if not _should_try_coarse_fallback(exc):
                            result.markets_failed += 1
                            result.skipped.append({"market_id": market.id, "reason": str(exc)})
                            continue
                        try:
                            payload = await client.fetch_prices_history(
                                token_id,
                                interval=_COARSE_FALLBACK_INTERVAL,
                                start_ts_s=None,
                                end_ts_s=None,
                                fidelity_minutes=_COARSE_FALLBACK_FIDELITY_MINUTES,
                            )
                            interval_used = _COARSE_FALLBACK_INTERVAL
                        except Exception as fallback_exc:
                            _log_prices_history_failure(
                                token_id=token_id,
                                endpoint="/prices-history",
                                interval=_COARSE_FALLBACK_INTERVAL,
                                fidelity_minutes=_COARSE_FALLBACK_FIDELITY_MINUTES,
                                start_ts_s=None,
                                end_ts_s=None,
                                exc=fallback_exc,
                            )
                            result.markets_failed += 1
                            result.skipped.append(
                                {"market_id": market.id, "reason": str(fallback_exc)}
                            )
                            continue

                if payload is None:
                    result.markets_failed += 1
                    result.skipped.append(
                        {"market_id": market.id, "reason": "null payload"}
                    )
                    continue

                try:
                    parse_result = parse_prices_history(
                        payload,
                        token_id=token_id,
                        interval=interval_used,
                        market_id=market.id,
                        condition_id=market.condition_id,
                        outcome=outcome,
                    )
                    written = repo.append_many(parse_result.rows)
                    result.total_rows_written += written
                    result.markets_succeeded += 1
                    if parse_result.warnings:
                        for w in parse_result.warnings:
                            logger.debug("token %s: %s", token_id, w)
                except Exception as exc:
                    logger.warning("parse error for token %s: %s", token_id, exc)
                    result.markets_failed += 1
                    result.skipped.append({"market_id": market.id, "reason": str(exc)})

    return result


async def run_relationship_price_backfill(
    settings: Settings,
    cfg: BackfillConfig | None = None,
) -> tuple[BackfillResult, dict[str, dict]]:
    """Backfill price history for every token referenced by relationship candidates.

    Uses the same batch/retry/fallback logic as run_price_history_backfill().
    Returns (BackfillResult, token_coverage_report) where token_coverage_report maps
    token_id → {has_price_history, tick_count, first_ts_ms, last_ts_ms}.
    """
    if cfg is None:
        cfg = BackfillConfig()

    data_root: Path = settings.data_root
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    market_repo = ParquetMarketsRepository(data_root)
    repo = ParquetPriceHistoryRepository(
        data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )
    raw_writer = RawWriter(data_root)

    # Collect all unique token IDs from relationship candidates
    all_rels = list(rel_repo.iter_latest())
    token_set: dict[str, str] = {}  # token_id → market_id (first seen)
    market_ids: list[str] = []
    for rel in all_rels:
        for tok, mid in [
            (rel.token_id_a_yes, rel.market_id_a),
            (rel.token_id_a_no, rel.market_id_a),
            (rel.token_id_b_yes, rel.market_id_b),
            (rel.token_id_b_no, rel.market_id_b),
        ]:
            if tok and tok not in token_set:
                token_set[tok] = mid
        market_ids.append(rel.market_id_a)
        market_ids.append(rel.market_id_b)

    # Also pull any clob_token_ids from market rows not already captured
    markets_map = market_repo.get_many_markets(market_ids)
    for market_row in markets_map.values():
        for tok in (market_row.clob_token_ids or []):
            if tok and tok not in token_set:
                token_set[tok] = market_row.id

    if not token_set:
        logger.warning("relationship_price_backfill: no token IDs found in relationship store")
        return BackfillResult(), {}

    # Build token tuples: (market_stub, token_id, outcome)
    tokens: list[tuple] = []
    for tok, mid in token_set.items():
        market = markets_map.get(mid)
        if market is None:
            market = MarketRow(
                id=mid, condition_id="", slug="", question="", description=None,
                end_date_ms=None, start_date_ms=None, closed_at_ms=None,
                resolved_at_ms=None, active=True, closed=False, archived=False,
                outcomes=[], gamma_outcome_prices_snapshot=[], clob_token_ids=[tok],
                volume=None, liquidity=None, event_id=None, neg_risk=False,
                text_hash="", schema_version=1, ingested_ts_ms=0,
            )
        outcome = None
        if market.clob_token_ids and tok in market.clob_token_ids:
            idx = market.clob_token_ids.index(tok)
            outcomes = market.outcomes or []
            outcome = outcomes[idx] if idx < len(outcomes) else None
        tokens.append((market, tok, outcome))

    start_ts_s = _now_s() - cfg.requested_days * 86400
    end_ts_s = _now_s()
    result = BackfillResult()

    async with AsyncHttpClient(settings.http) as http:
        client = ClobClient(clob_host=settings.clob_host, http=http, raw_writer=raw_writer)

        for batch_start in range(0, len(tokens), _BATCH_MAX):
            batch = tokens[batch_start : batch_start + _BATCH_MAX]
            batch_token_ids = [t[1] for t in batch]

            batch_payloads: list[dict[str, Any]] | None = None
            batch_interval = cfg.interval
            try:
                payloads = await client.fetch_batch_prices_history(
                    batch_token_ids,
                    interval=cfg.interval,
                    start_ts_s=start_ts_s,
                    end_ts_s=end_ts_s,
                    fidelity_minutes=cfg.fidelity_minutes,
                )
                if payloads and len(payloads) == len(batch):
                    batch_payloads = list(payloads)
            except Exception as exc:
                _log_prices_history_failure(
                    token_id=None, endpoint="/batch-prices-history",
                    interval=cfg.interval, fidelity_minutes=cfg.fidelity_minutes,
                    start_ts_s=start_ts_s, end_ts_s=end_ts_s, exc=exc, level=logging.DEBUG,
                )
                if _should_try_coarse_fallback(exc):
                    try:
                        payloads = await client.fetch_batch_prices_history(
                            batch_token_ids,
                            interval=_COARSE_FALLBACK_INTERVAL,
                            start_ts_s=None, end_ts_s=None,
                            fidelity_minutes=_COARSE_FALLBACK_FIDELITY_MINUTES,
                        )
                        if payloads and len(payloads) == len(batch):
                            batch_payloads = list(payloads)
                            batch_interval = _COARSE_FALLBACK_INTERVAL
                    except Exception as fe:
                        _log_prices_history_failure(
                            token_id=None, endpoint="/batch-prices-history",
                            interval=_COARSE_FALLBACK_INTERVAL,
                            fidelity_minutes=_COARSE_FALLBACK_FIDELITY_MINUTES,
                            start_ts_s=None, end_ts_s=None, exc=fe, level=logging.DEBUG,
                        )

            for i, (market, token_id, outcome) in enumerate(batch):
                result.markets_attempted += 1
                payload: dict | None = None
                interval_used = batch_interval
                if batch_payloads is not None:
                    payload = batch_payloads[i]
                else:
                    try:
                        payload = await client.fetch_prices_history(
                            token_id, interval=cfg.interval,
                            start_ts_s=start_ts_s, end_ts_s=end_ts_s,
                            fidelity_minutes=cfg.fidelity_minutes,
                        )
                    except Exception as exc:
                        _log_prices_history_failure(
                            token_id=token_id, endpoint="/prices-history",
                            interval=cfg.interval, fidelity_minutes=cfg.fidelity_minutes,
                            start_ts_s=start_ts_s, end_ts_s=end_ts_s, exc=exc,
                        )
                        if not _should_try_coarse_fallback(exc):
                            result.markets_failed += 1
                            result.skipped.append({"market_id": market.id, "reason": str(exc)})
                            continue
                        try:
                            payload = await client.fetch_prices_history(
                                token_id, interval=_COARSE_FALLBACK_INTERVAL,
                                start_ts_s=None, end_ts_s=None,
                                fidelity_minutes=_COARSE_FALLBACK_FIDELITY_MINUTES,
                            )
                            interval_used = _COARSE_FALLBACK_INTERVAL
                        except Exception as fe:
                            _log_prices_history_failure(
                                token_id=token_id, endpoint="/prices-history",
                                interval=_COARSE_FALLBACK_INTERVAL,
                                fidelity_minutes=_COARSE_FALLBACK_FIDELITY_MINUTES,
                                start_ts_s=None, end_ts_s=None, exc=fe,
                            )
                            result.markets_failed += 1
                            result.skipped.append({"market_id": market.id, "reason": str(fe)})
                            continue

                if payload is None:
                    result.markets_failed += 1
                    result.skipped.append({"market_id": market.id, "reason": "null payload"})
                    continue

                try:
                    parse_result = parse_prices_history(
                        payload, token_id=token_id, interval=interval_used,
                        market_id=market.id, condition_id=market.condition_id, outcome=outcome,
                    )
                    written = repo.append_many(parse_result.rows)
                    result.total_rows_written += written
                    result.markets_succeeded += 1
                except Exception as exc:
                    logger.warning("parse error for relationship token %s: %s", token_id, exc)
                    result.markets_failed += 1
                    result.skipped.append({"market_id": market.id, "reason": str(exc)})

    # Post-backfill token coverage stats (single bulk query)
    all_token_ids = list(token_set.keys())
    stats = repo.stats_for_tokens(all_token_ids)
    coverage_report: dict[str, dict] = {
        tok: {
            "market_id": token_set[tok],
            "has_price_history": tok in stats,
            "tick_count": int(stats[tok]["ticks"]) if tok in stats else 0,
            "first_ts_ms": int(stats[tok]["first_ts_ms"]) if tok in stats else None,
            "last_ts_ms": int(stats[tok]["last_ts_ms"]) if tok in stats else None,
        }
        for tok in all_token_ids
    }
    return result, coverage_report
