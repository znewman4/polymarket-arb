"""Suspicious-match spot-check sampler for Phase G reports."""

from __future__ import annotations

import csv
import random
from collections import Counter
from pathlib import Path
from typing import Any

_AUDIT_COLS = [
    "bucket",
    "source_file",
    "relationship_id",
    "bundle_event_id",
    "relationship_type",
    "relationship_subtype",
    "outcome_space_id",
    "strategy_lane",
    "question_a",
    "question_b",
    "flags",
    "rejection_reason",
    "suggested_action",
]


def generate_suspicious_match_audit(
    report_dir: Path,
    *,
    samples_per_bucket: int = 20,
    seed: int = 7,
    output_dir: Path | None = None,
) -> Path:
    """Write deterministic random spot-check samples for Phase G audit review.

    Buckets match the Claude plan: accepted strict, exploratory, rejected,
    traded, and suspicious. The sampler reads the CSVs already produced by the
    opportunity-surface report, so it can be re-run independently.
    """
    out_dir = output_dir or report_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    buckets = {
        "accepted_strict": _accepted_strict_rows(report_dir),
        "exploratory": _exploratory_rows(report_dir),
        "rejected": _source_rows(report_dir / "blocked_opportunities.csv", "blocked_opportunities.csv"),
        "traded": _source_rows(report_dir / "accepted_simulated_trades.csv", "accepted_simulated_trades.csv"),
        "suspicious": _source_rows(report_dir / "suspicious_matches.csv", "suspicious_matches.csv"),
    }

    rng = random.Random(seed)
    sampled: list[dict[str, Any]] = []
    bucket_counts = {}
    for bucket, rows in buckets.items():
        stable_rows = sorted(rows, key=lambda r: (str(r.get("relationship_id", "")), str(r)))
        bucket_counts[bucket] = len(stable_rows)
        if len(stable_rows) > samples_per_bucket:
            selected = rng.sample(stable_rows, samples_per_bucket)
            selected = sorted(selected, key=lambda r: (str(r.get("relationship_id", "")), str(r)))
        else:
            selected = stable_rows
        for row in selected:
            sampled.append(_audit_row(bucket, row))

    _write_csv(out_dir / "suspicious_match_audit.csv", sampled)
    _write_md(out_dir / "suspicious_match_audit.md", bucket_counts, sampled, samples_per_bucket, seed)
    return out_dir


def _accepted_strict_rows(report_dir: Path) -> list[dict[str, Any]]:
    rows = _source_rows(report_dir / "trade_candidates.csv", "trade_candidates.csv")
    accepted = []
    for row in rows:
        lane = str(row.get("strategy_lane", ""))
        replay_path = str(row.get("replay_path", ""))
        preset = str(row.get("preset", ""))
        if _is_exploratory(lane, replay_path, preset):
            continue
        if str(row.get("accepted_for_simulation", "")).lower() in {"true", "1", "yes"}:
            accepted.append(row)
    return accepted


def _exploratory_rows(report_dir: Path) -> list[dict[str, Any]]:
    rows = _source_rows(report_dir / "opportunity_surface.csv", "opportunity_surface.csv")
    return [
        row for row in rows
        if _is_exploratory(
            str(row.get("strategy_lane", "")),
            str(row.get("replay_path", "")),
            str(row.get("preset", "")),
        )
    ]


def _source_rows(path: Path, source_file: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except Exception:
        rows = []
    for row in rows:
        row.setdefault("source_file", source_file)
    return rows


def _audit_row(bucket: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "bucket": bucket,
        "source_file": row.get("source_file", ""),
        "relationship_id": row.get("relationship_id", ""),
        "bundle_event_id": row.get("bundle_event_id", ""),
        "relationship_type": row.get("relationship_type", ""),
        "relationship_subtype": row.get("relationship_subtype", ""),
        "outcome_space_id": row.get("outcome_space_id", ""),
        "strategy_lane": row.get("strategy_lane", ""),
        "question_a": row.get("question_a", ""),
        "question_b": row.get("question_b", ""),
        "flags": row.get("flags", ""),
        "rejection_reason": row.get("rejection_reason", ""),
        "suggested_action": row.get("suggested_action", ""),
    }


def _is_exploratory(lane: str, replay_path: str, preset: str) -> bool:
    text = " ".join([lane, replay_path, preset]).lower()
    return "exploratory" in text or "research" in text


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_AUDIT_COLS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(
    path: Path,
    bucket_counts: dict[str, int],
    sampled: list[dict[str, Any]],
    samples_per_bucket: int,
    seed: int,
) -> None:
    sampled_counts = Counter(str(row.get("bucket", "")) for row in sampled)
    lines = [
        "# Suspicious Match Audit Sample",
        "",
        "RESEARCH-ONLY spot-check sampler. Rows are deterministic random samples from each bucket.",
        "",
        f"- samples_per_bucket: `{samples_per_bucket}`",
        f"- seed: `{seed}`",
        "",
        "| Bucket | Available rows | Sampled rows |",
        "| --- | ---: | ---: |",
    ]
    for bucket in sorted(bucket_counts):
        lines.append(f"| {bucket} | {bucket_counts[bucket]} | {sampled_counts.get(bucket, 0)} |")
    lines += [
        "",
        "## First Sample Rows",
        "",
        "| Bucket | Relationship | Flags | Suggested action |",
        "| --- | --- | --- | --- |",
    ]
    for row in sampled[:20]:
        lines.append(
            "| "
            f"{_md(row.get('bucket', ''))} | "
            f"{_md(row.get('relationship_id') or row.get('bundle_event_id') or '')} | "
            f"{_md(row.get('flags', ''))} | "
            f"{_md(row.get('suggested_action', ''))} |"
        )
    if not sampled:
        lines.append("| none | none | none | none |")
    path.write_text("\n".join(lines), encoding="utf-8")


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|")
