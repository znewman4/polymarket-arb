"""Append-only event-table repositories.

Phase 0 ships a working ``RiskSnapshotsRepository`` (used by every CLI
command). Order/fill/position repos have working impls too — they will only
be exercised in Phase 7 when paper trading begins, but they're available now
so unit tests can validate the parquet contract.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

import duckdb

from ..base import (
    FillEvent,
    OrderEvent,
    PositionSnapshot,
    RiskSnapshot,
)
from ._writer import write_table_part
from .schemas import (
    FILL_EVENTS_SCHEMA_V1,
    ORDER_EVENTS_SCHEMA_V1,
    POSITION_SNAPSHOTS_SCHEMA_V1,
    RISK_SNAPSHOTS_SCHEMA_V1,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _glob(data_root: Path, table: str) -> str:
    return str(data_root / "normalised" / table / "dt=*" / "*.parquet")


# Hive partitioning leaks a synthesised ``dt`` column when reading parquet
# globs; strip it before splatting rows back into our dataclasses.
def _strip_partition_cols(row: dict) -> dict:
    row.pop("dt", None)
    return row


# ─── Risk snapshots (Phase 0 — actually written) ───────────────────────────


class ParquetRiskSnapshotsRepository:
    def __init__(self, data_root: Path, *, compression: str = "zstd",
                 row_group_size: int = 50_000) -> None:
        self._root = data_root
        self._compression = compression
        self._row_group_size = row_group_size

    def append(self, snap: RiskSnapshot) -> None:
        self.append_many([snap])

    def append_many(self, snaps: Iterable[RiskSnapshot]) -> int:
        rows: list[dict] = []
        for s in snaps:
            d = asdict(s)
            checks = d.pop("checks", [])
            d["checks_json"] = json.dumps(checks)
            d["ingested_ts_ms"] = d.get("ingested_ts_ms") or _now_ms()
            rows.append(d)
        if not rows:
            return 0
        write_table_part(
            self._root, "risk_snapshots", RISK_SNAPSHOTS_SCHEMA_V1, rows,
            compression=self._compression, row_group_size=self._row_group_size,
        )
        return len(rows)

    def recent(self, limit: int = 50) -> list[RiskSnapshot]:
        glob = _glob(self._root, "risk_snapshots")
        if not list((self._root / "normalised" / "risk_snapshots").glob("dt=*/*.parquet")):
            return []
        con = duckdb.connect()
        try:
            rows = con.execute(
                f"SELECT * FROM read_parquet('{glob}') "
                f"ORDER BY event_ts_ms DESC LIMIT ?",
                [limit],
            ).fetchall()
            cols = [c[0] for c in con.description]
        finally:
            con.close()
        out: list[RiskSnapshot] = []
        for r in rows:
            d = _strip_partition_cols(dict(zip(cols, r, strict=False)))
            checks = json.loads(d.pop("checks_json") or "[]")
            out.append(RiskSnapshot(checks=checks, **d))
        return out


# ─── Order / fill / position event tables (interfaces real, callers later) ──


class ParquetOrderEventsRepository:
    def __init__(self, data_root: Path, *, compression: str = "zstd",
                 row_group_size: int = 50_000) -> None:
        self._root = data_root
        self._compression = compression
        self._row_group_size = row_group_size

    def _to_row(self, e: OrderEvent) -> dict:
        d = asdict(e)
        payload = d.pop("payload", {}) or {}
        d["payload_json"] = json.dumps(payload, default=str)
        d["ingested_ts_ms"] = d.get("ingested_ts_ms") or _now_ms()
        return d

    def append(self, event: OrderEvent) -> None:
        self.append_many([event])

    def append_many(self, events: Iterable[OrderEvent]) -> int:
        rows = [self._to_row(e) for e in events]
        if not rows:
            return 0
        write_table_part(self._root, "order_events", ORDER_EVENTS_SCHEMA_V1, rows,
                         compression=self._compression, row_group_size=self._row_group_size)
        return len(rows)

    def for_order(self, order_id: str) -> list[OrderEvent]:
        if not list((self._root / "normalised" / "order_events").glob("dt=*/*.parquet")):
            return []
        con = duckdb.connect()
        try:
            rows = con.execute(
                f"SELECT * FROM read_parquet('{_glob(self._root, 'order_events')}') "
                f"WHERE order_id = ? ORDER BY event_ts_ms",
                [order_id],
            ).fetchall()
            cols = [c[0] for c in con.description]
        finally:
            con.close()
        out: list[OrderEvent] = []
        for r in rows:
            d = _strip_partition_cols(dict(zip(cols, r, strict=False)))
            payload = json.loads(d.pop("payload_json") or "{}")
            out.append(OrderEvent(payload=payload, **d))
        return out


class ParquetFillEventsRepository:
    def __init__(self, data_root: Path, *, compression: str = "zstd",
                 row_group_size: int = 50_000) -> None:
        self._root = data_root
        self._compression = compression
        self._row_group_size = row_group_size

    def _to_row(self, e: FillEvent) -> dict:
        d = asdict(e)
        payload = d.pop("payload", {}) or {}
        d["payload_json"] = json.dumps(payload, default=str)
        d["ingested_ts_ms"] = d.get("ingested_ts_ms") or _now_ms()
        return d

    def append(self, event: FillEvent) -> None:
        self.append_many([event])

    def append_many(self, events: Iterable[FillEvent]) -> int:
        rows = [self._to_row(e) for e in events]
        if not rows:
            return 0
        write_table_part(self._root, "fill_events", FILL_EVENTS_SCHEMA_V1, rows,
                         compression=self._compression, row_group_size=self._row_group_size)
        return len(rows)

    def for_order(self, order_id: str) -> list[FillEvent]:
        if not list((self._root / "normalised" / "fill_events").glob("dt=*/*.parquet")):
            return []
        con = duckdb.connect()
        try:
            rows = con.execute(
                f"SELECT * FROM read_parquet('{_glob(self._root, 'fill_events')}') "
                f"WHERE order_id = ? ORDER BY event_ts_ms",
                [order_id],
            ).fetchall()
            cols = [c[0] for c in con.description]
        finally:
            con.close()
        out: list[FillEvent] = []
        for r in rows:
            d = _strip_partition_cols(dict(zip(cols, r, strict=False)))
            payload = json.loads(d.pop("payload_json") or "{}")
            out.append(FillEvent(payload=payload, **d))
        return out


class ParquetPositionSnapshotsRepository:
    def __init__(self, data_root: Path, *, compression: str = "zstd",
                 row_group_size: int = 50_000) -> None:
        self._root = data_root
        self._compression = compression
        self._row_group_size = row_group_size

    def append(self, snap: PositionSnapshot) -> None:
        self.append_many([snap])

    def append_many(self, snaps: Iterable[PositionSnapshot]) -> int:
        rows = [
            {**asdict(s), "ingested_ts_ms": s.ingested_ts_ms or _now_ms()} for s in snaps
        ]
        if not rows:
            return 0
        write_table_part(self._root, "position_snapshots", POSITION_SNAPSHOTS_SCHEMA_V1, rows,
                         compression=self._compression, row_group_size=self._row_group_size)
        return len(rows)

    def latest_for(self, token_id: str) -> PositionSnapshot | None:
        if not list((self._root / "normalised" / "position_snapshots").glob("dt=*/*.parquet")):
            return None
        con = duckdb.connect()
        try:
            row = con.execute(
                f"SELECT * FROM read_parquet('{_glob(self._root, 'position_snapshots')}') "
                f"WHERE token_id = ? ORDER BY event_ts_ms DESC LIMIT 1",
                [token_id],
            ).fetchone()
            cols = [c[0] for c in con.description]
        finally:
            con.close()
        if row is None:
            return None
        return PositionSnapshot(**_strip_partition_cols(dict(zip(cols, row, strict=False))))
