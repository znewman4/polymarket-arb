"""``<think>`` strip discipline. The strip must:
- handle a single block, multiple blocks, nested-looking content
- be idempotent
- discard stray openers/closers
- never raise on garbled input
"""

from __future__ import annotations

import pytest

from polymarket_arb.nlp.thinking_filter import strip_thinking


@pytest.mark.parametrize("raw,expected_substr_absent,expected_substr_present", [
    ("<think>secret reasoning</think>{\"x\": 1}", "<think>", '{"x"'),
    ("Plain text only", "<think>", "Plain text"),
    ("", "", ""),
    # multiple blocks
    ("<think>a</think>{\"x\":1}<think>b</think>", "<think>", "{"),
    # case-insensitive
    ("<THINK>UPPER</THINK>{\"x\":1}", "<think>", "{"),
    # stray opener with no closer — drop everything until JSON character
    ("<think>incomplete reasoning that runs into {\"x\":1}", "<think>", "{"),
    # stray closer alone — drop the closer, keep the rest
    ("</think>{\"x\":1}", "</think>", "{"),
])
def test_strip(raw, expected_substr_absent, expected_substr_present):
    out = strip_thinking(raw)
    if expected_substr_absent:
        assert expected_substr_absent.lower() not in out.lower()
    if expected_substr_present:
        assert expected_substr_present in out


def test_strip_is_idempotent():
    a = strip_thinking("<think>r</think>x")
    b = strip_thinking(a)
    assert a == b


def test_thinking_never_returned():
    raw = "<think>This is the thinking — never store this</think>{\"answer\": 1}"
    out = strip_thinking(raw)
    assert "thinking" not in out.lower() or "answer" in out.lower()
    assert "<think>" not in out
    assert "</think>" not in out
