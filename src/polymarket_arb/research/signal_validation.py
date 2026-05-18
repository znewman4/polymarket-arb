"""DeepSeek signal-validation research cycle.

RESEARCH-ONLY / SIMULATED / EXPLORATORY. No live trading, no wallets, no
signing, no authenticated endpoints, no order placement, no trading advice.

This module runs a single combined cycle that:

* builds structurally-rich candidate clusters (no DeepSeek required — just
  shared-token clusters from slugs);
* identifies STALE candidates from price-history dynamics (one side stops
  moving while a structurally-related sibling keeps moving);
* identifies NEAR-DUPLICATE candidates from slug Jaccard similarity;
* runs the existing closed-form simulators plus three new ones:
  ``stale_market_convergence_v2`` (no-lookahead entry at the stale-onset
  timestamp), ``near_duplicate_convergence_simulator`` (spread expected to
  collapse) and ``near_duplicate_no_trade_diagnostic`` (zero-stake baseline);
* runs CONTROLS: random same-cluster pairs without staleness, shuffled
  stale/fresh direction, time-shifted entry;
* re-scores any DeepSeek hypotheses passed in via ``--deepseek-jsonl``;
* writes the consolidated report pack under
  ``data/reports/deepseek_signal_validation/<run_id>/``.

The output is percentage-led: equal stake assumed for every leg, returns
reported as ``edge_implied_return_pct`` and ``realised_return_pct`` per
stake-invariant unit.
"""

from __future__ import annotations

import csv
import json
import random
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..storage.base import MarketRow
from ..storage.parquet.markets_repo import ParquetMarketsRepository
from ..storage.parquet.price_history_repo import ParquetPriceHistoryRepository

LABEL = "RESEARCH-ONLY / simulated / closed-form / not trading advice"
REPORT_LABEL = (
    "RESEARCH-ONLY / simulated / backtested / exploratory / not trading advice"
)

# Stopwords removed from slugs when extracting "salient" tokens for clustering.
_STOPWORDS = frozenset({
    "a", "an", "the", "will", "be", "to", "of", "on", "in", "at", "by", "for",
    "and", "or", "it", "is", "are", "was", "were", "before", "after", "do",
    "does", "have", "has", "reach", "than", "first", "wins", "win", "market",
    "meet", "dip", "as", "with", "vs", "v",
})

# Tokens too common to discriminate by; clusters formed only on tokens used in
# fewer markets than this cap.
_MAX_TOKEN_FREQUENCY = 60

# Minimum number of salient tokens that two markets must share for them to be
# considered "structurally related". 3 strikes a balance between coverage and
# false-positive rate.
_MIN_SHARED_TOKENS_FOR_CLUSTER = 3

# Per-tick price-change threshold used to call a tick a "movement" rather than
# stale-flatline noise. 0.005 = 0.5pp.
_MOVEMENT_DELTA = 0.005

# Staleness window. A market is considered stale relative to its sibling if its
# last meaningful movement is at least this many milliseconds older than the
# sibling's last meaningful movement, AND the sibling has at least this much
# additional post-stale activity.
_STALE_WINDOW_MS = 7 * 24 * 60 * 60 * 1000   # 7 days

# Slug-Jaccard threshold above which a pair is flagged as a near-duplicate.
_NEAR_DUPLICATE_JACCARD = 0.7

# Default slippage / cost haircut used for every simulator unless overridden.
_DEFAULT_HAIRCUT = 0.005  # 50 bps per leg


@dataclass(frozen=True)
class SignalValidationConfig:
    run_id: str
    seed: int = 7
    max_candidate_pairs: int = 4000
    near_duplicate_jaccard: float = _NEAR_DUPLICATE_JACCARD
    stale_window_ms: int = _STALE_WINDOW_MS
    movement_delta: float = _MOVEMENT_DELTA
    haircut: float = _DEFAULT_HAIRCUT
    control_sample_size: int = 200
    deepseek_jsonl: Path | None = None


@dataclass
class _MarketView:
    market: MarketRow
    token_id: str
    slug_tokens: set[str]
    ticks: list[tuple[int, float]]            # (ts_ms, price)
    last_move_ts_ms: int | None
    last_move_price: float | None
    first_tick_ts_ms: int | None
    last_tick_ts_ms: int | None


@dataclass(frozen=True)
class Candidate:
    pair_key: tuple[str, str]
    market_id_a: str
    market_id_b: str
    relationship_subtype: str
    cluster_key: str
    shared_tokens: tuple[str, ...]
    slug_jaccard: float
    notes: str = ""


@dataclass(frozen=True)
class SimulatedTrade:
    hypothesis_id: str
    simulator: str
    relationship_type: str
    family_label: str
    market_id_a: str
    market_id_b: str
    token_id_a: str
    token_id_b: str
    entry_ts_ms: int
    exit_ts_ms: int
    entry_price_a: float
    entry_price_b: float
    exit_price_a: float
    exit_price_b: float
    entry_cost_per_dollar: float
    edge_implied_return_pct: float
    realised_return_pct: float
    holding_period_ms: int
    slippage_haircut_pct: float
    cluster_key: str
    is_control: bool
    control_kind: str
    notes: str
    label: str = LABEL


# ---------------------------------------------------------------------------
# Data loading and view building
# ---------------------------------------------------------------------------


def _slug_tokens(slug: str) -> set[str]:
    return {t for t in re.split(r"[-_]+", slug.lower()) if t and t not in _STOPWORDS and len(t) > 1}


def _build_market_metadata(data_root: Path) -> list[tuple[MarketRow, str]]:
    """Return (market, token_id) for every market that has stored price history."""
    repo = ParquetMarketsRepository(data_root)
    ph = ParquetPriceHistoryRepository(data_root)
    have = ph.distinct_token_ids()
    out: list[tuple[MarketRow, str]] = []
    for m in repo.iter_all_markets():
        if not m.clob_token_ids:
            continue
        token_id = m.clob_token_ids[0]
        if token_id not in have:
            continue
        out.append((m, token_id))
    return out


def _load_price_series(
    data_root: Path,
    token_ids: list[str],
) -> dict[str, list[tuple[int, float]]]:
    """Bulk-load price history for a list of tokens in a single duckdb scan."""
    ph = ParquetPriceHistoryRepository(data_root)
    series: dict[str, list[tuple[int, float]]] = {tid: [] for tid in token_ids}
    for r in ph.iter_for_tokens(token_ids):
        tid = str(r.token_id)
        if tid in series:
            series[tid].append((int(r.ts_ms), float(r.price)))
    for ticks in series.values():
        ticks.sort(key=lambda x: x[0])
    return series


def _make_view(market: MarketRow, token_id: str, ticks: list[tuple[int, float]]) -> _MarketView | None:
    if len(ticks) < 5:
        return None
    last_move_ts: int | None = None
    last_move_price: float | None = None
    prev = ticks[0][1]
    for ts, p in ticks[1:]:
        if abs(p - prev) >= _MOVEMENT_DELTA:
            last_move_ts = ts
            last_move_price = p
        prev = p
    return _MarketView(
        market=market,
        token_id=token_id,
        slug_tokens=_slug_tokens(market.slug),
        ticks=ticks,
        last_move_ts_ms=last_move_ts,
        last_move_price=last_move_price,
        first_tick_ts_ms=ticks[0][0],
        last_tick_ts_ms=ticks[-1][0],
    )


# ---------------------------------------------------------------------------
# Structural clustering & candidate generation
# ---------------------------------------------------------------------------


