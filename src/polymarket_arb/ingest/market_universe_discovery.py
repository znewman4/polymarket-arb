"""Public Gamma market-universe discovery.

Writes deterministic JSONL artifacts under ``data/raw/market_universe/<run_id>``.
All inputs are public/read-only Gamma endpoints.  No trading, wallets, or order
placement live in this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .gamma.client import GammaClient

_LABEL = "RESEARCH-ONLY universe discovery — public/read-only endpoints"


@dataclass(frozen=True)
class UniverseDiscoveryResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    stats_path: Path
    counts: dict[str, int] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_gamma_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _within_lookback(raw: dict[str, Any], cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    for key in ("resolvedTime", "resolutionDate", "closedTime", "endDate", "startDate"):
        dt = _parse_gamma_dt(raw.get(key))
        if dt is not None:
            return dt >= cutoff
    return True


def _record_id(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, default=str, sort_keys=True) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str, indent=2, sort_keys=True), encoding="utf-8")


async def run_universe_discovery(
    client: GammaClient,
    *,
    run_id: str,
    active: bool = True,
    closed: bool = True,
    lookback_days: int = 365,
    include_tags: bool = True,
    include_related_tags: bool = True,
    include_series: bool = True,
    include_sports: bool = True,
    include_teams: bool = True,
    max_pages: int | None = None,
) -> UniverseDiscoveryResult:
    """Discover a broad public market universe and persist JSONL artifacts."""

    output_dir = client.data_root / "raw" / "market_universe" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    cutoff = (
        datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
        if lookback_days > 0
        else None
    )

    files = {
        "events": output_dir / "events.jsonl",
        "markets": output_dir / "markets.jsonl",
        "tags": output_dir / "tags.jsonl",
        "related_tags": output_dir / "related_tags.jsonl",
        "series": output_dir / "series.jsonl",
        "sports": output_dir / "sports.jsonl",
        "teams": output_dir / "teams.jsonl",
    }
    for path in files.values():
        if path.exists():
            path.unlink()

    counts = {name: 0 for name in files}
    seen_markets: set[str] = set()
    seen_events: set[str] = set()
    seen_tags: set[str] = set()

    async def _write_market(raw: dict[str, Any], source: str) -> None:
        mid = _record_id(raw, "id", "market_id", "marketId")
        if not mid or mid in seen_markets:
            return
        seen_markets.add(mid)
        _append_jsonl(files["markets"], {"source": source, "payload": raw})
        counts["markets"] += 1

    async def _write_event(raw: dict[str, Any], source: str) -> None:
        eid = _record_id(raw, "id", "event_id", "eventId")
        if not eid or eid in seen_events:
            return
        seen_events.add(eid)
        _append_jsonl(files["events"], {"source": source, "payload": raw})
        counts["events"] += 1
        for market in raw.get("markets") or []:
            if isinstance(market, dict):
                await _write_market(market, f"{source}:nested_event_market")

    market_modes: list[tuple[bool | None, bool | None, str]] = []
    if active:
        market_modes.append((True, False, "gamma_markets_active"))
    if closed:
        market_modes.append((None, True, "gamma_markets_closed"))
    if not market_modes:
        market_modes.append((None, None, "gamma_markets_all"))

    for active_filter, closed_filter, source in market_modes:
        async for market in client.iter_raw_markets(
            active=active_filter,
            closed=closed_filter,
            archived=False,
            max_pages=max_pages,
        ):
            if closed_filter and not _within_lookback(market, cutoff):
                continue
            await _write_market(market, source)

    event_modes: list[tuple[bool | None, bool | None, str]] = []
    if active:
        event_modes.append((True, None, "gamma_events_active"))
    if closed:
        event_modes.append((None, True, "gamma_events_closed"))
    if not event_modes:
        event_modes.append((None, None, "gamma_events_all"))

    for active_filter, closed_filter, source in event_modes:
        async for event in client.iter_raw_events(
            active=active_filter,
            closed=closed_filter,
            archived=False,
            max_pages=max_pages,
        ):
            if closed_filter and not _within_lookback(event, cutoff):
                continue
            await _write_event(event, source)

    tag_ids: list[str] = []
    if include_tags:
        async for tag in client.iter_tags(max_pages=max_pages):
            tid = _record_id(tag, "id", "tag_id", "tagId")
            if not tid or tid in seen_tags:
                continue
            seen_tags.add(tid)
            tag_ids.append(tid)
            _append_jsonl(files["tags"], {"source": "gamma_tags", "payload": tag})
            counts["tags"] += 1

    if include_related_tags:
        for tag_id in tag_ids:
            for related in await client.iter_related_tags(tag_id):
                _append_jsonl(
                    files["related_tags"],
                    {"source": "gamma_related_tags", "tag_id": tag_id, "payload": related},
                )
                counts["related_tags"] += 1

    if include_series:
        async for series in client.iter_series(max_pages=max_pages):
            _append_jsonl(files["series"], {"source": "gamma_series", "payload": series})
            counts["series"] += 1

    sports: list[dict[str, Any]] = []
    if include_sports:
        sports = await client.iter_sports()
        for sport in sports:
            _append_jsonl(files["sports"], {"source": "gamma_sports", "payload": sport})
            counts["sports"] += 1

    if include_teams:
        sport_ids = [_record_id(s, "id", "sport_id", "sportId") for s in sports] if sports else [""]
        for sport_id in [s for s in sport_ids if s] or [None]:
            async for team in client.iter_teams(sport_id=sport_id, max_pages=max_pages):
                _append_jsonl(
                    files["teams"],
                    {"source": "gamma_teams", "sport_id": sport_id, "payload": team},
                )
                counts["teams"] += 1

    manifest = {
        "label": _LABEL,
        "run_id": run_id,
        "started_at": _now_iso(),
        "config": {
            "active": active,
            "closed": closed,
            "lookback_days": lookback_days,
            "include_tags": include_tags,
            "include_related_tags": include_related_tags,
            "include_series": include_series,
            "include_sports": include_sports,
            "include_teams": include_teams,
            "max_pages": max_pages,
        },
    }
    stats = {"run_id": run_id, "counts": counts, "files": {k: str(v) for k, v in files.items()}}
    manifest_path = output_dir / "discovery_manifest.json"
    stats_path = output_dir / "discovery_stats.json"
    _write_json(manifest_path, manifest)
    _write_json(stats_path, stats)

    return UniverseDiscoveryResult(
        run_id=run_id,
        output_dir=output_dir,
        manifest_path=manifest_path,
        stats_path=stats_path,
        counts=counts,
        files=files,
    )
