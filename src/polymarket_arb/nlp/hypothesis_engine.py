"""Ollama-embedding-based hypothesis engine.

We do NOT have a generative LLM running locally — the only Ollama model
available is `nomic-embed-text` (an embedding model).  This module uses it
to detect *near-duplicate / cross-event* market pairs that the deterministic
relationship taxonomy missed, and emits them as a hypothesis JSONL.

Each hypothesis is an explicit, auditable record:

    * the two markets it pairs
    * cosine similarity between their question embeddings
    * which deterministic templates ran and produced nothing
    * a human-readable explanation
    * required-review flag
    * confidence
    * uncertainty_flags

When ollama is unreachable we fall back to a pure-Python TF-IDF / token-Jaccard
similarity over question text — clearly labelled `hypothesis_engine=jaccard_fallback`.

Every output line is **RESEARCH-ONLY**.  Nothing here places a trade.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..storage.base import MarketRow

OLLAMA_BASE_DEFAULT = "http://172.22.16.1:11434"
OLLAMA_EMBED_MODEL = "nomic-embed-text:latest"


# ── data shape ────────────────────────────────────────────────────────────────


@dataclass
class Hypothesis:
    hypothesis_id: str
    market_id_a: str
    market_id_b: str
    question_a: str
    question_b: str
    similarity: float
    hypothesis_type: str
    explanation: str
    confidence: float
    sources_used: list[str]
    hypothesis_engine: str
    outside_current_relationship_space: bool
    uncertainty_flags: list[str]
    proposed_trade_logic: str
    human_review_required: bool
    expected_failure_modes: list[str]

    def to_jsonl(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True)


# ── embeddings via Ollama ────────────────────────────────────────────────────


def _ollama_embed(text: str, base: str, timeout_s: float = 8.0) -> list[float] | None:
    body = json.dumps({"model": OLLAMA_EMBED_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(
        f"{base}/api/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read())
        emb = data.get("embedding")
        if isinstance(emb, list) and emb:
            return [float(x) for x in emb]
        return None
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── token-overlap fallback ───────────────────────────────────────────────────


_TOKEN_RX = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {"will", "be", "the", "a", "an", "of", "in", "on", "by", "before", "is",
     "are", "and", "or", "for", "to", "with", "at", "as", "have", "has",
     "their", "this", "that", "from", "any", "more", "than", "next", "all", "if", "into", "yes", "no", "do", "does", "did", "win", "wins"}
)


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RX.findall(text.lower()) if t not in _STOP and len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ── hypothesis generation ────────────────────────────────────────────────────


def _stable_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def generate_hypotheses(
    markets: list[MarketRow],
    *,
    existing_pair_keys: set[frozenset],
    sim_threshold: float,
    max_pairs_per_market: int,
    overall_pair_cap: int,
    ollama_base: str | None = None,
) -> tuple[list[Hypothesis], dict[str, Any]]:
    """Generate near-duplicate / suspicious-overlap hypothesis pairs.

    `existing_pair_keys` is the set of `frozenset({market_id_a, market_id_b})`
    pairs already covered by the deterministic relationship registry — those
    are filtered out so every emitted hypothesis is strictly outside the
    current relationship space.

    Returns the list and a metadata dict (engine used, sample sizes, etc.).
    """
    if not markets:
        return [], {"engine": "none", "reason": "no markets"}

    base = ollama_base or os.environ.get("POLYMARKET_OLLAMA_BASE", OLLAMA_BASE_DEFAULT)

    # Try the Ollama embed endpoint once to decide which mode to use.
    probe = _ollama_embed("ping", base, timeout_s=5.0)
    engine = "ollama_nomic_embed" if probe is not None else "jaccard_fallback"
    meta: dict[str, Any] = {
        "engine": engine,
        "ollama_base": base,
        "embed_model": OLLAMA_EMBED_MODEL,
        "sim_threshold": sim_threshold,
        "markets_considered": len(markets),
        "existing_pairs_filtered": len(existing_pair_keys),
    }

    # Sort markets for determinism.
    markets = sorted(markets, key=lambda m: m.id)

    if engine == "ollama_nomic_embed":
        embeddings: dict[str, list[float]] = {}
        skipped = 0
        for m in markets:
            emb = _ollama_embed(m.question or "", base, timeout_s=8.0)
            if emb is None:
                skipped += 1
                continue
            embeddings[m.id] = emb
        meta["embeddings_obtained"] = len(embeddings)
        meta["embeddings_skipped"] = skipped

        def _sim_fn(mid_a: str, mid_b: str) -> float:
            if mid_a not in embeddings or mid_b not in embeddings:
                return 0.0
            return _cosine(embeddings[mid_a], embeddings[mid_b])
    else:
        toks = {m.id: _tokens(m.question or "") for m in markets}

        def _sim_fn(mid_a: str, mid_b: str) -> float:
            return _jaccard(toks[mid_a], toks[mid_b])

    out: list[Hypothesis] = []
    per_market_count: dict[str, int] = {}
    for i, a in enumerate(markets):
        if len(out) >= overall_pair_cap:
            break
        if per_market_count.get(a.id, 0) >= max_pairs_per_market:
            continue
        for b in markets[i + 1:]:
            if per_market_count.get(a.id, 0) >= max_pairs_per_market:
                break
            if len(out) >= overall_pair_cap:
                break
            if a.id == b.id:
                continue
            key = frozenset({a.id, b.id})
            if key in existing_pair_keys:
                continue
            sim = _sim_fn(a.id, b.id)
            if sim < sim_threshold:
                continue
            # Suppress trivially-similar pairs that are clearly the same
            # market on two different surfaces (we want NEW pairs, not echoes).
            if (a.question or "").strip().lower() == (b.question or "").strip().lower():
                continue
            hypothesis_type = _classify_hypothesis(a.question, b.question, sim)
            uncertainty: list[str] = []
            if sim < sim_threshold + 0.05:
                uncertainty.append("borderline_similarity")
            if (a.question or "").strip().count(" ") < 4:
                uncertainty.append("question_a_very_short")
            if (b.question or "").strip().count(" ") < 4:
                uncertainty.append("question_b_very_short")
            hid = _stable_hash(a.id, b.id, hypothesis_type, str(round(sim, 3)))
            out.append(Hypothesis(
                hypothesis_id=hid,
                market_id_a=a.id,
                market_id_b=b.id,
                question_a=a.question or "",
                question_b=b.question or "",
                similarity=round(float(sim), 4),
                hypothesis_type=hypothesis_type,
                explanation=_describe(a.question, b.question, hypothesis_type, sim, engine),
                confidence=min(0.95, round(0.45 + sim * 0.45, 3)),
                sources_used=["market.question", "ollama:nomic-embed-text"
                              if engine == "ollama_nomic_embed" else "token_jaccard"],
                hypothesis_engine=engine,
                outside_current_relationship_space=True,
                uncertainty_flags=uncertainty,
                proposed_trade_logic=_proposed_logic(hypothesis_type),
                human_review_required=sim < (sim_threshold + 0.1) or len(uncertainty) > 0,
                expected_failure_modes=_failure_modes(hypothesis_type),
            ))
            per_market_count[a.id] = per_market_count.get(a.id, 0) + 1
            per_market_count[b.id] = per_market_count.get(b.id, 0) + 1
    meta["hypotheses_emitted"] = len(out)
    meta["generated_ts_ms"] = int(time.time() * 1000)
    return out, meta


def _classify_hypothesis(qa: str, qb: str, sim: float) -> str:
    a = (qa or "").lower()
    b = (qb or "").lower()
    if sim >= 0.92:
        return "likely_duplicate_market"
    if "before" in a and "before" in b:
        return "temporal_ordering_pair"
    if (("primary" in a and "primary" in b) or ("nominee" in a and "nominee" in b)):
        return "primary_race_pairwise"
    if any(team in a for team in ("nba", "nfl", "mlb", "nhl")) and any(
        team in b for team in ("nba", "nfl", "mlb", "nhl")
    ):
        return "sports_event_pair"
    if (("trump" in a and "trump" in b) or ("biden" in a and "biden" in b)):
        return "political_actor_pair"
    return "near_duplicate_or_overlap"


def _describe(qa: str, qb: str, htype: str, sim: float, engine: str) -> str:
    return (
        f"{engine} similarity={sim:.3f}. classified as {htype}. "
        f"Q_A: {qa[:100]} | Q_B: {qb[:100]} | "
        "research-only / not yet verified by deterministic taxonomy."
    )


def _proposed_logic(htype: str) -> str:
    if htype == "likely_duplicate_market":
        return (
            "if prices diverge on these two near-duplicate markets, buy the cheaper "
            "YES and sell / buy NO on the more expensive YES (simulated only)."
        )
    if htype == "temporal_ordering_pair":
        return (
            "if both markets reference the same event with different deadlines, "
            "the earlier-deadline YES probability should not exceed the "
            "later-deadline YES probability (simulated only)."
        )
    if htype == "primary_race_pairwise":
        return (
            "if both markets are candidates in the same primary, their YES "
            "probabilities should not sum above 1 (simulated only)."
        )
    return "research-only inconsistency test; no execution"


def _failure_modes(htype: str) -> list[str]:
    common = ["wording_mismatch", "different_resolution_source",
              "different_event_horizon", "ambiguous_subject_entity"]
    if htype == "likely_duplicate_market":
        return [*common, "one_market_resolved_other_open"]
    if htype == "temporal_ordering_pair":
        return [*common, "one_market_passes_deadline_first"]
    if htype == "primary_race_pairwise":
        return [*common, "one_candidate_withdrew", "party_changed"]
    return common


def write_hypotheses_jsonl(out_path: Path, hypotheses: list[Hypothesis], meta: dict[str, Any]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"_meta": meta, "schema_version": 1}, sort_keys=True) + "\n")
        for h in hypotheses:
            fh.write(h.to_jsonl() + "\n")
