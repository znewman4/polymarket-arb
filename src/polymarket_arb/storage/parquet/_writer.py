"""Shared parquet append helper.

Writes new part-files under ``data/normalised/{table}/dt=YYYY-MM-DD/``.
Append-only: existing files are never edited.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ..exceptions import SchemaMismatchError


def normalised_table_dir(data_root: Path, table: str, ts: datetime) -> Path:
    return data_root / "normalised" / table / f"dt={ts.strftime('%Y-%m-%d')}"


def write_table_part(
    data_root: Path,
    table: str,
    schema: pa.Schema,
    rows: list[dict],
    *,
    compression: str = "zstd",
    row_group_size: int = 50_000,
    ts: datetime | None = None,
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
    return path
