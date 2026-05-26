"""DuckDB-backed read service for the dashboard.

One ``DuckDBQueryService`` per Flask app: it owns a single ``duckdb.connect()``
and exposes one method per dashboard panel.  Methods return plain Python types
(dicts, lists of dicts, primitives) so they're trivially renderable from a
Jinja template or serialisable as JSON.

All queries are read-only against ``data_root/normalised/{table}/dt=*/...``
parquet partitions.  Each method short-circuits to an empty / zero result when
its source table has no parquet files yet — that's the normal state on a brand
new day, not an error.
"""

from __future__ import annotations

import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb


class DuckDBQueryService:
    """Thread-safe wrapper around one DuckDB connection.

    DuckDB connections aren't safe for concurrent use, so every query takes a
    lock.  This is fine for a single-user dashboard served over an SSM tunnel;
    if we ever need real concurrency, swap this for a tiny connection pool.
    """

    def __init__(self, data_root: Path) -> None:
        self._data_root = Path(data_root)
        self._con = duckdb.connect(database=":memory:")
        self._lock = threading.Lock()

    # ─── housekeeping ────────────────────────────────────────────────────────

    def close(self) -> None:
        with self._lock:
            self._con.close()

    def _has_data(self, table: str) -> bool:
        return any((self._data_root / "normalised" / table).glob("dt=*/*.parquet"))

    def _glob(self, table: str) -> str:
        return str(self._data_root / "normalised" / table / "dt=*" / "*.parquet")

    def _glob_recent(self, table: str, days: int = 7) -> str:
        """Return a DuckDB list literal of explicit partition globs for the last ``days`` days.

        Only includes partition directories that exist on disk, so DuckDB never
        errors on a missing path.  Falls back to the wildcard glob if no recent
        partitions exist (caller is gated by _has_data upstream).
        """
        base = self._data_root / "normalised" / table
        today = date.today()
        paths = [
            str(base / f"dt={today - timedelta(days=i)}" / "*.parquet")
            for i in range(days)
            if (base / f"dt={today - timedelta(days=i)}").exists()
        ]
        if not paths:
            return f"'{base / 'dt=*' / '*.parquet'}'"
        return "['" + "', '".join(paths) + "']"

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _fetchall(self, sql: str, params: list[Any] | None = None) -> list[tuple]:
        with self._lock:
            cur = self._con.execute(sql, params or [])
            return cur.fetchall()

    def _fetchall_dict(self, sql: str, params: list[Any] | None = None) -> list[dict]:
        with self._lock:
            cur = self._con.execute(sql, params or [])
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    # ─── overview ────────────────────────────────────────────────────────────

    def overview_counters(self, today: str | None = None) -> dict[str, Any]:
        today = today or self._today()
        empty = {"total": 0, "filled": 0, "fill_rate_pct": 0.0, "by_status": {}}
        if not self._has_data("orders_log"):
            return empty
        rows = self._fetchall(
            f"SELECT status, COUNT(*) AS n FROM read_parquet({self._glob_recent('orders_log')}, "
            "hive_partitioning=true) WHERE dt = ? GROUP BY status",
            [today],
        )
        by_status = {status: int(n) for status, n in rows}
        total = sum(by_status.values())
        filled = by_status.get("paper_filled", 0)
        fill_rate = (filled / total * 100.0) if total else 0.0
        return {
            "total": total,
            "filled": filled,
            "fill_rate_pct": round(fill_rate, 1),
            "by_status": by_status,
        }

    def signals_by_strategy(self, today: str | None = None) -> list[dict]:
        today = today or self._today()
        if not self._has_data("orders_log"):
            return []
        return self._fetchall_dict(
            f"SELECT strategy_id, COUNT(*) AS n FROM read_parquet({self._glob_recent('orders_log')}, "
            "hive_partitioning=true) WHERE dt = ? GROUP BY strategy_id ORDER BY n DESC",
            [today],
        )

    def signals_per_hour_last_24h(self) -> list[dict]:
        """One row per UTC hour for the last 24 hours, zero-filled."""
        if not self._has_data("orders_log"):
            return _empty_hour_buckets()
        # Pull non-zero hours from the lake then zero-fill in Python so we never
        # render gaps on the chart.
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        cutoff_ms = now_ms - 24 * 3600 * 1000
        # Scope to the last two daily partitions for cheap pruning.
        today = self._today()
        yesterday = (
            datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc)
            .strftime("%Y-%m-%d")
        )
        rows = self._fetchall(
            f"SELECT strftime(to_timestamp(ts_ms/1000), '%Y-%m-%d %H:00') AS hour_bucket, "
            f"COUNT(*) AS n FROM read_parquet({self._glob_recent('orders_log', days=2)}, "
            "hive_partitioning=true) "
            "WHERE dt IN (?, ?) AND ts_ms >= ? GROUP BY hour_bucket",
            [today, yesterday, cutoff_ms],
        )
        observed = {hour: int(n) for hour, n in rows}
        buckets = _empty_hour_buckets()
        for b in buckets:
            b["n"] = observed.get(b["hour_bucket"], 0)
        return buckets

    def top_markets_by_signal(
        self, today: str | None = None, limit: int = 10
    ) -> list[dict]:
        today = today or self._today()
        if not self._has_data("orders_log"):
            return []
        markets_join = ""
        if self._has_data("markets"):
            markets_join = (
                f"LEFT JOIN (SELECT id, ANY_VALUE(question) AS question FROM "
                f"read_parquet('{self._glob('markets')}', hive_partitioning=true) "
                "GROUP BY id) m ON m.id = o.market_id"
            )
            select_q = "ANY_VALUE(m.question) AS question, "
        else:
            select_q = "NULL AS question, "
        return self._fetchall_dict(
            f"SELECT o.market_id, {select_q}COUNT(*) AS signals "
            f"FROM read_parquet({self._glob_recent('orders_log')}, hive_partitioning=true) o "
            f"{markets_join} "
            "WHERE o.dt = ? AND o.market_id <> '' "
            "GROUP BY o.market_id ORDER BY signals DESC LIMIT ?",
            [today, int(limit)],
        )

    # ─── orders ──────────────────────────────────────────────────────────────

    def orders_page(
        self,
        *,
        strategy_id: str | None = None,
        status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict[str, Any]:
        if not self._has_data("orders_log"):
            return {"rows": [], "total": 0, "page": page, "per_page": per_page, "pages": 0}
        where_sql, params = self._orders_filter(strategy_id, status, date_from, date_to)
        total_row = self._fetchall(
            f"SELECT COUNT(*) FROM read_parquet('{self._glob('orders_log')}', "
            f"hive_partitioning=true) o {where_sql}",
            params,
        )
        total = int(total_row[0][0]) if total_row else 0
        markets_join, question_select = self._markets_join()
        offset = max(0, (page - 1) * per_page)
        rows = self._fetchall_dict(
            f"SELECT o.ts_ms, o.strategy_id, o.market_id, {question_select} o.side, "
            "o.status, o.notional_usdc, o.notes "
            f"FROM read_parquet('{self._glob('orders_log')}', hive_partitioning=true) o "
            f"{markets_join} "
            f"{where_sql} "
            "ORDER BY o.ts_ms DESC LIMIT ? OFFSET ?",
            [*params, int(per_page), int(offset)],
        )
        pages = (total + per_page - 1) // per_page if per_page else 0
        return {
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
        }

    def iter_orders_for_csv(
        self,
        *,
        strategy_id: str | None = None,
        status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        chunk_size: int = 1000,
    ):
        """Yield (cols, rows_chunk) for streamed CSV export."""
        if not self._has_data("orders_log"):
            cols = [
                "ts_ms", "strategy_id", "market_id", "question", "side",
                "status", "notional_usdc", "notes",
            ]
            yield cols, []
            return
        where_sql, params = self._orders_filter(strategy_id, status, date_from, date_to)
        markets_join, question_select = self._markets_join()
        sql = (
            f"SELECT o.ts_ms, o.strategy_id, o.market_id, {question_select} o.side, "
            "o.status, o.notional_usdc, o.notes "
            f"FROM read_parquet('{self._glob('orders_log')}', hive_partitioning=true) o "
            f"{markets_join} "
            f"{where_sql} "
            "ORDER BY o.ts_ms DESC"
        )
        with self._lock:
            cur = self._con.execute(sql, params)
            cols = [c[0] for c in cur.description]
            yield cols, []  # header only
            while True:
                batch = cur.fetchmany(chunk_size)
                if not batch:
                    return
                yield cols, [list(r) for r in batch]

    def _orders_filter(
        self,
        strategy_id: str | None,
        status: str | None,
        date_from: str | None,
        date_to: str | None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if strategy_id:
            clauses.append("o.strategy_id = ?")
            params.append(strategy_id)
        if status:
            clauses.append("o.status = ?")
            params.append(status)
        if date_from:
            clauses.append("o.dt >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("o.dt <= ?")
            params.append(date_to)
        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where_sql, params

    def _markets_join(self) -> tuple[str, str]:
        if not self._has_data("markets"):
            return "", "NULL AS question, "
        join_sql = (
            f"LEFT JOIN (SELECT id, ANY_VALUE(question) AS question FROM "
            f"read_parquet('{self._glob('markets')}', hive_partitioning=true) "
            "GROUP BY id) m ON m.id = o.market_id"
        )
        return join_sql, "m.question AS question, "

    # ─── signals page ────────────────────────────────────────────────────────

    def no_fill_breakdown(self, today: str | None = None, limit: int = 25) -> list[dict]:
        today = today or self._today()
        if not self._has_data("orders_log"):
            return []
        markets_join, question_select = self._markets_join()
        return self._fetchall_dict(
            f"SELECT o.market_id, {question_select} o.status, COUNT(*) AS n "
            f"FROM read_parquet({self._glob_recent('orders_log')}, hive_partitioning=true) o "
            f"{markets_join} "
            "WHERE o.dt = ? AND o.status IN ('paper_no_fill','paper_no_book','paper_partial') "
            "GROUP BY o.market_id, m.question, o.status "
            "ORDER BY n DESC LIMIT ?",
            [today, int(limit)],
        )

    def edge_distribution(
        self, today: str | None = None, strategy_id: str = "relationship_aggressive"
    ) -> list[dict]:
        """Coarse 1¢-bucket histogram of |notional_usdc| as a proxy for edge.

        We don't currently store the gross_edge per-order in orders_log, so as a
        first cut we histogram the notional.  The /signals page documents this
        caveat; when a richer per-order edge column lands, swap the column.
        """
        today = today or self._today()
        if not self._has_data("orders_log"):
            return []
        return self._fetchall_dict(
            "SELECT FLOOR(CAST(notional_usdc AS DOUBLE)) AS bucket, COUNT(*) AS n "
            f"FROM read_parquet({self._glob_recent('orders_log')}, hive_partitioning=true) "
            "WHERE dt = ? AND strategy_id = ? AND notional_usdc <> '' "
            "GROUP BY bucket ORDER BY bucket",
            [today, strategy_id],
        )

    def limitless_open_gaps(self, today: str | None = None, limit: int = 25) -> list[dict]:
        """Parse ``arb_gap=X similarity=Y`` out of notes for limitless_arb signals."""
        today = today or self._today()
        if not self._has_data("orders_log"):
            return []
        return self._fetchall_dict(
            "SELECT ts_ms, market_id, notes, "
            "TRY_CAST(regexp_extract(notes, 'arb_gap=([0-9.+-]+)', 1) AS DOUBLE) AS arb_gap, "
            "TRY_CAST(regexp_extract(notes, 'similarity=([0-9.+-]+)', 1) AS DOUBLE) AS similarity "
            f"FROM read_parquet({self._glob_recent('orders_log')}, hive_partitioning=true) "
            "WHERE dt = ? AND strategy_id = 'limitless_arb' "
            "AND regexp_matches(notes, 'arb_gap=') "
            "ORDER BY arb_gap DESC NULLS LAST LIMIT ?",
            [today, int(limit)],
        )

    # ─── markets page ────────────────────────────────────────────────────────

    def market_coverage(self, today: str | None = None) -> dict[str, int]:
        today = today or self._today()
        out = {"total_markets": 0, "active_markets": 0, "markets_with_book_today": 0}
        if self._has_data("markets"):
            row = self._fetchall(
                "SELECT COUNT(DISTINCT id), "
                "COUNT(DISTINCT CASE WHEN active AND NOT closed THEN id END) "
                f"FROM read_parquet('{self._glob('markets')}', hive_partitioning=true)"
            )
            if row:
                out["total_markets"] = int(row[0][0])
                out["active_markets"] = int(row[0][1])
        if self._has_data("orderbook_snapshots"):
            row = self._fetchall(
                "SELECT COUNT(DISTINCT condition_id) "
                f"FROM read_parquet({self._glob_recent('orderbook_snapshots', days=1)}, "
                "hive_partitioning=true) WHERE dt = ?",
                [today],
            )
            if row:
                out["markets_with_book_today"] = int(row[0][0])
        return out

    def relationship_type_breakdown(self) -> list[dict]:
        if not self._has_data("relationship_candidates"):
            return []
        return self._fetchall_dict(
            "SELECT relationship_type, COUNT(*) AS n "
            f"FROM read_parquet('{self._glob('relationship_candidates')}', "
            "hive_partitioning=true) GROUP BY relationship_type ORDER BY n DESC"
        )

    def markets_with_most_relationships(self, limit: int = 20) -> list[dict]:
        if not self._has_data("relationship_candidates"):
            return []
        return self._fetchall_dict(
            "SELECT market_id, COUNT(*) AS n FROM ("
            "SELECT market_id_a AS market_id FROM read_parquet('"
            f"{self._glob('relationship_candidates')}', hive_partitioning=true) "
            "UNION ALL "
            "SELECT market_id_b FROM read_parquet('"
            f"{self._glob('relationship_candidates')}', hive_partitioning=true)"
            ") GROUP BY market_id ORDER BY n DESC LIMIT ?",
            [int(limit)],
        )

    # ─── health ──────────────────────────────────────────────────────────────

    def health_snapshot(self) -> dict[str, Any]:
        today = self._today()
        recorder_last_ms = self._max_ts("orderbook_snapshots", "timestamp_ms", today)
        agent_last_ms = self._max_ts("orders_log", "ts_ms", today)
        snapshot_count = 0
        if self._has_data("orderbook_snapshots"):
            row = self._fetchall(
                f"SELECT COUNT(*) FROM read_parquet({self._glob_recent('orderbook_snapshots', days=1)}, "
                "hive_partitioning=true) WHERE dt = ?",
                [today],
            )
            snapshot_count = int(row[0][0]) if row else 0
        return {
            "today_utc": today,
            "recorder_last_cycle_ts_ms": recorder_last_ms,
            "agent_last_tick_ts_ms": agent_last_ms,
            "orderbook_snapshots_today": snapshot_count,
            "orders_log_writable": self._lake_writable(),
            "services": {
                "recorder": _is_fresh(recorder_last_ms, max_age_s=120),
                "agent": _is_fresh(agent_last_ms, max_age_s=300),
            },
        }

    def _max_ts(self, table: str, col: str, today: str) -> int | None:
        if not self._has_data(table):
            return None
        row = self._fetchall(
            f"SELECT MAX({col}) FROM read_parquet({self._glob_recent(table, days=1)}, "
            "hive_partitioning=true) WHERE dt = ?",
            [today],
        )
        if not row or row[0][0] is None:
            return None
        return int(row[0][0])

    def _lake_writable(self) -> bool:
        probe_dir = self._data_root / "normalised" / "orders_log"
        try:
            probe_dir.mkdir(parents=True, exist_ok=True)
            probe = probe_dir / f".dashboard_probe_{uuid.uuid4().hex}"
            probe.write_text("")
            probe.unlink()
            return True
        except OSError:
            return False


def _empty_hour_buckets() -> list[dict]:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    out: list[dict] = []
    for i in range(23, -1, -1):
        ts = now.timestamp() - i * 3600
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:00")
        out.append({"hour_bucket": hour, "n": 0})
    return out


def _is_fresh(last_ts_ms: int | None, *, max_age_s: int) -> bool:
    if last_ts_ms is None:
        return False
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return (now_ms - last_ts_ms) <= max_age_s * 1000


__all__ = ["DuckDBQueryService"]


# Re-exported for callers that want today's date in the dashboard's TZ.
def today_utc() -> date:
    return datetime.now(timezone.utc).date()
