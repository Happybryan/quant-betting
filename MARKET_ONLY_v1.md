# MARKET_ONLY_v1: pre-registration

**Written and committed 2026-09-28, before `market_only.py` exists and before any forward result has been observed.**
The git commit containing this file is the timestamp. Nothing below may change during v1. A change means MARKET_ONLY_v2 with a new cohort.

## What it is
A **market-pricing strategy, not a sports prediction model.** Hypothesis: when a Kalshi outcome's taker ask plus fee is below the most conservative no-vig Pinnacle probability for an economically equivalent outcome, by at least a quality-dependent margin, the position has positive expected value. Measured primarily by **EV at close** and secondarily by realized P/L.

## Versions
Strategy `MARKET_ONLY_v1` · Registry `REGISTRY_v1.0` · Matcher `MATCH_v1.0` · Rules `RULES_v1.0` · Quality `QUALITY_v0.1` · Data `DATA_v1.3` · Exec: paper, taker fill at the snapshot ask.

## Eligible universe (decided per candidate at scan time)
Every Kalshi GAME/MATCH-winner outcome whose event is in the registry with:
- match confidence ≥ 90 (MATCH_v1.0 AUTO_THRESHOLD),
- rules equivalence EXACT or NEAR_EQUIVALENT (tennis is therefore excluded: MATERIAL_DIFFERENCE),
- the Kalshi outcome mapped one-to-one to a Pinnacle outcome (Tie ↔ Draw) with the full Pinnacle outcome set present.
There is no sport or league cherry-picking: every league meeting these conditions is in, including ones added later by the registry.

## Gate (all must pass, else PASS)
| # | Check | Rule |
|---|---|---|
| 1 | EVENT MATCH VERIFIED | confidence ≥ 90 and not ambiguous |
| 2 | MARKET EQUIVALENCE VERIFIED | EXACT or NEAR_EQUIVALENT |
| 3 | RULES VERIFIED | same as 2, Kalshi rules parsed from this event's own contract text |
| 4 | CURRENT PRICES | Kalshi pull for the league ≤ 5 min old; Pinnacle sport job HEALTHY |
| 5 | VIG REMOVED | fair = min over {multiplicative, power, Shin} ("low fair") |
| 6 | SOURCE QUALITY ACCEPTABLE | Pinnacle limit dimension not D |
| 7 | MARKET QUALITY ACCEPTABLE | QUALITY_v0.1 grade A, B or C (D = never) |
| 8 | LIQUIDITY | Kalshi ask size ≥ 100 contracts (the paper stake) |
| 9 | SPREAD | Kalshi spread dimension not D |
| 10 | MOVEMENT CHECKED | Pinnacle 6h range dimension not D; range recorded |
| 11 | EXECUTION COSTS | taker fee for 100 contracts at the ask, series fee multiplier from the API |
| 12 | EDGE SURVIVES CONSERVATIVE | low fair − all-in cost ≥ required edge for the grade: **A 1.5pp · B 3.0pp · C 5.0pp** |
| 13 | COLLECTOR HEALTHY | as 4 |
| 14 | PRE-BET SNAPSHOT SAVED | row + full JSON + sha256 stored before the event, immutable |
| – | Timing | 0.25h ≤ time to start ≤ 168h |

## Entries and observations
- **ENTRY** (the strategy): the first scan at which an outcome passes the gate. At most one entry per Kalshi market, ever. Paper stake: 100 contracts at the ask.
- **OBSERVATION** (research, not bets): every eligible outcome with two-sided prices, recorded once at the first scan within 0.25–1.0h before start, **regardless of edge**. This unfiltered sample tests whether estimated edge predicts EV at close and results (directive 56), without the selection bias of only logging qualifiers.

## Settlement
- **Close** = Pinnacle low fair from the last complete pull at or before start, valid only if the Pinnacle sport job had a successful run within 15 minutes before start. Otherwise **CLOSE MISSING** (never filled in).
- **EV at close** = close fair (multiplicative) − all-in entry cost per contract. Primary metric.
- **Result** from Kalshi's own settlement (yes / no / settlement value), so P/L = payout × 100 − cost.

## Evaluation (fixed in advance)
- **Primary test:** pooled mean EV at close over ENTRIES with a valid close, one-sided, H1: mean > 0.
- **Looks:** at 50, 100, 200 and 400 valid-close entries. Each look uses α = 0.0125 (Bonferroni over 4 looks) to control peeking.
- **Discovery / confirmation:** entries sorted by entry time and split into halves. A claim needs the same sign in both halves.
- **Per-sport / per-league claims:** exploratory only unless significant after **Holm correction** across all sports (or leagues) with ≥ 20 entries.
- **Calibration:** realized win rate vs mean close fair, with a 95% CI, overall and per grade.
- **Edge vs outcome** (from OBSERVATIONS): slope of EV at close on estimated edge; edge buckets 0-2, 2-4, 4-6, 6-8, 8+pp.
- **Go-live eligibility:** at a look, primary test significant **and** both halves positive **and** ≤ 20% of entries with a missing close **and** calibration CI covers the mean close fair. Even then only small real stakes, and only in sports that individually pass Holm.
- **Stop rule:** at any look with ≥ 100 valid-close entries, if the 95% CI upper bound of mean EV at close is < 0 → **reject v1**.
- **Data integrity pause:** if > 20% of closes are missing at a look, evaluation pauses until collection is fixed.

## What would change it
Only a new version (v2), written before being run. Allowed input for re-estimating thresholds: the **discovery half** of v1 data only.

## Amendments (statistical audit, 2026-09-28, made while 0 ENTRIES existed)
- **A1 Clustering.** The primary CI is **event-clustered** (outcomes of one event are dependent). Looks and alphas are unchanged.
- **A2 Execution.** A gate-passing outcome becomes an ENTRY only if the **live Kalshi order book** fills 100 contracts and the edge at the VWAP (plus fee) still meets the grade's required edge. The book is stored in the payload.
- **A3 Uncertainty.** Every assessment records edge SD (close-drift SD by horizon ⊕ devig-method half-range), a 90% interval and P(edge > 0) (an upper bound: Pinnacle's own error vs truth is not included). Recorded only; not a v1 gate.
- **A4 Thresholds.** A 1.5 / B 3 / C 5 pp are **HEURISTIC**. With measured drift SD 1.1-2.0pp, a 1.5pp edge has P(true > 0) ≈ 0.77-0.87. v2 must derive thresholds (see GO_LIVE.md §Thresholds) from the v1 **discovery half** only.
