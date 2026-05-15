"""Local-AI semantic extraction layer (Phase 1.5+).

Public surface:
    - ``LLMClient`` / ``EmbeddingClient`` Protocols
    - ``LLMResponse`` / ``EmbeddingResponse`` dataclasses
    - ``MockLLMClient`` / ``MockEmbeddingClient`` (tests)
    - ``OllamaLLMClient`` / ``OllamaEmbeddingClient`` (live)
    - ``MarketSemantics`` Pydantic schema
    - ``LLMOutputError``
    - ``thinking_filter.strip_thinking``

Architectural rule: AI extracts structured labels; Python + YAML compute
final scores deterministically. We never store raw model response text in
durable storage — see the ``thinking_filter`` module docstring for the
full discipline.
"""

from .protocols import EmbeddingClient, LLMClient
from .responses import EmbeddingResponse, LLMResponse
from .schemas import MarketSemantics
from .thinking_filter import strip_thinking

__all__ = [
    "EmbeddingClient",
    "EmbeddingResponse",
    "LLMClient",
    "LLMResponse",
    "MarketSemantics",
    "strip_thinking",
]
