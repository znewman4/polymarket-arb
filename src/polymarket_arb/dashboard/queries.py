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

import csv
import math
import re
import threading
import time
import uuid
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any

import duckdb

from ..monitoring import kill_switch

_QUERY_CACHE_TTL_S = 60.0
_QUERY_CACHE: dict[str, tuple[Any, float]] = {}
_QUERY_CACHE_LOCK = threading.Lock()


def clear_query_cache() -> None:
    """Clear the process-local dashboard query cache."""
    with _QUERY_CACHE_LOCK:
        _QUERY_CACHE.clear()


def _cache_get(key: str) -> Any | None:
    now = time.monotonic()
    with _QUERY_CACHE_LOCK:
        cached = _QUERY_CACHE.get(key)
        if cached is None:
            return None
        result, ts = cached
        if now - ts >= _QUERY_CACHE_TTL_S:
            _QUERY_CACHE.pop(key, None)
            return None
        return result


def _cache_set(key: str, result: Any) -> None:
    with _QUERY_CACHE_LOCK:
        _QUERY_CACHE[key] = (result, time.monotonic())


def _ttl_cached(fn):
    """Cache a query method by method name for a short dashboard TTL."""

    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        key = fn.__name__
        cached = _cache_get(key)
        if cached is not None:
            return cached
        result = fn(self, *args, **kwargs)
        _cache_set(key, result)
        return result

    return wrapper


def _ttl_cached_iter(fn):
    """Cache streamed query chunks by method name while returning a fresh iterator."""

    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        key = fn.__name__
        chunks = _cache_get(key)
        if chunks is None:
            chunks = list(fn(self, *args, **kwargs))
            _cache_set(key, chunks)
        return iter(chunks)

    return wrapper


