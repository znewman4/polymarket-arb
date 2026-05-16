"""Deterministic relationship expansion passes.

Each expansion pass reads ingested market data and emits new relationship
candidates that were not discovered by the standard miner.  All emitted
relationships carry:

  generated_by          = "deterministic_expansion"
  expansion_pass_id     = "<pass_name>_v<version>"
  expansion_version     = <int>
  guard_results         = <JSON dict of which guards passed/failed>
  parse_evidence        = <JSON dict of extracted numeric/text fields>

These are encoded inside ``evidence_json`` on the RelationshipCandidateRow
so no schema changes are required.

Available passes
----------------
sports_ranking    — exact-position and top-N ladder implication / mutual exclusion
sports_progression — stage-ordering nesting (Phase C+)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExpansionResult:
    """Summary of one expansion pass run."""

    pass_id: str
    emitted_count: int
    skipped_existing: int = 0        # pairs that already exist in the store
    skipped_guard_fail: int = 0      # pairs that failed a deterministic guard
    needs_review_count: int = 0      # emitted but flagged for manual review
    guard_failure_counts: dict[str, int] = field(default_factory=dict)
    audit_rows: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = False
