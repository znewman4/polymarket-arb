"""Derive candidate outcome/context spaces from universe-discovery JSONL."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class SpaceDiscoveryResult:
    discovery_run_id: str
    output_dir: Path
    output_path: Path
    rows: list[dict[str, Any]] = field(default_factory=list)


_COLUMNS = [
    "space_id",
    "source_type",
    "source_ids",
    "domain",
    "market_count",
    "event_count",
    "tags",
    "series",
    "sport",
    "teams",
    "candidate_entities",
    "inferred_relationship_families_possible",
    "confidence",
    "needs_semantic_extraction",
    "discovery_reason",
]


def run_space_discovery(
    discovery_run_id: str,
    data_root: Path,
    *,
    output_dir: Path | None = None,
) -> SpaceDiscoveryResult:
    """Read a discovery JSONL run and emit candidate spaces parquet."""

    run_dir = data_root / "raw" / "market_universe" / discovery_run_id
    out_dir = output_dir or data_root / "normalised" / "discovered_spaces" / discovery_run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / "spaces.parquet"

    markets = _read_payloads(run_dir / "markets.jsonl")
    events = _read_payloads(run_dir / "events.jsonl")
    tags = _read_payloads(run_dir / "tags.jsonl")
    related_tags = _read_related_tags(run_dir / "related_tags.jsonl")

    tag_labels = {
        _id(t): str(t.get("label") or t.get("name") or t.get("slug") or _id(t))
        for t in tags
        if _id(t)
    }
    # Tags from event payloads carry their labels inline — index them too.
    for market in markets:
        for ev in _nested_events(market):
            for t in ev.get("tags") or []:
                if isinstance(t, dict):
                    tid = _id(t)
                    if tid and tid not in tag_labels:
                        tag_labels[tid] = str(
                            t.get("label") or t.get("name") or t.get("slug") or tid
                        )
    market_by_id = {_id(m): m for m in markets if _id(m)}
    rows: list[dict[str, Any]] = []

    event_groups: dict[str, set[str]] = defaultdict(set)
    event_payloads = {_id(e): e for e in events if _id(e)}
    for event_id, event in event_payloads.items():
        for mid in _event_market_ids(event):
            if mid:
                event_groups[event_id].add(mid)
    for market in markets:
        event_id = _event_id(market)
        mid = _id(market)
        if event_id and mid:
            event_groups[event_id].add(mid)

    for event_id, market_ids in sorted(event_groups.items()):
        if len(market_ids) < 2:
            continue
        event = event_payloads.get(event_id, {})
        event_markets = [market_by_id[mid] for mid in sorted(market_ids) if mid in market_by_id]
        rows.append(_space_row(
            space_id=f"event:{event_id}",
            source_type="event",
            source_ids=[event_id],
            markets=event_markets,
            domain=_domain_for(event, event_markets),
            confidence=0.9,
            discovery_reason="markets share event_id",
        ))

    tag_groups: dict[str, set[str]] = defaultdict(set)
    for market in markets:
        mid = _id(market)
        for tag_id in _market_tag_ids(market):
            if mid and tag_id:
                tag_groups[tag_id].add(mid)
    for tag_id, market_ids in sorted(tag_groups.items()):
        if len(market_ids) < 2:
            continue
        label = tag_labels.get(tag_id, tag_id)
        # Skip meta-tag "All" which Gamma stamps on every market.
        if _slug(str(label)) in {"all", "uncategorized"}:
            continue
        tag_markets = [market_by_id[mid] for mid in sorted(market_ids) if mid in market_by_id]
        # Wider buckets are weaker candidates; gate confidence on breadth.
        confidence = 0.65 if len(market_ids) <= 50 else 0.45
        rows.append(_space_row(
            space_id=f"tag:{_slug(str(label))}",
            source_type="tag",
            source_ids=[tag_id],
            markets=tag_markets,
            domain=str(label),
            confidence=confidence,
            discovery_reason="markets share Gamma tag",
        ))

    for cluster_id, cluster_tag_ids in _related_tag_clusters(related_tags).items():
        market_ids = set()
        for tag_id in cluster_tag_ids:
            market_ids.update(tag_groups.get(tag_id, set()))
        if len(market_ids) < 2:
            continue
        cluster_markets = [market_by_id[mid] for mid in sorted(market_ids) if mid in market_by_id]
        rows.append(_space_row(
            space_id=f"related_tags:{_slug(cluster_id)}",
            source_type="related_tags",
            source_ids=sorted(cluster_tag_ids),
            markets=cluster_markets,
            domain="related_tags",
            confidence=0.6,
            discovery_reason="markets share a related-tag cluster",
        ))

    for source_type, getter, prefix, confidence in (
        ("series", _series_id, "series", 0.7),
        ("sport", _sport_id, "sport", 0.7),
        ("team", _team_ids, "team", 0.7),
    ):
        grouped: dict[str, set[str]] = defaultdict(set)
        for market in markets:
            mid = _id(market)
            values = getter(market)
            if isinstance(values, str):
                values = [values]
            for value in values:
                if mid and value:
                    grouped[str(value)].add(mid)
        for value, market_ids in sorted(grouped.items()):
            if len(market_ids) < 2:
                continue
            grouped_markets = [market_by_id[mid] for mid in sorted(market_ids) if mid in market_by_id]
            rows.append(_space_row(
                space_id=f"{prefix}:{_slug(value)}",
                source_type=source_type,
                source_ids=[value],
                markets=grouped_markets,
                domain=source_type,
                confidence=confidence,
                discovery_reason=f"markets share {source_type}_id",
            ))

    category_groups: dict[str, set[str]] = defaultdict(set)
    for market in markets:
        mid = _id(market)
        cat = _market_category(market)
        if mid and cat:
            category_groups[cat].add(mid)
    for category, market_ids in sorted(category_groups.items()):
        if len(market_ids) < 2:
            continue
        cat_markets = [market_by_id[mid] for mid in sorted(market_ids) if mid in market_by_id]
        rows.append(_space_row(
            space_id=f"category:{_slug(category)}",
            source_type="category",
            source_ids=[category],
            markets=cat_markets,
            domain=category,
            confidence=0.5,
            discovery_reason="markets share Gamma category (broad candidate container)",
        ))

    outcome_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for market in markets:
        key = _winner_pattern_key(market)
        if key:
            outcome_groups[key].append(market)
    for key, grouped_markets in sorted(outcome_groups.items()):
        if len(grouped_markets) < 2:
            continue
        rows.append(_space_row(
            space_id=f"outcome_pattern:{_slug(key)}",
            source_type="outcome_pattern",
            source_ids=[key],
            markets=grouped_markets,
            domain="shared_outcome_pattern",
            confidence=0.55,
            discovery_reason="questions share winner/outcome pattern",
            families=["mutual_exclusion"],
        ))

    rows = _dedupe_spaces(rows)
    pd.DataFrame(rows, columns=_COLUMNS).to_parquet(output_path, index=False)
    return SpaceDiscoveryResult(
        discovery_run_id=discovery_run_id,
        output_dir=out_dir,
        output_path=output_path,
        rows=rows,
    )


def _read_payloads(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        payload = raw.get("payload", raw)
        if isinstance(payload, dict):
            out.append(payload)
    return out


def _read_related_tags(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    pairs: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        tag_id = str(raw.get("tag_id") or "")
        payload = raw.get("payload", raw)
        related_id = _id(payload) if isinstance(payload, dict) else ""
        if tag_id and related_id:
            pairs.append((tag_id, related_id))
    return pairs


def _space_row(
    *,
    space_id: str,
    source_type: str,
    source_ids: list[str],
    markets: list[dict[str, Any]],
    domain: str,
    confidence: float,
    discovery_reason: str,
    families: list[str] | None = None,
) -> dict[str, Any]:
    event_ids = sorted({_event_id(m) for m in markets if _event_id(m)})
    tags = sorted({tag for m in markets for tag in _market_tag_ids(m)})
    series = sorted({_series_id(m) for m in markets if _series_id(m)})
    sports = sorted({_sport_id(m) for m in markets if _sport_id(m)})
    teams = sorted({team for m in markets for team in _team_ids(m)})
    entities = sorted({_candidate_entity(m) for m in markets if _candidate_entity(m)})
    inferred = families or ["mutual_exclusion", "nesting"]
    return {
        "space_id": space_id,
        "source_type": source_type,
        "source_ids": json.dumps(source_ids, sort_keys=True),
        "domain": domain or "unknown",
        "market_count": len({_id(m) for m in markets if _id(m)}),
        "event_count": len(event_ids),
        "tags": json.dumps(tags, sort_keys=True),
        "series": json.dumps(series, sort_keys=True),
        "sport": sports[0] if len(sports) == 1 else json.dumps(sports, sort_keys=True),
        "teams": json.dumps(teams, sort_keys=True),
        "candidate_entities": json.dumps(entities, sort_keys=True),
        "inferred_relationship_families_possible": json.dumps(inferred, sort_keys=True),
        "confidence": confidence,
        "needs_semantic_extraction": True,
        "discovery_reason": discovery_reason,
    }


def _dedupe_spaces(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        existing = by_id.get(row["space_id"])
        if existing is None or float(row["confidence"]) > float(existing["confidence"]):
            by_id[row["space_id"]] = row
    return sorted(by_id.values(), key=lambda r: (r["source_type"], r["space_id"]))


def _id(raw: dict[str, Any]) -> str:
    for key in ("id", "market_id", "marketId"):
        value = raw.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _event_id(raw: dict[str, Any]) -> str:
    for key in ("event_id", "eventId"):
        value = raw.get(key)
        if value not in (None, ""):
            return str(value)
    event = raw.get("event")
    if isinstance(event, dict):
        eid = _id(event)
        if eid:
            return eid
    events = raw.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict):
            return _id(first)
    return ""


def _nested_events(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Return inline event payloads carried on a market (Gamma `events` array)."""
    out: list[dict[str, Any]] = []
    events = raw.get("events")
    if isinstance(events, list):
        for ev in events:
            if isinstance(ev, dict):
                out.append(ev)
    event = raw.get("event")
    if isinstance(event, dict):
        out.append(event)
    return out


