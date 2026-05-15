"""End-to-end LLM/Embedding client tests with respx-mocked Ollama HTTP.

Each shape exercised: ``/api/generate`` + ``/api/chat`` for LLM,
``/api/embed`` + ``/api/embeddings`` for embeddings. Confirms that swapping
``nlp.ollama_*_endpoint`` is the only change needed to support a different
Ollama build.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from polymarket_arb.http.client import AsyncHttpClient
from polymarket_arb.nlp.ollama_client import OllamaEmbeddingClient, OllamaLLMClient
from polymarket_arb.settings import NlpSettings


def _nlp(*, llm: str = "generate", emb: str = "embed",
         base: str = "http://ollama.example") -> NlpSettings:
    return NlpSettings(
        enabled=True, provider="ollama",
        base_url=base, llm_model="deepseek-r1:8b",
        embedding_model="nomic-embed-text",
        timeout_s=10, prompt_version="market_semantics_v1",
        ollama_llm_endpoint=llm, ollama_embedding_endpoint=emb,
    )


@pytest.mark.asyncio
async def test_generate_endpoint_strips_thinking(settings):
    nlp = _nlp(llm="generate")
    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        router.post("http://ollama.example/api/generate").mock(
            return_value=Response(200, json={
                "response": "<think>secret</think>{\"x\": 1}",
                "eval_count": 42,
            })
        )
        client = OllamaLLMClient(http=http, nlp=nlp)
        out = await client.complete_json(system="s", user="u",
                                         prompt_version="v")
    assert "<think>" not in out.text
    assert out.text.startswith("{")
    assert out.metadata.get("eval_count") == 42


@pytest.mark.asyncio
async def test_chat_endpoint_strips_thinking(settings):
    nlp = _nlp(llm="chat")
    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        router.post("http://ollama.example/api/chat").mock(
            return_value=Response(200, json={
                "message": {"role": "assistant",
                            "content": "<think>R</think>{\"x\": 1}"},
            })
        )
        client = OllamaLLMClient(http=http, nlp=nlp)
        out = await client.complete_json(system="s", user="u",
                                         prompt_version="v")
    assert "<think>" not in out.text


@pytest.mark.asyncio
async def test_embed_endpoint(settings):
    nlp = _nlp(emb="embed")
    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        router.post("http://ollama.example/api/embed").mock(
            return_value=Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})
        )
        client = OllamaEmbeddingClient(http=http, nlp=nlp)
        out = await client.embed_text(text="hello")
    assert out.vector == [pytest.approx(0.1), pytest.approx(0.2), pytest.approx(0.3)]
    assert out.dimensions == 3


@pytest.mark.asyncio
async def test_embeddings_endpoint(settings):
    nlp = _nlp(emb="embeddings")
    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        router.post("http://ollama.example/api/embeddings").mock(
            return_value=Response(200, json={"embedding": [0.4, 0.5, 0.6]})
        )
        client = OllamaEmbeddingClient(http=http, nlp=nlp)
        out = await client.embed_text(text="world")
    assert out.vector == [pytest.approx(0.4), pytest.approx(0.5), pytest.approx(0.6)]
