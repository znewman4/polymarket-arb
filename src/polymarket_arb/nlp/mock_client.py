"""Deterministic in-process LLM/embedding clients for tests.

``MockLLMClient`` returns a canned ``LLMResponse`` whose ``text`` is taken
from a callable supplied at construction time, so each test can craft the
exact JSON shape it wants to assert against.

``MockEmbeddingClient`` returns deterministic vectors based on a stable
hash of the input text — same input always gives the same vector,
different inputs almost certainly differ.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable

from .responses import EmbeddingResponse, LLMResponse
from .thinking_filter import strip_thinking


class MockLLMClient:
    def __init__(
        self,
        *,
        responder: Callable[[str, str, str], str],
        model: str = "mock-llm",
    ) -> None:
        self._responder = responder
        self._model = model

    async def complete_json(
        self, *, system: str, user: str, prompt_version: str,
    ) -> LLMResponse:
        text = strip_thinking(self._responder(system, user, prompt_version))
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return LLMResponse(
            text=text, model=self._model,
            raw_response_hash=h, duration_ms=1, metadata={"source": "mock"},
        )


class MockEmbeddingClient:
    def __init__(self, *, dimensions: int = 8, model: str = "mock-embed") -> None:
        self._dim = dimensions
        self._model = model

    async def embed_text(self, *, text: str) -> EmbeddingResponse:
        vec = self._deterministic_vector(text)
        h = hashlib.sha256(struct.pack(f"{len(vec)}f", *vec)).hexdigest()
        return EmbeddingResponse(
            vector=vec, model=self._model, dimensions=self._dim,
            raw_response_hash=h, metadata={"source": "mock"},
        )

    def _deterministic_vector(self, text: str) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        # Tile/truncate the digest to ``dimensions`` floats in [-1, 1).
        out: list[float] = []
        i = 0
        while len(out) < self._dim:
            chunk = seed[i % len(seed): (i % len(seed)) + 4]
            if len(chunk) < 4:
                chunk = chunk + seed[: 4 - len(chunk)]
            value = int.from_bytes(chunk, "big", signed=False) / 2**32
            out.append(value * 2 - 1)
            i += 4
        return out