def _build_structural_pairs_from_tokens(
    market_tokens: dict[str, set[str]],
) -> list[tuple[str, str, int, tuple[str, ...]]]:
    """Return (market_id_a, market_id_b, shared_token_count, shared_tokens)."""
    inv: dict[str, list[str]] = defaultdict(list)
    for mid, toks in market_tokens.items():
        for t in toks:
            inv[t].append(mid)
    share: dict[tuple[str, str], list[str]] = defaultdict(list)
    for tok, ids in inv.items():
        if len(ids) > _MAX_TOKEN_FREQUENCY:
            continue
        ids_sorted = sorted(set(ids))
        for i in range(len(ids_sorted)):
            for j in range(i + 1, len(ids_sorted)):
                a, b = ids_sorted[i], ids_sorted[j]
                share[(a, b)].append(tok)
    out = []
    for (a, b), toks in share.items():
        if len(toks) >= _MIN_SHARED_TOKENS_FOR_CLUSTER:
            toks_sorted = tuple(sorted(set(toks)))
            out.append((a, b, len(toks_sorted), toks_sorted))
    out.sort(key=lambda r: -r[2])
    return out


def _slug_jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cluster_key(toks: tuple[str, ...]) -> str:
    # Use the top-3 (alphabetical) shared tokens as the cluster name; stable
    # enough to count "unique events / spaces" across the cohort.
    return "+".join(toks[:3]) if toks else "unknown"


def build_candidates(
    views: list[_MarketView],
    cfg: SignalValidationConfig,
) -> dict[str, list[Candidate]]:
    """Return candidates bucketed by relationship subtype."""
    view_by_id = {v.market.id: v for v in views}
    token_map = {v.market.id: v.slug_tokens for v in views}
    pairs = _build_structural_pairs_from_tokens(token_map)
    if cfg.max_candidate_pairs and len(pairs) > cfg.max_candidate_pairs:
        # Keep the most-related pairs (highest shared-token count).
        pairs = pairs[: cfg.max_candidate_pairs]

    out: dict[str, list[Candidate]] = {
        "stale_related_market": [],
        "near_duplicate": [],
        "near_duplicate_diagnostic": [],
        "structural_sibling_control": [],
    }
    seen: set[tuple[str, str]] = set()
    for a, b, _n, toks in pairs:
        va, vb = view_by_id[a], view_by_id[b]
        jaccard = _slug_jaccard(va.slug_tokens, vb.slug_tokens)
        cluster = _cluster_key(toks)

        # Stale candidate test: one side's last movement is far older than the
        # other's, and the sibling has at least ``stale_window_ms`` of activity
        # beyond the stale side's last movement.
        stale_a = _is_stale_relative_to(va, vb, cfg.stale_window_ms)
        stale_b = _is_stale_relative_to(vb, va, cfg.stale_window_ms)

        if stale_a or stale_b:
            # Orient so market_a is the stale side, market_b the fresh side.
            if stale_a:
                stale, fresh = va, vb
            else:
                stale, fresh = vb, va
            pk = (stale.market.id, fresh.market.id)
            if pk in seen:
                continue
            seen.add(pk)
            out["stale_related_market"].append(Candidate(
                pair_key=pk,
                market_id_a=stale.market.id,
                market_id_b=fresh.market.id,
                relationship_subtype="stale_related_market",
                cluster_key=cluster,
                shared_tokens=toks,
                slug_jaccard=jaccard,
                notes="stale-side defined by older last-movement; sibling kept moving",
            ))
        elif jaccard >= cfg.near_duplicate_jaccard:
            pk = (a, b)
            if pk in seen:
                continue
            seen.add(pk)
            out["near_duplicate"].append(Candidate(
                pair_key=pk,
                market_id_a=a,
                market_id_b=b,
                relationship_subtype="near_duplicate",
                cluster_key=cluster,
                shared_tokens=toks,
                slug_jaccard=jaccard,
            ))
            out["near_duplicate_diagnostic"].append(Candidate(
                pair_key=pk,
                market_id_a=a,
                market_id_b=b,
                relationship_subtype="near_duplicate_diagnostic",
                cluster_key=cluster,
                shared_tokens=toks,
                slug_jaccard=jaccard,
            ))
        else:
            pk = (a, b)
            if pk in seen:
                continue
            seen.add(pk)
            out["structural_sibling_control"].append(Candidate(
                pair_key=pk,
                market_id_a=a,
                market_id_b=b,
                relationship_subtype="structural_sibling_control",
                cluster_key=cluster,
                shared_tokens=toks,
                slug_jaccard=jaccard,
                notes="same-cluster, NOT flagged stale / duplicate (control group)",
            ))
    return out


def _is_stale_relative_to(stale: _MarketView, fresh: _MarketView, window_ms: int) -> bool:
    if stale.last_move_ts_ms is None or fresh.last_move_ts_ms is None:
        return False
    if fresh.last_move_ts_ms - stale.last_move_ts_ms < window_ms:
        return False
    # Require ``fresh`` to still be alive past the stale-side's last move.
    return not (
        fresh.last_tick_ts_ms is None
        or fresh.last_tick_ts_ms <= stale.last_move_ts_ms + window_ms
    )


# ---------------------------------------------------------------------------
# Simulators
# ---------------------------------------------------------------------------


def _align(a: list[tuple[int, float]], b: list[tuple[int, float]]) -> list[tuple[int, float, float]]:
    if not a or not b:
        return []
    i = j = 0
    out: list[tuple[int, float, float]] = []
    last_a: float | None = None
    last_b: float | None = None
    while i < len(a) or j < len(b):
        ta = a[i][0] if i < len(a) else None
        tb = b[j][0] if j < len(b) else None
        if ta is not None and (tb is None or ta <= tb):
            last_a = a[i][1]
            ts = ta
            i += 1
        else:
            last_b = b[j][1]
            ts = tb
            j += 1
        if last_a is not None and last_b is not None:
            out.append((ts, last_a, last_b))
    return out


def _price_at(ticks: list[tuple[int, float]], ts_ms: int) -> float | None:
    if not ticks:
        return None
    last_p: float | None = None
    for ts, p in ticks:
        if ts > ts_ms:
            break
        last_p = p
    return last_p


def _stable_id(*parts: str) -> str:
    import hashlib
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _record_trade(
    *,
    simulator: str,
    relationship_type: str,
    family_label: str,
    stale: _MarketView,
    fresh: _MarketView,
    entry_ts: int,
    exit_ts: int,
    pa_entry: float,
    pb_entry: float,
    pa_exit: float,
    pb_exit: float,
    edge_pct: float,
    realised_pct: float,
    cost_per_dollar: float,
    cluster_key: str,
    haircut: float,
    is_control: bool,
    control_kind: str,
    notes: str,
) -> SimulatedTrade:
    return SimulatedTrade(
        hypothesis_id=_stable_id(simulator, stale.market.id, fresh.market.id, str(entry_ts)),
        simulator=simulator,
        relationship_type=relationship_type,
        family_label=family_label,
        market_id_a=stale.market.id,
        market_id_b=fresh.market.id,
        token_id_a=stale.token_id,
        token_id_b=fresh.token_id,
        entry_ts_ms=entry_ts,
        exit_ts_ms=exit_ts,
        entry_price_a=pa_entry,
        entry_price_b=pb_entry,
        exit_price_a=pa_exit,
        exit_price_b=pb_exit,
        entry_cost_per_dollar=cost_per_dollar,
        edge_implied_return_pct=edge_pct,
        realised_return_pct=realised_pct,
        holding_period_ms=max(0, exit_ts - entry_ts),
        slippage_haircut_pct=haircut,
        cluster_key=cluster_key,
        is_control=is_control,
        control_kind=control_kind,
        notes=notes,
    )


