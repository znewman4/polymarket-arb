"""Flat-name aliases for the user's spec.

Each alias re-binds the same Click command callback at the top level so
``polymarket-arb fetch-markets`` and ``polymarket-arb gamma fetch-markets``
route to the identical implementation. Kept in one place so every alias is
auditable in a single grep.

A test (``tests/test_cli/test_aliases.py``) enumerates the table and asserts
each alias resolves to the same callback as its canonical form.
"""

from __future__ import annotations

import click

from . import clob, gamma, nlp, score

# (canonical_subgroup, canonical_command_name, alias_name)
ALIASES: list[tuple[click.Group, str, str]] = [
    # Phase 1 — Gamma
    (gamma.gamma, "fetch-markets", "fetch-markets"),
    (gamma.gamma, "list-markets", "list-markets"),
    (gamma.gamma, "search-markets", "search-markets"),
    (gamma.gamma, "show-market", "show-market"),
    # Phase 1.5 / 1.6 / 2 — NLP
    (nlp.nlp, "extract-market-semantics", "extract-market-semantics"),
    (nlp.nlp, "show-semantics", "show-semantics"),
    (nlp.nlp, "review-semantics", "review-semantics"),
    (nlp.nlp, "embed-markets", "embed-markets"),
    (nlp.nlp, "score-semantics", "score-semantics"),
    (nlp.nlp, "extract-implications", "extract-implications"),
    (nlp.nlp, "show-implications", "show-implications"),
    (nlp.nlp, "review-implications", "review-implications"),
    # Phase 3 — CLOB
    (clob.clob, "fetch-quotes", "fetch-quotes"),
    (clob.clob, "fetch-orderbook", "fetch-orderbook"),
    (clob.clob, "snapshot-active-markets", "snapshot-active-markets"),
    # Phase 4 — Score
    (score.score, "score-markets", "score-markets"),
    (score.score, "show-score", "show-score"),
]


def register(root: click.Group) -> None:
    """Attach every alias to ``root`` reusing the canonical command object."""

    for subgroup, canonical_name, alias_name in ALIASES:
        cmd = subgroup.commands.get(canonical_name)
        if cmd is None:
            raise RuntimeError(
                f"alias config refers to {subgroup.name}/{canonical_name}, "
                f"but that command is not registered"
            )
        # Hide aliases from the top-level --help to keep it readable —
        # subgroup form is canonical. They remain invokable.
        root.add_command(cmd, name=alias_name)


def alias_pairs(root: click.Group) -> list[tuple[str, click.Command, click.Command]]:
    """Returns ``(alias_name, canonical_command, alias_command)`` triples
    used by ``tests/test_cli/test_aliases.py`` to assert each alias resolves
    to the same callback as its canonical form.
    """

    triples: list[tuple[str, click.Command, click.Command]] = []
    for subgroup, canonical_name, alias_name in ALIASES:
        canonical = subgroup.commands[canonical_name]
        alias = root.commands.get(alias_name)
        if alias is None:
            raise RuntimeError(f"alias {alias_name!r} not registered on root")
        triples.append((alias_name, canonical, alias))
    return triples
