from __future__ import annotations

import pytest

from polymarket_arb.nlp.mock_client import MockEmbeddingClient, MockLLMClient


@pytest.mark.asyncio
async def test_mock_llm_returns_responder_output():
    llm = MockLLMClient(responder=lambda s, u, v: f"sys={s}|user={u}|v={v}")
    res = await llm.complete_json(system="S", user="U", prompt_version="V")
    assert res.text == "sys=S|user=U|v=V"
    assert res.model == "mock-llm"
    assert len(res.raw_response_hash) == 64


@pytest.mark.asyncio
async def test_mock_embedding_is_deterministic():
    e = MockEmbeddingClient(dimensions=8)
    a = await e.embed_text(text="hello")
    b = await e.embed_text(text="hello")
    c = await e.embed_text(text="different")
    assert a.vector == b.vector
    assert a.vector != c.vector
    assert a.dimensions == 8 and len(a.vector) == 8
