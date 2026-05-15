"""Manual context rule import/review workflow."""

from __future__ import annotations

import csv
from pathlib import Path

from polymarket_arb.context.manual_rules import (
    export_review_queue,
    import_manual_rules,
    import_review_queue,
)
from polymarket_arb.storage.parquet.context_rules_repo import ParquetContextRulesRepository

MANUAL_RULES = Path("configs/context_spaces/manual_rules_v1.yaml")


def test_manual_yaml_imports_rules(tmp_data_root):
    result = import_manual_rules(tmp_data_root, MANUAL_RULES)
    assert result["rules"] >= 5

    rules = list(ParquetContextRulesRepository(tmp_data_root).iter_latest())
    assert any(r.rule_type == "championship_implies_conference" for r in rules)
    assert all("rule_family" in r.rule_json for r in rules)


def test_review_export_import_appends_approved_version(tmp_data_root):
    pending_rules = tmp_data_root / "pending_manual_rules.yaml"
    pending_rules.write_text(
        MANUAL_RULES.read_text(encoding="utf-8").replace(
            "human_review_status: approved",
            "human_review_status: pending",
        ),
        encoding="utf-8",
    )
    import_manual_rules(tmp_data_root, pending_rules)
    review_csv = tmp_data_root / "context" / "manual_review_queue.csv"
    exported = export_review_queue(tmp_data_root, review_csv)
    assert exported["exported"] > 0

    rows = list(csv.DictReader(review_csv.open(encoding="utf-8")))
    rows[0]["human_review_status"] = "approved"
    rows[0]["human_review_notes"] = "approved in fixture"
    with review_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    imported = import_review_queue(tmp_data_root, review_csv)
    assert imported["imported"] == 1
    latest = ParquetContextRulesRepository(tmp_data_root).get_latest(rows[0]["context_rule_id"])
    assert latest is not None
    assert latest.human_review_status == "approved"
    assert latest.human_review_notes == "approved in fixture"
