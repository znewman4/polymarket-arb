from __future__ import annotations

import json
from datetime import datetime, timezone

from polymarket_arb.storage.parquet.raw_writer import RawWriter


def test_write_json_atomic(tmp_data_root):
    writer = RawWriter(tmp_data_root)
    ts = datetime(2026, 5, 8, 12, 34, 56, tzinfo=timezone.utc)
    path = writer.write_json("gamma/markets", {"hello": "world"}, ts=ts)
    assert path.exists()
    assert "2026-05-08" in str(path)
    assert json.loads(path.read_text()) == {"hello": "world"}


def test_append_jsonl(tmp_data_root):
    writer = RawWriter(tmp_data_root)
    ts = datetime(2026, 5, 8, 12, 34, 56, tzinfo=timezone.utc)
    p1 = writer.append_jsonl("clob/ws", {"a": 1}, ts=ts, stem="ticks")
    p2 = writer.append_jsonl("clob/ws", {"a": 2}, ts=ts, stem="ticks")
    assert p1 == p2
    lines = p1.read_text().splitlines()
    assert json.loads(lines[0]) == {"a": 1}
    assert json.loads(lines[1]) == {"a": 2}