class DuckDBQueryService:
    """Thread-safe wrapper around one DuckDB connection.

    DuckDB connections aren't safe for concurrent use, so every query takes a
    lock.  This is fine for a single-user dashboard served over an SSM tunnel;
    if we ever need real concurrency, swap this for a tiny connection pool.
    """

    def __init__(
        self,
        data_root: Path,
        *,
        limitless_paper_mode: bool = True,
        limitless_poly_paper_mode: bool = True,
        relationship_paper_mode: bool = True,
    ) -> None:
        self._data_root = Path(data_root)
        self._con = duckdb.connect(database=":memory:")
        self._lock = threading.Lock()
        self._limitless_paper_mode = limitless_paper_mode
        self._limitless_poly_paper_mode = limitless_poly_paper_mode
        self._relationship_paper_mode = relationship_paper_mode

    def _limitless_mode_label(self) -> str:
        lim_live = not self._limitless_paper_mode
        poly_live = not self._limitless_poly_paper_mode
        if lim_live and poly_live:
            return "LIVE"
        if lim_live and not poly_live:
            return "LIVE (Limitless) / PAPER (Poly)"
        if not lim_live and poly_live:
            return "PAPER (Limitless) / LIVE (Poly)"
        return "PAPER"

    def _relationship_mode_label(self) -> str:
        if not self._relationship_paper_mode:
            return "LIVE"
        return "PAPER"

    def mode_flags(self) -> dict:
        return {
            "limitless_label": self._limitless_mode_label(),
            "relationship_label": self._relationship_mode_label(),
            "limitless_any_live": (
                not self._limitless_paper_mode
                or not self._limitless_poly_paper_mode
            ),
            "relationship_any_live": not self._relationship_paper_mode,
        }

    # ─── housekeeping ────────────────────────────────────────────────────────

    def close(self) -> None:
        clear_query_cache()
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

    @_ttl_cached
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

    @_ttl_cached
    def signals_by_strategy(self, today: str | None = None) -> list[dict]:
        today = today or self._today()
        if not self._has_data("orders_log"):
            return []
        return self._fetchall_dict(
            f"SELECT strategy_id, COUNT(*) AS n FROM read_parquet({self._glob_recent('orders_log')}, "
            "hive_partitioning=true) WHERE dt = ? GROUP BY strategy_id ORDER BY n DESC",
            [today],
        )

    @_ttl_cached
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

    @_ttl_cached
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

    @_ttl_cached
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

    @_ttl_cached
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

    @_ttl_cached
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

    def overview_summary(self) -> dict[str, Any]:
        """Operational overview scoped to the two paper strategies."""
        kill_switches = self.arb_kill_switch_status()
        kill_by_label = {str(row["label"]): row for row in kill_switches}

        open_arb = self.open_arb_positions()
        closed_arb = self.closed_arb_positions()
        arb_realised = [float(row.get("realised_profit") or 0.0) for row in closed_arb]

        relationship_candidates = self.relationship_candidates_summary()
        relationship_open = self.relationship_open_trades()
        relationship_closed = self.relationship_closed_trades()
        relationship_realised = [
            float(row.get("realised_pnl") or 0.0) for row in relationship_closed
        ]
        last_limitless_scan_ts_ms = self._max_order_ts_for_strategy("limitless_arb")
        last_relationship_mine_ts_ms = self._latest_relationship_mine_ts()

        health = self.health_snapshot()
        global_active = bool(kill_by_label.get("Global", {}).get("active"))
        limitless_active = bool(kill_by_label.get("Limitless arb", {}).get("active"))
        relationship_active = bool(kill_by_label.get("Relationship agent", {}).get("active"))

        return {
            "today_utc": health.get("today_utc"),
            "limitless_arb": {
                "display_name": "Limitless Arb",
                "mode": self._limitless_mode_label(),
                "kill_switch_active": global_active or limitless_active,
                "kill_switches": [
                    kill_by_label.get("Global"),
                    kill_by_label.get("Limitless arb"),
                ],
                "open_positions": len(open_arb),
                "closed_positions": len(closed_arb),
                "realised_pnl": round(sum(arb_realised), 4),
                "last_scan_ts_ms": last_limitless_scan_ts_ms,
            },
            "relationship_agent": {
                "display_name": "Relationship Aggressive",
                "mode": self._relationship_mode_label(),
                "kill_switch_active": global_active or relationship_active,
                "kill_switches": [
                    kill_by_label.get("Global"),
                    kill_by_label.get("Relationship agent"),
                ],
                "active_relationships": relationship_candidates["total_accepted"],
                "open_trades": len(relationship_open),
                "closed_trades": len(relationship_closed),
                "realised_pnl": round(sum(relationship_realised), 4),
                "last_mine_ts_ms": last_relationship_mine_ts_ms,
            },
            "health": {
                "orderbook_snapshots_today": health.get("orderbook_snapshots_today", 0),
                "last_limitless_scan_ts_ms": last_limitless_scan_ts_ms,
                "last_relationship_mine_ts_ms": last_relationship_mine_ts_ms,
                "last_orderbook_snapshot_ts_ms": health.get("recorder_last_cycle_ts_ms"),
                "last_agent_tick_ts_ms": health.get("agent_last_tick_ts_ms"),
                "orders_log_writable": health.get("orders_log_writable", False),
            },
        }

    def relationship_candidates_summary(self, min_confidence: float = 0.85) -> dict[str, Any]:
        """Accepted relationship candidates grouped by type for dashboard summary."""
        rows = self._latest_relationship_candidate_rows()
        by_type = {
            "inverse": 0,
            "nested": 0,
            "mutually_exclusive": 0,
            "same_reference_clock": 0,
            "other": 0,
        }
        confidence_buckets = {
            "0.95+": 0,
            "0.90-0.95": 0,
            "0.85-0.90": 0,
        }
        accepted: list[dict] = []
        for row in rows:
            if row.get("validation_status") != "accepted":
                continue
            confidence = _float_or_none(row.get("final_confidence")) or 0.0
            if confidence < min_confidence:
                continue
            accepted.append(row)
            by_type[_relationship_type_bucket(row.get("relationship_type"))] += 1
            if confidence >= 0.95:
                confidence_buckets["0.95+"] += 1
            elif confidence >= 0.90:
                confidence_buckets["0.90-0.95"] += 1
            else:
                confidence_buckets["0.85-0.90"] += 1
        return {
            "min_confidence": min_confidence,
            "total_accepted": len(accepted),
            "by_type": by_type,
            "confidence_buckets": confidence_buckets,
        }

    def relationship_open_trades(self) -> list[dict]:
        """Open relationship_aggressive paper trades with no exit."""
        rows = [
            row for row in self._latest_strategy_position_rows("relationship_aggressive")
            if row.get("status") == "open"
        ]
        if not rows:
            return self._relationship_open_trades_from_orders()

        rel_lookup = {
            str(row.get("relationship_id") or ""): row
            for row in self._latest_relationship_candidate_rows()
        }
        token_ids = {str(row.get("token_id") or "") for row in rows if row.get("token_id")}
        mid_by_token = self._latest_orderbook_mid_by_token(token_ids)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        grouped: dict[str, dict] = {}
        market_ids: set[str] = set()
        for row in rows:
            rel_id = str(row.get("relationship_id") or _note_value(row.get("notes") or "", "relationship_id") or "")
            if not rel_id:
                rel_id = f"{row.get('market_id') or ''}:{row.get('token_id') or ''}"
            rel = rel_lookup.get(rel_id, {})
            market_ids.update(
                mid
                for mid in (
                    str(rel.get("market_id_a") or ""),
                    str(rel.get("market_id_b") or ""),
                    str(row.get("market_id") or ""),
                )
                if mid
            )
            group = grouped.setdefault(
                rel_id,
                {
                    "relationship_id": rel_id,
                    "relationship_type": rel.get("relationship_type")
                    or row.get("relationship_type")
                    or "",
                    "type_bucket": _relationship_type_bucket(
                        rel.get("relationship_type") or row.get("relationship_type")
                    ),
                    "confidence": _float_or_none(rel.get("final_confidence")),
                    "question_a": rel.get("question_a") or row.get("market_id") or "",
                    "question_b": rel.get("question_b") or "",
                    "market_id_a": rel.get("market_id_a") or "",
                    "market_id_b": rel.get("market_id_b") or "",
                    "open_ts_ms": int(row.get("open_ts_ms") or 0),
                    "gross_edge": _float_or_none(row.get("gross_edge"))
                    or _note_float(row.get("notes") or "", "gross_edge"),
                    "stake_usdc": 0.0,
                    "_mtm_values": [],
                },
            )
            group["open_ts_ms"] = min(
                int(group.get("open_ts_ms") or 0), int(row.get("open_ts_ms") or 0)
            )
            if group.get("gross_edge") is None:
                group["gross_edge"] = (
                    _float_or_none(row.get("gross_edge"))
                    or _note_float(row.get("notes") or "", "gross_edge")
                )

            slot, outcome_label = _relationship_position_slot(row, rel)
            leg_key = f"leg_{slot}"
            if slot not in {"a", "b"}:
                leg_key = "leg_a" if not group.get("leg_a") else "leg_b"
                outcome_label = str(row.get("side") or "").upper() or "-"
            entry_price = _float_or_none(row.get("entry_price"))
            size = _float_or_none(row.get("size"))
            notional = _float_or_none(row.get("notional_usdc")) or 0.0
            current_mid = mid_by_token.get(str(row.get("token_id") or ""))
            leg_mtm = _position_leg_mtm(
                side=row.get("side"),
                entry_price=entry_price,
                current_price=current_mid,
                size=size,
            )
            if leg_mtm is not None:
                group["_mtm_values"].append(leg_mtm)
            group["stake_usdc"] = float(group.get("stake_usdc") or 0.0) + notional
            group[leg_key] = {
                "side": outcome_label,
                "entry_price": entry_price,
                "current_price": current_mid,
                "token_id": row.get("token_id") or "",
                "market_id": row.get("market_id") or "",
            }

        expiry_by_market = self._market_end_date_ms_by_id(market_ids)
        out: list[dict] = []
        for group in grouped.values():
            leg_a = group.get("leg_a") or {}
            leg_b = group.get("leg_b") or {}
            group["side_a"] = leg_a.get("side") or "-"
            group["side_b"] = leg_b.get("side") or "-"
            group["entry_price_a"] = leg_a.get("entry_price")
            group["entry_price_b"] = leg_b.get("entry_price")
            group["current_price_a"] = leg_a.get("current_price")
            group["current_price_b"] = leg_b.get("current_price")
            mtm_values = group.pop("_mtm_values", [])
            group["current_mtm"] = round(sum(mtm_values), 4) if mtm_values else None
            open_ms = int(group.get("open_ts_ms") or 0)
            group["time_open_seconds"] = max(0, (now_ms - open_ms) // 1000) if open_ms else None
            group["time_open_label"] = _format_duration(group["time_open_seconds"])
            expiry_candidates = [
                expiry_by_market.get(str(group.get("market_id_a") or "")),
                expiry_by_market.get(str(group.get("market_id_b") or "")),
            ]
            expiry_candidates = [int(ts) for ts in expiry_candidates if ts]
            expiry_ts = min(expiry_candidates) if expiry_candidates else None
            group["expiry_ts_ms"] = expiry_ts
            group["days_to_expiry"] = (
                round(max(0, expiry_ts - now_ms) / 86_400_000, 1)
                if expiry_ts is not None
                else None
            )
            out.append(group)
        return sorted(out, key=lambda row: int(row.get("open_ts_ms") or 0), reverse=True)

    def _relationship_open_trades_from_orders(self) -> list[dict]:
        if not self._has_data("orders_log"):
            return []
        rows = self._fetchall_dict(
            "SELECT ts_ms, market_id, token_id, side, filled_size, avg_fill_price, "
            "notional_usdc, source_relationship_id, notes "
            f"FROM read_parquet({self._glob_recent('orders_log', days=90)}, "
            "hive_partitioning=true, union_by_name=true) "
            "WHERE strategy_id = 'relationship_aggressive' AND status = 'paper_filled' "
            "ORDER BY ts_ms DESC"
        )
        if not rows:
            return []

        closed_ids = {
            str(row.get("relationship_id") or "") for row in self.relationship_closed_trades()
        }
        rel_lookup = {
            str(row.get("relationship_id") or ""): row
            for row in self._latest_relationship_candidate_rows()
        }
        token_ids = {str(row.get("token_id") or "") for row in rows if row.get("token_id")}
        mid_by_token = self._latest_orderbook_mid_by_token(token_ids)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        grouped: dict[str, dict] = {}
        market_ids: set[str] = set()
        for row in rows:
            notes = row.get("notes") or ""
            rel_id = str(row.get("source_relationship_id") or _note_value(notes, "relationship_id") or "")
            if not rel_id or rel_id in closed_ids:
                continue
            rel = rel_lookup.get(rel_id, {})
            market_ids.update(
                mid
                for mid in (
                    str(rel.get("market_id_a") or ""),
                    str(rel.get("market_id_b") or ""),
                    str(row.get("market_id") or ""),
                )
                if mid
            )
            group = grouped.setdefault(
                rel_id,
                {
                    "relationship_id": rel_id,
                    "relationship_type": rel.get("relationship_type") or "",
                    "type_bucket": _relationship_type_bucket(rel.get("relationship_type")),
                    "confidence": _float_or_none(rel.get("final_confidence")),
                    "question_a": rel.get("question_a") or row.get("market_id") or "",
                    "question_b": rel.get("question_b") or "",
                    "market_id_a": rel.get("market_id_a") or "",
                    "market_id_b": rel.get("market_id_b") or "",
                    "open_ts_ms": int(row.get("ts_ms") or 0),
                    "gross_edge": _note_float(notes, "gross_edge"),
                    "stake_usdc": 0.0,
                    "_mtm_values": [],
                },
            )
            group["open_ts_ms"] = min(int(group["open_ts_ms"] or 0), int(row.get("ts_ms") or 0))
            if group.get("gross_edge") is None:
                group["gross_edge"] = _note_float(notes, "gross_edge")

            slot, outcome_label = _relationship_position_slot(row, rel)
            leg_key = f"leg_{slot}"
            if slot not in {"a", "b"}:
                leg_key = "leg_a" if not group.get("leg_a") else "leg_b"
                outcome_label = str(row.get("side") or "").upper() or "-"
            entry_price = _float_or_none(row.get("avg_fill_price"))
            size = _float_or_none(row.get("filled_size"))
            notional = _float_or_none(row.get("notional_usdc")) or 0.0
            current_mid = mid_by_token.get(str(row.get("token_id") or ""))
            leg_mtm = _position_leg_mtm(
                side=row.get("side"),
                entry_price=entry_price,
                current_price=current_mid,
                size=size,
            )
            if leg_mtm is not None:
                group["_mtm_values"].append(leg_mtm)
            group["stake_usdc"] = float(group.get("stake_usdc") or 0.0) + notional
            group[leg_key] = {
                "side": outcome_label,
                "entry_price": entry_price,
                "current_price": current_mid,
                "token_id": row.get("token_id") or "",
                "market_id": row.get("market_id") or "",
            }

        expiry_by_market = self._market_end_date_ms_by_id(market_ids)
        out: list[dict] = []
        for group in grouped.values():
            leg_a = group.get("leg_a") or {}
            leg_b = group.get("leg_b") or {}
            group["side_a"] = leg_a.get("side") or "-"
            group["side_b"] = leg_b.get("side") or "-"
            group["entry_price_a"] = leg_a.get("entry_price")
            group["entry_price_b"] = leg_b.get("entry_price")
            group["current_price_a"] = leg_a.get("current_price")
            group["current_price_b"] = leg_b.get("current_price")
            mtm_values = group.pop("_mtm_values", [])
            group["current_mtm"] = round(sum(mtm_values), 4) if mtm_values else None
            open_ms = int(group.get("open_ts_ms") or 0)
            group["time_open_seconds"] = max(0, (now_ms - open_ms) // 1000) if open_ms else None
            group["time_open_label"] = _format_duration(group["time_open_seconds"])
            expiry_candidates = [
                expiry_by_market.get(str(group.get("market_id_a") or "")),
                expiry_by_market.get(str(group.get("market_id_b") or "")),
            ]
            expiry_candidates = [int(ts) for ts in expiry_candidates if ts]
            expiry_ts = min(expiry_candidates) if expiry_candidates else None
            group["expiry_ts_ms"] = expiry_ts
            group["days_to_expiry"] = (
                round(max(0, expiry_ts - now_ms) / 86_400_000, 1)
                if expiry_ts is not None
                else None
            )
            out.append(group)
        return sorted(out, key=lambda row: int(row.get("open_ts_ms") or 0), reverse=True)

    def relationship_closed_trades(self) -> list[dict]:
        """Closed/resolved relationship_aggressive trades with PnL."""
        rows = [
            row for row in self._latest_strategy_position_rows("relationship_aggressive")
            if row.get("status") in {"closed", "resolved"}
        ]
        if not rows:
            return []

        rel_lookup = {
            str(row.get("relationship_id") or ""): row
            for row in self._latest_relationship_candidate_rows()
        }
        grouped: dict[str, dict] = {}
        for row in rows:
            notes = row.get("notes") or ""
            rel_id = str(row.get("relationship_id") or _note_value(notes, "relationship_id") or "")
            if not rel_id:
                rel_id = f"{row.get('market_id') or ''}:{row.get('token_id') or ''}"
            rel = rel_lookup.get(rel_id, {})
            group = grouped.setdefault(
                rel_id,
                {
                    "relationship_id": rel_id,
                    "relationship_type": rel.get("relationship_type")
                    or row.get("relationship_type")
                    or "",
                    "type_bucket": _relationship_type_bucket(
                        rel.get("relationship_type") or row.get("relationship_type")
                    ),
                    "confidence": _float_or_none(rel.get("final_confidence")),
                    "question_a": rel.get("question_a") or row.get("market_id") or "",
                    "question_b": rel.get("question_b") or "",
                    "open_ts_ms": int(row.get("open_ts_ms") or 0),
                    "exit_ts_ms": int(row.get("ingested_ts_ms") or row.get("open_ts_ms") or 0),
                    "_pnl_values": [],
                },
            )
            group["open_ts_ms"] = min(
                int(group.get("open_ts_ms") or 0), int(row.get("open_ts_ms") or 0)
            )
            group["exit_ts_ms"] = max(
                int(group.get("exit_ts_ms") or 0),
                int(row.get("ingested_ts_ms") or row.get("open_ts_ms") or 0),
            )
            realised = (
                _note_float(notes, "realised_pnl")
                or _note_float(notes, "realised_profit")
                or _note_float(notes, "pnl")
            )
            if realised is not None:
                group["_pnl_values"].append(realised)

            slot, outcome_label = _relationship_position_slot(row, rel)
            leg_key = f"leg_{slot}"
            if slot not in {"a", "b"}:
                leg_key = "leg_a" if not group.get("leg_a") else "leg_b"
                outcome_label = str(row.get("side") or "").upper() or "-"
            group[leg_key] = {
                "side": outcome_label,
                "entry_price": _float_or_none(row.get("entry_price")),
                "exit_price": _note_float(notes, "exit_price")
                or _note_float(notes, "close_price")
                or _float_or_none(row.get("entry_price")),
            }

        out: list[dict] = []
        for group in grouped.values():
            leg_a = group.get("leg_a") or {}
            leg_b = group.get("leg_b") or {}
            group["side_a"] = leg_a.get("side") or "-"
            group["side_b"] = leg_b.get("side") or "-"
            group["entry_price_a"] = leg_a.get("entry_price")
            group["entry_price_b"] = leg_b.get("entry_price")
            group["exit_price_a"] = leg_a.get("exit_price")
            group["exit_price_b"] = leg_b.get("exit_price")
            pnl_values = group.pop("_pnl_values", [])
            group["realised_pnl"] = round(sum(pnl_values), 4) if pnl_values else 0.0
            hold_seconds = None
            if group.get("open_ts_ms") and group.get("exit_ts_ms"):
                hold_seconds = max(
                    0,
                    (int(group["exit_ts_ms"]) - int(group["open_ts_ms"])) // 1000,
                )
            group["hold_duration_label"] = _format_duration(hold_seconds)
            out.append(group)
        return sorted(out, key=lambda row: int(row.get("exit_ts_ms") or 0), reverse=True)

    def relationship_browser(
        self,
        *,
        min_confidence: float = 0.85,
        page: int = 1,
        per_page: int = 50,
    ) -> dict[str, Any]:
        """Paginated browser of accepted relationship candidates."""
        rows = []
        for row in self._latest_relationship_candidate_rows():
            confidence = _float_or_none(row.get("final_confidence")) or 0.0
            if row.get("validation_status") != "accepted" or confidence < min_confidence:
                continue
            row = dict(row)
            row["final_confidence"] = confidence
            row["type_bucket"] = _relationship_type_bucket(row.get("relationship_type"))
            row["question_a_short"] = _question_excerpt(row.get("question_a"))
            row["question_b_short"] = _question_excerpt(row.get("question_b"))
            rows.append(row)

        open_ids = {str(row.get("relationship_id") or "") for row in self.relationship_open_trades()}
        closed_ids = {str(row.get("relationship_id") or "") for row in self.relationship_closed_trades()}
        for row in rows:
            rel_id = str(row.get("relationship_id") or "")
            if rel_id in open_ids:
                row["trade_status"] = "open"
            elif rel_id in closed_ids:
                row["trade_status"] = "closed"
            else:
                row["trade_status"] = "untraded"

        rows.sort(
            key=lambda row: (
                float(row.get("final_confidence") or 0.0),
                int(row.get("ingested_ts_ms") or 0),
            ),
            reverse=True,
        )
        page = max(1, int(page))
        per_page = max(1, int(per_page))
        total = len(rows)
        pages = (total + per_page - 1) // per_page if per_page else 0
        offset = (page - 1) * per_page
        return {
            "rows": rows[offset: offset + per_page],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
            "min_confidence": min_confidence,
        }

    def _latest_relationship_candidate_rows(self) -> list[dict]:
        if not self._has_data("relationship_candidates"):
            return []
        return self._fetchall_dict(
            "WITH latest AS ("
            "  SELECT relationship_id, market_id_a, market_id_b, token_id_a_yes, "
            "  token_id_a_no, token_id_b_yes, token_id_b_no, question_a, question_b, "
            "  relationship_type, TRY_CAST(final_confidence AS DOUBLE) AS final_confidence, "
            "  validation_status, ingested_ts_ms, "
            "  row_number() OVER (PARTITION BY relationship_id ORDER BY ingested_ts_ms DESC) AS rn "
            f"  FROM read_parquet('{self._glob('relationship_candidates')}', "
            "  hive_partitioning=true, union_by_name=true)"
            ") SELECT relationship_id, market_id_a, market_id_b, token_id_a_yes, token_id_a_no, "
            "token_id_b_yes, token_id_b_no, question_a, question_b, relationship_type, "
            "final_confidence, validation_status, ingested_ts_ms "
            "FROM latest WHERE rn = 1"
        )

    def _latest_strategy_position_rows(self, strategy_id: str) -> list[dict]:
        if not self._has_data("positions"):
            return []
        return self._fetchall_dict(
            "WITH latest AS ("
            "  SELECT position_id, strategy_id, market_id, token_id, side, open_ts_ms, "
            "  entry_price, size, notional_usdc, gross_edge, relationship_id, "
            "  relationship_type, notes, status, ingested_ts_ms, "
            "  row_number() OVER (PARTITION BY position_id "
            "    ORDER BY COALESCE(ingested_ts_ms, open_ts_ms) DESC, open_ts_ms DESC) AS rn "
            f"  FROM read_parquet({self._glob_recent('positions', days=90)}, "
            "  hive_partitioning=true, union_by_name=true) "
            "  WHERE strategy_id = ? AND side != 'snapshot'"
            ") SELECT position_id, strategy_id, market_id, token_id, side, open_ts_ms, "
            "entry_price, size, notional_usdc, gross_edge, relationship_id, relationship_type, "
            "notes, status, ingested_ts_ms FROM latest WHERE rn = 1",
            [strategy_id],
        )

    def _market_end_date_ms_by_id(self, market_ids: set[str]) -> dict[str, int]:
        if not market_ids or not self._has_data("markets"):
            return {}
        ids = sorted(mid for mid in market_ids if mid)
        if not ids:
            return {}
        rows = self._fetchall_dict(
            "WITH latest AS ("
            "  SELECT id, TRY_CAST(end_date_ms AS BIGINT) AS end_date_ms, "
            "  row_number() OVER (PARTITION BY id ORDER BY ingested_ts_ms DESC) AS rn "
            f"  FROM read_parquet({self._glob_recent('markets', days=30)}, "
            "  hive_partitioning=true, union_by_name=true) "
            "  WHERE id IN (" + ", ".join(["?"] * len(ids)) + ")"
            ") SELECT id, end_date_ms FROM latest WHERE rn = 1",
            ids,
        )
        return {
            str(row["id"]): int(row["end_date_ms"])
            for row in rows
            if row.get("end_date_ms") is not None
        }

    def _max_order_ts_for_strategy(self, strategy_id: str) -> int | None:
        if not self._has_data("orders_log"):
            return None
        row = self._fetchall(
            f"SELECT MAX(ts_ms) FROM read_parquet({self._glob_recent('orders_log', days=30)}, "
            "hive_partitioning=true) WHERE strategy_id = ?",
            [strategy_id],
        )
        if not row or row[0][0] is None:
            return None
        return int(row[0][0])

    def _latest_relationship_mine_ts(self) -> int | None:
        if not self._has_data("relationship_candidates"):
            return None
        row = self._fetchall(
            f"SELECT MAX(ingested_ts_ms) FROM read_parquet('{self._glob('relationship_candidates')}', "
            "hive_partitioning=true, union_by_name=true)"
        )
        if not row or row[0][0] is None:
            return None
        return int(row[0][0])

    # ─── live monitor ────────────────────────────────────────────────────────

    @_ttl_cached
    def live_monitor_data(self) -> dict:
        """Real-time trading activity for the live monitor page.

        Scopes entirely to today's orders_log partition for speed.
        Returns counts and recent rows for both paper and live orders.
        """
        empty = {
            "live_submitted_today": 0,
            "live_failed_today": 0,
            "paper_filled_today": 0,
            "rejected_today": 0,
            "total_live_notional_today": 0.0,
            "strategies_active": [],
            "recent_live_orders": [],
            "recent_all_orders": [],
            "last_order_ts_ms": None,
        }
        if not self._has_data("orders_log"):
            return empty

        today_glob = self._glob_recent("orders_log", days=1)

        counts = self._fetchall(
            f"SELECT status, COUNT(*) as cnt, "
            f"SUM(CASE WHEN notional_usdc <> '' THEN "
            f"TRY_CAST(notional_usdc AS DOUBLE) ELSE 0 END) as total_notional "
            f"FROM read_parquet({today_glob}, hive_partitioning=true) "
            f"GROUP BY status"
        )

        live_submitted = 0
        live_failed = 0
        paper_filled = 0
        rejected = 0
        total_live_notional = 0.0

        for status, cnt, notional in counts:
            if status == "live_submitted":
                live_submitted = int(cnt)
                total_live_notional += float(notional or 0)
            elif status == "live_failed":
                live_failed = int(cnt)
            elif status == "paper_filled":
                paper_filled = int(cnt)
            elif str(status).startswith("rejected"):
                rejected += int(cnt)

        strategies = self._fetchall(
            f"SELECT DISTINCT strategy_id FROM read_parquet({today_glob}, "
            f"hive_partitioning=true) WHERE strategy_id <> '' ORDER BY strategy_id"
        )
        strategies_active = [s[0] for s in strategies]

        recent_live = self._fetchall_dict(
            f"SELECT ts_ms, strategy_id, market_id, token_id, side, "
            f"notional_usdc, status, notes, reason "
            f"FROM read_parquet({today_glob}, hive_partitioning=true) "
            f"WHERE status IN ('live_submitted', 'live_failed') "
            f"ORDER BY ts_ms DESC LIMIT 20"
        )

        recent_all = self._fetchall_dict(
            f"SELECT ts_ms, strategy_id, market_id, side, "
            f"notional_usdc, status, notes "
            f"FROM read_parquet({today_glob}, hive_partitioning=true) "
            f"ORDER BY ts_ms DESC LIMIT 20"
        )

        last_ts = self._fetchall(
            f"SELECT MAX(ts_ms) FROM read_parquet({today_glob}, hive_partitioning=true)"
        )
        last_order_ts_ms = int(last_ts[0][0]) if last_ts and last_ts[0][0] else None

        return {
            "live_submitted_today": live_submitted,
            "live_failed_today": live_failed,
            "paper_filled_today": paper_filled,
            "rejected_today": rejected,
            "total_live_notional_today": round(total_live_notional, 2),
            "strategies_active": strategies_active,
            "recent_live_orders": recent_live,
            "recent_all_orders": recent_all,
            "last_order_ts_ms": last_order_ts_ms,
        }

    # ─── orders ──────────────────────────────────────────────────────────────

    @_ttl_cached
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

    @_ttl_cached_iter
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

    @_ttl_cached
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

    @_ttl_cached_iter
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

    @_ttl_cached
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
            "  WHERE side != 'snapshot'"
            "), latest_books AS ("
            "  SELECT token_id, "
            "    (CAST(bids[1].price AS DOUBLE) + CAST(asks[1].price AS DOUBLE)) / 2 "
            "      AS current_mid, "
            "    row_number() OVER (PARTITION BY token_id "
            "      ORDER BY timestamp_ms DESC, ingested_ts_ms DESC) AS rn "
            f"  FROM read_parquet({books_glob}, hive_partitioning=true) "
            "  WHERE dt >= current_date - INTERVAL 1 DAY "
            "  AND len(bids) > 0 AND len(asks) > 0"
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

    @_ttl_cached
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

    @_ttl_cached
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

    @_ttl_cached
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

    @_ttl_cached
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
                "hive_partitioning=true) "
                "WHERE dt >= current_date - INTERVAL 1 DAY AND dt = ?",
                [today],
            )
            if row:
                out["markets_with_book_today"] = int(row[0][0])
        return out

    @_ttl_cached
    def relationship_type_breakdown(self) -> list[dict]:
        if not self._has_data("relationship_candidates"):
            return []
        return self._fetchall_dict(
            "SELECT relationship_type, COUNT(*) AS n "
            f"FROM read_parquet('{self._glob('relationship_candidates')}', "
            "hive_partitioning=true) GROUP BY relationship_type ORDER BY n DESC"
        )

    @_ttl_cached
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

    @_ttl_cached
    def health_snapshot(self) -> dict[str, Any]:
        today = self._today()
        recorder_last_ms = self._max_ts("orderbook_snapshots", "timestamp_ms", today)
        agent_last_ms = self._max_ts("orders_log", "ts_ms", today)
        snapshot_count = 0
        if self._has_data("orderbook_snapshots"):
            row = self._fetchall(
                f"SELECT COUNT(*) FROM read_parquet({self._glob_recent('orderbook_snapshots', days=1)}, "
                "hive_partitioning=true) "
                "WHERE dt >= current_date - INTERVAL 1 DAY AND dt = ?",
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
        where_sql = "dt = ?"
        if table == "orderbook_snapshots":
            where_sql = "dt >= current_date - INTERVAL 1 DAY AND dt = ?"
        row = self._fetchall(
            f"SELECT MAX({col}) FROM read_parquet({self._glob_recent(table, days=1)}, "
            f"hive_partitioning=true) WHERE {where_sql}",
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

    # ─── arb monitor ────────────────────────────────────────────────────────

    def arb_kill_switch_status(self) -> list[dict]:
        """Return global and per-strategy kill-switch file states."""
        paths = [
            ("Global", self._data_root / ".killswitch"),
            ("Limitless arb", self._data_root / Path(kill_switch.LIMITLESS_ARB_PATH).name),
            ("Relationship agent", self._data_root / Path(kill_switch.AGENT_PATH).name),
        ]
        return [
            {
                "label": label,
                "path": str(path),
                "active": path.exists(),
            }
            for label, path in paths
        ]

    def open_arb_positions(self) -> list[dict]:
        """Open limitless_arb positions from positions parquet."""
        if not self._has_data("positions"):
            return []

        rows = self._fetchall_dict(
            "SELECT position_id, strategy_id, market_id, token_id, side, open_ts_ms, "
            "entry_price, size, notional_usdc, gross_edge, relationship_id, "
            "relationship_type, notes, status, ingested_ts_ms "
            f"FROM read_parquet({self._glob_recent('positions', days=90)}, "
            "hive_partitioning=true, union_by_name=true) "
            "WHERE strategy_id = 'limitless_arb' "
            "ORDER BY COALESCE(ingested_ts_ms, open_ts_ms) DESC"
        )
        latest_by_position: dict[str, dict] = {}
        snapshots_by_relationship: dict[str, dict] = {}
        for row in rows:
            row["ingested_ts_ms"] = row.get("ingested_ts_ms") or row.get("open_ts_ms") or 0
            position_id = str(row.get("position_id") or "")
            if not position_id:
                continue
            current = latest_by_position.get(position_id)
            if current is None or int(row["ingested_ts_ms"]) > int(current["ingested_ts_ms"]):
                latest_by_position[position_id] = row
            if row.get("side") == "snapshot":
                rel_id = str(row.get("relationship_id") or position_id)
                snap = snapshots_by_relationship.get(rel_id)
                if snap is None or int(row["ingested_ts_ms"]) > int(snap["ingested_ts_ms"]):
                    snapshots_by_relationship[rel_id] = row

        grouped: dict[str, dict] = {}
        lim_token_ids: set[str] = set()
        for row in latest_by_position.values():
            if row.get("status") != "open" or row.get("side") == "snapshot":
                continue
            rel_id = str(row.get("relationship_id") or _arb_relationship_id(row))
            if not rel_id:
                continue
            notes = row.get("notes") or ""
            group = grouped.setdefault(
                rel_id,
                {
                    "position_id": rel_id,
                    "market_slug": _note_value(notes, "slug") or row.get("market_id") or "",
                    "entry_arb_gap": _float_or_none(row.get("gross_edge"))
                    or _note_float(notes, "arb_gap"),
                    "lim_entry_price": _note_float(notes, "lim_entry"),
                    "poly_yes_entry_price": _note_float(notes, "poly_yes_entry"),
                    "stake_usdc": 0.0,
                    "open_ts_ms": int(row.get("open_ts_ms") or 0),
                    "lim_token_id": "",
                    "current_mtm": None,
                    "current_lim_yes": None,
                    "current_poly_yes": None,
                    "current_gap": None,
                    "snapshot_ts_ms": None,
                },
            )
            group["open_ts_ms"] = min(group["open_ts_ms"], int(row.get("open_ts_ms") or 0))
            if not group["market_slug"]:
                group["market_slug"] = _note_value(notes, "slug") or row.get("market_id") or ""
            if group["entry_arb_gap"] is None:
                group["entry_arb_gap"] = _float_or_none(row.get("gross_edge")) or _note_float(notes, "arb_gap")
            if group["lim_entry_price"] is None:
                group["lim_entry_price"] = _note_float(notes, "lim_entry")
            if group["poly_yes_entry_price"] is None:
                group["poly_yes_entry_price"] = _note_float(notes, "poly_yes_entry")
            group["stake_usdc"] = max(
                float(group["stake_usdc"] or 0.0),
                _float_or_none(row.get("notional_usdc")) or _float_or_none(row.get("size")) or 0.0,
            )
            if str(row.get("position_id") or "").endswith("_lim"):
                group["lim_token_id"] = str(row.get("token_id") or "")
                if group["lim_token_id"]:
                    lim_token_ids.add(group["lim_token_id"])

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        quote_by_slug = self._latest_cross_market_quotes()
        lim_mid_by_token = self._latest_orderbook_mid_by_token(lim_token_ids)
        out: list[dict] = []
        for rel_id, group in grouped.items():
            snap = snapshots_by_relationship.get(rel_id)
            snapshot_notes = ""
            if snap is not None:
                snapshot_notes = snap.get("notes") or ""
                group["snapshot_ts_ms"] = int(snap.get("ingested_ts_ms") or snap.get("open_ts_ms") or 0)
            quote = quote_by_slug.get(str(group.get("market_slug") or ""))
            current_lim_yes = (
                lim_mid_by_token.get(str(group.get("lim_token_id") or ""))
                or (quote or {}).get("current_lim_yes")
                or _note_float(snapshot_notes, "lim_now")
            )
            current_poly_yes = (
                (quote or {}).get("current_poly_yes")
                or _note_float(snapshot_notes, "poly_yes_now")
            )
            current_gap = (quote or {}).get("current_gap")
            if current_gap is None and current_lim_yes is not None and current_poly_yes is not None:
                current_gap = 1.0 - (current_lim_yes + current_poly_yes)
            group["current_lim_yes"] = current_lim_yes
            group["current_poly_yes"] = current_poly_yes
            group["current_gap"] = current_gap
            group["current_mtm"] = _arb_mtm(
                lim_entry=group.get("lim_entry_price"),
                poly_yes_entry=group.get("poly_yes_entry_price"),
                current_lim_yes=current_lim_yes,
                current_poly_yes=current_poly_yes,
                stake=group.get("stake_usdc"),
            )
            group.update(_convergence_progress(group.get("entry_arb_gap"), current_gap))
            open_ms = int(group["open_ts_ms"] or 0)
            group["time_open_seconds"] = max(0, (now_ms - open_ms) // 1000) if open_ms else None
            group["time_open_label"] = _format_duration(group["time_open_seconds"])
            out.append(group)
        return sorted(out, key=lambda row: int(row.get("open_ts_ms") or 0), reverse=True)

    def _latest_orderbook_mid_by_token(self, token_ids: set[str]) -> dict[str, float]:
        if not token_ids or not self._has_data("orderbook_snapshots"):
            return {}
        rows = self._fetchall_dict(
            "WITH latest_books AS ("
            "  SELECT token_id, "
            "    (CAST(bids[1].price AS DOUBLE) + CAST(asks[1].price AS DOUBLE)) / 2 AS mid, "
            "    row_number() OVER (PARTITION BY token_id "
            "      ORDER BY timestamp_ms DESC, ingested_ts_ms DESC) AS rn "
            f"  FROM read_parquet({self._glob_recent('orderbook_snapshots', days=1)}, "
            "hive_partitioning=true) "
            "  WHERE dt >= current_date - INTERVAL 1 DAY "
            "  AND len(bids) > 0 AND len(asks) > 0 "
            "  AND token_id IN (" + ", ".join(["?"] * len(token_ids)) + ")"
            ") SELECT token_id, mid FROM latest_books WHERE rn = 1",
            list(token_ids),
        )
        return {
            str(row["token_id"]): float(row["mid"])
            for row in rows
            if row.get("mid") is not None
        }

    def _latest_cross_market_quotes(self) -> dict[str, dict]:
        base = self._data_root / "cross_market_arb"
        if not base.exists():
            return {}

        quotes: dict[str, dict] = {}
        files = sorted(
            [*base.rglob("*.parquet"), *base.rglob("*.csv")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in files:
            if path.suffix == ".parquet":
                try:
                    rows = self._fetchall_dict(
                        "SELECT limitless_slug, limitless_yes, poly_yes, arb_gap "
                        "FROM read_parquet(?)",
                        [str(path)],
                    )
                except Exception:
                    continue
                for row in rows:
                    _record_arb_quote(quotes, row)
            elif path.suffix == ".csv":
                try:
                    with path.open("r", encoding="utf-8", newline="") as fh:
                        for row in csv.DictReader(fh):
                            _record_arb_quote(quotes, row)
                except OSError:
                    continue
        return quotes

    def closed_arb_positions(self) -> list[dict]:
        """Closed positions with realised PnL parsed from orders_log exit rows."""
        if not self._has_data("orders_log"):
            return []

        rows = self._fetchall_dict(
            "SELECT ts_ms, market_id, side, avg_fill_price, status, "
            "source_relationship_id, notes "
            f"FROM read_parquet({self._glob_recent('orders_log', days=90)}, "
            "hive_partitioning=true) "
            "WHERE strategy_id = 'limitless_arb_exit' "
            "ORDER BY ts_ms DESC"
        )
        grouped: dict[str, dict] = {}
        for row in rows:
            notes = row.get("notes") or ""
            rel_id = str(row.get("source_relationship_id") or _note_value(notes, "position_id") or "")
            if not rel_id:
                rel_id = f"{row.get('market_id') or ''}:{row.get('ts_ms') or ''}"
            group = grouped.setdefault(
                rel_id,
                {
                    "position_id": rel_id,
                    "market_slug": "",
                    "realised_profit": None,
                    "exit_ts_ms": int(row.get("ts_ms") or 0),
                    "lim_exit_price": None,
                    "poly_exit_price": None,
                    "status": row.get("status") or "",
                },
            )
            group["exit_ts_ms"] = max(group["exit_ts_ms"], int(row.get("ts_ms") or 0))
            realised = _note_float(notes, "realised_profit")
            if realised is not None:
                group["realised_profit"] = realised
            side = str(row.get("side") or "").lower()
            if side == "sell_yes":
                group["market_slug"] = str(row.get("market_id") or group["market_slug"])
                group["lim_exit_price"] = _note_float(notes, "lim_exit") or _float_or_none(
                    row.get("avg_fill_price")
                )
            elif side in {"sell", "sell_no"}:
                group["poly_exit_price"] = _float_or_none(row.get("avg_fill_price"))
                if group["poly_exit_price"] is None:
                    poly_yes = _note_float(notes, "poly_yes_current")
                    if poly_yes is not None:
                        group["poly_exit_price"] = 1.0 - poly_yes
            if not group["market_slug"] and _note_value(notes, "slug"):
                group["market_slug"] = _note_value(notes, "slug") or ""

        out = []
        for group in grouped.values():
            if group["realised_profit"] is None:
                group["realised_profit"] = 0.0
            out.append(group)
        return sorted(out, key=lambda row: int(row.get("exit_ts_ms") or 0), reverse=True)


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


def _note_value(notes: str, key: str) -> str | None:
    match = re.search(rf"(?:^|\s){re.escape(key)}=([^\s]+)", notes or "")
    return match.group(1) if match else None


def _note_float(notes: str, key: str) -> float | None:
    value = _note_value(notes, key)
    return _float_or_none(value)


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(out):
        return out
    return None


def _arb_relationship_id(row: dict) -> str:
    position_id = str(row.get("position_id") or "")
    for suffix in ("_lim", "_poly"):
        if position_id.endswith(suffix):
            return position_id[: -len(suffix)]
    return position_id


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    days, rem = divmod(max(0, int(seconds)), 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _record_arb_quote(quotes: dict[str, dict], row: dict) -> None:
    slug = str(row.get("limitless_slug") or "")
    if not slug or slug in quotes:
        return
    current_lim_yes = _float_or_none(row.get("limitless_yes"))
    current_poly_yes = _float_or_none(row.get("poly_yes"))
    current_gap = _float_or_none(row.get("arb_gap"))
    if current_gap is None and current_lim_yes is not None and current_poly_yes is not None:
        current_gap = 1.0 - (current_lim_yes + current_poly_yes)
    quotes[slug] = {
        "current_lim_yes": current_lim_yes,
        "current_poly_yes": current_poly_yes,
        "current_gap": current_gap,
    }


def _arb_mtm(
    *,
    lim_entry: Any,
    poly_yes_entry: Any,
    current_lim_yes: Any,
    current_poly_yes: Any,
    stake: Any,
) -> float | None:
    lim_entry_f = _float_or_none(lim_entry)
    poly_entry_f = _float_or_none(poly_yes_entry)
    current_lim_f = _float_or_none(current_lim_yes)
    current_poly_f = _float_or_none(current_poly_yes)
    stake_f = _float_or_none(stake)
    if (
        lim_entry_f is None
        or poly_entry_f is None
        or current_lim_f is None
        or current_poly_f is None
        or stake_f is None
    ):
        return None
    return ((current_lim_f - lim_entry_f) + (poly_entry_f - current_poly_f)) * stake_f


def _convergence_progress(entry_gap: Any, current_gap: Any) -> dict[str, Any]:
    entry = _float_or_none(entry_gap)
    current = _float_or_none(current_gap)
    if entry is None or current is None or entry == 0:
        return {
            "convergence_pct": None,
            "convergence_fill_pct": 0.0,
            "convergence_state": "neutral",
        }
    pct = (entry - current) / abs(entry) * 100.0
    if pct < 0:
        state = "bad"
    elif pct < 5:
        state = "warn"
    else:
        state = "good"
    return {
        "convergence_pct": round(pct, 1),
        "convergence_fill_pct": round(max(0.0, min(100.0, pct)), 1),
        "convergence_state": state,
    }


def _relationship_type_bucket(value: Any) -> str:
    rel_type = str(value or "").lower()
    if "mutually_exclusive" in rel_type or "exclusive" in rel_type:
        return "mutually_exclusive"
    if "same_reference_clock" in rel_type or "reference_clock" in rel_type:
        return "same_reference_clock"
    if "inverse" in rel_type or "contrapositive" in rel_type or "contradiction" in rel_type:
        return "inverse"
    if "nested" in rel_type or "implies" in rel_type:
        return "nested"
    return "other"


def _relationship_position_slot(row: dict, rel: dict) -> tuple[str, str]:
    token_id = str(row.get("token_id") or "")
    market_id = str(row.get("market_id") or "")
    side = str(row.get("side") or "").upper() or "-"
    token_map = {
        str(rel.get("token_id_a_yes") or ""): ("a", "A YES"),
        str(rel.get("token_id_a_no") or ""): ("a", "A NO"),
        str(rel.get("token_id_b_yes") or ""): ("b", "B YES"),
        str(rel.get("token_id_b_no") or ""): ("b", "B NO"),
    }
    if token_id in token_map and token_id:
        return token_map[token_id]
    if market_id == str(rel.get("market_id_a") or ""):
        return "a", f"A {side}"
    if market_id == str(rel.get("market_id_b") or ""):
        return "b", f"B {side}"
    return "", side


def _position_leg_mtm(
    *,
    side: Any,
    entry_price: Any,
    current_price: Any,
    size: Any,
) -> float | None:
    entry = _float_or_none(entry_price)
    current = _float_or_none(current_price)
    qty = _float_or_none(size)
    if entry is None or current is None or qty is None:
        return None
    if str(side or "").lower().startswith("sell"):
        return (entry - current) * qty
    return (current - entry) * qty


def _question_excerpt(value: Any, *, limit: int = 60) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


__all__ = ["DuckDBQueryService"]


# Re-exported for callers that want today's date in the dashboard's TZ.
def today_utc() -> date:
    return datetime.now(timezone.utc).date()
