"""The date-ambiguity validator is the load-bearing safety net for the
"model invented June 26, 2026 from 'before June 2026'" failure mode."""

from __future__ import annotations

import pytest

from polymarket_arb.nlp.validators import detect_temporal_phrase


@pytest.mark.parametrize("question,expected_resolution", [
    ("Will X happen before June 2026?",      "month"),
    ("Will X happen by June 2026?",          "month"),
    ("Will X happen in June 2026?",          "month"),
    ("Will X happen by Q3 2026?",            "quarter"),
    ("Will X happen this summer?",           "vague"),
    ("Will X happen by the end of 2026?",    "year"),
    ("Will X happen in 2026?",               "year"),
    ("Will X happen this year?",             "year"),
    ("Will X happen in the next 12 months?", "vague"),
    ("Will X happen by 2026-12-31?",         "exact_date"),
    ("Will X happen by 31 December 2026?",   "exact_date"),
    ("Will X happen at all?",                "open_ended"),
    ("",                                     "open_ended"),
])
def test_classification(question, expected_resolution):
    _, resolution = detect_temporal_phrase(question)
    assert resolution == expected_resolution
