"""Best-effort public trade history backfill.

The Polymarket /trades endpoint may require auth or be unavailable for
some token IDs. All failures are logged as WARN and counted — never raised.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..http.client import AsyncHttpClient
from ..ingest.clob.client import ClobClient
from ..settings import Settings
from ..storage.base import TradeHistoryRow
from ..storage.parquet.raw_writer import RawWriter
from ..storage.parquet.trade_history_repo import ParquetTradeHistoryRepository
from .market_backfill import select_universe
from .models import BackfillConfig, BackfillResult

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


def _now_ms() -> int:
    return int(time.time() * 1000)


def _parse_trade_payload(
    payload: object,
    *,
    market_id: str,
    condition_id: str | None,
    token_id: str,
    outcome: str | None,
) -> list[TradeHistoryRow]:
    if not isinstance(payload, (dict, list)):
        return []
    items: list = payload if isinstance(payload, list) else payload.get("data", []) or []
    rows: list[TradeHistoryRow] = []
    ingested_ts_ms = _now_ms()
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            price = Decimal(str(item.get("price", "0")))
            size = Decimal(str(item.get("size", item.get("amount", "0"))))
            if price < 0 or price > 1 or size < 0:
                continue
            ts_val = item.get("timestamp", item.get("trade_ts", 0)) or 0
            ts_int = int(ts_val)
            ts_ms = ts_int * 1000 if ts_int < 10_000_000_000 else ts_int
            side = item.get("side", None)
            if isinstance(side, str):
                side = side.lower() or None
            rows.append(
                TradeHistoryRow(
                    market_id=market_id,
                    condition_id=condition_id or "",
                    token_id=token_id,
                    outcome=outcome or "",
                    trade_ts_ms=ts_ms,
                    price=price,
                    size=size,
                    side=side,
                    source="clob",
                    schema_version=_SCHEMA_VERSION,
                    ingested_ts_ms=ingested_ts_ms,
                )
            )
        except (InvalidOperation, TypeError, ValueError):
            continue
    return rows


async def run_trade_history_backfill(
    settings: Settings,
    cfg: BackfillConfig | None = None,
) -> BackfillResult:
    if cfg is None:
        cfg = BackfillConfig()

    result = BackfillResult()
    data_root: Path = settings.data_root
    repo = ParquetTradeHistoryRepository(
        data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )
    raw_writer = RawWriter(data_root)

    markets = select_universe(data_root, cfg)
    if not markets:
        logger.warning("trade_history_backfill: universe empty")
        return result

    async with AsyncHttpClient(settings.http) as http:
        client = ClobClient(clob_host=settings.clob_host, http=http, raw_writer=raw_writer)
        for market in markets:
            outcomes = market.outcomes or []
            for idx, token_id in enumerate(market.clob_token_ids or []):
                outcome = outcomes[idx] if idx < len(outcomes) else None
                result.markets_attempted += 1
                try:
                    payload = await client.fetch_trades(token_id)
                    if payload is None:
                        logger.debug("trade history unavailable for token %s", token_id)
                        result.skipped.append(
                            {"market_id": market.id, "reason": "endpoint unavailable"}
                        )
                        result.markets_failed += 1
                        continue
                    rows = _parse_trade_payload(
                        payload,
                        market_id=market.id,
                        condition_id=market.condition_id,
                        token_id=token_id,
                        outcome=outcome,
                    )
                    written = repo.append_many(rows)
                    result.total_rows_written += written
                    result.markets_succeeded += 1
                except Exception as exc:
                    logger.warning("trade fetch error for token %s: %s", token_id, exc)
                    result.markets_failed += 1
                    result.skipped.append({"market_id": market.id, "reason": str(exc)})

    return result
