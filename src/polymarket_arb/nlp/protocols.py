"""LLM / embedding Protocol surface.

Strategies and the extractor depend on these Protocols, never on a concrete
client. Tests inject ``MockLLMClient`` / ``MockEmbeddingClient`` to keep
unit tests fully offline.
"""

from __future__ import annotations

from typing import Protocol

from .responses import EmbeddingResponse, LLMResponse


class LLMClient(Protocol):
    """Issue one structured-output call and return cleaned text."""

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        prompt_version: str,
    ) -> LLMResponse: ...


class EmbeddingClient(Protocol):
    """Embed a single text into a fixed-dimensional vector."""

    async def embed_text(self, *, text: str) -> EmbeddingResponse: ...


class LLMOutputError(RuntimeError):
    """Raised when the model output cannot be parsed/validated. The caller
    is responsible for persisting an ``NlpValidationFailureRow``; the raw
    response text is *never* persisted, only its sha256."""
