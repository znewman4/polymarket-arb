"""Generate candidate market pairs for relationship scoring."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass

from ..storage.base import MarketRow, MarketSemanticsRow
from .semantic_similarity import cosine_similarity, entity_overlap_score
from .terms_similarity import infer_outcome_space_from_question, infer_temporal_terms_from_question


@dataclass
class RelationshipMiningConfig:
    max_candidates_per_market: int = 50
    max_total_candidates: int = 200_000
    embedding_threshold: float = 0.85
    time_window_days: int = 30


@dataclass
class CandidatePair:
    market_a: MarketRow
    market_b: MarketRow
    sources: list[str]  # e.g. ["event_id_match", "entity_overlap"]
    # hint to validator about likely relationship type (may be empty string)
    generation_source: str = ""


def generate_candidate_pairs(
    markets: list[MarketRow],
    *,
    semantics_by_market: dict[str, MarketSemanticsRow],
    events_by_market: dict[str, str | None],
    embeddings_by_market: dict[str, list[float]] | None = None,
    cfg: RelationshipMiningConfig | None = None,
) -> Iterator[CandidatePair]:
    """Yield de-duplicated candidate pairs using multiple overlap signals."""
    if cfg is None:
        cfg = RelationshipMiningConfig()

    seen: set[frozenset] = set()
    per_market_count: dict[str, int] = {}
    total = 0

    def _emit(
        a: MarketRow, b: MarketRow, sources: list[str], generation_source: str = ""
    ) -> CandidatePair | None:
        nonlocal total
        key = frozenset({a.id, b.id})
        if key in seen:
            return None
        if a.id == b.id:
            return None
        cnt_a = per_market_count.get(a.id, 0)
        cnt_b = per_market_count.get(b.id, 0)
        if cnt_a >= cfg.max_candidates_per_market or cnt_b >= cfg.max_candidates_per_market:
            return None
        if total >= cfg.max_total_candidates:
            return None
        seen.add(key)
        per_market_count[a.id] = cnt_a + 1
        per_market_count[b.id] = cnt_b + 1
        total += 1
        return CandidatePair(
            market_a=a, market_b=b,
            sources=sorted(set(sources)),
            generation_source=generation_source,
        )

    # Build lookup structures
    by_event: dict[str, list[MarketRow]] = {}
    for m in markets:
        eid = events_by_market.get(m.id)
        if eid:
            by_event.setdefault(eid, []).append(m)

    # Sort markets for deterministic ordering
    sorted_markets = sorted(markets, key=lambda m: m.id)

    # Source 1: same event_id
    for group in by_event.values():
        group_sorted = sorted(group, key=lambda m: m.id)
        for i, a in enumerate(group_sorted):
            for b in group_sorted[i + 1:]:
                pair = _emit(a, b, ["event_id_match"])
                if pair:
                    yield pair

    # Source 2: entity overlap (needs semantics for both)
    for i, a in enumerate(sorted_markets):
        sem_a = semantics_by_market.get(a.id)
        if not sem_a:
            continue
        for b in sorted_markets[i + 1:]:
            sem_b = semantics_by_market.get(b.id)
            if not sem_b:
                continue
            if total >= cfg.max_total_candidates:
                return
            score = entity_overlap_score(sem_a, sem_b)
            if score > 0.0:
                pair = _emit(a, b, ["entity_overlap"])
                if pair:
                    yield pair

    # Source 3: outcome_space cluster — same competition_id, different candidates
    # e.g. "Will Carolina Hurricanes win the Stanley Cup?" + "Will Flyers win?"
    by_competition: dict[str, list[MarketRow]] = {}
    for m in sorted_markets:
        sem = semantics_by_market.get(m.id)
        if not sem:
            continue
        os_data = None
        if sem.outcome_space_json:
            try:
                os_data = json.loads(sem.outcome_space_json)
            except (json.JSONDecodeError, TypeError):
                os_data = None
        if not isinstance(os_data, dict):
            os_data = infer_outcome_space_from_question(m.question, sem.subject_entities)
        if not os_data:
            continue
        comp_id = os_data.get("competition_id")
        kind = os_data.get("kind")
        if comp_id and kind == "single_winner_competition":
            by_competition.setdefault(comp_id, []).append(m)

    for _comp_id, group in by_competition.items():
        group_sorted = sorted(group, key=lambda m: m.id)
        for i, a in enumerate(group_sorted):
            for b in group_sorted[i + 1:]:
                if total >= cfg.max_total_candidates:
                    return
                pair = _emit(a, b, ["outcome_space_cluster"], "mutually_exclusive_category")
                if pair:
                    yield pair

    # Source 4: proposition cluster — markets sharing event atoms (temporal relations)
    # Groups markets that encode temporal propositions referencing the same events
    # e.g. "Rihanna before GTA VI" + "GTA VI before Rihanna" (inverse temporal)
    # or "Rihanna before GTA VI" + "GTA VI before June 2026" (transitive)
    event_to_markets: dict[str, list[MarketRow]] = {}
    for m in sorted_markets:
        sem = semantics_by_market.get(m.id)
        if not sem:
            continue
        atoms = None
        if sem.event_atoms_json:
            try:
                atoms = json.loads(sem.event_atoms_json) or []
            except (json.JSONDecodeError, TypeError):
                atoms = None
        if not atoms:
            atoms, _ = infer_temporal_terms_from_question(m.question)
        for atom in atoms:
            eid = atom.get("event_id")
            if eid:
                event_to_markets.setdefault(eid, []).append(m)

    proposition_emitted: set[frozenset] = set()
    for _event_id, group in event_to_markets.items():
        if len(group) < 2:
            continue
        group_sorted = sorted(group, key=lambda m: m.id)
        for i, a in enumerate(group_sorted):
            for b in group_sorted[i + 1:]:
                if total >= cfg.max_total_candidates:
                    return
                pair_key = frozenset({a.id, b.id})
                if pair_key in proposition_emitted:
                    continue
                proposition_emitted.add(pair_key)
                # Determine the temporal hint based on proposition data
                sem_a = semantics_by_market.get(a.id)
                sem_b = semantics_by_market.get(b.id)
                gen_source = _infer_proposition_source(sem_a, sem_b, a.question, b.question)
                pair = _emit(a, b, ["proposition_cluster"], gen_source)
                if pair:
                    yield pair

    # Source 5: embedding similarity (optional; never sufficient alone)
    if embeddings_by_market:
        market_ids = [m.id for m in sorted_markets if m.id in embeddings_by_market]
        for i, id_a in enumerate(market_ids):
            vec_a = embeddings_by_market[id_a]
            for id_b in market_ids[i + 1:]:
                if total >= cfg.max_total_candidates:
                    return
                vec_b = embeddings_by_market[id_b]
                sim = cosine_similarity(vec_a, vec_b)
                if sim >= cfg.embedding_threshold:
                    a = next(m for m in sorted_markets if m.id == id_a)
                    b = next(m for m in sorted_markets if m.id == id_b)
                    # Only emit if there's at least one other source already
                    key = frozenset({id_a, id_b})
                    if key in seen:
                        pass
                    else:
                        # Embedding-only: do NOT emit — validator blocks embedding_only.
                        pass


def _infer_proposition_source(
    sem_a: MarketSemanticsRow | None,
    sem_b: MarketSemanticsRow | None,
    question_a: str | None = None,
    question_b: str | None = None,
) -> str:
    """Infer likely temporal relationship type from proposition data of two markets."""
    if not sem_a or not sem_b:
        return "temporal_before"
    prop_a = _parse_json(sem_a.proposition_json)
    prop_b = _parse_json(sem_b.proposition_json)
    if not prop_a:
        _, prop_a = infer_temporal_terms_from_question(question_a or sem_a.question)
    if not prop_b:
        _, prop_b = infer_temporal_terms_from_question(question_b or sem_b.question)
    if not prop_a or not prop_b:
        return "temporal_before"
    left_a = prop_a.get("left_event")
    right_a = prop_a.get("right_event")
    left_b = prop_b.get("left_event")
    right_b = prop_b.get("right_event")
    # Inverse: A before B and B before A
    if (left_a == left_b and right_a == right_b):
        rel_a = prop_a.get("relation")
        rel_b = prop_b.get("relation")
        if rel_a in ("before", "after") and rel_a != rel_b:
            return "inverse_temporal_order"
    # If they share reference events, could be same_reference_clock or transitive
    if right_a == right_b and right_a:
        return "same_reference_clock"
    return "temporal_before"


def _parse_json(s: str | None) -> dict | None:
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None
