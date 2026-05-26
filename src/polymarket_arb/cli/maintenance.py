"""Lake maintenance: compact small parquet files into one per partition."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import click
import duckdb

from ..settings import Settings


@click.group(name="maintenance")
def maintenance_cmd() -> None:
    """Lake maintenance operations."""


@maintenance_cmd.command(name="compact-lake")
@click.option(
    "--older-than-days",
    type=int,
    default=1,
    show_default=True,
    help="Only compact partitions older than this many days.",
)
@click.option(
    "--min-files",
    type=int,
    default=10,
    show_default=True,
    help="Skip partitions with fewer than this many parquet files.",
)
@click.option("--dry-run/--no-dry-run", default=False, show_default=True)
@click.pass_context
def compact_lake(
    ctx: click.Context,
    older_than_days: int,
    min_files: int,
    dry_run: bool,
) -> None:
    """Compact small parquet files within each dt= partition into a single file.

    Iterates over every table under ``data/normalised/`` and every
    ``dt=YYYY-MM-DD`` partition older than the cutoff.  When a partition has
    more than ``--min-files`` parquet files, merges them into a single
    ``part-compacted.parquet`` and deletes the originals.  Idempotent: a
    partition that already holds a single compacted file is skipped.
    """
    settings: Settings = ctx.obj["settings"]
    root = Path(settings.data_root) / "normalised"
    cutoff = date.today() - timedelta(days=older_than_days)
    if not root.exists():
        click.echo(f"no normalised dir at {root}")
        return

    con = duckdb.connect(":memory:")
    total_compacted = 0
    total_saved_bytes = 0

    for table_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for partition in sorted(table_dir.glob("dt=*")):
            try:
                dt_str = partition.name.split("=", 1)[1]
                dt_val = date.fromisoformat(dt_str)
            except (IndexError, ValueError):
                continue
            if dt_val > cutoff:
                continue

            target = partition / "part-compacted.parquet"
            files = sorted(p for p in partition.glob("*.parquet") if p.name != target.name)
            has_target = target.exists()

            # Already a single compacted file with no stragglers — skip.
            if has_target and not files:
                continue

            # Include the existing target so we merge new arrivals into it.
            if has_target:
                files.append(target)

            if len(files) < min_files:
                continue

            size_before = sum(f.stat().st_size for f in files)
            click.echo(
                f"{table_dir.name}/{partition.name}: {len(files)} files, "
                f"{size_before / 1024 / 1024:.2f} MB → compacting"
                + (" (dry-run)" if dry_run else "")
            )
            if dry_run:
                continue

            tmp = partition / "part-compacted.parquet.tmp"
            file_list = "[" + ",".join(f"'{f}'" for f in files) + "]"
            con.execute(
                f"COPY (SELECT * FROM read_parquet({file_list})) "
                f"TO '{tmp}' (FORMAT PARQUET)"
            )
            for f in files:
                if f != target:
                    f.unlink()
            if target.exists():
                target.unlink()
            tmp.replace(target)
            size_after = target.stat().st_size
            saved = size_before - size_after
            total_saved_bytes += saved
            click.echo(
                f"  → {size_after / 1024 / 1024:.2f} MB "
                f"(saved {saved / 1024 / 1024:.2f} MB)"
            )
            total_compacted += 1

    click.echo(
        f"compacted {total_compacted} partition(s), "
        f"saved {total_saved_bytes / 1024 / 1024:.2f} MB total"
    )


__all__ = ["maintenance_cmd"]