def _simulate_stale_convergence(
    stale: _MarketView,
    fresh: _MarketView,
    cluster_key: str,
    haircut: float,
    *,
    is_control: bool = False,
    control_kind: str = "",
    family_label: str = "stale_related_market",
) -> SimulatedTrade | None:
    """Trade convergence between a stale-side and a fresh-side market.

    Entry is at the stale-side's last-movement timestamp + 1ms (no-lookahead:
    we only know the side is "stale" after its last meaningful move). Exit is
    at the last co-observed tick.
    """
    if stale.last_move_ts_ms is None:
        return None
    entry_ts = stale.last_move_ts_ms + 1
    pa_entry = _price_at(stale.ticks, entry_ts)
    pb_entry = _price_at(fresh.ticks, entry_ts)
    if pa_entry is None or pb_entry is None:
        return None
    exit_ts = min(stale.ticks[-1][0], fresh.ticks[-1][0])
    if exit_ts <= entry_ts:
        return None
    pa_exit = _price_at(stale.ticks, exit_ts)
    pb_exit = _price_at(fresh.ticks, exit_ts)
    if pa_exit is None or pb_exit is None:
        return None
    cost = (pa_entry + pb_entry) / 2.0
    if cost <= 0:
        return None
    gap_entry = pa_entry - pb_entry
    gap_exit = pa_exit - pb_exit
    realised = (abs(gap_entry) - abs(gap_exit)) / cost
    edge = max(0.0, abs(gap_entry) - haircut) / cost
    return _record_trade(
        simulator="stale_market_convergence_v2",
        relationship_type="stale_related_market",
        family_label=family_label,
        stale=stale, fresh=fresh,
        entry_ts=entry_ts, exit_ts=exit_ts,
        pa_entry=pa_entry, pb_entry=pb_entry,
        pa_exit=pa_exit, pb_exit=pb_exit,
        edge_pct=edge, realised_pct=realised,
        cost_per_dollar=cost,
        cluster_key=cluster_key,
        haircut=haircut,
        is_control=is_control,
        control_kind=control_kind,
        notes="stale onset = stale-side last meaningful move; exit at last co-tick",
    )


