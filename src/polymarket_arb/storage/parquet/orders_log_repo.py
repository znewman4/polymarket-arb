"""Parquet impl for the live-agent ``orders_log`` table.

Every ``OrderClient.place_order`` call writes one ``OrdersLogRow`` to this
table, regardless of outcome (paper-fill, paper-no-book, kill-switch reject,
preflight reject, live submit, live failure).  This is the persistent audit
trail used by the dashboard and post-hoc analysis.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ...live.models import OrdersLogRow
from ._writer import write_table_part
from .schemas import ORDERS_LOG_SCHEMA_V1

_TABLE = "orders_log"


class ParquetOrdersLogRepository:
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

    def append(self, row: OrdersLogRow) -> None:
        self.append_many([row])

    def append_many(self, rows: Iterable[OrdersLogRow]) -> int:
        rows_list = [asdict(r) for r in rows]
        if not rows_list:
            return 0
        write_table_part(
            self._root, _TABLE, ORDERS_LOG_SCHEMA_V1, rows_list,
            compression=self._compression, row_group_size=self._row_group_size,
        )
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def iter_recent(self, limit: int = 1000) -> Iterator[OrdersLogRow]:
        if not self._has_data():
            return iter(())
        con = duckdb.connect()
        try:
            cur = con.execute(
                f"SELECT * FROM read_parquet('{self._glob()}', hive_partitioning=true) "
                f"ORDER BY ts_ms DESC LIMIT {int(limit)}"
            )
            cols = [c[0] for c in cur.description]
            rows = cur.fetchall()
        finally:
            con.close()
        names = {f.name for f in fields(OrdersLogRow)}
        out = []
        for raw in rows:
            d = dict(zip(cols, raw, strict=False))
            d.pop("dt", None)
            filtered = {k: v for k, v in d.items() if k in names}
            out.append(OrdersLogRow(**filtered))
        return iter(out)
