"""Test the maintenance compact-lake CLI command."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from click.testing import CliRunner

from polymarket_arb.cli import cli


def _env_for(tmp_path: Path) -> dict[str, str]:
    return {
        "POLYMARKET_ARB_STORAGE__DATA_ROOT": str(tmp_path / "data"),
        "POLYMARKET_ARB_LOGGING__JSON_LOG_PATH": str(tmp_path / "logs" / "test.jsonl"),
    }


def _write_tiny_parquet(target: Path, value: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({"x": [value]})
    pq.write_table(table, target)


def test_compact_lake_merges_old_partitions(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    old_dt = date.today() - timedelta(days=5)
    partition = data_root / "normalised" / "demo" / f"dt={old_dt.isoformat()}"
    for i in range(15):
        _write_tiny_parquet(partition / f"part-{i:03d}.parquet", i)

    # Recent partition should be skipped by --older-than-days.
    today = date.today()
    recent = data_root / "normalised" / "demo" / f"dt={today.isoformat()}"
    for i in range(15):
        _write_tiny_parquet(recent / f"part-{i:03d}.parquet", i + 100)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["maintenance", "compact-lake", "--older-than-days", "1", "--min-files", "10"],
        env=_env_for(tmp_path),
    )
    assert result.exit_code == 0, result.output

    # Old partition is collapsed to a single compacted file.
    remaining = sorted(partition.glob("*.parquet"))
    assert len(remaining) == 1
    assert remaining[0].name == "part-compacted.parquet"

    # Row count preserved.
    con = duckdb.connect(":memory:")
    rows = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{remaining[0]}')"
    ).fetchone()
    assert rows[0] == 15

    # Recent partition untouched.
    recent_files = sorted(recent.glob("*.parquet"))
    assert len(recent_files) == 15


def test_compact_lake_idempotent(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    old_dt = date.today() - timedelta(days=5)
    partition = data_root / "normalised" / "demo" / f"dt={old_dt.isoformat()}"
    for i in range(12):
        _write_tiny_parquet(partition / f"part-{i:03d}.parquet", i)

    runner = CliRunner()
    env = _env_for(tmp_path)
    args = ["maintenance", "compact-lake", "--older-than-days", "1", "--min-files", "10"]

    result1 = runner.invoke(cli, args, env=env)
    assert result1.exit_code == 0

    result2 = runner.invoke(cli, args, env=env)
    assert result2.exit_code == 0
    # Second run should be a no-op — the single compacted file is below min-files.
    assert "compacted 0 partition" in result2.output

    files = sorted(partition.glob("*.parquet"))
    assert len(files) == 1
    assert files[0].name == "part-compacted.parquet"


def test_compact_lake_dry_run_keeps_files(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    old_dt = date.today() - timedelta(days=5)
    partition = data_root / "normalised" / "demo" / f"dt={old_dt.isoformat()}"
    for i in range(12):
        _write_tiny_parquet(partition / f"part-{i:03d}.parquet", i)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["maintenance", "compact-lake", "--older-than-days", "1", "--dry-run"],
        env=_env_for(tmp_path),
    )
    assert result.exit_code == 0
    assert len(list(partition.glob("*.parquet"))) == 12
