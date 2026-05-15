"""Parquet repository for context relationship decisions."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ..base import ContextRelationshipDecisionRow
from ._writer import write_table_part
from .schemas import CONTEXT_RELATIONSHIP_DECISIONS_SCHEMA_V1

_TABLE = "context_relationship_decisions"


class ParquetContextRelationshipDecisionsRepository:
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

    def append(self, row: ContextRelationshipDecisionRow) -> None:
        self.append_many([row])

    def append_many(self, rows: Iterable[ContextRelationshipDecisionRow]) -> int:
        rows_list = [asdict(r) for r in rows]
        if not rows_list:
            return 0
        write_table_part(
            self._root,
            _TABLE,
            CONTEXT_RELATIONSHIP_DECISIONS_SCHEMA_V1,
            rows_list,
            compression=self._compression,
            row_group_size=self._row_group_size,
        )
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def _row_from_dict(self, d: dict) -> ContextRelationshipDecisionRow:
        d = dict(d)
        d.pop("dt", None)
        names = {f.name for f in fields(ContextRelationshipDecisionRow)}
        return ContextRelationshipDecisionRow(**{k: v for k, v in d.items() if k in names})

    def _query(self, sql: str, params: list) -> list[dict]:
        if not self._has_data():
            return []
        con = duckdb.connect()
        try:
            cur = con.execute(sql.format(glob=self._glob()), params)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()

    _LATEST = (
        "WITH latest AS ("
        " SELECT *, row_number() OVER (PARTITION BY relationship_id "
        " ORDER BY ingested_ts_ms DESC) AS rn"
        " FROM read_parquet('{glob}', hive_partitioning=true, union_by_name=true)"
        ")"
    )

    def iter_latest(self) -> Iterator[ContextRelationshipDecisionRow]:
        rows = self._query(
            self._LATEST
            + " SELECT * EXCLUDE rn FROM latest WHERE rn = 1"
            + " ORDER BY context_space_id, relationship_id",
            [],
        )
        for row in rows:
            yield self._row_from_dict(row)

    def iter_for_relationship(
        self,
        relationship_id: str,
    ) -> Iterator[ContextRelationshipDecisionRow]:
        rows = self._query(
            self._LATEST
            + " SELECT * EXCLUDE rn FROM latest WHERE rn = 1"
            + " AND relationship_id = ? ORDER BY ingested_ts_ms DESC",
            [relationship_id],
        )
        for row in rows:
            yield self._row_from_dict(row)

    def iter_for_lane(self, strategy_lane: str) -> Iterator[ContextRelationshipDecisionRow]:
        rows = self._query(
            self._LATEST
            + " SELECT * EXCLUDE rn FROM latest WHERE rn = 1"
            + " AND strategy_lane = ? ORDER BY context_space_id, relationship_id",
            [strategy_lane],
        )
        for row in rows:
            yield self._row_from_dict(row)
