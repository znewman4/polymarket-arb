"""PASS/WARN/FAIL local data audits."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .models import AuditCheck, TableStatus
from .reports import counts_report, table_report


def audit_data(data_root: Path, *, repo_root: Path | None = None) -> list[AuditCheck]:
    checks: list[AuditCheck] = []
    tables = {t.name: t for t in table_report(data_root)}
    counts = counts_report(data_root)

    _required_table(checks, tables, "markets")
    _required_table(checks, tables, "events")
    if counts["total_markets"] > 0:
        checks.append(AuditCheck("PASS", "at least one market row exists", str(counts["total_markets"])))
    else:
        checks.append(AuditCheck("WARN", "at least one market row exists", "run gamma fetch-markets"))

    if counts["markets_missing_token_ids"]:
        checks.append(AuditCheck("WARN", "token IDs present for active markets",
                                 f"{counts['markets_missing_token_ids']} markets missing token IDs"))
    else:
        checks.append(AuditCheck("PASS", "token IDs present for active markets", "ok"))

    mismatch = counts["markets_with_malformed_outcomes"]
    checks.append(AuditCheck("WARN" if mismatch else "PASS", "outcomes/token IDs length mismatch count",
                             str(mismatch)))

    total = max(1, counts["total_markets"])
    for key, label in [
        ("markets_with_no_semantics", "semantics coverage"),
        ("markets_with_no_rulebook_score", "rulebook scoring coverage"),
        ("markets_with_no_implications", "implications coverage"),
        ("markets_with_no_clob_quote", "best quote coverage"),
        ("markets_with_no_market_score", "score coverage"),
    ]:
        missing = int(counts[key])
        pct = 100.0 * (total - missing) / total
        status = "PASS" if missing == 0 else "WARN"
        checks.append(AuditCheck(status, label, f"{pct:.1f}% covered; {missing} missing"))

    normalised = data_root / "normalised"
    if _grep_absent(normalised, "<think>"):
        checks.append(AuditCheck("PASS", "no <think> found in normalised data", "ok"))
    else:
        checks.append(AuditCheck("FAIL", "no <think> found in normalised data", "found chain-of-thought marker"))
    if _grep_absent(normalised, '"thinking"'):
        checks.append(AuditCheck("PASS", "no thinking fields found in normalised data", "ok"))
    else:
        checks.append(AuditCheck("FAIL", "no thinking fields found in normalised data", "found thinking key"))

    if repo_root is not None:
        src = repo_root / "src"
        risky = _grep(src, r"private_key|place_order|OrderClient|wallet|funder|signature|signer")
        allowed = [
            line for line in risky
            if "/risk/" in line
            or "/inspect/audit.py" in line
            or "future" in line.lower()
        ]
        unexpected = [line for line in risky if line not in allowed]
        status = "PASS" if not unexpected else "WARN"
        detail = "ok" if not unexpected else f"{len(unexpected)} suspicious source hits"
        checks.append(AuditCheck(status, "no obvious live trading code paths in src", detail))
        clob = repo_root / "src" / "polymarket_arb" / "ingest" / "clob"
        auth_hits = _grep(clob, r"Authorization|signature|private_key|wallet|place_order")
        checks.append(AuditCheck("PASS" if not auth_hits else "FAIL",
                                 "no authenticated CLOB usage found in ingest/clob",
                                 "ok" if not auth_hits else f"{len(auth_hits)} hits"))
    return checks


def _required_table(checks: list[AuditCheck], tables: dict[str, TableStatus], table: str) -> None:
    status = tables[table]
    file_count = status.file_count
    checks.append(AuditCheck("PASS" if file_count else "WARN", f"{table} table exists",
                             f"{file_count} parquet files"))


def _grep_absent(path: Path, pattern: str) -> bool:
    return not _grep(path, pattern)


def _grep(path: Path, pattern: str) -> list[str]:
    if not path.exists():
        return []
    try:
        proc = subprocess.run(
            ["grep", "-R", "-n", "-E", pattern, str(path)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]
