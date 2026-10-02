# Statistical validity audit #1 (2026-09-28)

Question: could this research program falsely convince us we found an edge? Short answer: **yes, in several specific ways**. Some are now fixed; the rest are listed with the risk each one creates.

## Evidence produced by the audit
- **Reproducibility:** E1-E3, H4, H4b, H7 and H8 re-run from local raw data reproduce the published outputs **byte-for-byte** (commit c2a6675; output sha256 b357c0ed / 8e73c667 / 79ee9bc8 / 326a0271 / cf5f441d; seeds fixed in code). Caveat: raw data was not versioned → now hashed in `research/data_manifest.json` (detects change, cannot restore it).
- **Scale of search (research/registry.jsonl):** 15 hypotheses, 47 confirmatory tests, ~250 exploratory cells examined, 26 parameter settings searched. **Benjamini-Hochberg (q=0.10) over all 5 edge-claim tests: none survives.** No edge has been found, and none is being claimed.
- **Effective samples:** H4: 6,551 contract-sides = 3,310 events / 163 dates. H4b: 13,737 = 6,883 events / 362 dates. H8: 984 props = 61 kickoff slots (betting arm: 167 bets in 38 slots). H9-A: 9,095 player-games (36,380 prop observations) = 429 games / 492 players / 92 dates.
- **Clustering changes:** H8 M1 betting ROI CI [−25.8, +32.9] → **[−29.3, +45.7]** clustered. H9-A design effects 1.1-1.4. No conclusion changed (all were already "not validated").
- **Edge uncertainty:** Pinnacle fair drift to close has an SD of 1.1-2.0pp (315 events, provisional). A 1.5pp "grade A" edge has P(true > 0) ≈ 0.77-0.87, which is an **upper bound** because Pinnacle's own error is excluded.
- **Latency:** 20 upward Pinnacle moves with a later Kalshi quote → **0** left a fillable positive edge after fees.

## CRITICAL (could manufacture a false edge)
1. **Pinnacle is both the yardstick and the referee.** Entry fair and close are both Pinnacle, so "positive CLV" presumes Pinnacle's close is unbiased. *Partial fix:* Kalshi's own close is recorded; the go-live rule also requires outcome calibration. *Open:* no independent third sharp source.
2. **Edge thresholds were heuristic and looser than measured uncertainty.** *Fixed for future versions:* labeled HEURISTIC, uncertainty recorded per candidate, derivation method frozen (GO_LIVE.md). v1 is unchanged (no retroactive edits).
3. **Unlimited re-tries.** Failing v1 and registering v2, v3... would eventually "work" by chance. *Fixed:* every test goes in the append-only registry (edits are detected), and any promotion must survive BH across **all** confirmatory tests ever run.
4. **Execution realism in history.** H4/H8/H9-B used hourly candle bid/ask with no depth. *Fixed forward:* A2 requires a live-book VWAP fill. *Historical:* unfixable, but the bias runs against us (it would only make those negative results worse).

## MAJOR
5. Correlated props treated as independent (H8, H9-A). *Fixed:* clustered re-analysis; A1 makes event-clustered CIs primary for MARKET_ONLY.
6. Burned data windows: NFL 2006-2025 lines, nflverse 2025-26 wk2, Kalshi game winners Apr 2025 → Sep 27 2026, Kalshi pass TDs Dec 2025 → Sep 2026, ESPN NBA 2025-26 incl. playoffs. *Fixed:* `research_log.py burned` reports conflicts. *Open:* scripts don't call it automatically yet.
7. Pre-registration deviations (all disclosed): H4 partial peek; H4b written after H4; H7 post-game attempts filter; H8's 212 non-random exclusions; H9 dispersion method not in the pre-reg text; MARKET_ONLY Kalshi refresh plumbing.
8. Sample selection by laptop uptime (Kalshi can't be collected from the cloud); 3 of 5 early observations lack closes (outage).
9. Unversioned matcher edits. *Fixed:* config hash in every version string.
10. Copy-bet test measured win rate, not incremental information. *Fixed:* H10 v2 logistic test with randomized same-game controls.

## MINOR
11. Drift SD comes from one day of data. 12. One matcher audit only (0/100). 13. Fee modeled at a 100-lot, not the actual stake. 14. Move-timing resolution median 180 min. 15. NBA injury timestamps unknown (unused). 16. PrizePicks SHADOW tier uses a hypothetical 3x break-even. 17. Cross-game PrizePicks independence ignores shared shocks (weather, league news).

## Fixes still required
- Auto-invoke `burned()` inside every experiment script; refuse to run on a burned window.
- Kalshi collection on a non-Cloudflare always-on host (removes the laptop-uptime selection).
- Outcome-level calibration of Pinnacle close in OUR universe (settled OBSERVATIONS) before trusting Pinnacle as truth per sport.
- Re-estimate drift SD monthly; move it into versioned config.
- A universal simulation engine for drawdown (needed by the MICRO-LIVE gate).
- PrizePicks payout capture per slip (manual today); a same-game correlation model (same-game legs are rejected today).
