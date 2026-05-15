"""Local evidence helpers for context snapshots."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def excerpt(text: str, *, max_chars: int = 280) -> str:
    cleaned = clean_text(text)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def evidence_path(data_root: Path, context_space_id: str, filename: str) -> Path:
    safe_space = re.sub(r"[^a-zA-Z0-9_.-]+", "_", context_space_id)
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", filename)
    return data_root / "raw" / "context" / safe_space / safe_name
