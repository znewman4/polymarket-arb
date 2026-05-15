"""Per-process DuckDB engine that exposes Parquet globs as views."""

from __future__ import annotations

from pathlib import Path

import duckdb


class DuckDBEngine:
    """Read-only DuckDB facade over the normalised Parquet lake."""

    def __init__(self, data_root: Path) -> None:
        self._root = data_root
        self._con: duckdb.DuckDBPyConnection | None = None

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            self._con = duckdb.connect()
            self._register_views()
        return self._con

    def _register_views(self) -> None:
        from .views import VIEW_DEFINITIONS
        assert self._con is not None
        root = str(self._root.resolve())
        for _view_name, sql_template in VIEW_DEFINITIONS.items():
            try:
                self._con.execute(sql_template.format(root=root))
            except duckdb.Error:
                # No parquet files for this view's table yet; that's fine.
                # Strategies/CLI commands that need this view will re-call
                # connect() after data is written.
                continue

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None
