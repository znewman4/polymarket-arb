"""Parquet impl of ``NlpValidationFailuresRepository``.

Stores hashes + structured error JSON only — never raw model text. The
``raw_response_hash`` lets us correlate failures with prompt version
without retaining the underlying text in durable storage.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from ..base import NlpValidationFailureRow
from ._writer import write_table_part
from .schemas import NLP_VALIDATION_FAILURES_SCHEMA_V1

_TABLE = "nlp_validation_failures"


class ParquetNlpValidationFailuresRepository:
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

    def append(self, row: NlpValidationFailureRow) -> None:
        self.append_many([row])

    def append_many(self, rows: Iterable[NlpValidationFailureRow]) -> int:
        rows_list = list(rows)
        if not rows_list:
            return 0
        write_table_part(
            self._root, _TABLE, NLP_VALIDATION_FAILURES_SCHEMA_V1,
            [asdict(r) for r in rows_list],
            compression=self._compression, row_group_size=self._row_group_size,
        )
        return len(rows_list)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def recent(self, limit: int = 50) -> list[NlpValidationFailureRow]:
        if not self._has_data():
            return []
        con = duckdb.connect()
        try:
            cur = con.execute(
                f"SELECT * FROM read_parquet('{self._glob()}', hive_partitioning=true) "
                f"ORDER BY attempted_ts_ms DESC LIMIT ?",
                [limit],
            )
            cols = [c[0] for c in cur.description]
            rows = cur.fetchall()
        finally:
            con.close()
        out: list[NlpValidationFailureRow] = []
        names = {f.name for f in fields(NlpValidationFailureRow)}
        for r in rows:
            d = dict(zip(cols, r, strict=False))
            d.pop("dt", None)
            out.append(NlpValidationFailureRow(**{k: v for k, v in d.items() if k in names}))
        return out
