"""Select backfill universe from the lake."""

from __future__ import annotations

import time
from pathlib import Path

import duckdb

from ..storage.base import MarketRow
from ..storage.parquet.markets_repo import ParquetMarketsRepository
from .models import BackfillConfig


def _now_ms() -> int:
    return int(time.time() * 1000)


def select_universe(data_root: Path, cfg: BackfillConfig) -> list[MarketRow]:
    """Return up to ``cfg.market_limit`` markets for backfilling.

    Combines active markets + recently-resolved markets (closed within
    ``cfg.requested_days``), deduped by id.
    """
    repo = ParquetMarketsRepository(data_root)
    markets: dict[str, MarketRow] = {}

    if cfg.include_active:
        for m in repo.iter_active_markets():
            markets[m.id] = m

    if cfg.include_recently_resolved:
        cutoff_ms = _now_ms() - cfg.requested_days * 24 * 3600 * 1000
        glob = str(data_root / "normalised" / "markets" / "dt=*" / "*.parquet")
        has_data = any((data_root / "normalised" / "markets").glob("dt=*/*.parquet"))
        if has_data:
            con = duckdb.connect()
            try:
                cur = con.execute(
                    f"""
                    WITH latest AS (
                      SELECT *, row_number() OVER (PARTITION BY id ORDER BY ingested_ts_ms DESC) AS rn
                      FROM read_parquet('{glob}', hive_partitioning=true)
                    )
                    SELECT * EXCLUDE rn FROM latest
                    WHERE rn = 1
                      AND closed = true
                      AND end_date_ms IS NOT NULL
                      AND end_date_ms >= ?
                      AND clob_token_ids IS NOT NULL
                    ORDER BY end_date_ms DESC
                    """,
                    [cutoff_ms],
                )
                col_names = [c[0] for c in cur.description]
                from dataclasses import fields as _fields
                from decimal import Decimal

                from ..storage.base import MarketRow as _MR
                known_fields = {f.name for f in _fields(_MR)}
                for row_tuple in cur.fetchall():
                    d = dict(zip(col_names, row_tuple, strict=False))
                    d.pop("dt", None)
                    d = {k: v for k, v in d.items() if k in known_fields}
                    # Parquet stores list<string> which comes back as list already
                    # but Decimal fields need conversion
                    for price_field in ("gamma_outcome_prices_snapshot",):
                        if price_field in d and d[price_field] is not None:
                            d[price_field] = [
                                Decimal(str(p)) if not isinstance(p, Decimal) else p
                                for p in (d[price_field] or [])
                            ]
                    for dec_field in ("volume", "liquidity"):
                        if d.get(dec_field) is not None:
                            d[dec_field] = Decimal(str(d[dec_field]))
                    try:
                        m = _MR(**d)
                        if m.id not in markets:
                            markets[m.id] = m
                    except (TypeError, ValueError):
                        pass
            finally:
                con.close()

    result = list(markets.values())
    # Stable ordering: active first (no end_date_ms or future), then by end_date_ms desc
    result.sort(key=lambda m: (m.end_date_ms or 0), reverse=True)
    return result[: cfg.market_limit]
