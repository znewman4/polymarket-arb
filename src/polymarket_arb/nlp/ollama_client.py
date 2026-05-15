"""Live Ollama HTTP clients (LLM + embedding).

These thin wrappers compose ``AsyncHttpClient`` with the
``ollama_compat`` extractors so the rest of the NLP code path is endpoint-
shape-agnostic.
"""

from __future__ import annotations

import time

import httpx
from loguru import logger

from ..http.client import AsyncHttpClient, HttpError, TransientError
from ..settings import NlpSettings
from .ollama_compat import (
    OllamaCompatConfig,
    _embedding_url_and_body,
    _llm_url_and_body,
    build_embedding_response,
    build_llm_response,
)
from .protocols import LLMOutputError
from .responses import EmbeddingResponse, LLMResponse


def _config_from_settings(nlp: NlpSettings, *, debug_dir=None) -> OllamaCompatConfig:
    return OllamaCompatConfig(
        base_url=nlp.base_url,
        llm_model=nlp.llm_model,
        embedding_model=nlp.embedding_model,
        llm_endpoint=nlp.ollama_llm_endpoint,
        embedding_endpoint=nlp.ollama_embedding_endpoint,
        debug_capture_thinking=nlp.debug_capture_thinking,
        debug_dir=debug_dir,
    )


class OllamaLLMClient:
    """Implements ``LLMClient.complete_json`` against /api/{generate|chat}."""

    def __init__(
        self,
        *,
        http: AsyncHttpClient,
        nlp: NlpSettings,
        debug_dir=None,
    ) -> None:
        self._http = http
        self._cfg = _config_from_settings(nlp, debug_dir=debug_dir)
        self._timeout_s = nlp.timeout_s

    async def complete_json(
        self, *, system: str, user: str, prompt_version: str,
        market_id_hint: str | None = None,
    ) -> LLMResponse:
        url, body = _llm_url_and_body(self._cfg, system=system, user=user)
        started = time.monotonic()
        try:
            payload = await self._http.post_json(
                url, json=body, timeout=httpx.Timeout(self._timeout_s),
            )
        except (HttpError, TransientError) as exc:
            raise LLMOutputError(f"ollama HTTP error: {exc}") from exc
        try:
            return build_llm_response(
                self._cfg, payload, started_at_monotonic=started,
                market_id_hint=market_id_hint,
            )
        except LLMOutputError:
            raise
        except Exception as exc:  # belt + braces
            logger.error("unexpected llm parsing error", error=str(exc))
            raise LLMOutputError(f"unexpected llm parsing error: {exc}") from exc


class OllamaEmbeddingClient:
    """Implements ``EmbeddingClient.embed_text`` against /api/{embed|embeddings}."""

    def __init__(self, *, http: AsyncHttpClient, nlp: NlpSettings) -> None:
        self._http = http
        self._cfg = _config_from_settings(nlp)
        self._timeout_s = nlp.timeout_s

    async def embed_text(self, *, text: str) -> EmbeddingResponse:
        url, body = _embedding_url_and_body(self._cfg, text=text)
        try:
            payload = await self._http.post_json(
                url, json=body, timeout=httpx.Timeout(self._timeout_s),
            )
        except (HttpError, TransientError) as exc:
            raise LLMOutputError(f"ollama embedding HTTP error: {exc}") from exc
        return build_embedding_response(self._cfg, payload)
