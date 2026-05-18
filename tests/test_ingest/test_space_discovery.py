"""Tests for JSONL-to-space discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from polymarket_arb.ingest.space_discovery import run_space_discovery


def _append_jsonl(path: Path, payloads: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for payload in payloads:
            fh.write(json.dumps({"payload": payload}) + "\n")


def test_space_discovery_groups_event_markets(tmp_data_root):
    run_dir = tmp_data_root / "raw" / "market_universe" / "disc"
    _append_jsonl(run_dir / "markets.jsonl", [
        {"id": "m1", "question": "Will A win the cup?", "eventId": "e1"},
        {"id": "m2", "question": "Will B win the cup?", "eventId": "e1"},
        {"id": "m3", "question": "Will C win the cup?", "eventId": "e1"},
    ])
    _append_jsonl(run_dir / "events.jsonl", [{"id": "e1", "title": "Cup"}])

    result = run_space_discovery("disc", tmp_data_root)
    df = pd.read_parquet(result.output_path)
    event_rows = df[df["source_type"] == "event"]
    assert len(event_rows) == 1
    assert event_rows.iloc[0]["space_id"] == "event:e1"
    assert event_rows.iloc[0]["market_count"] == 3
    assert event_rows.iloc[0]["confidence"] > 0


def test_space_discovery_groups_sport_across_events(tmp_data_root):
    run_dir = tmp_data_root / "raw" / "market_universe" / "disc_sport"
    _append_jsonl(run_dir / "markets.jsonl", [
        {"id": "m1", "question": "Will A win?", "eventId": "e1", "sportId": "soccer"},
        {"id": "m2", "question": "Will B win?", "eventId": "e2", "sportId": "soccer"},
    ])
    _append_jsonl(run_dir / "events.jsonl", [
        {"id": "e1", "title": "Game 1"},
        {"id": "e2", "title": "Game 2"},
    ])

    result = run_space_discovery("disc_sport", tmp_data_root)
    df = pd.read_parquet(result.output_path)
    sport_rows = df[df["source_type"] == "sport"]
    assert len(sport_rows) == 1
    assert sport_rows.iloc[0]["event_count"] == 2
    assert sport_rows.iloc[0]["market_count"] == 2
    assert {"space_id", "source_type", "market_count", "confidence"}.issubset(df.columns)
