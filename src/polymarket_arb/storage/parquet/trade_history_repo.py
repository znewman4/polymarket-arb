"""Parquet impl of TradeHistoryRepository."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ..base import TradeHistoryRow
from ._writer import write_table_part
from .schemas import TRADE_HISTORY_SCHEMA_V1

_TABLE = "trade_history"


class ParquetTradeHistoryRepository:
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

    def append(self, row: TradeHistoryRow) -> None:
        self.append_many([row])

    def append_many(self, rows: Iterable[TradeHistoryRow]) -> int:
        rows_list = [asdict(r) for r in rows]
        if not rows_list:
            return 0
        write_table_part(
            self._root,
            _TABLE,
            TRADE_HISTORY_SCHEMA_V1,
            rows_list,
            compression=self._compression,
            row_group_size=self._row_group_size,
        )
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def _row_from_dict(self, d: dict) -> TradeHistoryRow:
        d = dict(d)
        d.pop("dt", None)
        names = {f.name for f in fields(TradeHistoryRow)}
        return TradeHistoryRow(**{k: v for k, v in d.items() if k in names})

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

    def iter_for_token(self, token_id: str) -> Iterator[TradeHistoryRow]:
        rows = self._query(
            "SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
            "WHERE token_id = ? ORDER BY trade_ts_ms ASC",
            [token_id],
        )
        for r in rows:
            yield self._row_from_dict(r)

    def iter_for_tokens(self, token_ids: Iterable[str]) -> Iterator[TradeHistoryRow]:
        ids = list(dict.fromkeys(token_ids))
        if not ids:
            return
        placeholders = ", ".join("?" for _ in ids)
        rows = self._query(
            "SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
            f"WHERE token_id IN ({placeholders}) ORDER BY token_id, trade_ts_ms ASC",
            ids,
        )
        for r in rows:
            yield self._row_from_dict(r)

    def count_for_token(self, token_id: str) -> int:
        rows = self._query(
            "SELECT count(*) AS c FROM read_parquet('{glob}', hive_partitioning=true) "
            "WHERE token_id = ?",
            [token_id],
        )
        return int(rows[0]["c"]) if rows else 0
