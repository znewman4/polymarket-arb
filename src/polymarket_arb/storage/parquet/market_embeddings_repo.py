"""Parquet impl of ``MarketEmbeddingsRepository``."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ..base import MarketEmbeddingRow
from ._writer import write_table_part
from .schemas import MARKET_EMBEDDINGS_SCHEMA_V1

_TABLE = "market_embeddings"


class ParquetMarketEmbeddingsRepository:
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

    def upsert(self, row: MarketEmbeddingRow) -> None:
        self.upsert_many([row])

    def upsert_many(self, rows: Iterable[MarketEmbeddingRow]) -> int:
        rows_list = list(rows)
        if not rows_list:
            return 0
        write_table_part(
            self._root, _TABLE, MARKET_EMBEDDINGS_SCHEMA_V1,
            [asdict(r) for r in rows_list],
            compression=self._compression, row_group_size=self._row_group_size,
        )
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def _row_from_dict(self, d: dict) -> MarketEmbeddingRow:
        d = dict(d)
        d.pop("dt", None)
        names = {f.name for f in fields(MarketEmbeddingRow)}
        return MarketEmbeddingRow(**{k: v for k, v in d.items() if k in names})

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

    def get_latest(self, market_id: str, embedding_space: str) -> MarketEmbeddingRow | None:
        rows = self._query(
            "SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
            "WHERE market_id = ? AND embedding_space = ? "
            "ORDER BY ingested_ts_ms DESC LIMIT 1",
            [market_id, embedding_space],
        )
        return self._row_from_dict(rows[0]) if rows else None

    def known_text_hashes(self, embedding_space: str) -> set[str]:
        rows = self._query(
            "SELECT DISTINCT text_hash FROM read_parquet('{glob}', hive_partitioning=true) "
            "WHERE embedding_space = ?",
            [embedding_space],
        )
        return {r["text_hash"] for r in rows if r.get("text_hash")}
