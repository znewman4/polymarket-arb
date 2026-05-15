"""Domain models for deterministic single-market implications."""

from __future__ import annotations

from typing import Literal

ImplicationType = Literal[
    "necessary_for_yes",
    "sufficient_for_yes",
    "necessary_for_no",
    "sufficient_for_no",
    "evidence_updates_yes",
    "evidence_updates_no",
    "ambiguity_warning",
]
