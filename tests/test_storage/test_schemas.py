from __future__ import annotations

from datetime import datetime, timezone

import pytest

from polymarket_arb.storage.exceptions import SchemaMismatchError
from polymarket_arb.storage.parquet._writer import _COMPACT_THRESHOLD, write_table_part
from polymarket_arb.storage.parquet.schemas import RISK_SNAPSHOTS_SCHEMA_V1

_VALID_ROW = {
    "event_id": "e1",
    "event_ts_ms": 0,
    "strategy_id": None,
    "order_id": None,
    "overall": "PASS",
    "checks_json": "[]",
    "schema_version": 1,
    "ingested_ts_ms": 0,
}


def test_compaction_triggered_at_threshold(tmp_data_root):
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(_COMPACT_THRESHOLD + 1):
        write_table_part(
            tmp_data_root,
            "risk_snapshots",
            RISK_SNAPSHOTS_SCHEMA_V1,
            [{**_VALID_ROW, "event_id": f"e{i}"}],
            ts=ts,
        )
    partition_dir = tmp_data_root / "normalised" / "risk_snapshots" / "dt=2026-01-01"
    files = list(partition_dir.glob("*.parquet"))
    # After compaction there should be exactly one file, named compacted-*
    assert len(files) == 1
    assert files[0].name.startswith("compacted-")


def test_no_compaction_below_threshold(tmp_data_root):
    ts = datetime(2026, 1, 2, tzinfo=timezone.utc)
    for i in range(_COMPACT_THRESHOLD):
        write_table_part(
            tmp_data_root,
            "risk_snapshots",
            RISK_SNAPSHOTS_SCHEMA_V1,
            [{**_VALID_ROW, "event_id": f"e{i}"}],
            ts=ts,
        )
    partition_dir = tmp_data_root / "normalised" / "risk_snapshots" / "dt=2026-01-02"
    files = list(partition_dir.glob("*.parquet"))
    assert len(files) == _COMPACT_THRESHOLD
    assert all(f.name.startswith("part-") for f in files)


def test_invalid_row_raises_schema_mismatch(tmp_data_root):
    bad = [{
        "event_id": "e1",
        "event_ts_ms": "not-an-int",
        "strategy_id": None, "order_id": None, "overall": "PASS",
        "checks_json": "[]",
        "schema_version": 1, "ingested_ts_ms": 0,
    }]
    with pytest.raises(SchemaMismatchError):
        write_table_part(tmp_data_root, "risk_snapshots", RISK_SNAPSHOTS_SCHEMA_V1, bad)


def test_empty_rows_rejected(tmp_data_root):
    with pytest.raises(ValueError):
        write_table_part(tmp_data_root, "risk_snapshots", RISK_SNAPSHOTS_SCHEMA_V1, [])
