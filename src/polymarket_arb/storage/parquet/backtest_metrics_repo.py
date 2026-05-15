"""Parquet impl of BacktestMetricsRepository."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, fields
from decimal import Decimal
from pathlib import Path

import duckdb

from ..base import BacktestMetricsRow
from ._writer import write_table_part
from .schemas import BACKTEST_METRICS_SCHEMA_V1

_TABLE = "backtest_metrics"

_DECIMAL_FIELDS = (
    "starting_cash_usdc", "ending_cash_usdc", "ending_equity_usdc",
    "gross_pnl_usdc", "net_pnl_usdc", "total_fees_usdc", "total_slippage_usdc",
    "null_baseline_pnl_usdc",
)


class ParquetBacktestMetricsRepository:
    def __init__(self, data_root: Path, *, compression: str = "zstd", row_group_size: int = 50_000) -> None:
        self._root = data_root
        self._compression = compression
        self._row_group_size = row_group_size

    def append(self, row: BacktestMetricsRow) -> None:
        self.append_many([row])

    def append_many(self, rows: Iterable[BacktestMetricsRow]) -> int:
        rows_list = [asdict(r) for r in rows]
        if not rows_list:
            return 0
        write_table_part(self._root, _TABLE, BACKTEST_METRICS_SCHEMA_V1, rows_list,
                         compression=self._compression, row_group_size=self._row_group_size)
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def _row_from_dict(self, d: dict) -> BacktestMetricsRow:
        d = dict(d)
        d.pop("dt", None)
        names = {f.name for f in fields(BacktestMetricsRow)}
        filtered = {k: v for k, v in d.items() if k in names}
        for fld in _DECIMAL_FIELDS:
            if fld in filtered and filtered[fld] is not None:
                filtered[fld] = Decimal(str(filtered[fld]))
        return BacktestMetricsRow(**filtered)

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

    def get_latest_for_run(self, run_id: str) -> BacktestMetricsRow | None:
        rows = self._query(
            "SELECT * FROM read_parquet('{glob}', hive_partitioning=true)"
            " WHERE run_id = ?"
            " ORDER BY ingested_ts_ms DESC LIMIT 1",
            [run_id],
        )
        return self._row_from_dict(rows[0]) if rows else None
