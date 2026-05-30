"""Parquet implementation for open and closed live-agent position states."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ...live.models import PositionRow
from ._writer import write_table_part
from .schemas import POSITIONS_SCHEMA_V1

_TABLE = "positions"


class ParquetPositionsRepository:
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

    def append(self, row: PositionRow) -> None:
        self.append_many([row])

    def append_many(self, rows: Iterable[PositionRow]) -> int:
        rows_list = [asdict(row) for row in rows]
        if not rows_list:
            return 0
        write_table_part(
            self._root,
            _TABLE,
            POSITIONS_SCHEMA_V1,
            rows_list,
            compression=self._compression,
            row_group_size=self._row_group_size,
        )
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def iter_recent(self, limit: int = 1000) -> Iterator[PositionRow]:
        if not self._has_data():
            return iter(())
        con = duckdb.connect()
        try:
            cur = con.execute(
                f"SELECT * FROM read_parquet('{self._glob()}', hive_partitioning=true, "
                "union_by_name=true) "
                f"ORDER BY open_ts_ms DESC LIMIT {int(limit)}"
            )
            cols = [c[0] for c in cur.description]
            rows = cur.fetchall()
        finally:
            con.close()
        names = {field.name for field in fields(PositionRow)}
        out = []
        for raw in rows:
            values = dict(zip(cols, raw, strict=False))
            values.pop("dt", None)
            values["gross_edge"] = values.get("gross_edge") or ""
            values["relationship_id"] = (
                values.get("relationship_id") or values.get("source_relationship_id") or ""
            )
            values["relationship_type"] = values.get("relationship_type") or ""
            values["ingested_ts_ms"] = values.get("ingested_ts_ms") or values["open_ts_ms"]
            out.append(PositionRow(**{key: value for key, value in values.items() if key in names}))
        return iter(out)

    def iter_open(self, strategy_id: str = "limitless_arb") -> Iterator[PositionRow]:
        """Return latest state rows that are currently open for a strategy."""
        if not self._has_data():
            return iter(())
        con = duckdb.connect()
        try:
            cur = con.execute(
                "WITH latest AS ("
                "  SELECT *, row_number() OVER ("
                "    PARTITION BY position_id ORDER BY ingested_ts_ms DESC"
                "  ) AS rn "
                f"  FROM read_parquet('{self._glob()}', hive_partitioning=true, union_by_name=true) "
                "  WHERE strategy_id = ?"
                ") SELECT * EXCLUDE rn FROM latest WHERE rn = 1 AND status = 'open' "
                "ORDER BY open_ts_ms DESC",
                [strategy_id],
            )
            cols = [c[0] for c in cur.description]
            rows = cur.fetchall()
        finally:
            con.close()
        names = {field.name for field in fields(PositionRow)}
        out = []
        for raw in rows:
            values = dict(zip(cols, raw, strict=False))
            values.pop("dt", None)
            values["gross_edge"] = values.get("gross_edge") or ""
            values["relationship_id"] = (
                values.get("relationship_id") or values.get("source_relationship_id") or ""
            )
            values["relationship_type"] = values.get("relationship_type") or ""
            values["ingested_ts_ms"] = values.get("ingested_ts_ms") or values["open_ts_ms"]
            out.append(PositionRow(**{key: value for key, value in values.items() if key in names}))
        return iter(out)
