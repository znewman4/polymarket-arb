"""Parquet impl of deterministic ``market_implications`` rows."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ..base import MarketImplicationRow
from ._writer import write_table_part
from .schemas import MARKET_IMPLICATIONS_SCHEMA_V1

_TABLE = "market_implications"


class ParquetMarketImplicationsRepository:
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

    def append(self, row: MarketImplicationRow) -> None:
        self.append_many([row])

    def append_many(self, rows: Iterable[MarketImplicationRow]) -> int:
        rows_list = list(rows)
        if not rows_list:
            return 0
        write_table_part(
            self._root,
            _TABLE,
            MARKET_IMPLICATIONS_SCHEMA_V1,
            [asdict(r) for r in rows_list],
            compression=self._compression,
            row_group_size=self._row_group_size,
        )
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def _row_from_dict(self, d: dict) -> MarketImplicationRow:
        d = dict(d)
        d.pop("dt", None)
        names = {f.name for f in fields(MarketImplicationRow)}
        return MarketImplicationRow(**{k: v for k, v in d.items() if k in names})

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

    def for_market(self, market_id: str) -> list[MarketImplicationRow]:
        rows = self._query(
            "SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
            "WHERE market_id = ? ORDER BY ingested_ts_ms DESC, implication_type",
            [market_id],
        )
        return [self._row_from_dict(r) for r in rows]

    def market_ids_with_implications(self, market_ids: Iterable[str]) -> set[str]:
        ids = list(dict.fromkeys(market_ids))
        if not ids:
            return set()
        placeholders = ", ".join("?" for _ in ids)
        rows = self._query(
            "SELECT DISTINCT market_id FROM read_parquet('{glob}', hive_partitioning=true) "
            f"WHERE market_id IN ({placeholders})",
            ids,
        )
        return {str(r["market_id"]) for r in rows}

    def needs_review(self, limit: int = 50) -> list[MarketImplicationRow]:
        rows = self._query(
            "SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
            "WHERE needs_manual_review = true ORDER BY ingested_ts_ms DESC LIMIT ?",
            [limit],
        )
        return [self._row_from_dict(r) for r in rows]
