from __future__ import annotations

import time
from decimal import Decimal

from polymarket_arb.storage.base import (
    FillEvent,
    OrderEvent,
    PositionSnapshot,
    RiskSnapshot,
)
from polymarket_arb.storage.parquet.account_events import (
    ParquetFillEventsRepository,
    ParquetOrderEventsRepository,
    ParquetPositionSnapshotsRepository,
    ParquetRiskSnapshotsRepository,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def test_risk_snapshot_round_trip(tmp_data_root):
    repo = ParquetRiskSnapshotsRepository(tmp_data_root, row_group_size=4)
    snap = RiskSnapshot(
        event_id="e1", event_ts_ms=_now_ms(),
        strategy_id=None, order_id=None, overall="FAIL",
        checks=[{"name": "orders_allowed_flag", "status": "FAIL",
                 "reason": "orders_allowed=false", "detail": {}}],
        schema_version=1, ingested_ts_ms=_now_ms(),
    )
    repo.append(snap)
    recent = repo.recent(limit=5)
    assert len(recent) == 1
    assert recent[0].overall == "FAIL"
    assert recent[0].checks[0]["name"] == "orders_allowed_flag"


def test_order_event_round_trip(tmp_data_root):
    repo = ParquetOrderEventsRepository(tmp_data_root, row_group_size=4)
    e = OrderEvent(
        event_id="ev1", event_ts_ms=_now_ms(),
        order_id="ord1", strategy_id="strat1", kind="create",
        token_id="tok1", side="buy",
        price=Decimal("0.4123"), size=Decimal("100.0"),
        payload={"note": "test"},
        schema_version=1, ingested_ts_ms=_now_ms(),
    )
    repo.append(e)
    fetched = repo.for_order("ord1")
    assert len(fetched) == 1
    assert fetched[0].price == Decimal("0.4123")
    assert fetched[0].payload == {"note": "test"}


def test_fill_event_round_trip(tmp_data_root):
    repo = ParquetFillEventsRepository(tmp_data_root, row_group_size=4)
    e = FillEvent(
        event_id="f1", event_ts_ms=_now_ms(),
        order_id="ord1", fill_id="fill1",
        token_id="tok1", side="buy",
        price=Decimal("0.4123"), size=Decimal("50.0"), fee=Decimal("0.0"),
        payload={},
        schema_version=1, ingested_ts_ms=_now_ms(),
    )
    repo.append(e)
    fetched = repo.for_order("ord1")
    assert len(fetched) == 1
    assert fetched[0].size == Decimal("50.0")


def test_position_snapshot_latest(tmp_data_root):
    repo = ParquetPositionSnapshotsRepository(tmp_data_root, row_group_size=4)
    older = PositionSnapshot(
        event_id="p1", event_ts_ms=1_000,
        token_id="tok1", quantity=Decimal("10"), avg_entry_price=Decimal("0.4"),
        realised_pnl=Decimal("0"), unrealised_pnl=Decimal("0"),
        schema_version=1, ingested_ts_ms=_now_ms(),
    )
    newer = PositionSnapshot(
        event_id="p2", event_ts_ms=2_000,
        token_id="tok1", quantity=Decimal("20"), avg_entry_price=Decimal("0.42"),
        realised_pnl=Decimal("0"), unrealised_pnl=Decimal("0.4"),
        schema_version=1, ingested_ts_ms=_now_ms(),
    )
    repo.append(older)
    repo.append(newer)
    latest = repo.latest_for("tok1")
    assert latest is not None
    assert latest.quantity == Decimal("20")


def test_append_only_no_overwrite(tmp_data_root):
    """Two appends create two distinct part-files; neither is overwritten."""
    repo = ParquetRiskSnapshotsRepository(tmp_data_root, row_group_size=4)
    for i in range(3):
        repo.append(RiskSnapshot(
            event_id=f"e{i}", event_ts_ms=_now_ms() + i,
            strategy_id=None, order_id=None, overall="PASS",
            checks=[], schema_version=1, ingested_ts_ms=_now_ms(),
        ))
    files = list((tmp_data_root / "normalised" / "risk_snapshots").rglob("*.parquet"))
    assert len(files) == 3
    assert len(repo.recent(limit=10)) == 3
