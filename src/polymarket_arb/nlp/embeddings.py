"""Embedding orchestration: skip-cache by ``text_hash``, build storage rows."""

from __future__ import annotations

import hashlib
import time

from ..storage.base import MarketEmbeddingRow, MarketRow
from .protocols import EmbeddingClient

_SCHEMA_VERSION = 1


def _now_ms() -> int:
    return int(time.time() * 1000)


def text_for_embedding(market: MarketRow) -> str:
    """Canonical text fed to the embedder. Stable so ``text_hash`` is too."""

    return f"{market.question}\n\n{market.description or ''}".strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def embed_market(
    *,
    market: MarketRow,
    client: EmbeddingClient,
    embedding_space: str,
) -> MarketEmbeddingRow:
    text = text_for_embedding(market)
    resp = await client.embed_text(text=text)
    return MarketEmbeddingRow(
        market_id=market.id,
        embedding_space=embedding_space,
        text_hash=text_hash(text),
        dimensions=resp.dimensions,
        vector=list(resp.vector),
        model_name=resp.model,
        schema_version=_SCHEMA_VERSION,
        ingested_ts_ms=_now_ms(),
    )
