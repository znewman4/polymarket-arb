"""Write API responses verbatim to ``data/raw/...`` before any normalisation.

Atomic via tmp + rename. Date-partitioned. The raw lake is the source of
truth: if our Pydantic models miscategorise something we can re-derive every
normalised table from these files.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RawWriter:
    """Date-partitioned raw JSON writer. Sync — raw writes are tiny & fast."""

    def __init__(self, data_root: Path) -> None:
        self._root = data_root

    def _path_for(self, source: str, *, suffix: str, ts: datetime) -> Path:
        date_dir = self._root / "raw" / source / ts.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        ts_part = ts.strftime("%H%M%S")
        rand = uuid.uuid4().hex[:8]
        return date_dir / f"{ts_part}_{rand}.{suffix}"

    def write_json(self, source: str, payload: Any, *, ts: datetime | None = None) -> Path:
        ts = ts or datetime.now(timezone.utc)
        path = self._path_for(source, suffix="json", ts=ts)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def append_jsonl(self, source: str, payload: Any, *, ts: datetime | None = None,
                     stem: str | None = None) -> Path:
        """Append one JSON object per call to a daily JSONL file. Used by the
        WebSocket recorder in Phase 3."""

        ts = ts or datetime.now(timezone.utc)
        date_dir = self._root / "raw" / source / ts.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        name = stem or "stream"
        path = date_dir / f"{name}.jsonl"
        line = json.dumps(payload, default=str) + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        return path
