"""Coverage audit report: CSV + markdown summary.

RESEARCH-ONLY / DIAGNOSTIC-ONLY. Not trading advice.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..backtest.coverage_audit import RelationshipCoverageRow

_LABEL = "RESEARCH-ONLY / DIAGNOSTIC-ONLY — not trading advice"


def generate_coverage_audit_report(
    rows: list[RelationshipCoverageRow],
    output_dir: Path,
) -> tuple[Path, Path]:
    """Write CSV + markdown to output_dir.

    Returns (csv_path, md_path).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_path = output_dir / f"coverage_audit_{ts}.csv"
    md_path = output_dir / f"coverage_audit_{ts}.md"

    _write_csv(csv_path, rows)
    _write_markdown(md_path, rows, ts)
    return csv_path, md_path


def _write_csv(path: Path, rows: list[RelationshipCoverageRow]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    dicts = [asdict(r) for r in rows]
    fields = list(dicts[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(dicts)


def _write_markdown(path: Path, rows: list[RelationshipCoverageRow], ts: str) -> None:
    total = len(rows)
    both_ph = sum(1 for r in rows if r.both_have_price_history)
    no_ph_a = sum(1 for r in rows if not r.price_history_exists_a)
    no_ph_b = sum(1 for r in rows if not r.price_history_exists_b)
    no_gamma_a = sum(1 for r in rows if not r.gamma_exists_a)
    no_gamma_b = sum(1 for r in rows if not r.gamma_exists_b)
    no_decision = sum(1 for r in rows if not r.has_context_decision)
    no_blocker = sum(1 for r in rows if r.final_blocker == "none")

    blocker_counts: Counter[str] = Counter(r.final_blocker for r in rows)

    lines: list[str] = [
        f"# Coverage Audit — {ts}",
        "",
        f"> {_LABEL}",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "| --- | --- |",
        f"| Total relationships audited | {total} |",
        f"| Both markets have price history | {both_ph} |",
        f"| Missing price history (market A) | {no_ph_a} |",
        f"| Missing price history (market B) | {no_ph_b} |",
        f"| Missing Gamma metadata (market A) | {no_gamma_a} |",
        f"| Missing Gamma metadata (market B) | {no_gamma_b} |",
        f"| No context decision | {no_decision} |",
        f"| No blocker (fully covered) | {no_blocker} |",
        "",
        "## Blocker breakdown",
        "",
        "| Blocker | Count |",
        "| --- | --- |",
    ]
    for blocker, count in blocker_counts.most_common():
        lines.append(f"| `{blocker}` | {count} |")

    lines += [
        "",
        "## Per-relationship detail",
        "",
        "| relationship_id | type | confidence | has_ph_a | ticks_a | has_ph_b | ticks_b | coverage_pair | blocker |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in sorted(rows, key=lambda x: x.final_blocker):
        lines.append(
            f"| `{r.relationship_id[:16]}` "
            f"| {r.relationship_type} "
            f"| {r.final_confidence:.2f} "
            f"| {'✓' if r.price_history_exists_a else '✗'} "
            f"| {r.tick_count_a} "
            f"| {'✓' if r.price_history_exists_b else '✗'} "
            f"| {r.tick_count_b} "
            f"| {r.coverage_score_pair:.3f} "
            f"| `{r.final_blocker}` |"
        )

    lines += [
        "",
        "## Questions",
        "",
    ]
    for r in rows:
        lines += [
            f"### `{r.relationship_id[:24]}`",
            f"- **A**: {r.question_a}",
            f"- **B**: {r.question_b}",
            f"- **Type**: {r.relationship_type}",
            f"- **Validation**: {r.validation_status} / eligibility: {r.strategy_eligibility_status}",
            f"- **Lane**: {r.strategy_lane or 'unassigned'}",
            f"- **Blocker**: `{r.final_blocker}`",
            "",
        ]

    path.write_text("\n".join(lines), encoding="utf-8")