def _market_category(raw: dict[str, Any]) -> str:
    """Return the category string for a market (from inline event when missing)."""
    direct = raw.get("category")
    if direct not in (None, ""):
        return str(direct).strip()
    for ev in _nested_events(raw):
        cat = ev.get("category")
        if cat not in (None, ""):
            return str(cat).strip()
    return ""


def _event_market_ids(event: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in event.get("markets") or event.get("market_ids") or event.get("marketIds") or []:
        mid = _id(item) if isinstance(item, dict) else str(item)
        if mid:
            ids.append(mid)
    return ids


def _market_tag_ids(raw: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("tag_id", "tagId"):
        if raw.get(key) not in (None, ""):
            ids.append(str(raw[key]))
    for key in ("tag_ids", "tagIds"):
        for value in raw.get(key) or []:
            ids.append(str(value))
    for tag in raw.get("tags") or []:
        if isinstance(tag, dict):
            tid = _id(tag)
            if tid:
                ids.append(tid)
        elif tag not in (None, ""):
            ids.append(str(tag))
    # Gamma markets keep tags on the nested event payload, not the market itself.
    for ev in _nested_events(raw):
        for tag in ev.get("tags") or []:
            if isinstance(tag, dict):
                tid = _id(tag)
                if tid:
                    ids.append(tid)
            elif tag not in (None, ""):
                ids.append(str(tag))
    return sorted(set(ids))


def _related_tag_clusters(pairs: list[tuple[str, str]]) -> dict[str, set[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in pairs:
        union(a, b)
    clusters: dict[str, set[str]] = defaultdict(set)
    for value in list(parent):
        clusters[find(value)].add(value)
    return clusters


def _series_id(raw: dict[str, Any]) -> str:
    for key in ("series_id", "seriesId"):
        if raw.get(key) not in (None, ""):
            return str(raw[key])
    series = raw.get("series")
    if isinstance(series, dict):
        sid = _id(series)
        if sid:
            return sid
    if isinstance(series, list) and series:
        first = series[0]
        if isinstance(first, dict):
            sid = _id(first)
            if sid:
                return sid
    # Fall back to nested event payloads
    for ev in _nested_events(raw):
        s = ev.get("series")
        if isinstance(s, dict):
            sid = _id(s)
            if sid:
                return sid
        if isinstance(s, list) and s:
            first = s[0]
            if isinstance(first, dict):
                sid = _id(first)
                if sid:
                    return sid
        for key in ("series_id", "seriesId", "seriesSlug"):
            if ev.get(key) not in (None, ""):
                return str(ev[key])
    return ""


def _sport_id(raw: dict[str, Any]) -> str:
    for key in ("sport_id", "sportId", "sport"):
        if raw.get(key) not in (None, ""):
            return str(raw[key])
    # Gamma markets carry sport on the nested event payload (often as `category`).
    for ev in _nested_events(raw):
        for key in ("sport_id", "sportId", "sport"):
            value = ev.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _team_ids(raw: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("team_id", "teamId"):
        if raw.get(key) not in (None, ""):
            ids.append(str(raw[key]))
    for key in ("team_ids", "teamIds"):
        ids.extend(str(v) for v in raw.get(key) or [])
    for team in raw.get("teams") or []:
        if isinstance(team, dict):
            tid = _id(team)
            if tid:
                ids.append(tid)
        elif team not in (None, ""):
            ids.append(str(team))
    return sorted(set(ids))


def _candidate_entity(raw: dict[str, Any]) -> str:
    q = str(raw.get("question") or raw.get("title") or "")
    match = re.match(r"\s*will\s+(.+?)\s+win\b", q, flags=re.IGNORECASE)
    return match.group(1).strip(" ?") if match else ""


def _winner_pattern_key(raw: dict[str, Any]) -> str:
    q = str(raw.get("question") or raw.get("title") or "").lower()
    match = re.match(r"\s*will\s+(.+?)\s+win\s+(.+?)\??\s*$", q, flags=re.IGNORECASE)
    if not match:
        return ""
    competition = re.sub(r"\s+", " ", match.group(2).strip(" ?"))
    return competition if competition else ""


def _domain_for(event: dict[str, Any], markets: list[dict[str, Any]]) -> str:
    for key in ("category", "seriesSlug", "sport", "ticker", "slug"):
        value = event.get(key)
        if value:
            return str(value)
    for market in markets:
        sport = _sport_id(market)
        if sport:
            return sport
    return "event"


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "unknown"
