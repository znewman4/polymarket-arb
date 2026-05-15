"""Sensitivity analysis: run a grid of backtest configurations."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from ..strategies.models import RelationshipBacktestConfig
from .relationship_replay import run_relationship_backtest

_DEFAULT_GRID: dict[str, list] = {
    "fee_bps": ["0", "25", "50", "100"],
    "slippage_bps": ["25", "50", "100", "200"],
    "min_net_edge": [0.01, 0.02, 0.05, 0.10],
    "min_relationship_confidence": [0.5, 0.7, 0.85],
}

_SLIM_GRID: dict[str, list] = {
    "fee_bps": ["0", "50"],
    "slippage_bps": ["25", "100"],
    "min_net_edge": [0.01, 0.05],
    "min_relationship_confidence": [0.5, 0.85],
}


def run_sensitivity_grid(
    data_root: Path,
    base_cfg: RelationshipBacktestConfig,
    *,
    grid: dict[str, list] | None = None,
    slim: bool = False,
) -> list[dict[str, Any]]:
    """Run the full 192-cell (or slim 16-cell) sensitivity grid.

    Returns a list of summary dicts, one per cell.
    Aligned price series and relationships are cached across runs by the
    price_alignment module (rows are loaded from parquet once each).
    """
    from decimal import Decimal

    if grid is None:
        grid = _SLIM_GRID if slim else _DEFAULT_GRID

    results: list[dict[str, Any]] = []

    fee_list = grid.get("fee_bps", ["0"])
    slip_list = grid.get("slippage_bps", ["50"])
    edge_list = grid.get("min_net_edge", [0.01])
    conf_list = grid.get("min_relationship_confidence", [0.65])

    cell = 0
    for fee in fee_list:
        for slip in slip_list:
            for edge in edge_list:
                for conf in conf_list:
                    cell += 1
                    run_id = uuid.uuid4().hex[:12]
                    cell_cfg = base_cfg.model_copy(update={
                        "run_id": run_id,
                        "fee_bps": Decimal(str(fee)),
                        "slippage_bps": Decimal(str(slip)),
                        "min_net_edge": float(edge),
                        "min_relationship_confidence": float(conf),
                    })
                    result = run_relationship_backtest(data_root, cell_cfg)
                    m = result.get("metrics")
                    if m:
                        summary = {
                            "run_id": run_id,
                            "fee_bps": str(fee),
                            "slippage_bps": str(slip),
                            "min_net_edge": edge,
                            "min_relationship_confidence": conf,
                            "net_pnl_usdc": float(m.net_pnl_usdc),
                            "total_return_pct": m.total_return_pct,
                            "trades_executed": m.trades_executed,
                            "max_drawdown_pct": m.max_drawdown_pct,
                            "credibility_label": m.credibility_label,
                        }
                    else:
                        summary = {
                            "run_id": run_id,
                            "fee_bps": str(fee),
                            "slippage_bps": str(slip),
                            "min_net_edge": edge,
                            "min_relationship_confidence": conf,
                            "error": "no_metrics",
                        }
                    results.append(summary)

    # Write summary parquet
    out_dir = data_root / "backtests" / base_cfg.run_id / "sensitivity"
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "grid_summary.csv", results)
    _write_parquet(out_dir / "grid_summary.parquet", results)

    return results


def _write_csv(path: Path, rows: list[dict]) -> None:
    import csv
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_parquet(path: Path, rows: list[dict]) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        if not rows:
            return
        table = pa.Table.from_pylist([{k: str(v) for k, v in r.items()} for r in rows])
        pq.write_table(table, path)
    except Exception:
        pass
