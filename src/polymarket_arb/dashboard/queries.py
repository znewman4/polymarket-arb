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

import math
import re
import threading
import uuid
from contextlib import suppress
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
        """Return a DuckDB list literal of partition globs for the last ``days`` days.

        Enumerates partition directories that physically exist on disk *and*
        contain at least one parquet file.  This avoids DuckDB IOExceptions from
        empty directories left behind when the recorder's ``find … -mtime +N
        -delete`` removes files without removing the parent directory.
        """
        base = self._data_root / "normalised" / table
        cutoff = date.today() - timedelta(days=days)
        paths: list[str] = []
        for p in sorted(base.glob("dt=*")):
            if not p.is_dir():
                continue
            try:
                dt_val = date.fromisoformat(p.name[3:])  # strip "dt="
            except ValueError:
                continue
            if dt_val <= cutoff:
                continue
            if any(p.glob("*.parquet")):
                paths.append(str(p / "*.parquet"))
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
                f"read_parquet({self._glob_recent('markets', days=7)}, hive_partitioning=true) "
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

    def sharpe_ratio_stats(self) -> dict[str, Any]:
        """Annualised Sharpe of daily paper notional. None when <3 days of data.

        Note: ``notional_usdc`` is trade size, not realised P&L — see plan caveat.
        """
        empty = {
            "sharpe": None,
            "mean_daily_pnl": 0.0,
            "std_daily_pnl": 0.0,
            "days_of_data": 0,
        }
        if not self._has_data("orders_log"):
            return empty
        rows = self._fetchall(
            "SELECT dt, SUM(CAST(notional_usdc AS DOUBLE)) AS daily "
            f"FROM read_parquet({self._glob_recent('orders_log', days=30)}, "
            "hive_partitioning=true) "
            "WHERE status = 'paper_filled' AND notional_usdc <> '' "
            "GROUP BY dt ORDER BY dt"
        )
        daily = [float(d or 0.0) for _, d in rows]
        n = len(daily)
        if n < 3:
            return {**empty, "days_of_data": n}
        mean = sum(daily) / n
        var = sum((x - mean) ** 2 for x in daily) / (n - 1)
        std = math.sqrt(var)
        if std == 0:
            return {
                "sharpe": None,
                "mean_daily_pnl": round(mean, 4),
                "std_daily_pnl": 0.0,
                "days_of_data": n,
            }
        sharpe = (mean / std) * math.sqrt(365)
        return {
            "sharpe": round(sharpe, 2),
            "mean_daily_pnl": round(mean, 4),
            "std_daily_pnl": round(std, 4),
            "days_of_data": n,
        }

    def cumulative_notional_by_hour(self) -> list[dict]:
        """Cumulative deployed notional for filled paper trades over seven days."""
        if not self._has_data("orders_log"):
            return []
        rows = self._fetchall(
            "SELECT strftime(to_timestamp(ts_ms/1000), '%Y-%m-%d %H:00') AS hour_bucket, "
            "SUM(CAST(notional_usdc AS DOUBLE)) AS hourly_notional "
            f"FROM read_parquet({self._glob_recent('orders_log', days=7)}, "
            "hive_partitioning=true) "
            "WHERE status = 'paper_filled' AND notional_usdc <> '' "
            "GROUP BY hour_bucket ORDER BY hour_bucket"
        )
        series: list[dict] = []
        running = 0.0
        for hour_bucket, hourly_notional in rows:
            running += float(hourly_notional or 0.0)
            series.append({
                "hour_bucket": hour_bucket,
                "cumulative_notional": round(running, 4),
            })
        return series

    def expected_pnl_stats(self) -> dict[str, Any]:
        """Expected PnL estimated from ``gross_edge=X`` in filled-order notes."""
        empty = {
            "total_expected_pnl": 0.0,
            "total_cost_basis": 0.0,
            "expected_return_pct": 0.0,
            "trade_count": 0,
        }
        if not self._has_data("orders_log"):
            return empty
        rows = self._fetchall(
            "SELECT notional_usdc, notes "
            f"FROM read_parquet({self._glob_recent('orders_log', days=7)}, "
            "hive_partitioning=true) "
            "WHERE status = 'paper_filled' AND notional_usdc <> ''"
        )
        total_cost = 0.0
        total_expected = 0.0
        trade_count = 0
        for notional_str, notes_str in rows:
            try:
                notional = float(notional_str or 0)
            except (ValueError, TypeError):
                continue
            match = re.search(r"gross_edge=([0-9.+-]+)", notes_str or "")
            if match:
                with suppress(ValueError):
                    total_expected += float(match.group(1)) * notional
            total_cost += notional
            trade_count += 1
        return {
            "total_expected_pnl": round(total_expected, 2),
            "total_cost_basis": round(total_cost, 2),
            "expected_return_pct": round(
                total_expected / total_cost * 100.0 if total_cost else 0.0,
                4,
            ),
            "trade_count": trade_count,
        }

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
            return {
                "rows": [],
                "total": 0,
                "page": page,
                "per_page": per_page,
                "pages": 0,
                "total_notional": 0.0,
            }
        where_sql, params = self._orders_filter(strategy_id, status, date_from, date_to)
        total_row = self._fetchall(
            "SELECT COUNT(*), COALESCE(SUM(CASE WHEN notional_usdc <> '' "
            "THEN CAST(notional_usdc AS DOUBLE) ELSE 0 END), 0) "
            f"FROM read_parquet('{self._glob('orders_log')}', "
            f"hive_partitioning=true) o {where_sql}",
            params,
        )
        total = int(total_row[0][0]) if total_row else 0
        total_notional = float(total_row[0][1]) if total_row else 0.0
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
            "total_notional": round(total_notional, 2),
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

    def tradebook_page(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
    ) -> dict[str, Any]:
        """Paginated view of filled paper trades over the last 30 days."""
        empty_summary = {"trades": 0, "total_notional": 0.0, "avg_size": 0.0, "best": 0.0}
        if not self._has_data("orders_log"):
            return {
                "rows": [],
                "total": 0,
                "page": page,
                "per_page": per_page,
                "pages": 0,
                "summary": empty_summary,
            }
        glob = self._glob_recent("orders_log", days=30)
        total_row = self._fetchall(
            "SELECT COUNT(*), "
            "COALESCE(SUM(CAST(notional_usdc AS DOUBLE)), 0), "
            "COALESCE(AVG(CAST(notional_usdc AS DOUBLE)), 0), "
            "COALESCE(MAX(CAST(notional_usdc AS DOUBLE)), 0) "
            f"FROM read_parquet({glob}, hive_partitioning=true) "
            "WHERE status = 'paper_filled' AND notional_usdc <> ''"
        )
        total = int(total_row[0][0] or 0) if total_row else 0
        total_notional = float(total_row[0][1] or 0.0) if total_row else 0.0
        avg_size = float(total_row[0][2] or 0.0) if total_row else 0.0
        best = float(total_row[0][3] or 0.0) if total_row else 0.0
        markets_join, question_select = self._markets_join()
        offset = max(0, (page - 1) * per_page)
        rows = self._fetchall_dict(
            "SELECT * FROM ("
            f"  SELECT o.ts_ms, o.strategy_id, o.market_id, {question_select} o.side, "
            "  o.notional_usdc, o.notes, "
            "  SUM(CAST(o.notional_usdc AS DOUBLE)) OVER ("
            "    ORDER BY o.ts_ms ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
            "  ) AS cumulative_notional "
            f"  FROM read_parquet({glob}, hive_partitioning=true) o "
            f"  {markets_join} "
            "  WHERE o.status = 'paper_filled' AND o.notional_usdc <> '' "
            ") ORDER BY ts_ms DESC LIMIT ? OFFSET ?",
            [int(per_page), int(offset)],
        )
        pages = (total + per_page - 1) // per_page if per_page else 0
        return {
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
            "summary": {
                "trades": total,
                "total_notional": round(total_notional, 2),
                "avg_size": round(avg_size, 2),
                "best": round(best, 2),
            },
        }

    def iter_tradebook_for_csv(self, *, chunk_size: int = 1000):
        """Yield (cols, rows_chunk) of filled paper trades for streamed CSV export."""
        cols_static = [
            "ts_ms", "strategy_id", "market_id", "question", "side",
            "notional_usdc", "notes", "cumulative_notional",
        ]
        if not self._has_data("orders_log"):
            yield cols_static, []
            return
        glob = self._glob_recent("orders_log", days=30)
        markets_join, question_select = self._markets_join()
        sql = (
            "SELECT * FROM ("
            f"  SELECT o.ts_ms, o.strategy_id, o.market_id, {question_select} o.side, "
            "  o.notional_usdc, o.notes, "
            "  SUM(CAST(o.notional_usdc AS DOUBLE)) OVER ("
            "    ORDER BY o.ts_ms ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
            "  ) AS cumulative_notional "
            f"  FROM read_parquet({glob}, hive_partitioning=true) o "
            f"  {markets_join} "
            "  WHERE o.status = 'paper_filled' AND o.notional_usdc <> '' "
            ") ORDER BY ts_ms DESC"
        )
        with self._lock:
            cur = self._con.execute(sql)
            cols = [c[0] for c in cur.description]
            yield cols, []
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
            f"read_parquet({self._glob_recent('markets', days=7)}, hive_partitioning=true) "
            "GROUP BY id) m ON m.id = o.market_id"
        )
        return join_sql, "m.question AS question, "

    # ─── positions page ──────────────────────────────────────────────────────

    def open_positions_with_mtm(self) -> list[dict]:
        """Open positions joined against latest books for mark-to-market PnL.

        MTM PnL = (current_mid - entry_price) * size, where current_mid uses
        the latest two-sided orderbook. Limitless arb rows also expose the
        profit locked by their recorded arb gap.
        """
        if not self._has_data("positions") or not self._has_data("orderbook_snapshots"):
            return []
        positions_glob = self._glob_recent("positions", days=30)
        books_glob = self._glob_recent("orderbook_snapshots", days=1)
        return self._fetchall_dict(
            "WITH position_states AS ("
            "  SELECT *, row_number() OVER (PARTITION BY position_id "
            "    ORDER BY COALESCE(ingested_ts_ms, open_ts_ms) DESC, open_ts_ms DESC) AS rn "
            f"  FROM read_parquet({positions_glob}, hive_partitioning=true, union_by_name=true)"
            "), latest_books AS ("
            "  SELECT token_id, "
            "    (CAST(bids[1].price AS DOUBLE) + CAST(asks[1].price AS DOUBLE)) / 2 "
            "      AS current_mid, "
            "    row_number() OVER (PARTITION BY token_id "
            "      ORDER BY timestamp_ms DESC, ingested_ts_ms DESC) AS rn "
            f"  FROM read_parquet({books_glob}, hive_partitioning=true) "
            "  WHERE len(bids) > 0 AND len(asks) > 0"
            ") "
            "SELECT p.position_id, p.strategy_id, p.market_id, p.token_id, p.side, "
            "p.open_ts_ms, p.entry_price, p.size, p.notional_usdc, p.gross_edge, "
            "p.relationship_id, p.relationship_type, p.notes, p.status, "
            "p.schema_version, p.ingested_ts_ms, b.current_mid, "
            "(b.current_mid - CAST(p.entry_price AS DOUBLE)) * CAST(p.size AS DOUBLE) "
            "  AS mtm_pnl, "
            "CASE WHEN p.strategy_id = 'limitless_arb' THEN "
            "  TRY_CAST(regexp_extract(p.notes, 'arb_gap=([0-9.+-]+)', 1) AS DOUBLE) "
            "    * CAST(p.size AS DOUBLE) "
            "ELSE NULL END AS locked_profit "
            "FROM position_states p "
            "LEFT JOIN latest_books b ON b.token_id = p.token_id AND b.rn = 1 "
            "WHERE p.rn = 1 AND p.status = 'open' "
            "ORDER BY p.open_ts_ms DESC"
        )

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
                f"FROM read_parquet({self._glob_recent('markets', days=30)}, hive_partitioning=true)"
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
