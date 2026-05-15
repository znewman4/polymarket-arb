"""Timestamp-ordered local event stream."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from . import datasets
from .models import BacktestEvent


def build_event_stream(
    data_root: Path,
    *,
    start_ts_ms: int | None = None,
    end_ts_ms: int | None = None,
    market_ids: Iterable[str] | None = None,
) -> list[BacktestEvent]:
    wanted = list(market_ids) if market_ids is not None else None
    token_map = {token: mid for mid, tokens in _market_tokens(data_root).items() for token in tokens}
    events: list[BacktestEvent] = []
    for row in _optional(lambda: datasets.load_best_quotes(data_root, start_ts_ms, end_ts_ms, wanted)):
        token = str(row.get("token_id"))
        events.append(BacktestEvent(int(row.get("timestamp_ms") or row.get("ingested_ts_ms") or 0),
                                    "best_quote", token_map.get(token, token), row))
    for row in _optional(lambda: datasets.load_orderbook_snapshots(data_root, start_ts_ms, end_ts_ms, wanted)):
        token = str(row.get("token_id"))
        events.append(BacktestEvent(int(row.get("timestamp_ms") or row.get("ingested_ts_ms") or 0),
                                    "orderbook_snapshot", token_map.get(token, token), row))
    for row in _optional(lambda: datasets.load_market_scores(data_root, start_ts_ms, end_ts_ms, wanted)):
        events.append(BacktestEvent(int(row.get("ingested_ts_ms") or 0), "market_score",
                                    str(row.get("market_id")), row))
    return sorted(events, key=lambda e: (e.ts_ms, e.event_type))


def _optional(loader) -> list[dict]:
    try:
        return loader()
    except datasets.DatasetMissingError:
        return []


def _market_tokens(data_root: Path) -> dict[str, list[str]]:
    try:
        return {
            str(m["id"]): [str(t) for t in (m.get("clob_token_ids") or [])]
            for m in datasets.load_markets_latest(data_root)
        }
    except datasets.DatasetMissingError:
        return {}
