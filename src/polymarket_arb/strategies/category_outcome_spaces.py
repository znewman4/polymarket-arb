"""N-way category outcome-space grouping for mutually exclusive winner markets."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..storage.base import RelationshipCandidateRow


def normalize_outcome_space_id(value: str | None) -> str:
    if not value:
        return "unknown"
    words = re.findall(r"[a-z0-9]+", value.lower())
    return "_".join(words) or "unknown"


@dataclass(frozen=True)
class OutcomeSpaceMetadata:
    outcome_space_id: str
    name: str
    known_total_candidates: int | None = None
    aliases: tuple[str, ...] = ()
    outcome_subtype: str = ""
    completeness_policy: str = "complete_if_all_expected_present"
    allow_bundle_backtest: bool = True
    required_candidates_or_options: tuple[str, ...] = ()


@dataclass(frozen=True)
class CategoryCandidate:
    market_id: str
    candidate: str
    question: str
    yes_token_id: str
    no_token_id: str


@dataclass(frozen=True)
class CategoryOutcomeSpace:
    outcome_space_id: str
    display_name: str
    candidates: tuple[CategoryCandidate, ...]
    known_total_candidates: int | None
    source_relationship_ids: tuple[str, ...] = field(default_factory=tuple)
    registry_status: str = "unconfigured"
    configured_expected_total: int | None = None
    completeness_policy: str = "incomplete_unknown"
    allow_bundle_backtest: bool = False
    completeness_reason: str = ""


def load_outcome_space_metadata(path: Path | None) -> dict[str, OutcomeSpaceMetadata]:
    if path is None or not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("outcome_spaces") or {}
    result: dict[str, OutcomeSpaceMetadata] = {}
    for raw_id, data in entries.items():
        if not isinstance(data, dict):
            continue
        outcome_space_id = normalize_outcome_space_id(str(raw_id))
        aliases = tuple(normalize_outcome_space_id(str(a)) for a in data.get("aliases") or [])
        known_total = data.get("expected_total_outcomes", data.get("known_total_candidates"))
        allow_backtest = data.get("allow_bundle_backtest")
        if allow_backtest is None:
            allow_backtest = known_total is not None
        meta = OutcomeSpaceMetadata(
            outcome_space_id=outcome_space_id,
            name=str(data.get("display_name") or data.get("name") or raw_id),
            known_total_candidates=known_total,
            aliases=aliases,
            outcome_subtype=str(data.get("outcome_subtype") or ""),
            completeness_policy=str(data.get("completeness_policy") or "complete_if_all_expected_present"),
            allow_bundle_backtest=bool(allow_backtest),
            required_candidates_or_options=tuple(str(v) for v in data.get("required_candidates_or_options") or []),
        )
        result[outcome_space_id] = meta
        for alias in aliases:
            result[alias] = meta
    return result


def group_template_mutual_exclusion_spaces(
    relationships: list[RelationshipCandidateRow],
    *,
    template_rel_ids: set[str],
    strategy_conditions: frozenset[str] = frozenset({"mutually_exclusive"}),
) -> list[CategoryOutcomeSpace]:
    """Group template-approved mutual exclusion relationships into N-way bundles.

    Unlike ``group_category_outcome_spaces``, this function:
    - Accepts any relationship_type and any validation_status.
    - Groups by ``outcome_space_id`` (not ``shared_event``).
    - Marks produced spaces as ``allow_bundle_backtest=True`` (template-approved).
    - Only processes pairs whose relationship_id is in ``template_rel_ids``.

    All produced spaces are labelled ``registry_status="template_auto_applied"``
    and ``completeness_policy="auto_applied_partial"`` — meaning we treat the
    observed markets as the full bundle without registry verification.

    RESEARCH-ONLY. All bundles require human review before credibility upgrade.
    """
    grouped: dict[str, dict[str, Any]] = {}

    for rel in relationships:
        if rel.relationship_id not in template_rel_ids:
            continue
        space_id = (rel.outcome_space_id or "").strip()
        if not space_id or space_id in ("same_topic_no_trade", "unknown", ""):
            continue
        normalized = normalize_outcome_space_id(space_id)
        bucket = grouped.setdefault(normalized, {
            "display_name": normalized.replace("_", " ").title(),
            "known_total_candidates": None,
            "candidates": {},
            "relationship_ids": set(),
        })
        bucket["relationship_ids"].add(rel.relationship_id)
        # Prefer team_a/b as candidate name for sports; use candidate_a/b for electoral.
        cand_a = rel.candidate_a or rel.team_a or None
        cand_b = rel.candidate_b or rel.team_b or None
        _add_candidate(
            bucket["candidates"],
            market_id=rel.market_id_a,
            candidate=cand_a,
            question=rel.question_a,
            yes_token_id=rel.token_id_a_yes,
            no_token_id=rel.token_id_a_no,
        )
        _add_candidate(
            bucket["candidates"],
            market_id=rel.market_id_b,
            candidate=cand_b,
            question=rel.question_b,
            yes_token_id=rel.token_id_b_yes,
            no_token_id=rel.token_id_b_no,
        )

    spaces: list[CategoryOutcomeSpace] = []
    for outcome_space_id, bucket in grouped.items():
        candidates = tuple(sorted(bucket["candidates"].values(), key=lambda c: (c.candidate, c.market_id)))
        spaces.append(CategoryOutcomeSpace(
            outcome_space_id=outcome_space_id,
            display_name=bucket["display_name"],
            candidates=candidates,
            known_total_candidates=None,
            source_relationship_ids=tuple(sorted(bucket["relationship_ids"])),
            registry_status="template_auto_applied",
            configured_expected_total=None,
            completeness_policy="auto_applied_partial",
            allow_bundle_backtest=True,
            completeness_reason="template auto-applied — RESEARCH-ONLY, pending human review",
        ))
    return sorted(spaces, key=lambda s: (-len(s.candidates), s.outcome_space_id))


def group_category_outcome_spaces(
    relationships: list[RelationshipCandidateRow],
    *,
    metadata: dict[str, OutcomeSpaceMetadata] | None = None,
) -> list[CategoryOutcomeSpace]:
    metadata = metadata or {}
    grouped: dict[str, dict[str, Any]] = {}

    for rel in relationships:
        if rel.relationship_type != "mutually_exclusive_category":
            continue
        if rel.validation_status != "accepted":
            continue
        raw_space_id = rel.shared_event or _space_id_from_questions(rel.question_a, rel.question_b)
        normalized = normalize_outcome_space_id(raw_space_id)
        meta = metadata.get(normalized)
        outcome_space_id = meta.outcome_space_id if meta else normalized
        bucket = grouped.setdefault(outcome_space_id, {
            "display_name": meta.name if meta else raw_space_id,
            "known_total_candidates": meta.known_total_candidates if meta else None,
            "metadata": meta,
            "candidates": {},
            "relationship_ids": set(),
        })
        bucket["relationship_ids"].add(rel.relationship_id)
        _add_candidate(
            bucket["candidates"],
            market_id=rel.market_id_a,
            candidate=rel.candidate_a,
            question=rel.question_a,
            yes_token_id=rel.token_id_a_yes,
            no_token_id=rel.token_id_a_no,
        )
        _add_candidate(
            bucket["candidates"],
            market_id=rel.market_id_b,
            candidate=rel.candidate_b,
            question=rel.question_b,
            yes_token_id=rel.token_id_b_yes,
            no_token_id=rel.token_id_b_no,
        )

    spaces: list[CategoryOutcomeSpace] = []
    for outcome_space_id, bucket in grouped.items():
        candidates = tuple(sorted(bucket["candidates"].values(), key=lambda c: (c.candidate, c.market_id)))
        meta = bucket.get("metadata")
        registry_status = "configured" if meta else "unconfigured"
        reason = _completeness_reason(candidates, meta)
        spaces.append(CategoryOutcomeSpace(
            outcome_space_id=outcome_space_id,
            display_name=str(bucket["display_name"]),
            candidates=candidates,
            known_total_candidates=bucket["known_total_candidates"],
            source_relationship_ids=tuple(sorted(bucket["relationship_ids"])),
            registry_status=registry_status,
            configured_expected_total=bucket["known_total_candidates"],
            completeness_policy=meta.completeness_policy if meta else "incomplete_unknown",
            allow_bundle_backtest=bool(meta.allow_bundle_backtest) if meta else False,
            completeness_reason=reason,
        ))
    return sorted(spaces, key=lambda s: (s.outcome_space_id, len(s.candidates)))


def _add_candidate(
    candidates: dict[str, CategoryCandidate],
    *,
    market_id: str,
    candidate: str | None,
    question: str,
    yes_token_id: str | None,
    no_token_id: str | None,
) -> None:
    if not yes_token_id or not no_token_id:
        return
    candidate_name = (candidate or _candidate_from_question(question) or market_id).strip()
    key = normalize_outcome_space_id(candidate_name)
    existing = candidates.get(key)
    if existing and existing.market_id <= market_id:
        return
    candidates[key] = CategoryCandidate(
        market_id=market_id,
        candidate=candidate_name,
        question=question,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
    )


def _completeness_reason(
    candidates: tuple[CategoryCandidate, ...],
    meta: OutcomeSpaceMetadata | None,
) -> str:
    if meta is None:
        return "outcome space is not configured in registry"
    observed = len(candidates)
    if meta.completeness_policy == "incomplete_unknown":
        return "registry marks this outcome space as open-ended/incomplete"
    if meta.known_total_candidates and observed < meta.known_total_candidates:
        return f"observed {observed}/{meta.known_total_candidates} expected outcomes"
    if meta.required_candidates_or_options:
        present = {normalize_outcome_space_id(c.candidate) for c in candidates}
        missing = [
            option for option in meta.required_candidates_or_options
            if normalize_outcome_space_id(option) not in present
        ]
        if missing:
            return "missing required options: " + ", ".join(missing)
    return "registry completeness policy satisfied"


def _space_id_from_questions(question_a: str, question_b: str) -> str:
    return _competition_from_question(question_a) or _competition_from_question(question_b) or "unknown"


def _competition_from_question(question: str) -> str | None:
    match = re.match(r"^will\s+.+?\s+win\s+(?:the\s+)?(?P<competition>.+?)\??$", question.strip(), re.I)
    if not match:
        return None
    return normalize_outcome_space_id(match.group("competition"))


def _candidate_from_question(question: str) -> str | None:
    match = re.match(r"^will\s+(?P<candidate>.+?)\s+win\s+", question.strip(), re.I)
    if not match:
        return None
    return match.group("candidate").strip()
