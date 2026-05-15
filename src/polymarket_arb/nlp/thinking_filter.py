"""Strip ``<think>...</think>`` chain-of-thought from raw model output.

Why this is its own module:

DeepSeek-R1 (and similar reasoning models) emit free-form reasoning inside
``<think>...</think>`` tags before producing the structured answer. **We
never persist this content in durable storage.** The thinking stream is
not a reliable audit artefact — it can contain false intermediate
reasoning, invented details, and source-text leakage; it makes mistakes
look more justified than they are.

This module is the single canonical place where pre-strip text is touched.
A test (``tests/test_nlp/test_no_thinking_persisted.py``) audits every
parquet file produced by the test suite to assert ``<think>`` substrings
never reach disk.
"""

from __future__ import annotations

import re

# Greedy by default would happily eat across multiple think blocks; we
# want non-greedy so multiple back-to-back blocks are each removed.
_THINK_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)
# Defence-in-depth: if a tag opens but never closes, drop everything from
# the stray opener to the next JSON-shape character (`{` or `[`).
_STRAY_OPEN_RE = re.compile(r"<think>.*?(?=[\[\{])", flags=re.DOTALL | re.IGNORECASE)
_STRAY_CLOSE_RE = re.compile(r"</think>", flags=re.IGNORECASE)


def strip_thinking(raw: str) -> str:
    """Remove every ``<think>...</think>`` block and any stray openers /
    closers. Idempotent. Safe on empty input."""

    if not raw:
        return ""
    cleaned = _THINK_RE.sub("", raw)
    cleaned = _STRAY_OPEN_RE.sub("", cleaned)
    cleaned = _STRAY_CLOSE_RE.sub("", cleaned)
    return cleaned.strip()