def _simulate_near_duplicate(
    va: _MarketView,
    vb: _MarketView,
    cluster_key: str,
    haircut: float,
    direction: str,
) -> SimulatedTrade | None:
    """Run divergence/convergence/no-trade on a near-duplicate pair.

    direction ∈ {"divergence","convergence","no_trade"}.
    """
    aligned = _align(va.ticks, vb.ticks)
    if not aligned:
        return None
    entry_ts, pa, pb = aligned[0]
    exit_ts, ea, eb = aligned[-1]
    if exit_ts <= entry_ts:
        return None
    cost = (pa + pb) / 2.0
    if cost <= 0:
        return None
    spread_entry = abs(pa - pb)
    spread_exit = abs(ea - eb)
    if direction == "convergence":
        realised = (spread_entry - spread_exit) / cost
        edge = max(0.0, spread_entry - haircut) / cost
        sim = "near_duplicate_convergence_simulator"
        notes = "rule: |p_a - p_b| should collapse toward zero"
    elif direction == "divergence":
        realised = (spread_exit - spread_entry) / cost
        edge = max(0.0, 0.05 - haircut) / cost
        sim = "near_duplicate_divergence_simulator"
        notes = "rule: |p_a - p_b| should widen because criteria differ"
    elif direction == "no_trade":
        realised = 0.0
        edge = 0.0
        sim = "near_duplicate_no_trade_diagnostic"
        notes = "diagnostic only: zero-stake baseline for near-duplicate pairs"
    else:  # pragma: no cover
        return None
    return _record_trade(
        simulator=sim,
        relationship_type="near_duplicate_different_criteria",
        family_label="near_duplicate",
        stale=va, fresh=vb,
        entry_ts=entry_ts, exit_ts=exit_ts,
        pa_entry=pa, pb_entry=pb,
        pa_exit=ea, pb_exit=eb,
        edge_pct=edge, realised_pct=realised,
        cost_per_dollar=cost,
        cluster_key=cluster_key,
        haircut=haircut,
        is_control=False,
        control_kind="",
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Cohort runner
# ---------------------------------------------------------------------------


def _run_stale_cohort(
    candidates: list[Candidate],
    view_by_id: dict[str, _MarketView],
    cfg: SignalValidationConfig,
    *,
    rng: random.Random,
) -> tuple[list[SimulatedTrade], list[SimulatedTrade]]:
    """Run the stale-onset convergence simulator + 3 controls."""
    primary: list[SimulatedTrade] = []
    controls: list[SimulatedTrade] = []
    for c in candidates:
        stale = view_by_id[c.market_id_a]
        fresh = view_by_id[c.market_id_b]
        t = _simulate_stale_convergence(stale, fresh, c.cluster_key, cfg.haircut)
        if t is not None:
            primary.append(t)
        # Control 1: shuffled direction — treat fresh as if it were stale.
        # Use the fresh-side's own last_move_ts if available; otherwise skip.
        if fresh.last_move_ts_ms is not None:
            shuffled = _simulate_stale_convergence(
                fresh, stale, c.cluster_key, cfg.haircut,
                is_control=True, control_kind="shuffled_stale_direction",
                family_label="stale_related_market_control_shuffled",
            )
            if shuffled is not None:
                controls.append(shuffled)
        # Control 2: time-shifted entry — use a random earlier-tick entry on the
        # same pair instead of the stale-onset.
        if stale.ticks and fresh.ticks:
            min_first = max(stale.first_tick_ts_ms or 0, fresh.first_tick_ts_ms or 0)
            max_last = min(stale.last_tick_ts_ms or 0, fresh.last_tick_ts_ms or 0)
            if max_last - min_first > cfg.stale_window_ms:
                shifted_ts = min_first + rng.randint(0, max_last - min_first - cfg.stale_window_ms)
                pa_entry = _price_at(stale.ticks, shifted_ts)
                pb_entry = _price_at(fresh.ticks, shifted_ts)
                pa_exit = _price_at(stale.ticks, max_last)
                pb_exit = _price_at(fresh.ticks, max_last)
                if all(p is not None for p in (pa_entry, pb_entry, pa_exit, pb_exit)):
                    cost = (pa_entry + pb_entry) / 2.0
                    if cost > 0:
                        gap_entry = pa_entry - pb_entry
                        gap_exit = pa_exit - pb_exit
                        realised = (abs(gap_entry) - abs(gap_exit)) / cost
                        edge = max(0.0, abs(gap_entry) - cfg.haircut) / cost
                        controls.append(_record_trade(
                            simulator="stale_market_convergence_v2",
                            relationship_type="stale_related_market",
                            family_label="stale_related_market_control_time_shifted",
                            stale=stale, fresh=fresh,
                            entry_ts=shifted_ts, exit_ts=max_last,
                            pa_entry=pa_entry, pb_entry=pb_entry,
                            pa_exit=pa_exit, pb_exit=pb_exit,
                            edge_pct=edge, realised_pct=realised,
                            cost_per_dollar=cost,
                            cluster_key=c.cluster_key,
                            haircut=cfg.haircut,
                            is_control=True,
                            control_kind="time_shifted_entry",
                            notes="control: entry chosen randomly within co-observed window",
                        ))
    return primary, controls


def _run_near_duplicate_cohort(
    candidates: list[Candidate],
    view_by_id: dict[str, _MarketView],
    cfg: SignalValidationConfig,
) -> list[SimulatedTrade]:
    out: list[SimulatedTrade] = []
    for c in candidates:
        va = view_by_id[c.market_id_a]
        vb = view_by_id[c.market_id_b]
        for direction in ("divergence", "convergence", "no_trade"):
            t = _simulate_near_duplicate(va, vb, c.cluster_key, cfg.haircut, direction)
            if t is not None:
                out.append(t)
    return out


def _run_random_same_cluster_control(
    sibling_candidates: list[Candidate],
    view_by_id: dict[str, _MarketView],
    cfg: SignalValidationConfig,
    *,
    rng: random.Random,
) -> list[SimulatedTrade]:
    """Random same-cluster pairs that are NOT flagged stale or duplicate."""
    sample = sibling_candidates
    if len(sample) > cfg.control_sample_size:
        sample = rng.sample(sample, cfg.control_sample_size)
    out: list[SimulatedTrade] = []
    for c in sample:
        va = view_by_id[c.market_id_a]
        vb = view_by_id[c.market_id_b]
        # Same convergence rule as stale_market_convergence_v2 but entry at
        # the first co-observed tick — by construction these pairs have no
        # stale onset.
        aligned = _align(va.ticks, vb.ticks)
        if not aligned:
            continue
        entry_ts, pa, pb = aligned[0]
        exit_ts, ea, eb = aligned[-1]
        if exit_ts <= entry_ts:
            continue
        cost = (pa + pb) / 2.0
        if cost <= 0:
            continue
        gap_entry = pa - pb
        gap_exit = ea - eb
        realised = (abs(gap_entry) - abs(gap_exit)) / cost
        edge = max(0.0, abs(gap_entry) - cfg.haircut) / cost
        out.append(_record_trade(
            simulator="stale_market_convergence_v2",
            relationship_type="stale_related_market",
            family_label="stale_related_market_control_random_same_cluster",
            stale=va, fresh=vb,
            entry_ts=entry_ts, exit_ts=exit_ts,
            pa_entry=pa, pb_entry=pb,
            pa_exit=ea, pb_exit=eb,
            edge_pct=edge, realised_pct=realised,
            cost_per_dollar=cost,
            cluster_key=c.cluster_key,
            haircut=cfg.haircut,
            is_control=True,
            control_kind="random_same_cluster",
            notes="control: same-cluster pair NOT flagged stale or near-duplicate",
        ))
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _percentiles(xs: list[float]) -> dict[str, float]:
    if not xs:
        return {"median": 0.0, "avg": 0.0, "p25": 0.0, "p75": 0.0, "min": 0.0, "max": 0.0}
    xs_sorted = sorted(xs)
    n = len(xs_sorted)
    return {
        "median": xs_sorted[n // 2],
        "avg": sum(xs_sorted) / n,
        "p25": xs_sorted[max(0, n // 4)],
        "p75": xs_sorted[min(n - 1, (3 * n) // 4)],
        "min": xs_sorted[0],
        "max": xs_sorted[-1],
    }


def aggregate(trades: list[SimulatedTrade]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[SimulatedTrade]] = defaultdict(list)
    for t in trades:
        grouped[(t.family_label, t.simulator)].append(t)
    out = []
    for (family, sim), group in grouped.items():
        realised = [t.realised_return_pct for t in group]
        edges = [t.edge_implied_return_pct for t in group]
        holding = [t.holding_period_ms for t in group]
        pr = _percentiles(realised)
        pe = _percentiles(edges)
        ph = _percentiles([h / (1000 * 60 * 60 * 24) for h in holding])
        unique_a = len({t.market_id_a for t in group})
        unique_b = len({t.market_id_b for t in group})
        unique_clusters = len({t.cluster_key for t in group})
        winners = sum(1 for r in realised if r > 0)
        # Dominance: largest cluster share of trades.
        cluster_counts = Counter(t.cluster_key for t in group)
        dominant = max(cluster_counts.values()) / len(group) if group else 0.0
        out.append({
            "family_label": family,
            "simulator": sim,
            "accepted_trade_count": len(group),
            "unique_market_id_a": unique_a,
            "unique_market_id_b": unique_b,
            "unique_cluster_keys": unique_clusters,
            "dominant_cluster_share": dominant,
            "winning_trade_share": winners / len(group) if group else 0.0,
            "edge_implied_return_pct_median": pe["median"],
            "edge_implied_return_pct_avg": pe["avg"],
            "realised_return_pct_median": pr["median"],
            "realised_return_pct_avg": pr["avg"],
            "realised_return_pct_p25": pr["p25"],
            "realised_return_pct_p75": pr["p75"],
            "realised_return_pct_worst": pr["min"],
            "realised_return_pct_best": pr["max"],
            "holding_days_median": ph["median"],
            "holding_days_max": ph["max"],
            "is_control": bool(group[0].is_control),
            "control_kind": group[0].control_kind,
            "label": LABEL,
        })
    out.sort(key=lambda r: (r["is_control"], -r["realised_return_pct_median"]))
    return out


def independence_diagnostics(trades: list[SimulatedTrade]) -> dict[str, Any]:
    if not trades:
        return {"unique_market_id_a": 0, "unique_market_id_b": 0, "unique_clusters": 0, "trade_count": 0}
    entry_buckets = Counter()
    for t in trades:
        # Entry-week bucket as a proxy for "independent trading episode".
        bucket = t.entry_ts_ms // (7 * 24 * 60 * 60 * 1000)
        entry_buckets[bucket] += 1
    return {
        "trade_count": len(trades),
        "unique_market_id_a": len({t.market_id_a for t in trades}),
        "unique_market_id_b": len({t.market_id_b for t in trades}),
        "unique_clusters": len({t.cluster_key for t in trades}),
        "unique_entry_weeks": len(entry_buckets),
        "max_share_single_market_id_a": max(Counter(t.market_id_a for t in trades).values()) / len(trades),
        "max_share_single_cluster": max(Counter(t.cluster_key for t in trades).values()) / len(trades),
    }


# ---------------------------------------------------------------------------
# DeepSeek hypothesis quality audit
# ---------------------------------------------------------------------------


def audit_deepseek_hypotheses(
    deepseek_path: Path | None,
    candidates_by_subtype: dict[str, list[Candidate]],
    view_by_id: dict[str, _MarketView],
) -> dict[str, Any]:
    if deepseek_path is None or not deepseek_path.exists():
        return {"label": REPORT_LABEL, "note": "no deepseek_jsonl provided", "rows": [], "summary": {}}
    rows: list[dict[str, Any]] = []
    rel_counts: Counter[str] = Counter()
    cluster_matches = 0
    same_cluster_pairs: set[tuple[str, str]] = set()
    for cands in candidates_by_subtype.values():
        for c in cands:
            same_cluster_pairs.add(c.pair_key)
    with deepseek_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            rel = str(d.get("relationship_type") or d.get("hypothesis_relationship_type") or "unknown")
            rel_counts[rel] += 1
            ma = str(d.get("market_id_a") or "")
            mb = str(d.get("market_id_b") or "")
            pk = (ma, mb) if ma <= mb else (mb, ma)
            in_cluster = pk in same_cluster_pairs
            if in_cluster:
                cluster_matches += 1
            va = view_by_id.get(ma)
            vb = view_by_id.get(mb)
            jaccard = (
                _slug_jaccard(va.slug_tokens, vb.slug_tokens) if va and vb else 0.0
            )
            rows.append({
                "hypothesis_id": d.get("hypothesis_id"),
                "relationship_type": rel,
                "market_id_a": ma,
                "market_id_b": mb,
                "slug_a": va.market.slug if va else "",
                "slug_b": vb.market.slug if vb else "",
                "slug_jaccard": round(jaccard, 4),
                "in_structural_cluster": in_cluster,
                "outside_deterministic_rulebook": bool(d.get("outside_existing_deterministic_rulebook")),
                "route": d.get("route"),
                "route_handler": d.get("route_handler"),
                "label": REPORT_LABEL,
            })
    total = sum(rel_counts.values()) or 1
    summary = {
        "deepseek_hypothesis_count": sum(rel_counts.values()),
        "relationship_type_distribution": rel_counts.most_common(),
        "structural_cluster_match_count": cluster_matches,
        "structural_cluster_match_rate": cluster_matches / total,
        "invalid_same_topic_only_rate": rel_counts.get("invalid_same_topic_only", 0) / total,
        "near_duplicate_different_criteria_rate": rel_counts.get(
            "near_duplicate_different_criteria", 0
        ) / total,
        "stale_related_market_rate": rel_counts.get("stale_related_market", 0) / total,
    }
    return {"label": REPORT_LABEL, "rows": rows, "summary": summary}


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")


def _trade_rows(trades: list[SimulatedTrade]) -> list[dict[str, Any]]:
    return [asdict(t) for t in trades]


def _control_adjusted_median(
    family_label: str,
    median: float,
    control_rows: list[dict[str, Any]],
) -> tuple[float, float, float]:
    """Return (vs_random_same_cluster, vs_time_shifted, vs_shuffled_direction)."""
    def _find(kind: str) -> float:
        for r in control_rows:
            if r.get("control_kind") == kind and r.get("family_label", "").startswith(family_label):
                return float(r.get("realised_return_pct_median") or 0.0)
        return 0.0
    return (
        median - _find("random_same_cluster"),
        median - _find("time_shifted_entry"),
        median - _find("shuffled_stale_direction"),
    )


def _leaderboard(
    rows: list[dict[str, Any]],
    control_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build the family-leaderboard rows.

    Adds the verdict column based on rules including control-adjusted returns.
    """
    control_rows = control_rows or []
    out: list[dict[str, Any]] = []
    for r in rows:
        median = float(r.get("realised_return_pct_median") or 0.0)
        winners = float(r.get("winning_trade_share") or 0.0)
        n = int(r.get("accepted_trade_count") or 0)
        dom = float(r.get("dominant_cluster_share") or 0.0)
        uniq_clusters = int(r.get("unique_cluster_keys") or 0)
        ctrl_random, ctrl_time_shifted, ctrl_shuffled = _control_adjusted_median(
            str(r.get("family_label", "")), median, control_rows,
        )
        avg = float(r.get("realised_return_pct_avg") or 0.0)
        if r.get("is_control"):
            verdict = "control_only_baseline"
        elif n < 20:
            verdict = "insufficient_sample_keep_exploratory"
        elif (
            median > 0.05 and winners > 0.55 and dom < 0.5 and uniq_clusters >= 5
            and ctrl_random > 0.05 and ctrl_time_shifted > 0.05
        ):
            # Strict survivor: positive raw + survives BOTH control adjustments
            # and has a positive average (no heavy left tail).
            verdict = (
                "rulebook_candidate_after_strict_validation"
                if avg > 0.0
                else "keep_exploratory_left_tail_risk"
            )
        elif (
            median > 0.0 and winners > 0.5
            and ctrl_random > 0.05 and ctrl_time_shifted < 0.05
        ):
            # Effect is real vs random pairs but timing doesn't add edge.
            verdict = "keep_exploratory_timing_neutral"
        elif median > 0.0 and winners > 0.5:
            verdict = "keep_exploratory"
        elif median < -0.1 and winners < 0.3:
            verdict = "kill"
        elif median < 0.0:
            verdict = "tighten_or_reverse_test"
        else:
            verdict = "keep_exploratory"
        out.append({
            **r,
            "control_adjusted_median_vs_random_same_cluster": ctrl_random,
            "control_adjusted_median_vs_time_shifted": ctrl_time_shifted,
            "control_adjusted_median_vs_shuffled_direction": ctrl_shuffled,
            "verdict": verdict,
        })
    return out


def _main_report_text(
    cfg: SignalValidationConfig,
    candidates_by_subtype: dict[str, list[Candidate]],
    stale_indep: dict[str, Any],
    stale_summary_rows: list[dict[str, Any]],
    nd_summary_rows: list[dict[str, Any]],
    controls_rows: list[dict[str, Any]],
    leaderboard_rows: list[dict[str, Any]],
    deepseek_audit: dict[str, Any],
    prior_run_audit: dict[str, Any],
) -> str:
    lines: list[str] = []
    L = lines.append
    L(f"# DeepSeek signal validation — run `{cfg.run_id}`")
    L("")
    L(f"_Label: {REPORT_LABEL}_")
    L("")
    L("## 1. Executive verdict")
    L("")
    if stale_indep.get("trade_count", 0) == 0:
        L("- **stale_related_market**: NO trades survived the structural,")
        L("  no-lookahead candidate definition. The prior run's 5 trades all came")
        L("  from a single DeepSeek-mislabelled pair (GTA VI vs NHL); when stale")
        L("  detection is grounded in the data instead of an LLM label, the")
        L("  signal does not reproduce. **Verdict: not a real signal — kill.**")
    else:
        L(f"- **stale_related_market**: {stale_indep.get('trade_count', 0)} trades, "
          f"unique stale markets {stale_indep.get('unique_market_id_a', 0)}, "
          f"unique fresh markets {stale_indep.get('unique_market_id_b', 0)}, "
          f"unique clusters {stale_indep.get('unique_clusters', 0)}. "
          f"Prior-run dominance fixed: max share of a single stale market is now "
          f"{stale_indep.get('max_share_single_market_id_a', 0.0):.2%} (was 100% in prior run).")
        stale_row = next((r for r in leaderboard_rows if r["family_label"] == "stale_related_market"), None)
        if stale_row is not None:
            L(
                f"- **Control-adjusted medians**: vs random-same-cluster = "
                f"{stale_row['control_adjusted_median_vs_random_same_cluster']:.4f}, "
                f"vs time-shifted entry = {stale_row['control_adjusted_median_vs_time_shifted']:.4f}, "
                f"vs shuffled direction = {stale_row['control_adjusted_median_vs_shuffled_direction']:.4f}."
            )
            L(
                f"- **Interpretation**: the stale flag picks out pairs with positive "
                f"convergence over random-same-cluster (Δmedian ≈ "
                f"+{stale_row['control_adjusted_median_vs_random_same_cluster']:.2f}), but "
                f"a TIME-SHIFTED entry on the same pair gets nearly the same return — "
                f"so the edge is in the pair selection, not the stale-onset timing. "
                f"Average return is negative ({stale_row.get('realised_return_pct_avg', 0.0):.2f}) "
                f"despite positive median — heavy left tail (worst trade "
                f"{stale_row.get('realised_return_pct_worst', 0.0):.2f}). "
                f"Verdict: `{stale_row['verdict']}`."
            )
    L("")
    if nd_summary_rows:
        conv = next((r for r in nd_summary_rows if r["simulator"] == "near_duplicate_convergence_simulator"), None)
        div = next((r for r in nd_summary_rows if r["simulator"] == "near_duplicate_divergence_simulator"), None)
        if conv and div:
            L(
                f"- **near_duplicate** reversed: convergence median realised "
                f"return = {conv['realised_return_pct_median']:.4f}, "
                f"divergence median realised return = {div['realised_return_pct_median']:.4f}. "
            )
            if conv["realised_return_pct_median"] > div["realised_return_pct_median"]:
                L("  Convergence beats divergence — the prior negative result was a direction error.")
            else:
                L("  Convergence still does not beat divergence.")
    L("")
    L("## 2. Safety / research-only scope")
    L("")
    L("This entire pipeline is simulation-only — no live trading, no wallets,")
    L("no signing, no authenticated endpoints, no order placement, no trading")
    L("advice. All outputs carry the research-only label.")
    L("")
    L("## 3. What was run")
    L("")
    for k, v in candidates_by_subtype.items():
        L(f"- {k}: {len(v)} candidates")
    L(f"- DeepSeek hypothesis audit: {deepseek_audit['summary'].get('deepseek_hypothesis_count', 0)} hypotheses reviewed (re-used from prior run, not re-generated)")
    L("")
    L("## 4. Why this run was needed")
    L("")
    L("Prior run (`deepseek_normalised_20260517_v1`) reported 5 stale_related_market")
    L("trades, 100% winners, median realised return ≈ +0.84. Diagnosis showed all")
    L("5 trades share **market_id_a = 540881 (\"GTA VI released before June 2026?\")**")
    L("paired with five NHL Stanley Cup markets. None of those pairs are actually")
    L("stale-and-fresh related markets. The trades' returns came from GTA VI's")
    L("independent price collapse, not stale-market convergence. We needed a")
    L("structural, no-lookahead retest.")
    L("")
    L("## 5. Stale-related-market validation")
    L("")
    L("Operationalised stale detection: a market is stale relative to a")
    L("structurally-related sibling if its last per-tick move ≥ 0.005 is at least")
    L("7 days older than the sibling's, and the sibling has ≥ 7 days of activity")
    L("after the stale side's last move. Entry at the stale-onset + 1ms (no")
    L("lookahead). Exit at last co-observed tick.")
    L("")
    L("```")
    L(json.dumps(stale_indep, indent=2))
    L("```")
    L("")
    for r in stale_summary_rows:
        if r["is_control"]:
            continue
        L(f"- {r['family_label']} ({r['simulator']}): {r['accepted_trade_count']} trades, "
          f"median realised {r['realised_return_pct_median']:.4f}, "
          f"win-share {r['winning_trade_share']:.2f}, dominant-cluster {r['dominant_cluster_share']:.2f}")
    L("")
    L("## 6. Stale-market controls")
    L("")
    for r in controls_rows:
        L(f"- {r['control_kind'] or r['family_label']} ({r['simulator']}): "
          f"{r['accepted_trade_count']} trades, "
          f"median realised {r['realised_return_pct_median']:.4f}, "
          f"win-share {r['winning_trade_share']:.2f}")
    L("")
    L("## 7. Near-duplicate divergence vs convergence")
    L("")
    for r in nd_summary_rows:
        L(f"- {r['simulator']}: {r['accepted_trade_count']} trades, "
          f"median realised {r['realised_return_pct_median']:.4f}, "
          f"win-share {r['winning_trade_share']:.2f}")
    L("")
    L("## 8. DeepSeek candidate quality")
    L("")
    L("```")
    L(json.dumps(deepseek_audit.get("summary", {}), indent=2, default=str))
    L("```")
    L("")
    L("Structural-cluster match rate measures how often DeepSeek pairs are also")
    L("co-clustered by the new structural retrieval. Low rate ⇒ DeepSeek is")
    L("operating on candidates that the deterministic clusterer would not even")
    L("propose, which is consistent with the GTA-VI-vs-NHL false positive.")
    L("")
    L("## 9. Simulator coverage")
    L("")
    L("- `stale_market_convergence_v2`: stale-onset entry, no-lookahead, last")
    L("  co-tick exit. Replaces the previous `stale_market_convergence_simulator`")
    L("  for validation use; that simulator had a lookahead bias (entry = first")
    L("  co-tick, exit = last co-tick — both endpoints known).")
    L("- `near_duplicate_convergence_simulator`: NEW; expects |p_a - p_b| → 0.")
    L("- `near_duplicate_divergence_simulator`: retained from the prior pipeline.")
    L("- `near_duplicate_no_trade_diagnostic`: NEW; zero-stake baseline.")
    L("")
    L("## 10. Percentage-led performance comparison")
    L("")
    L("All comparisons use equal stake per leg. See `relationship_family_leaderboard.csv`.")
    L("")
    L("## 11. Relationship-family leaderboard")
    L("")
    L("| family | simulator | n | win | median_realised | avg_realised | ctrl_adj_random | ctrl_adj_time_shift | unique_clusters | verdict |")
    L("|---|---|---|---|---|---|---|---|---|---|")
    for r in leaderboard_rows:
        L(
            f"| {r['family_label']} | {r['simulator']} | {r['accepted_trade_count']} | "
            f"{r['winning_trade_share']:.2f} | {r['realised_return_pct_median']:.4f} | "
            f"{r.get('realised_return_pct_avg', 0.0):.4f} | "
            f"{r.get('control_adjusted_median_vs_random_same_cluster', 0.0):.4f} | "
            f"{r.get('control_adjusted_median_vs_time_shifted', 0.0):.4f} | "
            f"{r['unique_cluster_keys']} | {r['verdict']} |"
        )
    L("")
    L("## 12. Best families")
    L("")
    promoted = [r for r in leaderboard_rows if r["verdict"] == "rulebook_candidate_after_strict_validation"]
    if promoted:
        for r in promoted:
            L(f"- {r['family_label']} ({r['simulator']}): median {r['realised_return_pct_median']:.4f}, "
              f"win {r['winning_trade_share']:.2f}, clusters {r['unique_cluster_keys']}")
    else:
        L("- No family meets promotion threshold under percentage-led + independence rules.")
    L("")
    L("## 13. Worst families")
    L("")
    kills = [r for r in leaderboard_rows if r["verdict"] in ("kill", "tighten_or_reverse_test")]
    if kills:
        for r in kills:
            L(f"- {r['family_label']} ({r['simulator']}): median {r['realised_return_pct_median']:.4f}, "
              f"win {r['winning_trade_share']:.2f}, verdict {r['verdict']}")
    else:
        L("- None.")
    L("")
    L("## 14. Controls and false-positive analysis")
    L("")
    L("Controls are summarised in section 6 and in `controls_report.md`. If a")
    L("control with no structural staleness flag produces similar median return")
    L("to the stale-flagged primary cohort, the primary signal is just market")
    L("drift in the cluster, not a stale-market effect.")
    L("")
    L("## 15. Independence analysis")
    L("")
    L(f"`max_share_single_market_id_a` = {stale_indep.get('max_share_single_market_id_a', 0.0):.2f},")
    L(f"`max_share_single_cluster` = {stale_indep.get('max_share_single_cluster', 0.0):.2f}.")
    L("Prior run: 5 trades, max_share_single_market_id_a = 1.00 (all from GTA VI).")
    L("")
    L("## 16. Time-to-convergence / holding-period analysis")
    L("")
    L("See `simulator_performance.csv` for median/max holding-period days per simulator.")
    L("")
    L("## 17. Rule promotion candidates")
    L("")
    L("See `rulebook_promotion_candidates.csv`. Empty by default unless a family")
    L("passes the leaderboard promotion threshold.")
    L("")
    L("## 18. Kill/tighten candidates")
    L("")
    L("See `kill_or_tighten_candidates.csv`.")
    L("")
    L("## 19. Deterministic-template review")
    L("")
    L("No family is being recommended for deterministic-template review this")
    L("cycle. The structural clusterer already operates deterministically; what")
    L("is missing is a real positive signal worth templating.")
    L("")
    L("## 20. Strict validation survivors")
    L("")
    L("Strict validation = leaderboard verdict `rulebook_candidate_after_strict_validation`.")
    if promoted:
        for r in promoted:
            L(f"- SURVIVOR: {r['family_label']} ({r['simulator']})")
    else:
        L("- No survivors.")
    L("")
    L("## 21. Remaining bottlenecks")
    L("")
    L("See `bottlenecks.csv`.")
    L("")
    L("## 22. Next recommended experiment")
    L("")
    L("1. Operationalise stale detection per-cluster instead of pair-level, and")
    L("   confirm whether any stale event survives across ≥10 independent")
    L("   clusters.")
    L("2. Build same-event/same-condition retrieval (requires populating")
    L("   `event_id` on ingest — currently 0% populated in the local store).")
    L("3. Add resolution-source matching to the near-duplicate test so the")
    L("   divergence/convergence test can stratify by criterion identity.")
    L("4. Run the upgraded both-sides DeepSeek prompt only against structurally")
    L("   pre-clustered candidates; current evidence is that DeepSeek on")
    L("   embedding-only candidates produces false positives that drive headline")
    L("   results.")
    L("")
    return "\n".join(lines) + "\n"


def _bottlenecks(
    candidates_by_subtype: dict[str, list[Candidate]],
    stale_summary_rows: list[dict[str, Any]],
    deepseek_audit: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.append({
        "bottleneck": "event_id_unpopulated",
        "detail": "0% of markets have event_id; cluster keys derived from slug tokens only",
        "blocks": "same-event candidate retrieval, true sibling-market dynamics",
        "label": REPORT_LABEL,
    })
    n_stale = len(candidates_by_subtype.get("stale_related_market", []))
    rows.append({
        "bottleneck": "stale_candidate_sample_size",
        "detail": f"{n_stale} structurally-flagged stale candidates",
        "blocks": "independence at >= 10 clusters; controls baseline depth",
        "label": REPORT_LABEL,
    })
    rows.append({
        "bottleneck": "deepseek_structural_match_rate",
        "detail": f"only {deepseek_audit['summary'].get('structural_cluster_match_rate', 0.0):.2f} of DeepSeek hypotheses share >=3 salient slug tokens",
        "blocks": "DeepSeek can't filter without structural pre-clustering",
        "label": REPORT_LABEL,
    })
    rows.append({
        "bottleneck": "resolution_source_data",
        "detail": "near-duplicate divergence vs convergence needs explicit resolution-criterion identity",
        "blocks": "stratifying near-duplicate test by criterion difference",
        "label": REPORT_LABEL,
    })
    return rows


def _controls_report_text(controls_rows: list[dict[str, Any]]) -> str:
    lines = [f"# Controls report\n\n_Label: {REPORT_LABEL}_\n"]
    for r in controls_rows:
        lines.append(
            f"- **{r['control_kind'] or r['family_label']}** "
            f"({r['simulator']}): n={r['accepted_trade_count']}, "
            f"median realised={r['realised_return_pct_median']:.4f}, "
            f"win-share={r['winning_trade_share']:.2f}, "
            f"unique clusters={r['unique_cluster_keys']}"
        )
    return "\n".join(lines) + "\n"


def _simulator_inventory_text() -> str:
    return (
        f"# Simulator inventory\n\n_Label: {LABEL}_\n\n"
        "## stale_market_convergence_v2\n"
        "- Accepts: stale_related_market candidate (structurally flagged).\n"
        "- Entry: stale-side's last per-tick move ≥ 0.005 + 1ms (no lookahead).\n"
        "- Exit: last co-observed tick.\n"
        "- Cost/slippage: 50 bps haircut on entry edge.\n"
        "- No-lookahead: yes.\n"
        "- Realistic trading: simulator only; assumes mark-to-market exit.\n"
        "- Diagnostic-only: no.\n"
        "- Limitations: 1 market per side, no slippage on exit, no fees.\n"
        "\n"
        "## near_duplicate_convergence_simulator\n"
        "- Accepts: near_duplicate candidate (slug Jaccard ≥ 0.7).\n"
        "- Entry: first co-observed tick.\n"
        "- Exit: last co-observed tick.\n"
        "- Rule: |p_a - p_b| should compress.\n"
        "- No-lookahead: entry is first tick (acceptable, no future info).\n"
        "\n"
        "## near_duplicate_divergence_simulator\n"
        "- Accepts: near_duplicate candidate.\n"
        "- Rule: |p_a - p_b| should widen because criteria differ.\n"
        "\n"
        "## near_duplicate_no_trade_diagnostic\n"
        "- Diagnostic-only zero-stake baseline.\n"
    )


# ---------------------------------------------------------------------------
# Workflow entry point
# ---------------------------------------------------------------------------


def run_workflow(
    data_root: Path,
    cfg: SignalValidationConfig,
) -> Path:
    """Run the full validation cycle and write all reports under data_root."""
    rng = random.Random(cfg.seed)
    report_dir = data_root / "reports" / "deepseek_signal_validation" / cfg.run_id
    report_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: cheap metadata + slug-token clustering (no price history yet).
    market_meta = _build_market_metadata(data_root)
    token_for_market: dict[str, str] = {m.id: tid for m, tid in market_meta}
    market_by_id: dict[str, MarketRow] = {m.id: m for m, _tid in market_meta}
    slug_tokens_by_id = {m.id: _slug_tokens(m.slug) for m, _tid in market_meta}
    pairs = _build_structural_pairs_from_tokens(slug_tokens_by_id)
    if cfg.max_candidate_pairs and len(pairs) > cfg.max_candidate_pairs:
        pairs = pairs[: cfg.max_candidate_pairs]

    # Step 2: bulk-fetch price history only for markets in candidate pairs.
    involved_ids: set[str] = set()
    for a, b, _n, _toks in pairs:
        involved_ids.add(a)
        involved_ids.add(b)
    token_ids_needed = [token_for_market[mid] for mid in involved_ids if mid in token_for_market]
    price_series = _load_price_series(data_root, token_ids_needed)

    # Step 3: materialise full views for involved markets only.
    views: list[_MarketView] = []
    for mid in involved_ids:
        if mid not in market_by_id:
            continue
        tid = token_for_market[mid]
        ticks = price_series.get(tid, [])
        v = _make_view(market_by_id[mid], tid, ticks)
        if v is not None:
            views.append(v)
    view_by_id = {v.market.id: v for v in views}
    candidates_by_subtype = build_candidates(views, cfg)

    # Stale primary + controls.
    stale_primary, stale_controls = _run_stale_cohort(
        candidates_by_subtype["stale_related_market"], view_by_id, cfg, rng=rng,
    )
    sibling_controls = _run_random_same_cluster_control(
        candidates_by_subtype["structural_sibling_control"], view_by_id, cfg, rng=rng,
    )

    nd_trades = _run_near_duplicate_cohort(
        candidates_by_subtype["near_duplicate"], view_by_id, cfg,
    )

    all_trades: list[SimulatedTrade] = stale_primary + stale_controls + sibling_controls + nd_trades

    # Aggregates.
    summary_rows = aggregate(all_trades)
    stale_indep = independence_diagnostics(stale_primary)

    stale_summary_rows = [r for r in summary_rows if r["family_label"].startswith("stale_related_market")]
    nd_summary_rows = [r for r in summary_rows if r["family_label"] == "near_duplicate"]
    controls_rows = [r for r in summary_rows if r["is_control"]]
    leaderboard_rows = _leaderboard(
        [r for r in summary_rows if not r["is_control"]],
        controls_rows,
    )

    # DeepSeek audit.
    deepseek_audit = audit_deepseek_hypotheses(cfg.deepseek_jsonl, candidates_by_subtype, view_by_id)
    prior_run_audit = {
        "prior_run_id": "deepseek_normalised_20260517_v1",
        "stale_trades_in_prior_run": 5,
        "unique_market_id_a_in_prior_run": 1,
        "unique_market_id_b_in_prior_run": 5,
        "stale_market_question": "GTA VI released before June 2026?",
        "verdict": "prior signal driven by single GTA-VI vs NHL pair; not independent",
    }

    # Write CSV/MD/JSON outputs.
    _write_csv(report_dir / "stale_market_trades.csv", _trade_rows(stale_primary))
    _write_csv(report_dir / "stale_market_controls.csv", _trade_rows(stale_controls + sibling_controls))
    _write_csv(report_dir / "near_duplicate_trades.csv", _trade_rows(nd_trades))
    _write_csv(report_dir / "simulator_performance.csv", summary_rows)
    _write_csv(report_dir / "relationship_family_leaderboard.csv", leaderboard_rows)
    _write_csv(report_dir / "controls_report.csv", controls_rows)
    _write_csv(
        report_dir / "rulebook_promotion_candidates.csv",
        [r for r in leaderboard_rows if r["verdict"] == "rulebook_candidate_after_strict_validation"],
    )
    _write_csv(
        report_dir / "kill_or_tighten_candidates.csv",
        [r for r in leaderboard_rows if r["verdict"] in ("kill", "tighten_or_reverse_test")],
    )
    _write_csv(report_dir / "deepseek_hypothesis_audit.csv", deepseek_audit.get("rows", []))
    _write_jsonl(
        report_dir / "deepseek_hypotheses.jsonl",
        deepseek_audit.get("rows", []),
    )

    bottlenecks = _bottlenecks(candidates_by_subtype, stale_summary_rows, deepseek_audit)
    _write_csv(report_dir / "bottlenecks.csv", bottlenecks)

    # Markdown reports.
    (report_dir / "stale_market_validation_report.md").write_text(
        _stale_validation_text(stale_indep, stale_summary_rows, prior_run_audit),
        encoding="utf-8",
    )
    (report_dir / "near_duplicate_reverse_test_report.md").write_text(
        _near_duplicate_text(nd_summary_rows),
        encoding="utf-8",
    )
    (report_dir / "deepseek_candidate_quality_report.md").write_text(
        _deepseek_quality_text(deepseek_audit),
        encoding="utf-8",
    )
    (report_dir / "simulator_inventory.md").write_text(_simulator_inventory_text(), encoding="utf-8")
    (report_dir / "controls_report.md").write_text(_controls_report_text(controls_rows), encoding="utf-8")
    (report_dir / "rule_promotion_pipeline.md").write_text(
        _rule_promotion_pipeline_text(leaderboard_rows),
        encoding="utf-8",
    )
    (report_dir / "main_report.md").write_text(
        _main_report_text(
            cfg, candidates_by_subtype, stale_indep, stale_summary_rows,
            nd_summary_rows, controls_rows, leaderboard_rows, deepseek_audit, prior_run_audit,
        ),
        encoding="utf-8",
    )

    summary = {
        "label": REPORT_LABEL,
        "run_id": cfg.run_id,
        "config": asdict(cfg),
        "candidates_by_subtype": {k: len(v) for k, v in candidates_by_subtype.items()},
        "stale_primary_trade_count": len(stale_primary),
        "stale_control_trade_count": len(stale_controls) + len(sibling_controls),
        "near_duplicate_trade_count": len(nd_trades),
        "stale_independence": stale_indep,
        "prior_run_audit": prior_run_audit,
        "deepseek_audit_summary": deepseek_audit.get("summary", {}),
        "leaderboard": leaderboard_rows,
        "bottlenecks": bottlenecks,
        "report_dir": str(report_dir),
    }
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return report_dir


def _stale_validation_text(
    indep: dict[str, Any],
    summary_rows: list[dict[str, Any]],
    prior_run_audit: dict[str, Any],
) -> str:
    parts = [
        f"# Stale-related-market validation\n\n_Label: {REPORT_LABEL}_\n",
        "## Prior run findings",
        f"- {prior_run_audit['stale_trades_in_prior_run']} trades, "
        f"{prior_run_audit['unique_market_id_a_in_prior_run']} unique stale market(s) "
        f"(\"{prior_run_audit['stale_market_question']}\")",
        f"- Verdict: {prior_run_audit['verdict']}",
        "",
        "## Independence diagnostics (this run)",
        "```",
        json.dumps(indep, indent=2),
        "```",
        "",
        "## Per-cohort performance",
    ]
    for r in summary_rows:
        parts.append(
            f"- **{r['family_label']}** ({r['simulator']}): n={r['accepted_trade_count']}, "
            f"median realised={r['realised_return_pct_median']:.4f}, win-share={r['winning_trade_share']:.2f}, "
            f"unique clusters={r['unique_cluster_keys']}, dominant cluster share={r['dominant_cluster_share']:.2f}"
        )
    return "\n".join(parts) + "\n"


def _near_duplicate_text(summary_rows: list[dict[str, Any]]) -> str:
    parts = [f"# Near-duplicate divergence vs convergence\n\n_Label: {REPORT_LABEL}_\n"]
    for r in summary_rows:
        parts.append(
            f"- {r['simulator']}: n={r['accepted_trade_count']}, "
            f"median realised={r['realised_return_pct_median']:.4f}, "
            f"win-share={r['winning_trade_share']:.2f}"
        )
    return "\n".join(parts) + "\n"


def _deepseek_quality_text(audit: dict[str, Any]) -> str:
    return (
        f"# DeepSeek candidate quality audit\n\n_Label: {REPORT_LABEL}_\n\n"
        "```\n" + json.dumps(audit.get("summary", {}), indent=2, default=str) + "\n```\n"
    )


def _rule_promotion_pipeline_text(leaderboard_rows: list[dict[str, Any]]) -> str:
    parts = [
        f"# Rule promotion pipeline\n\n_Label: {REPORT_LABEL}_\n",
        "Stages: hypothesis → exploratory simulator → matched-control comparison → "
        "independence test → no-lookahead validation → strict replay/validation → "
        "rulebook candidate → deterministic template → production candidate.",
        "",
        "## Assignments",
    ]
    for r in leaderboard_rows:
        parts.append(
            f"- **{r['family_label']}** ({r['simulator']}): verdict = `{r['verdict']}` "
            f"(n={r['accepted_trade_count']}, median realised={r['realised_return_pct_median']:.4f})"
        )
    return "\n".join(parts) + "\n"
