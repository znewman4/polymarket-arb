"""Parquet impl of ``MarketSemanticsRepository``.

Append-only. ``market_semantics_latest`` view returns one row per
``source_market_id`` ordered by ``ingested_ts_ms`` DESC.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ..base import MarketSemanticsRow
from ._writer import write_table_part
from .schemas import MARKET_SEMANTICS_SCHEMA_V2

_TABLE = "market_semantics"


class ParquetMarketSemanticsRepository:
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

    def upsert(self, row: MarketSemanticsRow) -> None:
        self.upsert_many([row])

    def upsert_many(self, rows: Iterable[MarketSemanticsRow]) -> int:
        rows_list = list(rows)
        if not rows_list:
            return 0
        write_table_part(
            self._root, _TABLE, MARKET_SEMANTICS_SCHEMA_V2,
            [asdict(r) for r in rows_list],
            compression=self._compression, row_group_size=self._row_group_size,
        )
        return len(rows_list)

    # ─── reads (DuckDB) ─────────────────────────────────────────────────

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def _row_from_dict(self, d: dict) -> MarketSemanticsRow:
        d = dict(d)
        d.pop("dt", None)
        d.setdefault("sufficient_conditions_for_no", [])
        names = {f.name for f in fields(MarketSemanticsRow)}
        return MarketSemanticsRow(**{k: v for k, v in d.items() if k in names})

    _LATEST = (
        "WITH latest AS ("
        "  SELECT *, row_number() OVER (PARTITION BY source_market_id "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{glob}', hive_partitioning=true, union_by_name=true)"
        ")"
    )

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

    def get_latest(self, market_id: str) -> MarketSemanticsRow | None:
        rows = self._query(
            self._LATEST +
            " SELECT * EXCLUDE rn FROM latest "
            " WHERE rn = 1 AND source_market_id = ? LIMIT 1",
            [market_id],
        )
        return self._row_from_dict(rows[0]) if rows else None

    def latest_for_market_ids(
        self,
        market_ids: Iterable[str],
    ) -> dict[str, MarketSemanticsRow]:
        ids = list(dict.fromkeys(market_ids))
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        rows = self._query(
            self._LATEST +
            " SELECT * EXCLUDE rn FROM latest "
            f" WHERE rn = 1 AND source_market_id IN ({placeholders})",
            ids,
        )
        return {
            str(r["source_market_id"]): self._row_from_dict(r)
            for r in rows
        }

    def needs_review(self, limit: int = 50) -> list[MarketSemanticsRow]:
        rows = self._query(
            self._LATEST +
            " SELECT * EXCLUDE rn FROM latest "
            " WHERE rn = 1 AND needs_manual_review = true "
            " ORDER BY ingested_ts_ms DESC LIMIT ?",
            [limit],
        )
        return [self._row_from_dict(r) for r in rows]

    def iter_latest(
        self,
        *,
        limit: int = 100,
        unscored_only: bool = False,
    ) -> list[MarketSemanticsRow]:
        where = " WHERE rn = 1"
        if unscored_only:
            where += " AND ambiguity_score IS NULL"
        rows = self._query(
            self._LATEST +
            " SELECT * EXCLUDE rn FROM latest " +
            where +
            " ORDER BY ingested_ts_ms DESC LIMIT ?",
            [limit],
        )
        return [self._row_from_dict(r) for r in rows]

    def latest_count(self) -> int:
        rows = self._query(
            self._LATEST + " SELECT count(*) AS c FROM latest WHERE rn = 1",
            [],
        )
        return int(rows[0]["c"]) if rows else 0
