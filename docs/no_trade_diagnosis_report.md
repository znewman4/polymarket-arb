# No-Trade Diagnosis Report

Generated: 2026-05-11

## Executive View

The system is receiving useful information. It is successfully identifying real semantic structure: mutually exclusive election and nomination markets, shared outcome spaces, and same-reference temporal markets around events like GTA VI. The reason it is not making simulated trades is not that the research layer is empty. It is that the information found so far has not become an executable price dislocation under the current conservative strategy rules.

In the latest pairwise run, the strongest evidence is the funnel:

```text
accepted_relationships_loaded: 96
strategy_eligible_relationships: 92
relationships_with_price_history: 92
relationships_with_aligned_price_series: 92
ticks_evaluated: 228067
gross_violations_found: 0
trades_executed: 0
no_price_violation: 228067
```

That means the backtest was not blocked by missing data. It had relationships, token IDs, price history, and aligned series. It simply did not find a price inequality violation.

My opinion: this is a healthy failure mode. The semantic layer is starting to see the board, but the current pairwise strategy is asking a narrow question: “Are two related markets mispriced enough right now to buy?” For the current universe, the answer is no.

## Example 1: Pairwise Category Relationships Are Real, But Not Mispriced

The system accepted this relationship:

```text
Will LeBron James win the 2028 US Presidential Election?
Will the Democrats win the 2028 US Presidential Election?

relationship_type: mutually_exclusive_category
shared_event: 2028_us_presidential_election
candidate_a: LeBron James
candidate_b: Democrats
YES prices: 0.0065 + 0.6050 = 0.6115
```

This is semantically interesting because both markets refer to outcomes inside the same broad 2028 presidential outcome space. But for the pairwise contradiction strategy, a trade would require a pair-level violation. For mutually exclusive outcomes, the pairwise overround trade is only interesting when:

```text
YES_A + YES_B > 1 + costs + edge
```

Here the pair sums to only `0.6115`, far below 1. There is no pairwise overround. The market is informative, but not executable as a two-market contradiction.

## Example 2: Low-Probability Candidate Pairs Generate Relationships, Not Trades

Another accepted relationship:

```text
Will Marco Rubio win the 2028 Republican presidential nomination?
Will Donald Trump Jr. win the 2028 Republican presidential nomination?

relationship_type: mutually_exclusive_category
shared_event: 2028_republican_presidential_nomination
YES prices: 0.2535 + 0.0235 = 0.2770
```

Again, the semantic structure is correct. Rubio and Trump Jr. cannot both win the same nomination. But their combined YES prices are nowhere near a pairwise contradiction. The model has learned something useful about the market universe, but the trade logic needs an actual overround or underround.

This is why “interesting relationship count” and “trade count” are not the same metric.

## Example 3: Category Bundles Are More Promising, But Completeness Blocks Simulation

The new category bundle scanner found three N-way outcome spaces:

```text
2028_democratic_presidential_nomination
candidate_count: 19
sum_yes_prices: 0.3240
known_total_candidates: not configured
status: analysis-only

2028_republican_presidential_nomination
candidate_count: 26
sum_yes_prices: 0.5060
known_total_candidates: not configured
status: analysis-only

2028_us_presidential_election
candidate_count: 38
sum_yes_prices: 1.9690
known_total_candidates: not configured
status: analysis-only
```

These are exactly the kind of structures we wanted to discover. The presidential election bundle is especially interesting because the observed YES sum is well above 1. But the scanner refuses to call this a hard arbitrage because the observed set is not known to be complete.

That is the right behavior. If we only see 38 candidate markets, but the true outcome space includes people or parties not present in the local dataset, then “buy all YES” or “buy all NO” is not guaranteed. The scanner is protecting us from mistaking a partial menu for an exhaustive market.

My opinion: this is the most promising next strategic direction, but it needs a curated completeness registry. The raw discovery is valuable. The missing ingredient is defensible exhaustiveness.

## Example 4: Some Accepted Relationships Are Blocked By Coverage

Four accepted relationships were not strategy eligible because coverage was too low. Example:

```text
Will LeBron James win the 2028 US Presidential Election?
Will Pete Hegseth win the 2028 US Presidential Election?

relationship_type: mutually_exclusive_category
shared_event: 2028_us_presidential_election
status: accepted but strategy-ineligible
reason: market_b coverage_score=0.56
```

This is not the main blocker, because 92 relationships did pass price-history and alignment gates. But it shows the system is still enforcing data quality before simulation.

My opinion: coverage gates are doing their job. I would not loosen them just to manufacture trades.

## Example 5: Temporal Relationships Are Interesting But Not Mature Enough Yet

The system found same-reference temporal structures:

```text
New Rihanna Album before GTA VI?
Will China invades Taiwan before GTA VI?

relationship_type: same_reference_clock
reference_event: gta_vi_release
validation_status: needs_manual_review
final_confidence: 0.42
```

And:

```text
Will bitcoin hit $1m before GTA VI?
Will China invades Taiwan before GTA VI?

relationship_type: same_reference_clock
reference_event: gta_vi_release
validation_status: needs_manual_review
final_confidence: 0.42
```

These examples show the system is correctly noticing a shared reference event: GTA VI release. But same-reference does not automatically imply a tradable relation. Two things can both be “before GTA VI” without one implying the other.

The current lake also has no populated v2 terms fields:

```text
event_atoms_json populated: 0
proposition_json populated: 0
outcome_space_json populated: 0
targeted_semantics_queue_rows: 152
```

My opinion: temporal strategy is still under-instrumented. We need the targeted v2 semantics run before temporal relationships can become a serious trading signal. Right now, they are useful research leads, not executable edges.

## Why We Are Not Making Trades

My diagnosis is:

1. The system is finding semantic structure, but most of it is not pairwise-tradable.
2. Pairwise contradiction is too narrow for the most interesting category information.
3. Category bundles are the right next shape, but the scanner is correctly refusing to trade incomplete or unknown outcome spaces.
4. The current universe is long-horizon politics-heavy, where prices are diffuse and many candidates are tiny-probability tails.
5. Temporal relationships need v2 event/proposition extraction before they can support implication-style trades.
6. The price data problem is no longer the primary blocker for pairwise. The latest pairwise run had aligned price series and still found zero violations.

Put simply: the research layer is producing a map, but the executable strategy layer is only allowed to fire when the map shows a locked-down, priced, complete, and mispriced structure. We are seeing structure, not yet arbitrage.

## My Recommendation

I would not loosen the validators to force trades. The zero-trade result is informative and mostly honest.

The next best move is to curate a small set of complete or near-complete outcome spaces and rerun category bundle analysis there. For example:

```text
Known-complete sports champion markets
Known-complete nominee/winner markets with official candidate lists
Threshold ladders with clear implication structure
Temporal before/after markets after v2 semantics are populated
```

For the current data, I would prioritize:

1. Add known-total metadata for categories only when we can defend the total.
2. Run terms-aware semantics on `data/backfill/targeted_semantics_queue_latest.csv`.
3. Build a curated universe around complete categories and threshold ladders, not random active markets.
4. Keep reporting incomplete bundles, but do not simulate them as hard arbitrage.

The system is not failing because it found nothing. It is refusing to confuse “interesting” with “tradeable.” That is exactly the line this project should hold.
