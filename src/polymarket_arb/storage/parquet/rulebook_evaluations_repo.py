"""Parquet impl for append-only rulebook evaluations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ..base import RulebookEvaluationRow
from ._writer import write_table_part
from .schemas import RULEBOOK_EVALUATIONS_SCHEMA_V1

_TABLE = "rulebook_evaluations"


class ParquetRulebookEvaluationsRepository:
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

    def append(self, row: RulebookEvaluationRow) -> None:
        self.append_many([row])

    def append_many(self, rows: Iterable[RulebookEvaluationRow]) -> int:
        rows_list = list(rows)
        if not rows_list:
            return 0
        write_table_part(
            self._root,
            _TABLE,
            RULEBOOK_EVALUATIONS_SCHEMA_V1,
            [asdict(r) for r in rows_list],
            compression=self._compression,
            row_group_size=self._row_group_size,
        )
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def _row_from_dict(self, d: dict) -> RulebookEvaluationRow:
        d = dict(d)
        d.pop("dt", None)
        names = {f.name for f in fields(RulebookEvaluationRow)}
        return RulebookEvaluationRow(**{k: v for k, v in d.items() if k in names})

    def recent(self, limit: int = 50) -> list[RulebookEvaluationRow]:
        if not self._has_data():
            return []
        con = duckdb.connect()
        try:
            cur = con.execute(
                f"SELECT * FROM read_parquet('{self._glob()}', hive_partitioning=true) "
                "ORDER BY evaluated_ts_ms DESC LIMIT ?",
                [limit],
            )
            cols = [c[0] for c in cur.description]
            rows = cur.fetchall()
        finally:
            con.close()
        return [self._row_from_dict(dict(zip(cols, r, strict=False))) for r in rows]
