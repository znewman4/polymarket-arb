"""Parquet impl for CLOB orderbook snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import duckdb

from ..base import OrderbookLevel, OrderbookSnapshot
from ._writer import write_table_part
from .schemas import ORDERBOOK_SNAPSHOTS_SCHEMA_V1

_TABLE = "orderbook_snapshots"


class ParquetOrderbookRepository:
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

    def append_snapshot(self, snap: OrderbookSnapshot) -> None:
        self.append_snapshots([snap])

    def append_snapshots(self, snaps: Iterable[OrderbookSnapshot]) -> int:
        rows = [asdict(s) for s in snaps]
        if not rows:
            return 0
        write_table_part(
            self._root,
            _TABLE,
            ORDERBOOK_SNAPSHOTS_SCHEMA_V1,
            rows,
            compression=self._compression,
            row_group_size=self._row_group_size,
        )
        return len(rows)

    def _glob(self) -> str:
        return str(self._root / "normalised" / _TABLE / "dt=*" / "*.parquet")

    def _has_data(self) -> bool:
        return any((self._root / "normalised" / _TABLE).glob("dt=*/*.parquet"))

    def latest_book(self, token_id: str) -> OrderbookSnapshot | None:
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
        return _row(dict(zip(cols, row, strict=False))) if row else None


def _row(d: dict) -> OrderbookSnapshot:
    d.pop("dt", None)
    return OrderbookSnapshot(
        token_id=d["token_id"],
        condition_id=d.get("condition_id"),
        market_slug=d.get("market_slug"),
        timestamp_ms=d["timestamp_ms"],
        bids=[_level(x) for x in d.get("bids") or []],
        asks=[_level(x) for x in d.get("asks") or []],
        book_hash=d.get("book_hash"),
        source=d["source"],
        schema_version=d["schema_version"],
        ingested_ts_ms=d["ingested_ts_ms"],
    )


def _level(x: dict) -> OrderbookLevel:
    return OrderbookLevel(price=Decimal(str(x["price"])), size=Decimal(str(x["size"])))
