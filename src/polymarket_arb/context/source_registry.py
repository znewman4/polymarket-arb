"""Load and validate context-space registry YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import COMPLETENESS_CLASSES, ContextRegistry, ContextSpace


def load_context_registry(path: Path | str) -> ContextRegistry:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    spaces_raw = raw.get("context_spaces") or {}
    spaces: dict[str, ContextSpace] = {}
    for key, data in spaces_raw.items():
        if not isinstance(data, dict):
            raise ValueError(f"context space {key!r} must be a mapping")
        merged = {"context_space_id": str(key), **data}
        space = ContextSpace.model_validate(merged)
        if space.context_space_id != str(key):
            raise ValueError(f"context_space_id mismatch for {key!r}")
        if space.completeness_class not in COMPLETENESS_CLASSES:
            raise ValueError(f"unknown completeness class for {key!r}")
        allowed = set(space.source_requirements.allowed_source_tiers)
        if not allowed or any(tier < 1 or tier > 4 for tier in allowed):
            raise ValueError(f"invalid source tier list for {key!r}")
        spaces[space.context_space_id] = space
    return ContextRegistry(context_spaces=spaces)


def registry_audit(path: Path | str) -> dict[str, object]:
    registry = load_context_registry(path)
    completeness_counts: dict[str, int] = {}
    human_review_required = 0
    for space in registry.context_spaces.values():
        completeness_counts[space.completeness_class] = (
            completeness_counts.get(space.completeness_class, 0) + 1
        )
        if space.human_review_required:
            human_review_required += 1
    return {
        "spaces": len(registry.context_spaces),
        "context_spaces": len(registry.context_spaces),
        "completeness_class_counts": completeness_counts,
        "completeness_counts": completeness_counts,
        "human_review_required": human_review_required,
        "space_ids": sorted(registry.context_spaces),
    }
