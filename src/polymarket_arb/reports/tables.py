"""HTML/CSV table utilities for reports."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def truncate(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def format_number(value: float | None, decimals: int = 4) -> str:
    if value is None:
        return ""
    return f"{value:.{decimals}f}"


def format_pct(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return ""
    return f"{value * 100:.{decimals}f}%"


def format_ts_ms(ms: int | None) -> str:
    if ms is None:
        return ""
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, OverflowError, ValueError):
        return str(ms)


def df_to_html(
    df: pd.DataFrame,
    *,
    truncate_columns: dict[str, int] | None = None,
) -> str:
    display = df.copy()
    if truncate_columns:
        for col, max_len in truncate_columns.items():
            if col in display.columns:
                display[col] = display[col].astype(str).apply(
                    lambda t, ml=max_len: truncate(t, ml)
                )
    return display.to_html(index=False, classes=["report-table"], escape=True, border=0)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
