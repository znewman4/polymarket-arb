"""Internal dataclasses returned by the LLM/embedding compatibility layer.

These are NOT the raw Ollama response — the wrapper in ``ollama_compat.py``
normalises every supported endpoint shape into one of these shapes so
callers don't need to branch on ``/api/chat`` vs ``/api/generate``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LLMResponse:
    text: str                            # cleaned, post-<think>-strip
    model: str
    raw_response_hash: str               # sha256 of `text` (NOT pre-strip)
    duration_ms: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingResponse:
    vector: list[float]
    model: str
    dimensions: int
    raw_response_hash: str               # sha256 of vector bytes
    metadata: dict[str, Any] = field(default_factory=dict)
