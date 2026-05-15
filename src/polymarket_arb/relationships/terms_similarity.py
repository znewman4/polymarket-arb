"""Scoring helpers for terms-aware relationship matching.

All functions are deterministic (no LLM calls). They operate on the
JSON-serialised outcome_space, proposition, and event_atoms stored on
MarketSemanticsRow after Phase 5.5 D+ extraction.
"""

from __future__ import annotations

import json
import re
from typing import Any


def _load(s: str | None) -> dict | list | None:
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def _slug(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.lower())
    return "_".join(words)


def _clean_candidate(value: str) -> str:
    value = value.strip(" ?.")
    value = re.sub(r"^(the|a|an)\s+", "", value, flags=re.IGNORECASE)
    return value.strip()


def _clean_event_phrase(value: str) -> str:
    value = value.strip(" ?.")
    value = re.sub(r"^will\s+", "", value, flags=re.IGNORECASE)
    return value.strip()


def _event_id(value: str) -> str:
    phrase = _clean_event_phrase(value).lower()
    phrase = phrase.replace("$1m", "1m")
    replacements = {
        "gta vi released": "gta vi release",
        "gta vi release": "gta vi release",
        "gta vi": "gta vi release",
        "new rihanna album": "rihanna album release",
        "rihanna album": "rihanna album release",
        "new playboi carti album": "playboi carti album release",
        "playboi carti album": "playboi carti album release",
    }
    phrase = replacements.get(phrase, phrase)
    phrase = phrase.replace(" hit 1m", " 1m")
    return _slug(phrase)


