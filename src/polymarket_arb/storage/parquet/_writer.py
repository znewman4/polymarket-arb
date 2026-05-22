"""Shared parquet append helper.

Writes new part-files under ``data/normalised/{table}/dt=YYYY-MM-DD/``.
Append-only: existing files are never edited.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from ..exceptions import SchemaMismatchError

def normalised_table_dir(data_root: Path, table: str, ts: datetime) -> Path:
    return data_root / "normalised" / table / f"dt={ts.strftime('%Y-%m-%d')}"


_COMPACT_THRESHOLD = 100
_COMPACT_BATCH = 50  # well under OS fd limit (~1024)


def _maybe_compact(dir_: Path, compression: str) -> None:
    """Merge one batch of part-files per call. Called after every write,
    so repeated writes progressively drain the backlog."""
    files = sorted(dir_.glob("*.parquet"))
    if len(files) <= _COMPACT_THRESHOLD:
        return

    # Take one batch only — avoids opening thousands of fds at once.
    batch = files[:_COMPACT_BATCH]
    uid = uuid.uuid4().hex[:8]
    tmp = dir_ / f"compacted-{uid}.parquet.tmp"
    out = dir_ / f"compacted-{uid}.parquet"
    quoted = ", ".join(f"'{f}'" for f in batch)

    con = duckdb.connect()
    try:
        con.execute(
            f"COPY (SELECT * FROM read_parquet([{quoted}])) "
            f"TO '{tmp}' (FORMAT PARQUET, COMPRESSION '{compression}')"
        )
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        con.close()

    os.replace(tmp, out)
    for f in batch:
        with contextlib.suppress(FileNotFoundError):
            f.unlink()


def write_table_part(
    data_root: Path,
    table: str,
    schema: pa.Schema,
    rows: list[dict],
    *,
    compression: str = "zstd",
    row_group_size: int = 50_000,
    ts: datetime | None = None,
    compact: bool = False,
) -> Path:
    """Append ``rows`` as a single parquet part-file. Returns the path."""

    if not rows:
        raise ValueError("write_table_part called with empty rows")

    ts = ts or datetime.now(timezone.utc)
    dir_ = normalised_table_dir(data_root, table, ts)
    dir_.mkdir(parents=True, exist_ok=True)
    fname = f"part-{ts.strftime('%H%M%S%f')}_{uuid.uuid4().hex[:8]}.parquet"
    path = dir_ / fname
    tmp = path.with_suffix(path.suffix + ".tmp")

    try:
        arrow = pa.Table.from_pylist(rows, schema=schema)
    except (pa.ArrowInvalid, pa.ArrowTypeError, KeyError, TypeError) as exc:
        raise SchemaMismatchError(
            f"row(s) failed validation against {table} schema: {exc}"
        ) from exc

    pq.write_table(arrow, str(tmp), compression=compression, row_group_size=row_group_size)
    os.replace(tmp, path)
    if compact:
        _maybe_compact(dir_, compression)
    return path
