"""Parquet impl of StrategyCandidatesRepository."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, fields
from decimal import Decimal
from pathlib import Path

import duckdb

from ..base import StrategyCandidateRow
from ._writer import write_table_part
from .schemas import STRATEGY_CANDIDATES_SCHEMA_V1

_TABLE = "strategy_candidates"


class ParquetStrategyCandidatesRepository:
    def __init__(self, data_root: Path, *, compression: str = "zstd", row_group_size: int = 50_000) -> None:
        self._root = data_root
        self._compression = compression
        self._row_group_size = row_group_size

    def append(self, row: StrategyCandidateRow) -> None:
        self.append_many([row])

    def append_many(self, rows: Iterable[StrategyCandidateRow]) -> int:
        rows_list = [asdict(r) for r in rows]
        if not rows_list:
            return 0
        write_table_part(self._root, _TABLE, STRATEGY_CANDIDATES_SCHEMA_V1, rows_list,
                         compression=self._compression, row_group_size=self._row_group_size)
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def _row_from_dict(self, d: dict) -> StrategyCandidateRow:
        d = dict(d)
        d.pop("dt", None)
        names = {f.name for f in fields(StrategyCandidateRow)}
        filtered = {k: v for k, v in d.items() if k in names}
        for decimal_field in ("price_a", "price_b", "theoretical_edge", "gross_edge",
                              "estimated_fee", "estimated_slippage", "net_edge_after_costs",
                              "stake_usdc"):
            if decimal_field in filtered and filtered[decimal_field] is not None:
                filtered[decimal_field] = Decimal(str(filtered[decimal_field]))
        return StrategyCandidateRow(**filtered)

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

    def iter_for_run(self, run_id: str) -> Iterator[StrategyCandidateRow]:
        rows = self._query(
            "SELECT * FROM read_parquet('{glob}', hive_partitioning=true)"
            " WHERE run_id = ? ORDER BY signal_ts_ms",
            [run_id],
        )
        for r in rows:
            yield self._row_from_dict(r)
