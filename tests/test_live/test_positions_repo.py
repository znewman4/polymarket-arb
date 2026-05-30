"""Tests for ParquetPositionsRepository.iter_open()."""

from __future__ import annotations

import time

from polymarket_arb.live.models import PositionRow
from polymarket_arb.storage.parquet.positions_repo import ParquetPositionsRepository


def _row(position_id: str, status: str, ts: int | None = None) -> PositionRow:
    ts = ts or int(time.time() * 1000)
    return PositionRow(
        position_id=position_id,
        strategy_id="limitless_arb",
        market_id="mkt",
        token_id="tok",
        side="buy",
        open_ts_ms=ts,
        entry_price="0.50",
        size="1.0",
        notional_usdc="1.0",
        gross_edge="0.05",
        relationship_id=position_id,
        relationship_type="limitless_poly_arb",
        notes="",
        status=status,
        schema_version=1,
        ingested_ts_ms=ts,
    )


def test_iter_open_returns_only_open_positions(tmp_data_root) -> None:
    repo = ParquetPositionsRepository(tmp_data_root)
    repo.append(_row("pos-a", "open", ts=1000))
    repo.append(_row("pos-b", "open", ts=2000))
    repo.append(_row("pos-b", "closed", ts=3000))

    result = list(repo.iter_open())

    assert {row.position_id for row in result} == {"pos-a"}


def test_iter_open_empty_when_no_data(tmp_data_root) -> None:
    repo = ParquetPositionsRepository(tmp_data_root)

    assert list(repo.iter_open()) == []


def test_iter_open_all_open(tmp_data_root) -> None:
    repo = ParquetPositionsRepository(tmp_data_root)
    repo.append(_row("pos-x", "open", ts=1000))
    repo.append(_row("pos-y", "open", ts=2000))

    result = list(repo.iter_open())

    assert len(result) == 2
