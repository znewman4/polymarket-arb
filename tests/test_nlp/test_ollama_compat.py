"""Parametrised over all four endpoint shapes: ``generate``/``chat`` LLM
endpoints and ``embed``/``embeddings`` embedding endpoints.

Asserts the wrapper returns identical-shape ``LLMResponse`` /
``EmbeddingResponse`` regardless of which Ollama variant the user
configured. Also asserts ``<think>`` never reaches ``LLMResponse.text``.
"""

from __future__ import annotations

import time

import pytest

from polymarket_arb.nlp.ollama_compat import (
    OllamaCompatConfig,
    _embedding_url_and_body,
    _llm_url_and_body,
    build_embedding_response,
    build_llm_response,
)
from polymarket_arb.nlp.protocols import LLMOutputError


def _cfg(*, llm: str = "generate", emb: str = "embed") -> OllamaCompatConfig:
    return OllamaCompatConfig(
        base_url="http://ollama.example",
        llm_model="deepseek-r1:8b",
        embedding_model="nomic-embed-text",
        llm_endpoint=llm,
        embedding_endpoint=emb,
    )


# ─── LLM endpoints ──────────────────────────────────────────────────────


@pytest.mark.parametrize("endpoint,expected_path,expected_body_key", [
    ("generate", "/api/generate", "prompt"),
    ("chat",     "/api/chat",     "messages"),
])
def test_llm_url_and_body(endpoint, expected_path, expected_body_key):
    url, body = _llm_url_and_body(_cfg(llm=endpoint),
                                  system="sys", user="hi")
    assert url == f"http://ollama.example{expected_path}"
    assert expected_body_key in body
    assert body["format"] == "json"
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0


@pytest.mark.parametrize("endpoint,response_payload", [
    ("generate", {"response": "<think>r</think>{\"x\": 1}"}),
    ("chat",     {"message": {"role": "assistant",
                              "content": "<think>r</think>{\"x\": 1}"}}),
])
def test_llm_response_strips_thinking(endpoint, response_payload):
    started = time.monotonic()
    out = build_llm_response(_cfg(llm=endpoint), response_payload,
                             started_at_monotonic=started)
    assert "<think>" not in out.text
    assert out.text.startswith("{")
    assert out.model == "deepseek-r1:8b"
    assert len(out.raw_response_hash) == 64
    assert out.duration_ms >= 0


def test_llm_chat_missing_message_raises():
    with pytest.raises(LLMOutputError):
        build_llm_response(_cfg(llm="chat"), {"unexpected": "shape"},
                           started_at_monotonic=time.monotonic())


def test_llm_generate_missing_response_raises():
    with pytest.raises(LLMOutputError):
        build_llm_response(_cfg(llm="generate"), {"unexpected": "shape"},
                           started_at_monotonic=time.monotonic())


def test_post_strip_empty_raises():
    # If the model returns ONLY a thinking block, post-strip is empty → fail.
    with pytest.raises(LLMOutputError):
        build_llm_response(_cfg(llm="generate"),
                           {"response": "<think>just thinking</think>"},
                           started_at_monotonic=time.monotonic())


# ─── Embedding endpoints ────────────────────────────────────────────────


@pytest.mark.parametrize("endpoint,expected_path,expected_body_key", [
    ("embed",      "/api/embed",      "input"),
    ("embeddings", "/api/embeddings", "prompt"),
])
def test_embedding_url_and_body(endpoint, expected_path, expected_body_key):
    url, body = _embedding_url_and_body(_cfg(emb=endpoint), text="hello")
    assert url == f"http://ollama.example{expected_path}"
    assert body[expected_body_key] == "hello"
    assert body["model"] == "nomic-embed-text"


@pytest.mark.parametrize("endpoint,payload", [
    ("embed",      {"embeddings": [[0.1, -0.2, 0.3]]}),
    ("embeddings", {"embedding": [0.1, -0.2, 0.3]}),
])
def test_embedding_response_normalises(endpoint, payload):
    out = build_embedding_response(_cfg(emb=endpoint), payload)
    assert out.vector == [pytest.approx(0.1), pytest.approx(-0.2), pytest.approx(0.3)]
    assert out.dimensions == 3
    assert out.model == "nomic-embed-text"
    assert len(out.raw_response_hash) == 64


def test_embedding_embed_empty_list_raises():
    with pytest.raises(LLMOutputError):
        build_embedding_response(_cfg(emb="embed"), {"embeddings": []})


def test_embedding_embeddings_missing_field_raises():
    with pytest.raises(LLMOutputError):
        build_embedding_response(_cfg(emb="embeddings"), {"unexpected": "shape"})
