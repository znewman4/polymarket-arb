"""Every table registered in ALL_SCHEMAS should have at least one DuckDB
view referencing it. Catches "added a schema, forgot a view" drift."""

from __future__ import annotations

from polymarket_arb.storage.parquet.schemas import ALL_SCHEMAS
from polymarket_arb.storage.views import VIEW_DEFINITIONS


def test_every_schema_has_a_view():
    missing: list[str] = []
    for table in ALL_SCHEMAS:
        if not any(f"normalised/{table}/" in sql for sql in VIEW_DEFINITIONS.values()):
            missing.append(table)
    assert not missing, f"tables without a DuckDB view: {missing}"
