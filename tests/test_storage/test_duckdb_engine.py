from __future__ import annotations

import time

from polymarket_arb.storage.base import RiskSnapshot
from polymarket_arb.storage.duckdb_engine import DuckDBEngine
from polymarket_arb.storage.parquet.account_events import ParquetRiskSnapshotsRepository


def test_views_register_after_data_exists(tmp_data_root):
    repo = ParquetRiskSnapshotsRepository(tmp_data_root)
    repo.append(RiskSnapshot(
        event_id="e1", event_ts_ms=int(time.time() * 1000),
        strategy_id=None, order_id=None, overall="PASS",
        checks=[], schema_version=1, ingested_ts_ms=int(time.time() * 1000),
    ))
    engine = DuckDBEngine(tmp_data_root)
    con = engine.connect()
    rows = con.execute("SELECT overall FROM risk_snapshots_all").fetchall()
    assert rows == [("PASS",)]
    engine.close()


def test_engine_tolerates_empty_lake(tmp_data_root):
    # No parquet files yet — connect must not raise.
    engine = DuckDBEngine(tmp_data_root)
    con = engine.connect()
    assert con is not None
    engine.close()
