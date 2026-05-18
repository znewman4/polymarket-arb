"""Parquet impl of PriceHistoryRepository."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ..base import PriceHistoryRow
from ._writer import write_table_part
from .schemas import PRICE_HISTORY_SCHEMA_V1

_TABLE = "price_history"


class ParquetPriceHistoryRepository:
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

    def append(self, row: PriceHistoryRow) -> None:
        self.append_many([row])

    def append_many(self, rows: Iterable[PriceHistoryRow]) -> int:
        rows_list = [asdict(r) for r in rows]
        if not rows_list:
            return 0
        write_table_part(
            self._root,
            _TABLE,
            PRICE_HISTORY_SCHEMA_V1,
            rows_list,
            compression=self._compression,
            row_group_size=self._row_group_size,
        )
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def _row_from_dict(self, d: dict) -> PriceHistoryRow:
        d = dict(d)
        d.pop("dt", None)
        names = {f.name for f in fields(PriceHistoryRow)}
        return PriceHistoryRow(**{k: v for k, v in d.items() if k in names})

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

    def iter_for_token(self, token_id: str) -> Iterator[PriceHistoryRow]:
        rows = self._query(
            "SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
            "WHERE token_id = ? ORDER BY ts_ms ASC",
            [token_id],
        )
        for r in rows:
            yield self._row_from_dict(r)

    def distinct_token_ids(self) -> set[str]:
        """Return every token_id with at least one stored price row."""
        rows = self._query(
            "SELECT DISTINCT token_id FROM read_parquet('{glob}', hive_partitioning=true)",
            [],
        )
        return {str(r["token_id"]) for r in rows if r.get("token_id") is not None}

    def count_for_token(self, token_id: str) -> int:
        rows = self._query(
            "SELECT count(*) AS c FROM read_parquet('{glob}', hive_partitioning=true) "
            "WHERE token_id = ?",
            [token_id],
        )
        return int(rows[0]["c"]) if rows else 0

    def iter_for_market(self, market_id: str) -> Iterator[PriceHistoryRow]:
        rows = self._query(
            "SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
            "WHERE market_id = ? ORDER BY ts_ms ASC",
            [market_id],
        )
        for r in rows:
            yield self._row_from_dict(r)

    def iter_for_markets(self, market_ids: Iterable[str]) -> Iterator[PriceHistoryRow]:
        ids = list(dict.fromkeys(market_ids))
        if not ids:
            return
        placeholders = ", ".join("?" for _ in ids)
        rows = self._query(
            "SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
            f"WHERE market_id IN ({placeholders}) ORDER BY market_id, ts_ms ASC",
            ids,
        )
        for r in rows:
            yield self._row_from_dict(r)

    def iter_for_tokens(self, token_ids: Iterable[str]) -> Iterator[PriceHistoryRow]:
        """Bulk fetch price history for multiple tokens in one scan."""
        ids = list(dict.fromkeys(token_ids))
        if not ids:
            return
        placeholders = ", ".join("?" for _ in ids)
        rows = self._query(
            "SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
            f"WHERE token_id IN ({placeholders}) ORDER BY token_id, ts_ms ASC",
            ids,
        )
        for r in rows:
            yield self._row_from_dict(r)

    def stats_for_tokens(self, token_ids: Iterable[str]) -> dict[str, dict]:
        """Return {token_id: {ticks, first_ts_ms, last_ts_ms}} in a single scan.

        Tokens not present in the store are absent from the result dict.
        """
        ids = list(dict.fromkeys(token_ids))
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        rows = self._query(
            "SELECT token_id, count(*) AS ticks, min(ts_ms) AS first_ts_ms, max(ts_ms) AS last_ts_ms "
            "FROM read_parquet('{glob}', hive_partitioning=true) "
            f"WHERE token_id IN ({placeholders}) GROUP BY token_id",
            ids,
        )
        return {str(r["token_id"]): r for r in rows}
