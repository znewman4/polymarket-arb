"""Ollama HTTP endpoint-shape compatibility layer.

Ollama's HTTP API has shape variance across builds. This wrapper normalises
every supported shape into ``LLMResponse`` / ``EmbeddingResponse`` so
callers never branch on which endpoint was used.

Endpoint dispatch:

| Setting                       | URL                   | Request body shape                                                                                   | Text/vector extractor              |
| ----------------------------- | --------------------- | ---------------------------------------------------------------------------------------------------- | ---------------------------------- |
| llm_endpoint = "generate"     | /api/generate         | {model, prompt, format:"json", stream:false, options}                                                | response["response"]               |
| llm_endpoint = "chat"         | /api/chat             | {model, messages:[{role,content}], format:"json", stream:false, options}                             | response["message"]["content"]     |
| embedding_endpoint = "embed"  | /api/embed            | {model, input:"<text>"}                                                                              | response["embeddings"][0]          |
| embedding_endpoint = "embeddings" | /api/embeddings   | {model, prompt:"<text>"}                                                                             | response["embedding"]              |

The ``<think>`` strip (and optional debug capture) happens BEFORE the
``LLMResponse`` is constructed, so ``LLMResponse.text`` never contains
chain-of-thought.
"""

from __future__ import annotations

import hashlib
import struct
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from .protocols import LLMOutputError
from .responses import EmbeddingResponse, LLMResponse
from .thinking_filter import strip_thinking

LLMEndpoint = Literal["generate", "chat"]
EmbeddingEndpoint = Literal["embed", "embeddings"]


@dataclass(frozen=True)
class OllamaCompatConfig:
    base_url: str
    llm_model: str
    embedding_model: str
    llm_endpoint: LLMEndpoint = "generate"
    embedding_endpoint: EmbeddingEndpoint = "embed"
    debug_capture_thinking: bool = False
    debug_dir: Path | None = None


def _llm_url_and_body(
    cfg: OllamaCompatConfig, *, system: str, user: str,
) -> tuple[str, dict[str, Any]]:
    base = cfg.base_url.rstrip("/")
    options = {"temperature": 0}
    if cfg.llm_endpoint == "chat":
        return (
            f"{base}/api/chat",
            {
                "model": cfg.llm_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "format": "json",
                "stream": False,
                "options": options,
            },
        )
    # default: generate
    prompt = f"{system}\n\n{user}" if system else user
    return (
        f"{base}/api/generate",
        {
            "model": cfg.llm_model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": options,
        },
    )


def _extract_llm_text(cfg: OllamaCompatConfig, payload: Any) -> str:
    if not isinstance(payload, dict):
        raise LLMOutputError(f"unexpected response (not a dict): {type(payload).__name__}")
    if cfg.llm_endpoint == "chat":
        msg = payload.get("message") or {}
        if not isinstance(msg, dict) or "content" not in msg:
            raise LLMOutputError("missing message.content in /api/chat response")
        return str(msg["content"])
    text = payload.get("response")
    if text is None:
        raise LLMOutputError("missing 'response' field in /api/generate response")
    return str(text)


def build_llm_response(
    cfg: OllamaCompatConfig,
    payload: Any,
    *,
    started_at_monotonic: float,
    market_id_hint: str | None = None,
) -> LLMResponse:
    raw_text = _extract_llm_text(cfg, payload)
    if cfg.debug_capture_thinking and cfg.debug_dir is not None:
        try:
            cfg.debug_dir.mkdir(parents=True, exist_ok=True)
            stem = market_id_hint or uuid.uuid4().hex[:12]
            (cfg.debug_dir / f"{stem}.txt").write_text(raw_text, encoding="utf-8")
        except OSError as exc:  # pragma: no cover — debug-only path
            logger.warning("debug_capture_thinking write failed", error=str(exc))
    cleaned = strip_thinking(raw_text)
    if not cleaned:
        raise LLMOutputError("post-<think>-strip text was empty")
    h = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
    duration_ms = int((time.monotonic() - started_at_monotonic) * 1000)
    metadata = {
        k: payload.get(k) for k in ("eval_count", "prompt_eval_count", "total_duration")
        if isinstance(payload, dict) and payload.get(k) is not None
    }
    return LLMResponse(
        text=cleaned, model=cfg.llm_model,
        raw_response_hash=h, duration_ms=duration_ms, metadata=metadata,
    )


def _embedding_url_and_body(
    cfg: OllamaCompatConfig, *, text: str,
) -> tuple[str, dict[str, Any]]:
    base = cfg.base_url.rstrip("/")
    if cfg.embedding_endpoint == "embeddings":
        return f"{base}/api/embeddings", {"model": cfg.embedding_model, "prompt": text}
    return f"{base}/api/embed", {"model": cfg.embedding_model, "input": text}


def _extract_embedding_vector(cfg: OllamaCompatConfig, payload: Any) -> list[float]:
    if not isinstance(payload, dict):
        raise LLMOutputError(f"unexpected embedding response: {type(payload).__name__}")
    if cfg.embedding_endpoint == "embeddings":
        vec = payload.get("embedding")
        if not isinstance(vec, list):
            raise LLMOutputError("missing 'embedding' (list) in /api/embeddings response")
        return [float(x) for x in vec]
    vec_list = payload.get("embeddings")
    if not isinstance(vec_list, list) or not vec_list:
        raise LLMOutputError("missing or empty 'embeddings' in /api/embed response")
    inner = vec_list[0]
    if not isinstance(inner, list):
        raise LLMOutputError("malformed 'embeddings[0]' in /api/embed response")
    return [float(x) for x in inner]


def build_embedding_response(
    cfg: OllamaCompatConfig, payload: Any,
) -> EmbeddingResponse:
    vec = _extract_embedding_vector(cfg, payload)
    h = hashlib.sha256(struct.pack(f"{len(vec)}f", *vec)).hexdigest()
    return EmbeddingResponse(
        vector=vec, model=cfg.embedding_model,
        dimensions=len(vec), raw_response_hash=h, metadata={},
    )


# Re-exported helpers used by the concrete client.
__all__ = [
    "OllamaCompatConfig",
    "_embedding_url_and_body",
    "_llm_url_and_body",
    "build_embedding_response",
    "build_llm_response",
]
