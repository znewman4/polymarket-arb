from __future__ import annotations

from polymarket_arb.live.models import PositionRow
from polymarket_arb.storage.parquet.positions_repo import ParquetPositionsRepository


def _position(position_id: str = "p1", *, open_ts_ms: int = 1) -> PositionRow:
    return PositionRow(
        position_id=position_id,
        strategy_id="relationship_diagnostic",
        market_id="m1",
        token_id="tok-1",
        side="buy",
        open_ts_ms=open_ts_ms,
        entry_price="0.42",
        size="10",
        notional_usdc="4.2",
        source_relationship_id="rel-1",
        notes="paper fill",
        status="open",
    )


def test_append_and_iter_recent_positions(tmp_data_root):
    repo = ParquetPositionsRepository(tmp_data_root, row_group_size=4)

    assert repo.append_many([_position("p1", open_ts_ms=1), _position("p2", open_ts_ms=2)]) == 2

    recent = list(repo.iter_recent())
    assert [row.position_id for row in recent] == ["p2", "p1"]
    assert recent[0].status == "open"
    assert recent[0].entry_price == "0.42"
