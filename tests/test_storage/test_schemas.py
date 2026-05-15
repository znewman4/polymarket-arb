from __future__ import annotations

import pytest

from polymarket_arb.storage.exceptions import SchemaMismatchError
from polymarket_arb.storage.parquet._writer import write_table_part
from polymarket_arb.storage.parquet.schemas import RISK_SNAPSHOTS_SCHEMA_V1


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
