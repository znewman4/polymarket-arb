"""Parquet impl of RelationshipCandidatesRepository."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ..base import RelationshipCandidateRow
from ._writer import write_table_part
from .schemas import RELATIONSHIP_CANDIDATES_SCHEMA_V2

_TABLE = "relationship_candidates"


class ParquetRelationshipCandidatesRepository:
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

    def append(self, row: RelationshipCandidateRow) -> None:
        self.append_many([row])

    def append_many(self, rows: Iterable[RelationshipCandidateRow]) -> int:
        rows_list = [asdict(r) for r in rows]
        if not rows_list:
            return 0
        write_table_part(
            self._root,
            _TABLE,
            RELATIONSHIP_CANDIDATES_SCHEMA_V2,
            rows_list,
            compression=self._compression,
            row_group_size=self._row_group_size,
        )
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def _row_from_dict(self, d: dict) -> RelationshipCandidateRow:
        d = dict(d)
        d.pop("dt", None)
        names = {f.name for f in fields(RelationshipCandidateRow)}
        return RelationshipCandidateRow(**{k: v for k, v in d.items() if k in names})

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
        "  SELECT *, row_number() OVER (PARTITION BY relationship_id "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{glob}', hive_partitioning=true, union_by_name=true)"
        ")"
    )

    def get_latest(self, relationship_id: str) -> RelationshipCandidateRow | None:
        rows = self._query(
            self._LATEST +
            " SELECT * EXCLUDE rn FROM latest WHERE rn = 1"
            " AND relationship_id = ? LIMIT 1",
            [relationship_id],
        )
        return self._row_from_dict(rows[0]) if rows else None

    def iter_latest(self) -> Iterator[RelationshipCandidateRow]:
        rows = self._query(
            self._LATEST +
            " SELECT * EXCLUDE rn FROM latest WHERE rn = 1"
            " ORDER BY final_confidence DESC",
            [],
        )
        for r in rows:
            yield self._row_from_dict(r)

    def iter_accepted(self) -> Iterator[RelationshipCandidateRow]:
        rows = self._query(
            self._LATEST +
            " SELECT * EXCLUDE rn FROM latest WHERE rn = 1"
            " AND validation_status = 'accepted'"
            " ORDER BY final_confidence DESC",
            [],
        )
        for r in rows:
            yield self._row_from_dict(r)
