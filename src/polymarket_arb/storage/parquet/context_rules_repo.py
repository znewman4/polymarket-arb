"""Parquet repository for structured context rules."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ..base import ContextRuleRow
from ._writer import write_table_part
from .schemas import CONTEXT_RULES_SCHEMA_V1

_TABLE = "context_rules"


class ParquetContextRulesRepository:
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

    def append(self, row: ContextRuleRow) -> None:
        self.append_many([row])

    def append_many(self, rows: Iterable[ContextRuleRow]) -> int:
        rows_list = [asdict(r) for r in rows]
        if not rows_list:
            return 0
        write_table_part(
            self._root,
            _TABLE,
            CONTEXT_RULES_SCHEMA_V1,
            rows_list,
            compression=self._compression,
            row_group_size=self._row_group_size,
        )
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def _row_from_dict(self, d: dict) -> ContextRuleRow:
        d = dict(d)
        d.pop("dt", None)
        names = {f.name for f in fields(ContextRuleRow)}
        return ContextRuleRow(**{k: v for k, v in d.items() if k in names})

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
        " SELECT *, row_number() OVER (PARTITION BY context_rule_id "
        " ORDER BY ingested_ts_ms DESC) AS rn"
        " FROM read_parquet('{glob}', hive_partitioning=true, union_by_name=true)"
        ")"
    )

    def get_latest(self, context_rule_id: str) -> ContextRuleRow | None:
        rows = self._query(
            self._LATEST
            + " SELECT * EXCLUDE rn FROM latest WHERE rn = 1"
            + " AND context_rule_id = ? LIMIT 1",
            [context_rule_id],
        )
        return self._row_from_dict(rows[0]) if rows else None

    def iter_latest(self) -> Iterator[ContextRuleRow]:
        rows = self._query(
            self._LATEST
            + " SELECT * EXCLUDE rn FROM latest WHERE rn = 1"
            + " ORDER BY context_space_id, rule_type, confidence DESC",
            [],
        )
        for row in rows:
            yield self._row_from_dict(row)

    def iter_for_space(self, context_space_id: str) -> Iterator[ContextRuleRow]:
        rows = self._query(
            self._LATEST
            + " SELECT * EXCLUDE rn FROM latest WHERE rn = 1"
            + " AND context_space_id = ? ORDER BY rule_type, confidence DESC",
            [context_space_id],
        )
        for row in rows:
            yield self._row_from_dict(row)

    def iter_for_rule_type(self, rule_type: str) -> Iterator[ContextRuleRow]:
        rows = self._query(
            self._LATEST
            + " SELECT * EXCLUDE rn FROM latest WHERE rn = 1"
            + " AND rule_type = ? ORDER BY context_space_id, confidence DESC",
            [rule_type],
        )
        for row in rows:
            yield self._row_from_dict(row)
