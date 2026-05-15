"""Fail-closed YAML rulebook loading."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import ValidationError

from .rulebook_models import (
    AmbiguityRulebook,
    ContradictionRulebook,
    EvidenceFusionRulebook,
    ImplicationRulebook,
    RelationshipRulebook,
    RulebookBase,
)

RulebookT = TypeVar("RulebookT", bound=RulebookBase)

_MODEL_BY_KIND: dict[str, type[RulebookBase]] = {
    "ambiguity": AmbiguityRulebook,
    "implication": ImplicationRulebook,
    "contradiction": ContradictionRulebook,
    "evidence_fusion": EvidenceFusionRulebook,
    "relationship": RelationshipRulebook,
}


class RulebookError(RuntimeError):
    """Raised when a rulebook cannot be loaded or validated."""


def load_rulebook(path: Path, *, kind: str) -> RulebookBase:
    if kind not in _MODEL_BY_KIND:
        raise RulebookError(f"unknown rulebook kind {kind!r}")
    if not path.exists():
        raise RulebookError(f"rulebook not found: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RulebookError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise RulebookError(f"rulebook {path} must be a mapping")
    model = _MODEL_BY_KIND[kind]
    try:
        return model.model_validate(loaded)
    except ValidationError as exc:
        raise RulebookError(f"invalid {kind} rulebook {path}: {exc}") from exc


def rulebook_content_hash(path: Path) -> str:
    if not path.exists():
        raise RulebookError(f"rulebook not found: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rulebook_path(config_dir: Path, filename: str) -> Path:
    p = Path(filename)
    return p if p.is_absolute() else config_dir / "semantic_rules" / p
