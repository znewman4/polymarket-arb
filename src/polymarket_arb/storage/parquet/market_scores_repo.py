"""Parquet impl for research-only market scores."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ..base import MarketScoreRow
from ._writer import write_table_part
from .schemas import MARKET_SCORES_SCHEMA_V1

_TABLE = "market_scores"


class ParquetMarketScoresRepository:
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

    def append(self, row: MarketScoreRow) -> None:
        self.append_many([row])

    def append_many(self, rows: Iterable[MarketScoreRow]) -> int:
        rows_list = list(rows)
        if not rows_list:
            return 0
        write_table_part(
            self._root,
            _TABLE,
            MARKET_SCORES_SCHEMA_V1,
            [asdict(r) for r in rows_list],
            compression=self._compression,
            row_group_size=self._row_group_size,
        )
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def _row_from_dict(self, d: dict) -> MarketScoreRow:
        d = dict(d)
        d.pop("dt", None)
        names = {f.name for f in fields(MarketScoreRow)}
        return MarketScoreRow(**{k: v for k, v in d.items() if k in names})

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

    def latest(self, market_id: str) -> MarketScoreRow | None:
        rows = self._query(
            "SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
            "WHERE market_id = ? ORDER BY ingested_ts_ms DESC LIMIT 1",
            [market_id],
        )
        return self._row_from_dict(rows[0]) if rows else None

    def latest_for_market_ids(self, market_ids: Iterable[str]) -> dict[str, MarketScoreRow]:
        ids = list(dict.fromkeys(market_ids))
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        rows = self._query(
            "WITH latest AS ("
            "  SELECT *, row_number() OVER (PARTITION BY market_id "
            "    ORDER BY ingested_ts_ms DESC) AS rn "
            "  FROM read_parquet('{glob}', hive_partitioning=true)"
            ")"
            f" SELECT * EXCLUDE rn FROM latest WHERE rn = 1 AND market_id IN ({placeholders})",
            ids,
        )
        return {str(r["market_id"]): self._row_from_dict(r) for r in rows}
