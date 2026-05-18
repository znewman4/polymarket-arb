"""Deterministic relationship taxonomy helpers.

The classifier in this module is intentionally conservative. It may use
semantic v2 JSON when present, but final strategy eligibility is determined
by explicit deterministic subtype/stage checks, not by broad topic overlap.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

_PARTY_ALIASES = {
    "democrats": "Democrats",
    "democratic": "Democrats",
    "democrat": "Democrats",
    "republicans": "Republicans",
    "republican": "Republicans",
    "gop": "Republicans",
}


@dataclass(frozen=True)
class OutcomeSide:
    outcome_space_id: str = ""
    outcome_subtype: str = "same_topic_no_trade"
    entity_type: str = "unknown"
    stage: str = ""
    candidate: str = ""
    party: str = ""
    team: str = ""
    shared_event: str = ""
    shared_reference_event: str = ""
    subtype_reason: str = ""


@dataclass(frozen=True)
class RelationshipTaxonomy:
    relationship_family: str
    relationship_subtype: str
    outcome_space_id: str
    outcome_subtype_a: str
    outcome_subtype_b: str
    entity_type_a: str
    entity_type_b: str
    stage_a: str
    stage_b: str
    candidate_a: str
    candidate_b: str
    party_a: str
    party_b: str
    team_a: str
    team_b: str
    shared_event: str
    shared_reference_event: str
    classification_reason_json: str
    mixed_subtype_reason: str
    strategy_family: str
    strategy_eligible_reason: str
    validation_status_override: str | None = None
    relationship_type_override: str | None = None


def classify_relationship(
    question_a: str,
    question_b: str,
    sem_a: Any | None = None,
    sem_b: Any | None = None,
    *,
    generation_source: str = "",
) -> RelationshipTaxonomy:
    """Classify a pair into family/subtype/stage before strategy validation."""
    side_a = classify_market(question_a, sem_a)
    side_b = classify_market(question_b, sem_b)
    reasons: list[dict[str, Any]] = [
        {"side": "a", "reason": side_a.subtype_reason},
        {"side": "b", "reason": side_b.subtype_reason},
    ]

    shared_space = _shared_space(side_a, side_b)
    shared_ref = side_a.shared_reference_event if side_a.shared_reference_event == side_b.shared_reference_event else ""

    family = "same_topic_no_trade"
    subtype = "same_topic_no_trade"
    mixed_reason = ""
    strategy_family = "none"
    strategy_reason = "not strategy eligible by default"
    status_override: str | None = None
    rel_type_override: str | None = None

    if shared_ref and generation_source == "same_reference_clock":
        family = "same_reference_only"
        subtype = "same_reference_clock_only"
        strategy_reason = "shared reference clock does not create a deterministic tradable pair"
        rel_type_override = "same_reference_clock"
    elif _same_candidate_different_nomination_party(side_a, side_b):
        family = "mixed_subtype"
        subtype = "mixed_party_nomination"
        mixed_reason = "same person appears in nomination markets for different parties"
        status_override = "needs_manual_review"
        rel_type_override = "mixed_subtype"
    elif _championship_conference_pair(side_a, side_b):
        family = "nesting"
        subtype = "championship_implies_conference"
        strategy_family = "nesting"
        strategy_reason = "championship implies conference title only when terms confirm same league path"
        rel_type_override = _nesting_direction(side_a, side_b)
    elif _exact_finish_top_n_pair(side_a, side_b):
        family = "nesting"
        subtype = "exact_finish_implies_top_n"
        strategy_family = "nesting"
        strategy_reason = "exact finish position implies top-N finish"
        rel_type_override = _nesting_direction(side_a, side_b)
    elif _nomination_general_dependency(side_a, side_b):
        family = "dependency"
        subtype = "nomination_to_general_dependency"
        strategy_reason = "staged nomination/general relationship is analysis-only by default"
        status_override = "needs_manual_review"
        rel_type_override = "dependency"
    elif _first_round_general_dependency(side_a, side_b):
        family = "dependency"
        subtype = "first_round_to_general_dependency"
        strategy_reason = "staged election relationship requires terms review"
        status_override = "needs_manual_review"
        rel_type_override = "dependency"
    elif side_a.outcome_subtype != side_b.outcome_subtype and shared_space:
        if {side_a.entity_type, side_b.entity_type} == {"candidate", "party"}:
            family = "mixed_subtype"
            subtype = "candidate_to_party_dependency"
            mixed_reason = "candidate market and party market share a broad election space"
        elif side_a.stage != side_b.stage:
            family = "dependency"
            subtype = _stage_dependency_subtype(side_a, side_b)
            mixed_reason = "same entity/topic but different event stages"
        elif side_a.entity_type != side_b.entity_type:
            family = "mixed_subtype"
            subtype = "mixed_entity_type"
            mixed_reason = "same broad outcome space but different entity types"
        else:
            family = "mixed_subtype"
            subtype = f"{side_a.outcome_subtype}_vs_{side_b.outcome_subtype}"
            mixed_reason = "same broad outcome space but different outcome subtypes"
        status_override = "needs_manual_review" if family == "dependency" else "rejected"
        rel_type_override = family
    elif (
        shared_space
        and side_a.outcome_subtype == side_b.outcome_subtype
        and side_a.stage == side_b.stage
        and side_a.entity_type == side_b.entity_type
        and side_a.entity_type in {"candidate", "party", "team", "option"}
        and _different_options(side_a, side_b)
    ):
        if side_a.outcome_subtype == "party_winner_same_election":
            family = "inverse"
            subtype = "party_winner_same_election"
            strategy_family = "party_inverse"
            strategy_reason = "party pair is tradable only when registry/terms handle third-party outcomes"
            rel_type_override = "inverse"
        else:
            family = "mutual_exclusion"
            subtype = side_a.outcome_subtype
            strategy_family = "pairwise_mutual_exclusion"
            strategy_reason = "same outcome subtype/stage/scope with different options"
            rel_type_override = "mutually_exclusive_category"
    elif shared_ref:
        family = "same_reference_only"
        subtype = "same_reference_clock_only"
        strategy_reason = "shared reference clock only; no deterministic implication"
        rel_type_override = "same_reference_clock"

    reason_payload = {
        "side_a": asdict(side_a),
        "side_b": asdict(side_b),
        "shared_space": shared_space,
        "shared_reference_event": shared_ref,
        "reasons": reasons,
    }
    outcome_space_id = shared_space or side_a.outcome_space_id or side_b.outcome_space_id
    shared_event = side_a.shared_event if side_a.shared_event == side_b.shared_event else outcome_space_id
    return RelationshipTaxonomy(
        relationship_family=family,
        relationship_subtype=subtype,
        outcome_space_id=outcome_space_id,
        outcome_subtype_a=side_a.outcome_subtype,
        outcome_subtype_b=side_b.outcome_subtype,
        entity_type_a=side_a.entity_type,
        entity_type_b=side_b.entity_type,
        stage_a=side_a.stage,
        stage_b=side_b.stage,
        candidate_a=side_a.candidate,
        candidate_b=side_b.candidate,
        party_a=side_a.party,
        party_b=side_b.party,
        team_a=side_a.team,
        team_b=side_b.team,
        shared_event=shared_event,
        shared_reference_event=shared_ref,
        classification_reason_json=json.dumps(reason_payload, sort_keys=True),
        mixed_subtype_reason=mixed_reason,
        strategy_family=strategy_family,
        strategy_eligible_reason=strategy_reason,
        validation_status_override=status_override,
        relationship_type_override=rel_type_override,
    )


def classify_market(question: str, sem: Any | None = None) -> OutcomeSide:
    q = _clean_question(question)
    from_sem = _side_from_semantic_outcome_space(q, sem)
    if from_sem.outcome_subtype != "same_topic_no_trade":
        return from_sem
    for classifier in (
        _politics_side,
        _sports_side,
        _entertainment_side,
        _crypto_side,
        _temporal_side,
    ):
        side = classifier(q)
        if side.outcome_subtype != "same_topic_no_trade":
            return side
    return OutcomeSide(subtype_reason="no deterministic subtype matched")


def taxonomy_to_reason(taxonomy: RelationshipTaxonomy) -> dict[str, Any]:
    return {
        "relationship_family": taxonomy.relationship_family,
        "relationship_subtype": taxonomy.relationship_subtype,
        "mixed_subtype_reason": taxonomy.mixed_subtype_reason,
        "strategy_family": taxonomy.strategy_family,
        "strategy_eligible_reason": taxonomy.strategy_eligible_reason,
    }


def _side_from_semantic_outcome_space(question: str, sem: Any | None) -> OutcomeSide:
    raw = getattr(sem, "outcome_space_json", None) if sem is not None else None
    if not raw:
        return OutcomeSide()
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return OutcomeSide()
    if not isinstance(data, dict):
        return OutcomeSide()
    if data.get("kind") != "single_winner_competition":
        return OutcomeSide()
    candidate = str(data.get("candidate") or "").strip()
    comp = str(data.get("competition_id") or "").strip()
    if not candidate or not comp:
        return OutcomeSide()
    q_side = _politics_side(question)
    if q_side.outcome_subtype != "same_topic_no_trade":
        return q_side
    sports_side = _sports_side(question)
    if sports_side.outcome_subtype != "same_topic_no_trade":
        return sports_side
    return OutcomeSide(
        outcome_space_id=_slug(comp),
        outcome_subtype="team_wins_championship",
        entity_type="team",
        stage="championship",
        team=candidate,
        candidate=candidate,
        shared_event=_slug(comp),
        subtype_reason="semantic single_winner_competition fallback",
    )


def _politics_side(q: str) -> OutcomeSide:
    year = _year(q)
    lower = q.lower()
    if "nomination" in lower:
        party = _party(lower)
        candidate = _extract_before(q, r"\s+wins?\s+(?:the\s+)?(?:democratic|republican|gop)\s+(?:presidential\s+)?nomination")
        if not candidate:
            candidate = _extract_before(q, r"\s+wins?\s+(?:the\s+)?(?:.+?\s+)?nomination")
        party_key = _slug(party) if party else "unknown_party"
        year_key = year or "unknown_year"
        return OutcomeSide(
            outcome_space_id=f"{year_key}_{party_key}_presidential_nomination",
            outcome_subtype="candidate_wins_nomination",
            entity_type="candidate",
            stage="nomination",
            candidate=candidate,
            party=party,
            shared_event=f"{year_key}_{party_key}_presidential_nomination",
            subtype_reason="candidate nomination pattern",
        )
    if "first round" in lower or "1st round" in lower:
        candidate = _extract_before(q, r"\s+wins?\s+(?:the\s+)?(?:1st|first)\s+round")
        space = _election_space(q, year, "first_round")
        return OutcomeSide(
            outcome_space_id=space,
            outcome_subtype="first_round_winner",
            entity_type="candidate",
            stage="first_round",
            candidate=candidate,
            shared_event=space,
            subtype_reason="first round election pattern",
        )
    if "presidential election" in lower or "election" in lower:
        party = _party(lower)
        if party and re.search(r"\b(democrats|democratic|republicans|republican|gop)\b.*\bwin", lower):
            space = _election_space(q, year, "party_winner")
            return OutcomeSide(
                outcome_space_id=space,
                outcome_subtype="party_winner_same_election",
                entity_type="party",
                stage="general_election",
                party=party,
                shared_event=space,
                subtype_reason="party winner election pattern",
            )
        candidate = _extract_before(q, r"\s+wins?\s+(?:the\s+)?(?:.+?\s+)?(?:presidential\s+)?election")
        if candidate:
            space = _election_space(q, year, "candidate_winner")
            return OutcomeSide(
                outcome_space_id=space,
                outcome_subtype="candidate_winner_same_election",
                entity_type="candidate",
                stage="general_election",
                candidate=candidate,
                party=party,
                shared_event=space,
                subtype_reason="candidate winner election pattern",
            )
    return OutcomeSide()


_LEAGUE_CHAMPIONSHIPS = re.compile(
    r"\b(?:premier\s+league|epl|bundesliga|la\s+liga|serie\s+a|ligue\s+1|champions\s+league"
    r"|super\s+bowl|world\s+series|stanley\s+cup|world\s+cup|nba\s+championship)\b",
    re.IGNORECASE,
)

_CONFERENCE_DIRECTIONS = re.compile(
    r"\b(?:western|eastern|north|south|afc|nfc)\b", re.IGNORECASE
)


def _sports_side(q: str) -> OutcomeSide:
    lower = q.lower()

    # ── Top-N finish ──────────────────────────────────────────────────────────
    if "finish" in lower and re.search(r"\btop\s+\d+\b", lower):
        # Handle "finish top 4", "finish in top 4", "finish in the top 4"
        team = _extract_before(q, r"\s+finish(?:es)?(?:\s+in(?:\s+the)?)?\s+top\s+\d+")
        league = _league_key(lower)
        space = f"{league}_finish_position" if league else "finish_position"
        return OutcomeSide(
            outcome_space_id=space,
            outcome_subtype="team_top_n_finish",
            entity_type="team",
            stage="league_finish",
            team=team,
            shared_event=space,
            subtype_reason="team top-N finish pattern",
        )

    # ── Exact ordinal finish position ─────────────────────────────────────────
    if "finish" in lower and re.search(r"\b\d+(st|nd|rd|th)\b", lower):
        # Handle "finish 2nd", "finish in 2nd", "finish in 2nd place"
        team = _extract_before(q, r"\s+finish(?:es)?(?:\s+in)?\s+\d+(?:st|nd|rd|th)")
        league = _league_key(lower)
        space = f"{league}_finish_position" if league else "finish_position"
        return OutcomeSide(
            outcome_space_id=space,
            outcome_subtype="team_exact_finish_position",
            entity_type="team",
            stage="league_finish",
            team=team,
            shared_event=space,
            subtype_reason="team exact finish position pattern",
        )

    # ── Conference winner (handles optional league prefix, e.g. "NBA Western") ─
    if (
        "conference finals" in lower
        or "western conference" in lower
        or "eastern conference" in lower
        or (
            _CONFERENCE_DIRECTIONS.search(lower)
            and "conference" in lower
        )
    ):
        # Allow one optional token (like "NBA") between "the" and the direction word
        team = _extract_before(
            q,
            r"\s+wins?\s+(?:the\s+)?(?:\w+\s+)?(?:western|eastern|north|south|afc|nfc)\s+conference",
        )
        conf = (
            "western_conference"
            if "western" in lower or "afc" in lower
            else ("eastern_conference" if "eastern" in lower or "nfc" in lower else "conference")
        )
        return OutcomeSide(
            outcome_space_id=f"nba_{conf}_winner",
            outcome_subtype="team_wins_conference",
            entity_type="team",
            stage="conference",
            team=team,
            shared_event=f"nba_{conf}_winner",
            subtype_reason="team wins conference pattern",
        )

    # ── Championship / finals / league title ──────────────────────────────────
    # Order matters: check "finals" before broader "wins the league" so
    # "NBA Finals" doesn't fall through to the league-title branch.
    if "finals" in lower or "championship" in lower or "stanley cup" in lower:
        team = _extract_before(q, r"\s+wins?\s+(?:the\s+)?")
        comp = "nba_finals" if "nba" in lower or "finals" in lower else _competition_tail(lower)
        return OutcomeSide(
            outcome_space_id=_slug(comp),
            outcome_subtype="team_wins_championship",
            entity_type="team",
            stage="championship",
            team=team,
            shared_event=_slug(comp),
            subtype_reason="team wins championship/finals pattern",
        )

    # ── League title (Premier League, Bundesliga, World Cup, etc.) ───────────
    # Separate from the "finals/championship" branch so "wins the Premier League"
    # (which has no "finals" keyword) is correctly classified as championship.
    league_match = _LEAGUE_CHAMPIONSHIPS.search(lower)
    if league_match and re.search(r"\bwins?\b", lower):
        league = _league_key(lower) or _slug(league_match.group(0))
        team = _extract_before(q, r"\s+wins?\s+(?:the\s+)?(?:\d{4}[–\-]\d{2,4}\s+)?")  # noqa: RUF001
        return OutcomeSide(
            outcome_space_id=f"{league}_champion",
            outcome_subtype="team_wins_championship",
            entity_type="team",
            stage="championship",
            team=team,
            shared_event=f"{league}_champion",
            subtype_reason="team wins league title pattern",
        )

    return OutcomeSide()


def _entertainment_side(q: str) -> OutcomeSide:
    lower = q.lower()
    if "before" in lower:
        left, right = _before_parts(q)
        if left and right:
            subtype = "album_release_before_reference" if "album" in left.lower() else "movie_release_before_reference"
            return OutcomeSide(
                outcome_space_id=f"{_slug(right)}_reference_clock",
                outcome_subtype=subtype,
                entity_type="event",
                stage="temporal",
                candidate=left,
                shared_reference_event=_slug(right),
                subtype_reason="entertainment before-reference pattern",
            )
    if "next" in lower and ("actor" in lower or "james bond" in lower):
        return OutcomeSide(
            outcome_space_id="next_actor_announced",
            outcome_subtype="next_actor_announced",
            entity_type="candidate",
            stage="announcement",
            candidate=_extract_before(q, r"\s+(?:be\s+)?(?:the\s+)?next"),
            shared_event="next_actor_announced",
            subtype_reason="next actor announced pattern",
        )
    return OutcomeSide()


def _crypto_side(q: str) -> OutcomeSide:
    lower = q.lower()
    if "bitcoin" in lower and ("$1m" in lower or "1m" in lower or "1 million" in lower):
        ref = ""
        if "before" in lower:
            _, right = _before_parts(q)
            ref = _slug(right)
        return OutcomeSide(
            outcome_space_id=f"{ref or 'date'}_reference_clock",
            outcome_subtype="price_hits_level_before_reference" if ref else "price_hits_level_before_date",
            entity_type="threshold",
            stage="temporal",
            candidate="Bitcoin 1m",
            shared_reference_event=ref,
            subtype_reason="crypto threshold before-reference pattern",
        )
    return OutcomeSide()


def _temporal_side(q: str) -> OutcomeSide:
    left, right = _before_parts(q)
    if left and right:
        return OutcomeSide(
            outcome_space_id=f"{_slug(right)}_reference_clock",
            outcome_subtype="event_a_before_event_b",
            entity_type="event",
            stage="temporal",
            candidate=left,
            shared_reference_event=_slug(right),
            subtype_reason="generic before-reference pattern",
        )
    return OutcomeSide()


def _shared_space(a: OutcomeSide, b: OutcomeSide) -> str:
    if a.outcome_space_id and a.outcome_space_id == b.outcome_space_id:
        return a.outcome_space_id
    broad_a = _broad_space(a.outcome_space_id)
    broad_b = _broad_space(b.outcome_space_id)
    if broad_a and broad_a == broad_b:
        return broad_a
    return ""


def _broad_space(space: str) -> str:
    return re.sub(r"_(candidate_winner|party_winner)$", "", space or "")


def _different_options(a: OutcomeSide, b: OutcomeSide) -> bool:
    return bool(_option(a) and _option(b) and _slug(_option(a)) != _slug(_option(b)))


def _option(side: OutcomeSide) -> str:
    return side.candidate or side.party or side.team


def _nomination_general_dependency(a: OutcomeSide, b: OutcomeSide) -> bool:
    return bool(
        {a.stage, b.stage} == {"nomination", "general_election"}
        and a.candidate
        and _slug(a.candidate) == _slug(b.candidate)
    )


def _first_round_general_dependency(a: OutcomeSide, b: OutcomeSide) -> bool:
    return bool(
        {a.stage, b.stage} == {"first_round", "general_election"}
        and a.candidate
        and _slug(a.candidate) == _slug(b.candidate)
    )


def _same_candidate_different_nomination_party(a: OutcomeSide, b: OutcomeSide) -> bool:
    return bool(
        a.stage == b.stage == "nomination"
        and a.candidate
        and _slug(a.candidate) == _slug(b.candidate)
        and a.party
        and b.party
        and a.party != b.party
    )


def _championship_conference_pair(a: OutcomeSide, b: OutcomeSide) -> bool:
    if {a.outcome_subtype, b.outcome_subtype} != {"team_wins_championship", "team_wins_conference"}:
        return False
    # If both team names are known they must match; if only one is known that's fine.
    if a.team and b.team:
        return _slug(a.team) == _slug(b.team)
    # At least one side has a team name — accept (mismatch caught elsewhere).
    return bool(a.team or b.team)


def _exact_finish_top_n_pair(a: OutcomeSide, b: OutcomeSide) -> bool:
    return bool(
        {a.outcome_subtype, b.outcome_subtype} == {"team_exact_finish_position", "team_top_n_finish"}
        and a.team
        and _slug(a.team) == _slug(b.team)
    )


def _nesting_direction(a: OutcomeSide, b: OutcomeSide) -> str:
    narrow = {"team_wins_championship", "team_exact_finish_position"}
    return "nested_a_implies_b" if a.outcome_subtype in narrow else "nested_b_implies_a"


def _stage_dependency_subtype(a: OutcomeSide, b: OutcomeSide) -> str:
    if {a.stage, b.stage} == {"nomination", "general_election"}:
        return "nomination_to_general_dependency"
    if {a.stage, b.stage} == {"first_round", "general_election"}:
        return "first_round_to_general_dependency"
    return "mixed_stage"


def _clean_question(question: str) -> str:
    return " ".join((question or "").strip().rstrip("?").split())


def _extract_before(q: str, pattern: str) -> str:
    match = re.search(pattern, q, flags=re.IGNORECASE)
    if not match:
        return ""
    value = re.sub(r"^will\s+", "", q[: match.start()].strip(), flags=re.IGNORECASE).strip()
    return re.sub(r"^(the|a|an)\s+", "", value, flags=re.IGNORECASE).strip()


def _year(q: str) -> str:
    match = re.search(r"\b(20\d{2})\b", q)
    return match.group(1) if match else "unknown_year"


def _party(lower: str) -> str:
    for key, canonical in _PARTY_ALIASES.items():
        if re.search(rf"\b{re.escape(key)}\b", lower):
            return canonical
    return ""


def _election_space(q: str, year: str, suffix: str) -> str:
    lower = q.lower()
    country = "us" if "us " in lower or "u.s." in lower or "presidential election" in lower else _slug(
        re.sub(r".*?\bof\s+", "", lower)
    )
    if "colomb" in lower:
        country = "colombian"
    return f"{year}_{country}_presidential_election_{suffix}"


def _before_parts(q: str) -> tuple[str, str]:
    match = re.match(r"^(?:will\s+)?(?P<left>.+?)\s+before\s+(?P<right>.+)$", q, flags=re.IGNORECASE)
    if not match:
        return "", ""
    return match.group("left").strip(), match.group("right").strip()


def _league_key(lower: str) -> str:
    if "premier league" in lower or "epl" in lower:
        return "premier_league"
    if "nba" in lower:
        return "nba"
    return ""


def _competition_tail(lower: str) -> str:
    match = re.search(r"win(?:s)?\s+(?:the\s+)?(?P<tail>.+)$", lower)
    return match.group("tail").strip() if match else lower


def _slug(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", (value or "").lower())
    return "_".join(words)
