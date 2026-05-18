"""Deterministic rule-based semantic extractor.

When the LLM extractor cannot run (e.g. Ollama is unavailable) we still want to
push a large number of markets through the relationship pipeline without
mis-classifying them as `same_topic_no_trade`.  This module recognises a fixed
set of high-signal question shapes that Polymarket reuses heavily and emits a
``MarketSemanticsRow`` with the bare minimum fields populated correctly:

    * canonical_question / subject_entities / event_entities
    * outcome_space_json (single_winner_competition or threshold)
    * positive/negative resolution_condition (stub but valid)
    * temporal_resolution / exact_deadline_ms (from market.end_date_ms)

Rows produced here are tagged ``model_name="deterministic_rules"`` and
``prompt_version="rules-v1"`` so they are easy to audit and re-process later if
a real LLM run is performed.

RESEARCH-ONLY.  No network calls, no LLM, no trading.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass

from ..storage.base import MarketRow, MarketSemanticsRow

_MODEL_NAME = "deterministic_rules"
_PROMPT_VERSION = "rules-v1"
_SCHEMA_VERSION = 2

# ── patterns ─────────────────────────────────────────────────────────────────

_PARTY = {
    "democratic": "Democrats",
    "democrat": "Democrats",
    "republican": "Republicans",
    "gop": "Republicans",
    "libertarian": "Libertarians",
    "green": "Greens",
}

_LEAGUE_PREFIXES = ("NBA", "NFL", "MLB", "NHL", "EPL", "MLS", "WNBA")

_STATE_NAMES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
)
_STATE_RX = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in _STATE_NAMES) + r")\b", re.IGNORECASE
)
_DISTRICT_RX = re.compile(r"\b([A-Z]{2}-(?:AL|\d{1,2}))\b")  # CA-12, ND-AL
_SEAT_RX = re.compile(r"\b([A-Z]{2}-Sen)\b", re.IGNORECASE)
_YEAR_RX = re.compile(r"\b(20\d{2})\b")
_CRYPTO_RX = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol|bnb|xrp|cardano|ada|"
    r"dogecoin|doge|polkadot|dot|avalanche|avax|chainlink|link|"
    r"polygon|matic|litecoin|ltc|tron|trx)\b",
    re.IGNORECASE,
)
_PRICE_RX = re.compile(r"\$([\d,]+(?:\.\d+)?)")
_LEAGUE_EVENT_RX = re.compile(
    r"\b(stanley cup|nba finals|super bowl|world series|world cup|"
    r"champions league|premier league|nba championship|nhl championship|"
    r"world chess championship)\b",
    re.IGNORECASE,
)


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "unknown"


# ── pattern handlers ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Inferred:
    canonical: str
    subject_entities: list[str]
    event_entities: list[str]
    outcome_space: dict
    market_type: str
    pos_cond: str
    neg_cond: str
    pattern_id: str
    confidence: float = 0.85


def _normalise(question: str) -> str:
    q = re.sub(r"\s+", " ", question.strip().rstrip("?")).strip()
    # Drop trailing parenthesised time/date notes like "(8 PM ET)" so they
    # don't break tail-anchored regexes.
    q = re.sub(r"\s+\([^)]{1,40}\)\s*$", "", q)
    return q.strip()


def _primary_nominee(q: str) -> _Inferred | None:
    # "Will X be the (Democratic|Republican|GOP) nominee for (district/seat/race)?"
    m = re.match(
        r"^will\s+(?P<cand>.+?)\s+be\s+the\s+(?P<party>democratic|republican|gop)\s+"
        r"nominee\s+for\s+(?P<race>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    candidate = m.group("cand").strip()
    party = _PARTY[m.group("party").lower()]
    race = m.group("race").strip()
    year_m = _YEAR_RX.search(q) or _YEAR_RX.search(race)
    year = year_m.group(1) if year_m else "unknown_year"
    # Comp id includes party + race
    comp_id = f"{year}_{_slug(race)}_{_slug(party)}_primary"
    return _Inferred(
        canonical=f"{candidate} wins the {year} {race} {party} primary",
        subject_entities=[candidate, party, race],
        event_entities=[f"{year} {race} {party} primary"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"{year} {race} {party} primary",
            "candidate": candidate,
            "winner_predicate": "win",
        },
        market_type="multi_outcome",
        pos_cond=(
            f"{candidate} is the official {party} nominee for {race} (resolved "
            f"on or before market end-date)."
        ),
        neg_cond=f"{candidate} is not the {party} nominee for {race}.",
        pattern_id="primary_nominee",
    )


def _primary_winner(q: str) -> _Inferred | None:
    # "Will X win the 2026 Democratic Primary in/for Y?"
    m = re.match(
        r"^will\s+(?P<cand>.+?)\s+win\s+(?:the\s+)?(?P<year>20\d{2})\s+"
        r"(?P<party>democratic|republican|gop)\s+primary(?:\s+(?:in|for)\s+(?P<race>.+))?$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        # Variation: "Will X win the 2026 <state> Democratic Primary?"
        m = re.match(
            r"^will\s+(?P<cand>.+?)\s+win\s+(?:the\s+)?(?P<year>20\d{2})\s+"
            r"(?P<race>.+?)\s+(?P<party>democratic|republican|gop)\s+primary$",
            q,
            flags=re.IGNORECASE,
        )
        if not m:
            return None
    candidate = m.group("cand").strip()
    party = _PARTY[m.group("party").lower()]
    year = m.group("year")
    race = (m.groupdict().get("race") or "").strip() or "primary"
    comp_id = f"{year}_{_slug(race)}_{_slug(party)}_primary"
    return _Inferred(
        canonical=f"{candidate} wins the {year} {race} {party} primary",
        subject_entities=[candidate, party, race],
        event_entities=[f"{year} {race} {party} primary"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"{year} {race} {party} primary",
            "candidate": candidate,
            "winner_predicate": "win",
        },
        market_type="multi_outcome",
        pos_cond=f"{candidate} wins the {year} {race} {party} primary.",
        neg_cond=f"{candidate} does not win the {year} {race} {party} primary.",
        pattern_id="primary_winner",
    )


def _state_election(q: str) -> _Inferred | None:
    # "Will X win the 2026 (state) (governor|senate|mayor) election?"
    m = re.match(
        r"^will\s+(?P<cand>.+?)\s+win\s+(?:the\s+)?(?P<year>20\d{2})\s+"
        r"(?P<state>.+?)\s+(?P<office>governor|gubernatorial|senate|senator|"
        r"mayor|mayoral|attorney\s+general|secretary\s+of\s+state)\s+election$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    candidate = m.group("cand").strip()
    year = m.group("year")
    state = m.group("state").strip()
    office_raw = m.group("office").lower()
    office = (
        "governor"
        if "governor" in office_raw or "gubernatorial" in office_raw
        else "senate"
        if "senat" in office_raw
        else "mayor"
        if "mayor" in office_raw
        else _slug(office_raw)
    )
    comp_id = f"{year}_{_slug(state)}_{office}_election"
    return _Inferred(
        canonical=f"{candidate} wins the {year} {state} {office} election",
        subject_entities=[candidate, state, office],
        event_entities=[f"{year} {state} {office} election"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"{year} {state} {office} election",
            "candidate": candidate,
            "winner_predicate": "win",
        },
        market_type="multi_outcome",
        pos_cond=f"{candidate} is certified winner of the {year} {state} {office} election.",
        neg_cond=f"{candidate} is not the winner of the {year} {state} {office} election.",
        pattern_id="state_election",
    )


def _h2h_spread(q: str) -> _Inferred | None:
    # NBA/NFL/MLB/NHL: Will the X beat the Y by more than N.M points/goals/runs
    # in their <date> matchup?
    m = re.match(
        r"^(?P<league>NBA|NFL|MLB|NHL):\s+will\s+the\s+(?P<team_a>.+?)\s+"
        r"beat\s+the\s+(?P<team_b>.+?)\s+by\s+more\s+than\s+(?P<spread>\d+(?:\.\d+)?)\s+"
        r"(?P<unit>points?|goals?|runs?)\s+in\s+their\s+(?P<date>.+?)\s+matchup$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    league = m.group("league").upper()
    team_a = m.group("team_a").strip()
    team_b = m.group("team_b").strip()
    spread = m.group("spread")
    unit = m.group("unit").lower().rstrip("s")
    date = m.group("date").strip()
    # Comp id has both teams sorted so A-vs-B and B-vs-A collide.
    teams = "_vs_".join(sorted([_slug(team_a), _slug(team_b)]))
    comp_id = f"{league.lower()}_{teams}_{_slug(date)}_spread"
    return _Inferred(
        canonical=(
            f"{team_a} beats {team_b} by more than {spread} {unit}s in their "
            f"{date} {league} matchup"
        ),
        subject_entities=[team_a, team_b, league],
        event_entities=[f"{date} {league} matchup: {team_a} vs {team_b}"],
        outcome_space={
            "kind": "threshold",
            "competition_id": comp_id,
            "competition_name": (
                f"{league} {team_a} vs {team_b} on {date} (spread)"
            ),
            "candidate": f"{team_a}_minus_{team_b}",
            "winner_predicate": f"beats_by_more_than_{spread}_{unit}",
        },
        market_type="binary",
        pos_cond=(
            f"{team_a}'s final score minus {team_b}'s final score is greater "
            f"than {spread} {unit}s."
        ),
        neg_cond=f"{team_a}'s margin of victory over {team_b} is <= {spread} {unit}s.",
        pattern_id="h2h_spread",
        confidence=0.7,
    )


def _h2h_winner(q: str) -> _Inferred | None:
    # NBA/NFL/MLB/NHL: Who will win X vs. Y, scheduled for <date>?
    m = re.match(
        r"^(?P<league>NBA|NFL|MLB|NHL):\s+who\s+will\s+win\s+"
        r"(?P<team_a>.+?)\s+(?:v|vs|v\.|vs\.)\s+(?P<team_b>.+?)"
        r"(?:,?\s+scheduled\s+for\s+(?P<date>.+))?$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    league = m.group("league").upper()
    team_a = m.group("team_a").strip().rstrip(".")
    team_b = m.group("team_b").strip().rstrip(".")
    date = (m.groupdict().get("date") or "").strip() or "unknown_date"
    teams = "_vs_".join(sorted([_slug(team_a), _slug(team_b)]))
    comp_id = f"{league.lower()}_{teams}_{_slug(date)}_winner"
    return _Inferred(
        canonical=f"{team_a} beats {team_b} in their {date} {league} matchup",
        subject_entities=[team_a, team_b, league],
        event_entities=[f"{date} {league} matchup: {team_a} vs {team_b}"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": (
                f"{league} {team_a} vs {team_b} on {date} (winner)"
            ),
            "candidate": team_a,
            "winner_predicate": "win",
        },
        market_type="binary",
        pos_cond=f"{team_a} is recorded as the winner of the {date} {league} matchup.",
        neg_cond=f"{team_a} does not win the {date} {league} matchup against {team_b}.",
        pattern_id="h2h_winner",
    )


def _matchup_pair_winner(q: str) -> _Inferred | None:
    # "Will the Celtics or the Wizards win their February 14th matchup?"
    m = re.match(
        r"^will\s+the\s+(?P<team_a>.+?)\s+or\s+the\s+(?P<team_b>.+?)\s+"
        r"win\s+their\s+(?P<date>.+?)\s+matchup$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    team_a = m.group("team_a").strip()
    team_b = m.group("team_b").strip()
    date = m.group("date").strip()
    teams = "_vs_".join(sorted([_slug(team_a), _slug(team_b)]))
    comp_id = f"{teams}_{_slug(date)}_winner"
    return _Inferred(
        canonical=f"{team_a} or {team_b} wins their {date} matchup",
        subject_entities=[team_a, team_b],
        event_entities=[f"{date} matchup: {team_a} vs {team_b}"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"{team_a} vs {team_b} on {date} (winner)",
            "candidate": f"{team_a}_or_{team_b}",
            "winner_predicate": "win",
        },
        market_type="binary",
        pos_cond=f"Either {team_a} or {team_b} wins their {date} matchup.",
        neg_cond=f"Neither {team_a} nor {team_b} wins their {date} matchup.",
        pattern_id="matchup_pair_winner",
        confidence=0.7,
    )


def _crypto_threshold(q: str) -> _Inferred | None:
    # Will (Token) (hit|reach|dip to|trade above|fall below) $X (by|before|in) <date>?
    m = re.match(
        r"^will\s+(?P<tok>\w+)\s+(?P<verb>hit|reach|dip\s+to|trade\s+above|"
        r"fall\s+below|cross|exceed)\s+\$(?P<price>[\d,]+(?:\.\d+)?)\s+"
        r"(?:by|before|in)\s+(?P<date>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    if not _CRYPTO_RX.search(m.group("tok")):
        return None
    token = m.group("tok").strip().title()
    price = m.group("price").replace(",", "")
    verb = m.group("verb").lower().strip()
    date = m.group("date").strip()
    direction = (
        "above"
        if verb in {"hit", "reach", "trade above", "cross", "exceed"}
        else "below"
    )
    comp_id = f"{_slug(token)}_{direction}_{price}_by_{_slug(date)}"
    return _Inferred(
        canonical=f"{token} trades {direction} ${price} on or before {date}",
        subject_entities=[token],
        event_entities=[f"{token} price observation on or before {date}"],
        outcome_space={
            "kind": "threshold",
            "competition_id": comp_id,
            "competition_name": f"{token} {direction} ${price} by {date}",
            "candidate": token,
            "winner_predicate": f"{direction}_{price}",
        },
        market_type="binary",
        pos_cond=(
            f"{token}'s last recorded price is {direction} ${price} at any time "
            f"on or before {date}."
        ),
        neg_cond=f"{token} never trades {direction} ${price} on or before {date}.",
        pattern_id="crypto_threshold",
    )


def _generic_winner(q: str) -> _Inferred | None:
    # Fallback: "Will X win the (year) (event)?"
    m = re.match(
        r"^will\s+(?P<cand>.+?)\s+win\s+(?:the\s+)?"
        r"(?P<year>20\d{2})?(?:\s+)?(?P<event>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    candidate = m.group("cand").strip()
    event = m.group("event").strip(" ?.")
    if len(event) < 3:
        return None
    year = m.group("year") or ""
    comp_id = _slug(f"{year} {event}" if year else event)
    return _Inferred(
        canonical=f"{candidate} wins the {year} {event}".strip(),
        subject_entities=[candidate],
        event_entities=[f"{year} {event}".strip()],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"{year} {event}".strip() or event,
            "candidate": candidate,
            "winner_predicate": "win",
        },
        market_type="multi_outcome",
        pos_cond=f"{candidate} wins the {year} {event}.".replace("  ", " "),
        neg_cond=f"{candidate} does not win the {year} {event}.".replace("  ", " "),
        pattern_id="generic_winner",
        confidence=0.55,
    )


# ── extended patterns (v2) ──────────────────────────────────────────────────


def _general_price_threshold(q: str) -> _Inferred | None:
    """'Will the price of <commodity> be above $X on/by <date>?' / 'Will $TOKEN
    be above $X on <date>?' — generic commodity & crypto price threshold."""
    m = re.match(
        r"^will\s+(?:the\s+price\s+of\s+)?\$?(?P<asset>[^?]+?)\s+"
        r"be\s+(?P<dir>above|below|at\s+least|over|under)\s+\$?"
        r"(?P<price>[\d,]+(?:\.\d+)?)\s+(?:on|by|as\s+of)\s+(?P<date>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    asset = m.group("asset").strip().strip("?")
    if len(asset) > 40 or len(asset) < 2:
        return None
    direction = (
        "above"
        if m.group("dir").lower() in {"above", "at least", "over"}
        else "below"
    )
    price = m.group("price").replace(",", "")
    date = m.group("date").strip()
    comp_id = f"{_slug(asset)}_price_{direction}_{price}_by_{_slug(date)}"
    return _Inferred(
        canonical=f"{asset} trades {direction} ${price} on or before {date}",
        subject_entities=[asset],
        event_entities=[f"{asset} price observation on or before {date}"],
        outcome_space={
            "kind": "threshold",
            "competition_id": comp_id,
            "competition_name": f"{asset} {direction} ${price} by {date}",
            "candidate": asset,
            "winner_predicate": f"{direction}_{price}",
        },
        market_type="binary",
        pos_cond=f"{asset}'s last recorded price is {direction} ${price} on or before {date}.",
        neg_cond=f"{asset} never trades {direction} ${price} on or before {date}.",
        pattern_id="general_price_threshold",
        confidence=0.75,
    )


def _floor_price_threshold(q: str) -> _Inferred | None:
    """'Will the floor price of <NFT> be X ETH or more on <date>?'"""
    m = re.match(
        r"^will\s+the\s+floor\s+price\s+of\s+(?P<nft>.+?)\s+be\s+"
        r"(?P<dir>below|above|at\s+least|over|under|or\s+more|or\s+less)?\s*"
        r"(?P<price>[\d.,]+)\s+(?P<unit>eth|usd|sol)\s+"
        r"(?:or\s+(?P<modifier>more|less)\s+)?(?:on|by)\s+(?P<date>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    nft = m.group("nft").strip()
    price = m.group("price")
    unit = m.group("unit").upper()
    mod = (m.group("modifier") or m.group("dir") or "").lower()
    direction = "above" if "more" in mod or "above" in mod or "at" in mod or "over" in mod else (
        "below" if "less" in mod or "below" in mod or "under" in mod else "above"
    )
    date = m.group("date").strip()
    comp_id = f"{_slug(nft)}_floor_{direction}_{price}_{unit.lower()}_by_{_slug(date)}"
    return _Inferred(
        canonical=f"{nft} floor price {direction} {price} {unit} by {date}",
        subject_entities=[nft],
        event_entities=[f"{nft} floor-price observation by {date}"],
        outcome_space={
            "kind": "threshold",
            "competition_id": comp_id,
            "competition_name": f"{nft} floor {direction} {price} {unit} by {date}",
            "candidate": nft,
            "winner_predicate": f"floor_{direction}_{price}_{unit.lower()}",
        },
        market_type="binary",
        pos_cond=f"{nft}'s floor price is {direction} {price} {unit} on or before {date}.",
        neg_cond=f"{nft}'s floor price never reaches {direction} {price} {unit} on or before {date}.",
        pattern_id="floor_price_threshold",
        confidence=0.7,
    )


def _fdv_threshold(q: str) -> _Inferred | None:
    """'<Project> FDV above $XB one day after launch?'"""
    m = re.match(
        r"^(?P<project>.+?)\s+fdv\s+(?P<dir>above|below|over|under)\s+"
        r"\$(?P<price>[\d.]+)(?P<unit>[MBb])\s+(?:one\s+day\s+after\s+launch|on\s+launch|by\s+launch)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    project = m.group("project").strip()
    price = m.group("price")
    unit = m.group("unit").upper()
    direction = "above" if m.group("dir").lower() in {"above", "over"} else "below"
    comp_id = f"{_slug(project)}_fdv_{direction}_{price}{unit}_launch"
    return _Inferred(
        canonical=f"{project} FDV {direction} ${price}{unit} one day after launch",
        subject_entities=[project],
        event_entities=[f"{project} launch"],
        outcome_space={
            "kind": "threshold",
            "competition_id": comp_id,
            "competition_name": f"{project} FDV {direction} ${price}{unit}",
            "candidate": project,
            "winner_predicate": f"fdv_{direction}_{price}{unit}",
        },
        market_type="binary",
        pos_cond=f"{project} FDV is {direction} ${price}{unit} one day after launch.",
        neg_cond=f"{project} FDV is not {direction} ${price}{unit} one day after launch.",
        pattern_id="fdv_threshold",
        confidence=0.7,
    )


def _will_x_by_date(q: str) -> _Inferred | None:
    """'Will <subject> <verb> by/before <date>?' — generic event."""
    m = re.match(
        r"^will\s+(?P<subject>.+?)\s+(?P<verb>(?:tweet|visit|announce|sign|release|launch|"
        r"resign|endorse|meet|file|appoint|nominate|publish|debate|run|"
        r"become|reach|hit|cross|drop|surge|spike|crash|exceed|fall|"
        r"declare|withdraw|join|leave|defeat|win|lose|finish|complete|"
        r"appear|attend|deliver|propose|veto|sue|indict|"
        r"strike|invade|annex|attack|withdraw))\s+"
        r"(?P<obj>.+?)\s+(?:by|before|in)\s+(?P<date>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    subject = m.group("subject").strip()
    verb = m.group("verb").lower()
    obj = m.group("obj").strip()
    if len(subject) > 60 or len(obj) > 80:
        return None
    date = m.group("date").strip()
    comp_id = f"{_slug(subject)}_{verb}_{_slug(obj)[:40]}_by_{_slug(date)}"
    return _Inferred(
        canonical=f"{subject} {verb}s {obj} on or before {date}",
        subject_entities=[subject],
        event_entities=[f"{subject} {verb}s {obj} (by {date})"],
        outcome_space={
            "kind": "binary_event",
            "competition_id": comp_id,
            "competition_name": f"{subject} {verb} {obj} by {date}",
            "candidate": subject,
            "winner_predicate": f"{verb}_by_{_slug(date)}",
        },
        market_type="binary",
        pos_cond=f"{subject} {verb}s {obj} on or before {date}.",
        neg_cond=f"{subject} does not {verb} {obj} on or before {date}.",
        pattern_id="will_x_by_date",
        confidence=0.55,
    )


def _f1_h2h(q: str) -> _Inferred | None:
    """'Formula 1: Will <driver_a> finish ahead of <driver_b> in the <gp>?'"""
    m = re.match(
        r"^formula\s*1:\s+will\s+(?P<a>.+?)\s+finish\s+ahead\s+of\s+(?P<b>.+?)\s+"
        r"in\s+the\s+(?P<gp>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    a = m.group("a").strip()
    b = m.group("b").strip()
    gp = m.group("gp").strip(" ?.")
    drivers = "_vs_".join(sorted([_slug(a), _slug(b)]))
    comp_id = f"f1_{_slug(gp)}_{drivers}_h2h"
    return _Inferred(
        canonical=f"{a} finishes ahead of {b} at the {gp}",
        subject_entities=[a, b, "Formula 1"],
        event_entities=[gp],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"F1 {gp} {a} vs {b}",
            "candidate": a,
            "winner_predicate": "finish_ahead_of_opponent",
        },
        market_type="binary",
        pos_cond=f"{a} finishes ahead of {b} in the {gp}.",
        neg_cond=f"{b} finishes ahead of {a} (or DNF/DSQ) in the {gp}.",
        pattern_id="f1_h2h",
        confidence=0.8,
    )


def _generic_h2h_question(q: str) -> _Inferred | None:
    """'Who will win <A> v(s.) <B>?' — generic combat/match without league prefix."""
    m = re.match(
        r"^(?:who\s+will\s+win\s+)(?P<a>.+?)\s+(?:v|vs|v\.|vs\.)\s+(?P<b>.+?)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    a = m.group("a").strip(" ?.")
    b = m.group("b").strip(" ?.")
    # remove trailing context like "Semifinals Game 5" or ", scheduled for..."
    b_main = re.split(r":|\s+scheduled", b, maxsplit=1)[0].strip()
    if not a or not b_main:
        return None
    pair = "_vs_".join(sorted([_slug(a), _slug(b_main)]))
    comp_id = f"{pair}_winner"
    return _Inferred(
        canonical=f"{a} beats {b_main}",
        subject_entities=[a, b_main],
        event_entities=[f"{a} vs {b_main}"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"{a} vs {b_main}",
            "candidate": a,
            "winner_predicate": "win",
        },
        market_type="binary",
        pos_cond=f"{a} beats {b_main}.",
        neg_cond=f"{a} does not beat {b_main}.",
        pattern_id="generic_h2h",
        confidence=0.65,
    )


def _runner_up(q: str) -> _Inferred | None:
    """'Who will come in second place in the <year> <party> Primary for <office>?'"""
    m = re.match(
        r"^who\s+will\s+come\s+in\s+second\s+place\s+in\s+the\s+"
        r"(?P<year>20\d{2})\s+(?P<rest>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    year = m.group("year")
    rest = m.group("rest").strip(" ?.")
    comp_id = f"{year}_{_slug(rest)}_runner_up"
    return _Inferred(
        canonical=f"runner-up of the {year} {rest}",
        subject_entities=[rest],
        event_entities=[f"{year} {rest}"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"{year} {rest} runner-up",
            "candidate": "open",
            "winner_predicate": "second_place",
        },
        market_type="multi_outcome",
        pos_cond=f"Question's named candidate is the runner-up of the {year} {rest}.",
        neg_cond=f"Named candidate is not the runner-up of the {year} {rest}.",
        pattern_id="runner_up",
        confidence=0.6,
    )


def _vaccine_total(q: str) -> _Inferred | None:
    """'Will X million Americans have received at least one dose ... by <date>?'"""
    m = re.match(
        r"^will\s+(?P<n>\d{1,4})\s+million\s+americans\s+have\s+received\s+"
        r"at\s+least\s+one\s+dose\s+(?:of\s+an?\s+approved\s+covid-19\s+vaccination\s+)?"
        r"by\s+(?P<date>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    n = m.group("n")
    date = m.group("date").strip()
    comp_id = f"us_vaccinations_above_{n}m_by_{_slug(date)}"
    return _Inferred(
        canonical=f"{n}M Americans vaccinated (1st dose) by {date}",
        subject_entities=["United States"],
        event_entities=[f"US vaccination total by {date}"],
        outcome_space={
            "kind": "threshold",
            "competition_id": comp_id,
            "competition_name": f"US vaccinations >= {n}M by {date}",
            "candidate": "United States",
            "winner_predicate": f"vaccinations_above_{n}m",
        },
        market_type="binary",
        pos_cond=f"At least {n}M Americans received 1st dose by {date}.",
        neg_cond=f"Fewer than {n}M Americans received 1st dose by {date}.",
        pattern_id="vaccine_total",
        confidence=0.8,
    )


def _chart_rank(q: str) -> _Inferred | None:
    """'Will <show/song> be the #1 <chart> ... on the week ending <date>?'"""
    m = re.match(
        r"^will\s+(?P<title>['\"].+?['\"]|.+?)\s+be\s+the\s+#1\s+(?P<chart>.+?)\s+"
        r"(?:show|song|album|item)?\s*(?:worldwide\s+)?on\s+the\s+week\s+ending\s+(?P<date>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    title = m.group("title").strip("'\"")
    chart = m.group("chart").strip()
    date = m.group("date").strip()
    comp_id = f"{_slug(chart)}_top1_week_ending_{_slug(date)}"
    return _Inferred(
        canonical=f"{title} is #1 on {chart} for week ending {date}",
        subject_entities=[title, chart],
        event_entities=[f"{chart} chart, week ending {date}"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"#1 {chart} week ending {date}",
            "candidate": title,
            "winner_predicate": "top1",
        },
        market_type="multi_outcome",
        pos_cond=f"{title} is recorded as #1 on {chart} for the week ending {date}.",
        neg_cond=f"{title} is not #1 on {chart} for the week ending {date}.",
        pattern_id="chart_rank",
        confidence=0.75,
    )


def _rotten_tomatoes(q: str) -> _Inferred | None:
    """'Will <title> get NN% or higher (Audience|Critic) Score on Rotten Tomatoes?'"""
    m = re.match(
        r"^will\s+(?P<title>['\"].+?['\"]|.+?)\s+get\s+(?P<pct>\d{1,3})%\s+"
        r"(?:or\s+higher\s+)?(?P<kind>audience|critic|critics|tomatometer)\s+score\s+"
        r"on\s+rotten\s+tomatoes",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    title = m.group("title").strip("'\"")
    pct = m.group("pct")
    kind = "audience" if "aud" in m.group("kind").lower() else "critics"
    comp_id = f"{_slug(title)}_rt_{kind}_above_{pct}"
    return _Inferred(
        canonical=f"{title} achieves {pct}% on Rotten Tomatoes {kind}",
        subject_entities=[title],
        event_entities=[f"{title} Rotten Tomatoes {kind} score"],
        outcome_space={
            "kind": "threshold",
            "competition_id": comp_id,
            "competition_name": f"{title} RT {kind} >= {pct}%",
            "candidate": title,
            "winner_predicate": f"rt_{kind}_above_{pct}",
        },
        market_type="binary",
        pos_cond=f"{title}'s {kind} score on Rotten Tomatoes is >= {pct}%.",
        neg_cond=f"{title}'s {kind} score on Rotten Tomatoes is < {pct}%.",
        pattern_id="rotten_tomatoes",
        confidence=0.75,
    )


def _largest_company(q: str) -> _Inferred | None:
    """'Will <X> be the largest company in the world by market cap on/by <date>?'"""
    m = re.match(
        r"^will\s+(?P<co>.+?)\s+be\s+the\s+largest\s+company\s+in\s+the\s+world\s+"
        r"by\s+market\s+cap\s+(?:on|by)\s+(?P<date>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    co = m.group("co").strip()
    date = m.group("date").strip()
    comp_id = f"largest_market_cap_by_{_slug(date)}"
    return _Inferred(
        canonical=f"{co} is the largest company by market cap by {date}",
        subject_entities=[co],
        event_entities=[f"global market cap leader by {date}"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"#1 market cap by {date}",
            "candidate": co,
            "winner_predicate": "top1_market_cap",
        },
        market_type="multi_outcome",
        pos_cond=f"{co} has the largest market cap globally on or before {date}.",
        neg_cond=f"{co} is not the largest by market cap on or before {date}.",
        pattern_id="largest_company",
        confidence=0.7,
    )


def _will_country_action(q: str) -> _Inferred | None:
    """'Will <country> (strike|invade|annex|attack) <obj> (in|by) <date>?'"""
    m = re.match(
        r"^will\s+(?P<actor>.+?)\s+(?P<verb>strike|invade|annex|attack|withdraw\s+from)\s+"
        r"(?P<obj>.+?)\s+(?:in|by|before|during)\s+(?P<date>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    actor = m.group("actor").strip()
    verb = m.group("verb").lower().replace(" ", "_")
    obj = m.group("obj").strip()
    date = m.group("date").strip()
    comp_id = f"{_slug(actor)}_{verb}_{_slug(obj)[:40]}_by_{_slug(date)}"
    return _Inferred(
        canonical=f"{actor} {verb}s {obj} by {date}",
        subject_entities=[actor, obj],
        event_entities=[f"{actor} {verb} {obj}"],
        outcome_space={
            "kind": "binary_event",
            "competition_id": comp_id,
            "competition_name": f"{actor} {verb} {obj} by {date}",
            "candidate": actor,
            "winner_predicate": verb,
        },
        market_type="binary",
        pos_cond=f"{actor} {verb}s {obj} on or before {date}.",
        neg_cond=f"{actor} does not {verb} {obj} on or before {date}.",
        pattern_id="will_country_action",
        confidence=0.65,
    )


def _ipo_before(q: str) -> _Inferred | None:
    """'<Company> IPO before <year>?'"""
    m = re.match(
        r"^(?P<co>.+?)\s+ipo\s+before\s+(?P<year>20\d{2})$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    co = m.group("co").strip()
    year = m.group("year")
    comp_id = f"{_slug(co)}_ipo_before_{year}"
    return _Inferred(
        canonical=f"{co} IPOs before {year}",
        subject_entities=[co],
        event_entities=[f"{co} IPO"],
        outcome_space={
            "kind": "binary_event",
            "competition_id": comp_id,
            "competition_name": f"{co} IPO by {year}",
            "candidate": co,
            "winner_predicate": f"ipo_before_{year}",
        },
        market_type="binary",
        pos_cond=f"{co} completes IPO before {year}.",
        neg_cond=f"{co} does not IPO before {year}.",
        pattern_id="ipo_before",
        confidence=0.75,
    )


def _governor_on_date(q: str) -> _Inferred | None:
    """'Will <X> be Governor of <state> on <date>?' / 'Mayor of', 'President of'."""
    m = re.match(
        r"^will\s+(?P<cand>.+?)\s+be\s+(?P<office>governor|mayor|president|senator)\s+of\s+"
        r"(?P<region>.+?)\s+on\s+(?P<date>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    cand = m.group("cand").strip()
    office = m.group("office").lower()
    region = m.group("region").strip()
    date = m.group("date").strip()
    year_m = _YEAR_RX.search(date)
    year = year_m.group(1) if year_m else "unknown_year"
    comp_id = f"{year}_{_slug(region)}_{office}_incumbent_on_{_slug(date)}"
    return _Inferred(
        canonical=f"{cand} is {office} of {region} on {date}",
        subject_entities=[cand, region, office],
        event_entities=[f"{office} of {region} as-of {date}"],
        outcome_space={
            "kind": "binary_event",
            "competition_id": comp_id,
            "competition_name": f"{office} of {region} on {date}",
            "candidate": cand,
            "winner_predicate": "incumbent",
        },
        market_type="binary",
        pos_cond=f"{cand} holds the office of {office} of {region} on {date}.",
        neg_cond=f"{cand} does not hold the office of {office} of {region} on {date}.",
        pattern_id="governor_on_date",
        confidence=0.78,
    )


def _advance_primary(q: str) -> _Inferred | None:
    """'Will <X> advance from the <year> <state> <office> primary election?'"""
    m = re.match(
        r"^will\s+(?P<cand>.+?)\s+advance\s+from\s+the\s+(?P<year>20\d{2})\s+"
        r"(?P<state>.+?)\s+(?P<office>governor|senate|senator|mayor|attorney\s+general|"
        r"secretary\s+of\s+state|presidential|president)\s+primary\s+election$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    cand = m.group("cand").strip()
    year = m.group("year")
    state = m.group("state").strip()
    office = m.group("office").lower().replace(" ", "_")
    comp_id = f"{year}_{_slug(state)}_{office}_primary_advancers"
    return _Inferred(
        canonical=f"{cand} advances from the {year} {state} {office} primary",
        subject_entities=[cand, state, office],
        event_entities=[f"{year} {state} {office} primary"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"{year} {state} {office} primary advancers",
            "candidate": cand,
            "winner_predicate": "advance",
        },
        market_type="multi_outcome",
        pos_cond=f"{cand} advances from the {year} {state} {office} primary.",
        neg_cond=f"{cand} does not advance from the {year} {state} {office} primary.",
        pattern_id="advance_primary",
        confidence=0.82,
    )


def _meet_action(q: str) -> _Inferred | None:
    """'Will <A> and <B> meet (next in <place>)? before <date>?'"""
    m = re.match(
        r"^will\s+(?P<a>[A-Z][\w'\-\.]+(?:\s+[A-Z][\w'\-\.]+)*)\s+and\s+"
        r"(?P<b>[A-Z][\w'\-\.]+(?:\s+[A-Z][\w'\-\.]+)*)\s+meet(?:\s+next)?"
        r"(?:\s+in\s+(?P<place>.+?))?\s+(?:before|in|by|during)\s+(?P<date>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    a = m.group("a").strip()
    b = m.group("b").strip()
    place = (m.group("place") or "").strip()
    date = m.group("date").strip()
    pair = "_".join(sorted([_slug(a), _slug(b)]))
    place_slug = _slug(place) if place else "any_location"
    comp_id = f"meet_{pair}_in_{place_slug}_by_{_slug(date)}"
    return _Inferred(
        canonical=f"{a} and {b} meet by {date}",
        subject_entities=[a, b],
        event_entities=[f"{a} and {b} meeting"],
        outcome_space={
            "kind": "binary_event",
            "competition_id": comp_id,
            "competition_name": f"{a} & {b} meeting by {date}",
            "candidate": f"{a}_and_{b}",
            "winner_predicate": f"meet_by_{_slug(date)}",
        },
        market_type="binary",
        pos_cond=f"{a} and {b} meet on or before {date}.",
        neg_cond=f"{a} and {b} do not meet on or before {date}.",
        pattern_id="meet_action",
        confidence=0.7,
    )


def _league_h2h_prefix(q: str) -> _Inferred | None:
    """'Premier League: Who will win the X v. Y game on <date>?' / La Liga / Bundesliga / etc."""
    m = re.match(
        r"^(?P<league>premier\s+league|la\s+liga|bundesliga|serie\s+a|ligue\s+1|"
        r"mls|ncaa|champions\s+league|europa\s+league|wnba):\s+"
        r"(?:who\s+will\s+win\s+the\s+|will\s+the\s+)?"
        r"(?P<team_a>.+?)\s+(?:v|vs|v\.|vs\.)\s+(?P<team_b>.+?)"
        r"(?:\s+(?:game|match|fixture))?\s+on\s+(?P<date>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    league = m.group("league").upper()
    team_a = m.group("team_a").strip().rstrip(".")
    team_b = m.group("team_b").strip().rstrip(".")
    date = m.group("date").strip()
    teams = "_vs_".join(sorted([_slug(team_a), _slug(team_b)]))
    comp_id = f"{_slug(league)}_{teams}_{_slug(date)}_winner"
    return _Inferred(
        canonical=f"{team_a} beats {team_b} in the {date} {league} fixture",
        subject_entities=[team_a, team_b, league],
        event_entities=[f"{date} {league} fixture: {team_a} vs {team_b}"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"{league} {team_a} vs {team_b} on {date}",
            "candidate": team_a,
            "winner_predicate": "win",
        },
        market_type="binary",
        pos_cond=f"{team_a} wins the {date} {league} fixture vs {team_b}.",
        neg_cond=f"{team_a} does not win the {date} {league} fixture vs {team_b}.",
        pattern_id="league_h2h_prefix",
        confidence=0.78,
    )


def _in_game_trading_pair(q: str) -> _Inferred | None:
    """'(In-Game/In-game Trading[, Low Fee Promotion]) Will/Who the X or Y win their <event>?'"""
    m = re.match(
        r"^\(in-game\s+trading[^\)]*\)\s+"
        r"(?:will\s+the\s+|who\s+will\s+win\s+the\s+)?"
        r"(?P<team_a>.+?)\s+(?:or|v|vs|v\.|vs\.)\s+the?\s*(?P<team_b>.+?)\s+"
        r"(?:win\s+(?:their\s+)?|game\s+on\s+|matchup)?(?P<rest>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    team_a = m.group("team_a").strip()
    team_b = m.group("team_b").strip()
    rest = m.group("rest").strip(" ?.")
    teams = "_vs_".join(sorted([_slug(team_a), _slug(team_b)]))
    comp_id = f"in_game_{teams}_{_slug(rest)[:40]}"
    return _Inferred(
        canonical=f"In-game trading: {team_a} or {team_b} wins {rest}",
        subject_entities=[team_a, team_b],
        event_entities=[f"in-game matchup: {team_a} vs {team_b}"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"In-game {team_a} vs {team_b}",
            "candidate": f"{team_a}_or_{team_b}",
            "winner_predicate": "win",
        },
        market_type="binary",
        pos_cond=f"{team_a} or {team_b} wins the in-game event.",
        neg_cond=f"Neither {team_a} nor {team_b} wins the in-game event.",
        pattern_id="in_game_trading_pair",
        confidence=0.7,
    )


def _oscar_category(q: str) -> _Inferred | None:
    """'Oscars 2022: Will <Name> win Best <Category>?'"""
    m = re.match(
        r"^(?P<award>oscars?|baftas?|emmys?|grammys?|globes?|tonys?)\s+(?P<year>20\d{2}):\s+"
        r"will\s+(?P<cand>.+?)\s+win\s+best\s+(?P<cat>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    award = m.group("award").lower()
    year = m.group("year")
    cand = m.group("cand").strip()
    cat = m.group("cat").strip(" ?.")
    comp_id = f"{award}_{year}_best_{_slug(cat)}"
    return _Inferred(
        canonical=f"{cand} wins Best {cat} at the {year} {award.title()}",
        subject_entities=[cand, award, cat],
        event_entities=[f"{year} {award.title()}"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"{year} {award.title()} Best {cat}",
            "candidate": cand,
            "winner_predicate": "win",
        },
        market_type="multi_outcome",
        pos_cond=f"{cand} is announced as Best {cat} at the {year} {award.title()}.",
        neg_cond=f"{cand} is not Best {cat} at the {year} {award.title()}.",
        pattern_id="oscar_category",
        confidence=0.85,
    )


def _ucl_top_scorer(q: str) -> _Inferred | None:
    """'Will <X> be the YYYY/YYYY top UCL goal scorer?' / topscorer / top assister"""
    m = re.match(
        r"^will\s+(?P<cand>.+?)\s+be\s+the\s+(?P<season>\d{4}(?:/\d{2,4})?)\s+"
        r"(?:top\s+)?(?P<comp>ucl|champions\s+league|premier\s+league|epl|la\s+liga|"
        r"bundesliga|serie\s+a|mls|wnba|nba)\s+"
        r"(?P<metric>goal\s+scorer|top\s+scorer|assister|mvp|"
        r"goal-scorer|topscorer|points\s+leader)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    cand = m.group("cand").strip()
    season = m.group("season")
    comp = m.group("comp").upper().replace(" ", "_")
    metric = m.group("metric").lower().replace(" ", "_")
    comp_id = f"{_slug(comp)}_{_slug(season)}_{metric}_winner"
    return _Inferred(
        canonical=f"{cand} wins {season} {comp} {metric}",
        subject_entities=[cand, comp],
        event_entities=[f"{season} {comp} {metric}"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"{season} {comp} {metric}",
            "candidate": cand,
            "winner_predicate": "top_metric",
        },
        market_type="multi_outcome",
        pos_cond=f"{cand} leads {season} {comp} {metric} at competition end.",
        neg_cond=f"{cand} is not the {season} {comp} {metric} leader.",
        pattern_id="league_top_scorer",
        confidence=0.85,
    )


def _art_auction(q: str) -> _Inferred | None:
    """'Will <artist>'s <work> [(Lot X)] sell for more than $Xm at <auction>'"""
    m = re.match(
        r"^will\s+(?P<artist>.+?)['’]s\s+[“\"‘']?(?P<work>.+?)[”\"’']?"  # noqa: RUF001
        r"(?:\s+\(lot\s+[^)]+\))?\s+sell\s+for\s+more\s+than\s+\$(?P<price>[\d.]+)(?P<unit>m|million|b|billion)"
        r"\s+at\s+(?P<auction>.+?)(?:\s+on\s+(?P<date>.+))?$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    artist = m.group("artist").strip()
    work = m.group("work").strip()
    price = m.group("price")
    unit = m.group("unit").lower()[0].upper()
    auction = m.group("auction").strip()
    (m.group("date") or "").strip()
    comp_id = f"{_slug(artist)}_{_slug(work)[:30]}_at_{_slug(auction)}_above_{price}{unit}"
    return _Inferred(
        canonical=f"{artist} {work} sells for >${price}{unit} at {auction}",
        subject_entities=[artist, work, auction],
        event_entities=[f"{auction} sale of {work}"],
        outcome_space={
            "kind": "threshold",
            "competition_id": comp_id,
            "competition_name": f"{artist} {work} >${price}{unit} at {auction}",
            "candidate": work,
            "winner_predicate": f"sells_above_{price}{unit}",
        },
        market_type="binary",
        pos_cond=f"{artist}'s {work} hammers at greater than ${price}{unit} at {auction}.",
        neg_cond=f"{artist}'s {work} sells at or below ${price}{unit} (or fails to sell).",
        pattern_id="art_auction",
        confidence=0.8,
    )


def _event_threshold_count(q: str) -> _Inferred | None:
    """'Will there be N or more <thing> in <event>?' / 'Will there be at least N <thing>?'"""
    m = re.match(
        r"^(?:[A-Za-z\s]+:\s+)?will\s+there\s+be\s+"
        r"(?P<dir>at\s+least|more\s+than|\d+\s+or\s+more|fewer\s+than)?\s*"
        r"(?P<n>\d+)\s*(?:\+|or\s+more|or\s+fewer)?\s+"
        r"(?P<thing>.+?)\s+(?:in|during|at|by|on)\s+(?P<event>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    n = m.group("n")
    thing = m.group("thing").strip()
    event = m.group("event").strip()
    direction = "below" if "fewer" in (m.group("dir") or "").lower() else "above"
    comp_id = f"count_of_{_slug(thing)[:40]}_in_{_slug(event)[:40]}_{direction}_{n}"
    return _Inferred(
        canonical=f"{n}+ {thing} in {event}",
        subject_entities=[event, thing],
        event_entities=[event],
        outcome_space={
            "kind": "threshold",
            "competition_id": comp_id,
            "competition_name": f">={n} {thing} in {event}",
            "candidate": event,
            "winner_predicate": f"count_{direction}_{n}",
        },
        market_type="binary",
        pos_cond=f"At least {n} {thing} occur in/at {event}.",
        neg_cond=f"Fewer than {n} {thing} occur in/at {event}.",
        pattern_id="event_threshold_count",
        confidence=0.7,
    )


def _will_not_event(q: str) -> _Inferred | None:
    """'Will <subject> not <verb> by <date>?' — negation form."""
    m = re.match(
        r"^will\s+(?P<subject>.+?)\s+not\s+(?P<verb>ipo|launch|release|"
        r"announce|deliver|complete|finish|win)\s+(?:by|before)\s+(?P<date>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    subject = m.group("subject").strip()
    verb = m.group("verb").lower()
    date = m.group("date").strip()
    comp_id = f"{_slug(subject)}_not_{verb}_by_{_slug(date)}"
    return _Inferred(
        canonical=f"{subject} does NOT {verb} by {date}",
        subject_entities=[subject],
        event_entities=[f"{subject} {verb} (by {date})"],
        outcome_space={
            "kind": "binary_event",
            "competition_id": comp_id,
            "competition_name": f"{subject} does not {verb} by {date}",
            "candidate": subject,
            "winner_predicate": f"not_{verb}_by_{_slug(date)}",
        },
        market_type="binary",
        pos_cond=f"{subject} does not {verb} on or before {date}.",
        neg_cond=f"{subject} {verb}s on or before {date}.",
        pattern_id="will_not_event",
        confidence=0.7,
    )


def _crypto_price_query(q: str) -> _Inferred | None:
    """'What will the price of $<token> be on <date>?' — open-numeric."""
    m = re.match(
        r"^what\s+will\s+the\s+price\s+of\s+\$?(?P<tok>\w+)\s+(?:\([^)]+\)\s+)?"
        r"be\s+on\s+(?P<date>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    tok = m.group("tok").strip()
    if not _CRYPTO_RX.search(tok):
        return None
    date = m.group("date").strip()
    comp_id = f"{_slug(tok)}_price_on_{_slug(date)}"
    return _Inferred(
        canonical=f"{tok.title()} price on {date}",
        subject_entities=[tok.title()],
        event_entities=[f"{tok.title()} price snapshot on {date}"],
        outcome_space={
            "kind": "other",
            "competition_id": comp_id,
            "competition_name": f"{tok.title()} price on {date}",
            "candidate": tok.title(),
            "winner_predicate": "price_observation",
        },
        market_type="scalar",
        pos_cond=f"{tok.title()}'s recorded price on {date}.",
        neg_cond=f"{tok.title()}'s recorded price on {date} (multi-outcome bins).",
        pattern_id="crypto_price_query",
        confidence=0.7,
    )


def _award_year_win(q: str) -> _Inferred | None:
    """'<AWARD> <year>: Will <name> win a <category>?' (Grammys/Tonys variant)."""
    m = re.match(
        r"^(?P<award>grammys?|baftas?|emmys?|tonys?|oscars?|globes?)\s+(?P<year>20\d{2}):\s+"
        r"will\s+(?P<cand>.+?)\s+win\s+(?:a|an|the)\s+(?P<cat>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    award = m.group("award").lower()
    year = m.group("year")
    cand = m.group("cand").strip()
    cat = m.group("cat").strip(" ?.")
    comp_id = f"{award}_{year}_winners_{_slug(cat)[:30]}"
    return _Inferred(
        canonical=f"{cand} wins {cat} at the {year} {award.title()}",
        subject_entities=[cand, award, cat],
        event_entities=[f"{year} {award.title()}"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"{year} {award.title()} {cat}",
            "candidate": cand,
            "winner_predicate": "win_award",
        },
        market_type="multi_outcome",
        pos_cond=f"{cand} wins {cat} at the {year} {award.title()}.",
        neg_cond=f"{cand} does not win {cat} at the {year} {award.title()}.",
        pattern_id="award_year_win",
        confidence=0.82,
    )


def _tournament_group(q: str) -> _Inferred | None:
    """'Will <team> be the Group <X> winner in the <tournament>?' / 'advance from group X'"""
    m = re.match(
        r"^will\s+(?P<team>.+?)\s+(?:be\s+the\s+|win\s+|advance\s+from\s+)group\s+"
        r"(?P<group>[A-Z]|[A-Z]\d?|\d)\s+(?:winner\s+)?in\s+the\s+(?P<tournament>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    team = m.group("team").strip()
    group = m.group("group").upper()
    tournament = m.group("tournament").strip()
    comp_id = f"{_slug(tournament)}_group_{group}_winner"
    return _Inferred(
        canonical=f"{team} wins Group {group} of {tournament}",
        subject_entities=[team, tournament],
        event_entities=[f"{tournament} Group {group}"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"{tournament} Group {group} winner",
            "candidate": team,
            "winner_predicate": "win_group",
        },
        market_type="multi_outcome",
        pos_cond=f"{team} finishes top of Group {group} at the {tournament}.",
        neg_cond=f"{team} does not finish top of Group {group} at the {tournament}.",
        pattern_id="tournament_group",
        confidence=0.8,
    )


def _acquire_event(q: str) -> _Inferred | None:
    """'Will <acquirer> acquire <target>?' (open-ended date)"""
    m = re.match(
        r"^will\s+(?P<acquirer>.+?)\s+(?P<verb>acquire|buy|takeover|merge\s+with)\s+(?P<target>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    acquirer = m.group("acquirer").strip()
    verb = m.group("verb").lower().replace(" ", "_")
    target = m.group("target").strip(" ?.")
    comp_id = f"{_slug(acquirer)}_{verb}_{_slug(target)[:40]}"
    return _Inferred(
        canonical=f"{acquirer} {verb}s {target}",
        subject_entities=[acquirer, target],
        event_entities=[f"{acquirer} {verb} {target}"],
        outcome_space={
            "kind": "binary_event",
            "competition_id": comp_id,
            "competition_name": f"{acquirer} {verb} {target}",
            "candidate": acquirer,
            "winner_predicate": verb,
        },
        market_type="binary",
        pos_cond=f"{acquirer} completes acquisition / merger with {target}.",
        neg_cond=f"{acquirer} does not acquire / merge with {target}.",
        pattern_id="acquire_event",
        confidence=0.72,
    )


def _which_x_higher(q: str) -> _Inferred | None:
    """'Which <category> will have higher <metric> on <date>: <a> or <b>?'"""
    m = re.match(
        r"^which\s+(?P<cat>.+?)\s+will\s+(?:have\s+|be\s+)?(?:the\s+)?higher\s+(?P<metric>.+?)\s+"
        r"(?:on|at|by)\s+(?P<date>[^:]+?):\s+(?P<a>.+?)\s+or\s+(?P<b>.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    cat = m.group("cat").strip()
    metric = m.group("metric").strip()
    date = m.group("date").strip()
    a = m.group("a").strip().rstrip(".")
    b = m.group("b").strip().rstrip(".")
    pair = "_vs_".join(sorted([_slug(a)[:20], _slug(b)[:20]]))
    comp_id = f"higher_{_slug(metric)[:30]}_{_slug(cat)[:30]}_{pair}_{_slug(date)}"
    return _Inferred(
        canonical=f"{a} has higher {metric} than {b} on {date}",
        subject_entities=[a, b, metric],
        event_entities=[f"{metric} comparison on {date}"],
        outcome_space={
            "kind": "single_winner_competition",
            "competition_id": comp_id,
            "competition_name": f"higher {metric} on {date}: {a} vs {b}",
            "candidate": a,
            "winner_predicate": "higher_metric",
        },
        market_type="binary",
        pos_cond=f"{a} has higher {metric} than {b} on {date}.",
        neg_cond=f"{b} has higher {metric} than {a} on {date}.",
        pattern_id="which_x_higher",
        confidence=0.78,
    )


def _will_catch_all(q: str) -> _Inferred | None:
    """Low-confidence catch-all: any 'Will <subject> ...' / 'Who/What ...?' question
    that didn't match a higher-specificity pattern. Confidence is low, the
    competition_id is a slug of the canonical question itself, so the pair
    only matches identical-question duplicates."""
    if not re.match(r"^(will|who|what|how\s+many|how\s+much|when|which|where)\b",
                    q, flags=re.IGNORECASE):
        return None
    comp_id = f"open_question_{_slug(q)[:60]}"
    return _Inferred(
        canonical=q,
        subject_entities=[],
        event_entities=[],
        outcome_space={
            "kind": "other",
            "competition_id": comp_id,
            "competition_name": q[:90],
            "candidate": "open",
            "winner_predicate": "open",
        },
        market_type="unknown",
        pos_cond=f"The question resolves YES per its written terms ({q[:90]}).",
        neg_cond=f"The question resolves NO per its written terms ({q[:90]}).",
        pattern_id="will_catch_all",
        confidence=0.35,
    )


def _release_before(q: str) -> _Inferred | None:
    """'<X> before <Y>?' / 'Will <X> happen before <Y>?'  Defaults to binary."""
    m = re.match(
        r"^(?:will\s+)?(?P<a>.+?)\s+before\s+(?P<b>.+?)$",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    a = m.group("a").strip(" ?.")
    b = m.group("b").strip(" ?.")
    if len(a) > 80 or len(b) > 60 or len(a) < 3 or len(b) < 3:
        return None
    comp_id = f"{_slug(a)[:40]}_before_{_slug(b)[:40]}"
    return _Inferred(
        canonical=f"{a} happens before {b}",
        subject_entities=[a, b],
        event_entities=[a, b],
        outcome_space={
            "kind": "temporal_order",
            "competition_id": comp_id,
            "competition_name": f"{a} before {b}",
            "candidate": a,
            "winner_predicate": "before_reference",
        },
        market_type="binary",
        pos_cond=f"{a} happens before {b}.",
        neg_cond=f"{a} does not happen before {b}.",
        pattern_id="release_before",
        confidence=0.55,
    )


# Ordered: most specific first.  Generic patterns are the final fallback.
_PATTERNS = (
    _primary_nominee,
    _primary_winner,
    _advance_primary,
    _state_election,
    _governor_on_date,
    _award_year_win,
    _oscar_category,
    _ucl_top_scorer,
    _vaccine_total,
    _floor_price_threshold,
    _fdv_threshold,
    _ipo_before,
    _in_game_trading_pair,
    _league_h2h_prefix,
    _h2h_spread,
    _h2h_winner,
    _matchup_pair_winner,
    _f1_h2h,
    _generic_h2h_question,
    _art_auction,
    _crypto_threshold,
    _crypto_price_query,
    _general_price_threshold,
    _chart_rank,
    _rotten_tomatoes,
    _largest_company,
    _which_x_higher,
    _runner_up,
    _tournament_group,
    _will_country_action,
    _acquire_event,
    _meet_action,
    _will_not_event,
    _event_threshold_count,
    _will_x_by_date,
    _release_before,
    _generic_winner,
    _will_catch_all,
)


def infer_semantics(market: MarketRow) -> _Inferred | None:
    if not market.question:
        return None
    q = _normalise(market.question)
    for handler in _PATTERNS:
        out = handler(q)
        if out is not None:
            return out
    return None


# ── public API ──────────────────────────────────────────────────────────────


def build_semantics_row(market: MarketRow) -> MarketSemanticsRow | None:
    """Return a populated ``MarketSemanticsRow`` for ``market`` if a pattern
    matches; otherwise return None.  Caller is responsible for batching the
    write."""
    inf = infer_semantics(market)
    if inf is None:
        return None

    raw_hash = hashlib.sha256(
        json.dumps({"q": market.question, "p": inf.pattern_id}, sort_keys=True).encode()
    ).hexdigest()
    extraction_id = hashlib.sha256(
        f"{market.id}|{_PROMPT_VERSION}|{_MODEL_NAME}|{raw_hash}".encode()
    ).hexdigest()

    deadline_ms = market.end_date_ms if hasattr(market, "end_date_ms") else None
    temporal_phrase = None
    temporal_resolution = "open_ended"
    if deadline_ms:
        temporal_resolution = "exact_date"
        temporal_phrase = f"by_ms_{deadline_ms}"

    ingested = int(time.time() * 1000)
    return MarketSemanticsRow(
        source_market_id=market.id,
        source_condition_id=market.condition_id,
        question=market.question,
        canonical_question=inf.canonical,
        market_type=inf.market_type,
        subject_entities=list(inf.subject_entities),
        event_entities=list(inf.event_entities),
        temporal_phrase=temporal_phrase,
        temporal_phrase_normalized=temporal_phrase,
        temporal_resolution=temporal_resolution,
        exact_deadline_ms=deadline_ms,
        date_constraints_json=json.dumps({}),
        jurisdiction=None,
        positive_resolution_condition=inf.pos_cond,
        negative_resolution_condition=inf.neg_cond,
        necessary_conditions_for_yes=[],
        sufficient_conditions_for_yes=[],
        necessary_conditions_for_no=[],
        sufficient_conditions_for_no=[],
        evidence_required=[],
        ambiguity_flags=[],
        ambiguity_score=None,
        semantic_confidence=float(inf.confidence),
        needs_manual_review=False,
        explanation_summary=f"deterministic rule: {inf.pattern_id}",
        flag_rationales_json=None,
        uncertainty_notes_json=None,
        rule_curation_notes_json=None,
        raw_response_hash=raw_hash,
        model_name=_MODEL_NAME,
        prompt_version=_PROMPT_VERSION,
        rulebook_id=None,
        rulebook_version=None,
        extraction_id=extraction_id,
        event_atoms_json=None,
        proposition_json=None,
        outcome_space_json=json.dumps(inf.outcome_space, sort_keys=True),
        tie_rule=None,
        if_event_never_occurs_rule=None,
        resolution_source=None,
        timezone_or_boundary=None,
        terms_confidence=float(inf.confidence),
        long_horizon=False,
        unresolved_reference_event=False,
        schema_version=_SCHEMA_VERSION,
        ingested_ts_ms=ingested,
    )
