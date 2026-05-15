"""Run manifest writer for local recording sessions."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import RecordingManifest


class ManifestWriter:
    def __init__(self, data_root: Path, *, command: str, args: dict[str, Any]) -> None:
        self.run_id = uuid.uuid4().hex
        self.path = data_root / "runs" / self.run_id / "manifest.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest = RecordingManifest(
            run_id=self.run_id,
            started_at_ms=_now_ms(),
            ended_at_ms=None,
            duration_s=int(args.get("duration_s") or 0),
            command=command,
            args=args,
            markets_limit=int(args.get("limit") or args.get("markets_limit") or 0),
            quote_interval_s=args.get("quote_interval_s"),
            score_interval_s=args.get("score_interval_s"),
            orderbook_interval_s=args.get("orderbook_interval_s"),
            code_version=_git_sha(),
            config_hash=_config_hash(args),
        )
        self.write()

    def complete(self, *, row_counts: dict[str, int], tables_written: list[str]) -> None:
        self.manifest.ended_at_ms = _now_ms()
        self.manifest.status = "completed"
        self.manifest.row_counts_by_table = row_counts
        self.manifest.tables_written = tables_written
        self.write()

    def fail(self, exc: BaseException, *, row_counts: dict[str, int] | None = None) -> None:
        self.manifest.ended_at_ms = _now_ms()
        self.manifest.status = "failed"
        self.manifest.error_summary = str(exc)
        self.manifest.row_counts_by_table = row_counts or {}
        self.write()

    def write(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self.manifest), indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _git_sha() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return proc.stdout.strip() or None


def _config_hash(args: dict[str, Any]) -> str:
    blob = json.dumps(args, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