def infer_temporal_terms_from_question(question: str | None) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Infer event atoms/proposition for simple before/after market questions."""
    if not question:
        return [], None
    q = " ".join(question.strip().rstrip("?").split())
    match = re.match(r"^(?:will\s+)?(?P<left>.+?)\s+(?P<relation>before|after)\s+(?P<right>.+)$", q, re.IGNORECASE)
    if not match:
        return [], None
    left = _clean_event_phrase(match.group("left"))
    right = _clean_event_phrase(match.group("right"))
    relation = match.group("relation").lower()
    left_id = _event_id(left)
    right_id = _event_id(right)
    if not left_id or not right_id:
        return [], None
    atoms = [
        {"event_id": left_id, "label": left},
        {"event_id": right_id, "label": right},
    ]
    proposition = {
        "type": "temporal_order",
        "left_event": left_id,
        "relation": relation,
        "right_event": right_id,
        "strictness": f"strict_{relation}",
    }
    return atoms, proposition


def infer_outcome_space_from_question(
    question: str | None,
    subject_entities: list[str] | None = None,
) -> dict[str, Any] | None:
    """Infer category-winner outcome space from common Polymarket phrasing.

    This is a deterministic back-compat bridge for older semantic rows that
    predate ``outcome_space_json``. It intentionally handles only high-signal
    patterns like "Will X win the 2026 NHL Stanley Cup?".
    """
    if not question:
        return None
    q = " ".join(question.strip().rstrip("?").split())

    patterns = [
        (r"^will\s+(?P<candidate>.+?)\s+win\s+(?:the\s+)?(?P<competition>.+)$", "win"),
        (r"^will\s+(?P<candidate>.+?)\s+be\s+elected\s+(?P<competition>.+)$", "elected"),
        (r"^will\s+(?P<candidate>.+?)\s+become\s+(?P<competition>.+)$", "elected"),
        (r"^will\s+(?P<candidate>.+?)\s+be\s+named\s+(?P<competition>.+)$", "winner"),
    ]
    for pattern, predicate in patterns:
        match = re.match(pattern, q, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = _clean_candidate(match.group("candidate"))
        competition = match.group("competition").strip(" ?.")
        if subject_entities:
            for entity in subject_entities:
                if entity and entity.lower() in candidate.lower():
                    candidate = entity
                    break
        comp_id = _slug(competition)
        if not candidate or not comp_id:
            return None
        return {
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": competition,
            "candidate": candidate,
            "winner_predicate": predicate,
        }
    return None


def _outcome_space(sem) -> dict[str, Any] | None:
    os_data = _load(sem.outcome_space_json) if hasattr(sem, "outcome_space_json") else None
    if isinstance(os_data, dict):
        return os_data
    return infer_outcome_space_from_question(
        getattr(sem, "question", None),
        list(getattr(sem, "subject_entities", []) or []),
    )


def _event_atoms(sem) -> list[dict[str, Any]]:
    atoms = _load(sem.event_atoms_json) if hasattr(sem, "event_atoms_json") else None
    if isinstance(atoms, list) and atoms:
        return [atom for atom in atoms if isinstance(atom, dict)]
    inferred, _ = infer_temporal_terms_from_question(getattr(sem, "question", None))
    return inferred


def _proposition(sem) -> dict[str, Any] | None:
    prop = _load(sem.proposition_json) if hasattr(sem, "proposition_json") else None
    if isinstance(prop, dict):
        return prop
    _, inferred = infer_temporal_terms_from_question(getattr(sem, "question", None))
    return inferred


def outcome_space_match_score(sem_a, sem_b) -> float:
    """Score how well two markets share an outcome space.

    Returns 1.0 when both markets are single_winner_competition with the
    same competition_id and the same winner_predicate. Returns 0.0 on any
    mismatch that would rule out the relationship.
    """
    os_a = _outcome_space(sem_a)
    os_b = _outcome_space(sem_b)

    if not os_a or not os_b:
        return 0.0
    if not isinstance(os_a, dict) or not isinstance(os_b, dict):
        return 0.0

    kind_a = os_a.get("kind", "")
    kind_b = os_b.get("kind", "")
    if kind_a != kind_b:
        return 0.0

    if kind_a == "single_winner_competition":
        comp_a = (os_a.get("competition_id") or "").strip().lower()
        comp_b = (os_b.get("competition_id") or "").strip().lower()
        if not comp_a or not comp_b:
            return 0.0
        if comp_a == comp_b:
            # Check winner_predicate compatibility
            pred_a = (os_a.get("winner_predicate") or "").strip().lower()
            pred_b = (os_b.get("winner_predicate") or "").strip().lower()
            if pred_a and pred_b and pred_a != pred_b:
                return 0.5  # different predicates, same competition — partial
            return 1.0
        # Fuzzy competition match: word overlap
        w_a = set(comp_a.replace("_", " ").split())
        w_b = set(comp_b.replace("_", " ").split())
        if not w_a or not w_b:
            return 0.0
        overlap = len(w_a & w_b) / max(len(w_a), len(w_b))
        return round(overlap, 4)

    return 0.3  # same kind but not single_winner — weak signal


def candidates_are_different(sem_a, sem_b) -> bool:
    """True if both markets represent different candidates for the same event."""
    os_a = _outcome_space(sem_a)
    os_b = _outcome_space(sem_b)
    if not os_a or not os_b:
        return True  # assume different if we can't check
    cand_a = (os_a.get("candidate") or "").strip().lower()
    cand_b = (os_b.get("candidate") or "").strip().lower()
    if not cand_a or not cand_b:
        return True
    return cand_a != cand_b


def reference_event_match_score(sem_a, sem_b) -> float:
    """Score how well two temporal markets share a reference event.

    Compares the right_event (reference clock) of each market's proposition.
    Returns 1.0 for identical event IDs, fuzzy word-overlap otherwise.
    """
    prop_a = _proposition(sem_a)
    prop_b = _proposition(sem_b)

    if not prop_a or not prop_b:
        return 0.0
    if not isinstance(prop_a, dict) or not isinstance(prop_b, dict):
        return 0.0

    ref_a = (prop_a.get("right_event") or "").strip().lower()
    ref_b = (prop_b.get("right_event") or "").strip().lower()
    if not ref_a or not ref_b:
        return 0.0
    if ref_a == ref_b:
        return 1.0
    w_a = set(ref_a.replace("_", " ").split())
    w_b = set(ref_b.replace("_", " ").split())
    if not w_a or not w_b:
        return 0.0
    overlap = len(w_a & w_b) / max(len(w_a), len(w_b))
    return round(overlap, 4)


def terms_match_score(sem_a, sem_b) -> float:
    """Score how compatible the terms/resolution criteria of two markets are.

    Uses terms_confidence as a proxy when full resolution text comparison
    is not available. Returns 0 if either market has missing terms.
    """
    if outcome_space_match_score(sem_a, sem_b) >= 1.0 and candidates_are_different(sem_a, sem_b):
        return 1.0
    conf_a = float(getattr(sem_a, "terms_confidence", 0.0) or 0.0)
    conf_b = float(getattr(sem_b, "terms_confidence", 0.0) or 0.0)
    if conf_a == 0.0 or conf_b == 0.0:
        return 0.0
    return round((conf_a + conf_b) / 2, 4)


def propositions_are_inverse(sem_a, sem_b) -> bool:
    """True when market A encodes 'X before Y' and market B encodes 'Y before X'."""
    prop_a = _proposition(sem_a)
    prop_b = _proposition(sem_b)
    if not prop_a or not prop_b:
        return False
    if not isinstance(prop_a, dict) or not isinstance(prop_b, dict):
        return False

    left_a = (prop_a.get("left_event") or "").strip().lower()
    right_a = (prop_a.get("right_event") or "").strip().lower()
    left_b = (prop_b.get("left_event") or "").strip().lower()
    right_b = (prop_b.get("right_event") or "").strip().lower()
    rel_a = (prop_a.get("relation") or "").strip().lower()
    rel_b = (prop_b.get("relation") or "").strip().lower()

    if not left_a or not right_a or not left_b or not right_b:
        return False

    opposites = {("before", "after"), ("after", "before")}
    same_events = (left_a == left_b and right_a == right_b)
    swapped_events = (left_a == right_b and right_a == left_b)

    return (
        (same_events and (rel_a, rel_b) in opposites)
        or (swapped_events and rel_a == rel_b)
    )


def get_competition_id(sem) -> str | None:
    """Extract competition_id from outcome_space_json."""
    os_data = _outcome_space(sem)
    if not os_data:
        return None
    return os_data.get("competition_id") or None


def get_candidate(sem) -> str | None:
    """Extract candidate from outcome_space_json."""
    os_data = _outcome_space(sem)
    if not os_data:
        return None
    return os_data.get("candidate") or None


def get_winner_predicate(sem) -> str | None:
    """Extract winner_predicate from outcome_space_json."""
    os_data = _outcome_space(sem)
    if not os_data:
        return None
    return os_data.get("winner_predicate") or None


def has_event_atoms(sem) -> bool:
    """True if the semantic row has extractable event atoms."""
    atoms = _event_atoms(sem)
    return bool(atoms)


def get_shared_reference_event(sem_a, sem_b) -> str | None:
    """Return the shared right_event if both markets reference the same one."""
    prop_a = _proposition(sem_a)
    prop_b = _proposition(sem_b)
    if not prop_a or not prop_b:
        return None
    ref_a = (prop_a.get("right_event") or "").strip().lower()
    ref_b = (prop_b.get("right_event") or "").strip().lower()
    if ref_a and ref_a == ref_b:
        return ref_a
    return None
