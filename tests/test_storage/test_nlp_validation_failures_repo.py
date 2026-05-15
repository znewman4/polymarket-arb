from __future__ import annotations

import time

from polymarket_arb.storage.base import NlpValidationFailureRow
from polymarket_arb.storage.parquet.nlp_validation_failures_repo import (
    ParquetNlpValidationFailuresRepository,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _row(market_id: str, kind: str = "invalid_json") -> NlpValidationFailureRow:
    return NlpValidationFailureRow(
        failure_id=f"f-{market_id}", market_id=market_id,
        model_name="mock", prompt_version="market_semantics_v1",
        prompt_hash="p" * 64, raw_response_hash="r" * 64,
        failure_kind=kind, validation_error_json='{"error":"x"}',
        attempted_ts_ms=_now_ms(), schema_version=1, ingested_ts_ms=_now_ms(),
    )


def test_append_and_recent(tmp_data_root):
    repo = ParquetNlpValidationFailuresRepository(tmp_data_root, row_group_size=4)
    assert repo.append_many([_row("m1"), _row("m2", "schema_violation")]) == 2
    rows = repo.recent(limit=10)
    assert len(rows) == 2
    kinds = sorted(r.failure_kind for r in rows)
    assert kinds == ["invalid_json", "schema_violation"]
    # Spot-check: no raw text field exists on the dataclass.
    assert not any(hasattr(r, "raw_text") for r in rows)


def test_empty_lake(tmp_data_root):
    repo = ParquetNlpValidationFailuresRepository(tmp_data_root)
    assert repo.recent() == []
