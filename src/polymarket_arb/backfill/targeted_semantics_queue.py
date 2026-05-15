"""Build targeted queues for terms-aware semantic extraction."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from ..storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)


@dataclass
class QueueItem:
    market_id: str
    question: str
    reasons: set[str] = field(default_factory=set)
    relationship_ids: set[str] = field(default_factory=set)
    relationship_types: set[str] = field(default_factory=set)


def build_targeted_semantics_queue(
    data_root: Path,
    *,
    output_path: Path | None = None,
) -> Path:
    """Write a CSV of markets worth rerunning with terms-aware semantics."""
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    sem_repo = ParquetMarketSemanticsRepository(data_root)
    items: dict[str, QueueItem] = {}

    for rel in rel_repo.iter_latest():
        reasons = _relationship_reasons(rel)
        if not reasons:
            continue
        _add_market(
            items,
            rel.market_id_a,
            rel.question_a,
            reasons,
            rel.relationship_id,
            rel.relationship_type,
        )
        _add_market(
            items,
            rel.market_id_b,
            rel.question_b,
            reasons,
            rel.relationship_id,
            rel.relationship_type,
        )

    output_path = output_path or (data_root / "backfill" / "targeted_semantics_queue_latest.csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "market_id",
            "question",
            "reasons",
            "relationship_ids",
            "relationship_types",
            "has_event_atoms_json",
            "has_proposition_json",
            "has_outcome_space_json",
            "prompt_version",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in sorted(items.values(), key=lambda i: (",".join(sorted(i.reasons)), i.market_id)):
            sem = sem_repo.get_latest(item.market_id)
            writer.writerow({
                "market_id": item.market_id,
                "question": item.question,
                "reasons": ";".join(sorted(item.reasons)),
                "relationship_ids": ";".join(sorted(item.relationship_ids)),
                "relationship_types": ";".join(sorted(item.relationship_types)),
                "has_event_atoms_json": bool(sem and sem.event_atoms_json),
                "has_proposition_json": bool(sem and sem.proposition_json),
                "has_outcome_space_json": bool(sem and sem.outcome_space_json),
                "prompt_version": sem.prompt_version if sem else "",
            })
    return output_path


def read_target_market_ids(queue_csv: Path) -> set[str]:
    if not queue_csv.exists():
        return set()
    with queue_csv.open(newline="", encoding="utf-8") as f:
        return {row["market_id"] for row in csv.DictReader(f) if row.get("market_id")}


def _relationship_reasons(rel: object) -> set[str]:
    relationship_type = getattr(rel, "relationship_type", "")
    validation_status = getattr(rel, "validation_status", "")
    question_a = getattr(rel, "question_a", "")
    question_b = getattr(rel, "question_b", "")
    final_confidence = float(getattr(rel, "final_confidence", 0.0) or 0.0)

    reasons: set[str] = set()
    if relationship_type == "mutually_exclusive_category" and validation_status == "accepted":
        reasons.add("accepted_category_group")
    if relationship_type in {"same_reference_clock", "temporal_before", "temporal_after", "inverse_temporal_order"}:
        reasons.add("same_reference_temporal_group")
    if _looks_threshold_like(question_a) or _looks_threshold_like(question_b):
        reasons.add("threshold_like_market")
    if validation_status == "needs_manual_review" or final_confidence >= 0.55:
        reasons.add("near_acceptance_relationship_candidate")
    return reasons


def _add_market(
    items: dict[str, QueueItem],
    market_id: str,
    question: str,
    reasons: set[str],
    relationship_id: str,
    relationship_type: str,
) -> None:
    item = items.setdefault(market_id, QueueItem(market_id=market_id, question=question))
    item.reasons.update(reasons)
    item.relationship_ids.add(relationship_id)
    item.relationship_types.add(relationship_type)


def _looks_threshold_like(question: str) -> bool:
    q = question.lower()
    return bool(
        re.search(r"(\$|>|<|\bunder\b|\bover\b|\bat least\b|\bmore than\b|\bless than\b|\bhit\b)", q)
    )
