"""Parquet impl for latest CLOB best quotes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ..base import BestQuote
from ._writer import write_table_part
from .schemas import BEST_QUOTES_SCHEMA_V1

_TABLE = "best_quotes"


class ParquetBestQuotesRepository:
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

    def append(self, quote: BestQuote) -> None:
        self.append_many([quote])

    def append_many(self, quotes: Iterable[BestQuote]) -> int:
        rows = [asdict(q) for q in quotes]
        if not rows:
            return 0
        write_table_part(
            self._root,
            _TABLE,
            BEST_QUOTES_SCHEMA_V1,
            rows,
            compression=self._compression,
            row_group_size=self._row_group_size,
        )
        return len(rows)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def latest(self, token_id: str) -> BestQuote | None:
        if not self._has_data():
            return None
        con = duckdb.connect()
        try:
            cur = con.execute(
                f"SELECT * FROM read_parquet('{self._glob()}', hive_partitioning=true) "
                "WHERE token_id = ? ORDER BY timestamp_ms DESC, ingested_ts_ms DESC LIMIT 1",
                [token_id],
            )
            cols = [c[0] for c in cur.description]
            row = cur.fetchone()
        finally:
            con.close()
        if row is None:
            return None
        d = dict(zip(cols, row, strict=False))
        d.pop("dt", None)
        names = {f.name for f in fields(BestQuote)}
        return BestQuote(**{k: v for k, v in d.items() if k in names})
