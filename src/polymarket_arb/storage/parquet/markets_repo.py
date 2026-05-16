"""Parquet impl of ``MarketsRepository``.

Append-only: every ``upsert_markets`` call writes a new part-file. "Latest"
state is the ``markets_latest`` DuckDB view (one row per id, newest first).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, fields
from decimal import Decimal
from pathlib import Path

import duckdb

from ..base import MarketRow
from ._writer import write_table_part
from .schemas import MARKETS_SCHEMA_V1

_TABLE = "markets"


class ParquetMarketsRepository:
    def __init__(
        self,
        data_root: Path,
        *,
        compression: str = "zstd",
        row_group_size: int = 50_000,
    ) -> None:
        self._root = data_root
        self._compression = compression
        self._row_group_size = row_group_size

    # ─── writes ─────────────────────────────────────────────────────────

    def upsert_markets(self, rows: Iterable[MarketRow]) -> int:
        rows_list = list(rows)
        if not rows_list:
            return 0
        write_table_part(
            self._root, _TABLE, MARKETS_SCHEMA_V1,
            [asdict(r) for r in rows_list],
            compression=self._compression,
            row_group_size=self._row_group_size,
        )
        return len(rows_list)

    # ─── reads (DuckDB) ─────────────────────────────────────────────────

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def _row_from_dict(self, d: dict) -> MarketRow:
        # DuckDB returns ``dt`` from hive partitioning; strip and coerce
        # decimal columns from dataclass-friendly types.
        d = dict(d)
        d.pop("dt", None)
        names = {f.name for f in fields(MarketRow)}
        return MarketRow(**{k: v for k, v in d.items() if k in names})

    def _query(self, sql: str, params: list) -> list[dict]:
        if not self._has_data():
            return []
        con = duckdb.connect()
        try:
            cur = con.execute(sql.format(glob=self._glob()), params)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
        finally:
            con.close()

    _LATEST_VIEW_SQL = (
        "WITH latest AS ("
        "  SELECT *, row_number() OVER (PARTITION BY id ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{glob}', hive_partitioning=true)"
        ")"
    )

    def iter_all_markets(self) -> "Iterator[MarketRow]":
        """Yield all latest-version markets regardless of active/closed status."""
        rows = self._query(
            self._LATEST_VIEW_SQL +
            " SELECT * EXCLUDE rn FROM latest WHERE rn = 1",
            [],
        )
        for r in rows:
            yield self._row_from_dict(r)

    def get_market(self, market_id: str) -> MarketRow | None:
        rows = self._query(
            self._LATEST_VIEW_SQL +
            " SELECT * EXCLUDE rn FROM latest WHERE rn = 1 AND id = ? LIMIT 1",
            [market_id],
        )
        return self._row_from_dict(rows[0]) if rows else None

    def iter_active_markets(self) -> Iterator[MarketRow]:
        # active=true AND closed=false AND (end_date_ms IS NULL OR end_date_ms > now*1000)
        rows = self._query(
            self._LATEST_VIEW_SQL +
            " SELECT * EXCLUDE rn FROM latest"
            " WHERE rn = 1 AND active = true AND closed = false"
            "   AND (end_date_ms IS NULL OR end_date_ms > epoch_ms(now()))",
            [],
        )
        for r in rows:
            yield self._row_from_dict(r)

    def search(self, query: str, limit: int = 20) -> list[MarketRow]:
        like = f"%{query.lower()}%"
        rows = self._query(
            self._LATEST_VIEW_SQL +
            " SELECT * EXCLUDE rn FROM latest"
            " WHERE rn = 1 AND lower(question) LIKE ?"
            " ORDER BY ingested_ts_ms DESC LIMIT ?",
            [like, limit],
        )
        return [self._row_from_dict(r) for r in rows]

    def get_many_markets(self, market_ids: Iterable[str]) -> dict[str, MarketRow]:
        """Return {market_id: MarketRow} for a batch of IDs in a single scan."""
        ids = list(dict.fromkeys(market_ids))
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        rows = self._query(
            self._LATEST_VIEW_SQL +
            f" SELECT * EXCLUDE rn FROM latest WHERE rn = 1 AND id IN ({placeholders})",
            ids,
        )
        return {str(r["id"]): self._row_from_dict(r) for r in rows}

    # Convenience helper used by the CLI.
    def latest_count(self) -> int:
        rows = self._query(
            self._LATEST_VIEW_SQL +
            " SELECT count(*) AS c FROM latest WHERE rn = 1",
            [],
        )
        return int(rows[0]["c"]) if rows else 0


# Re-export the Decimal symbol so type-checkers are happy when this module
# is imported next to ``Decimal``-using callers.
_ = Decimal
