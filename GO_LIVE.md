# Promotion protocol: RESEARCH → PAPER → SHADOW → MICRO-LIVE → LIVE (frozen 2026-09-28, before any strategy has results)

Applies per strategy AND per sport (confidence never transfers between sports). No arbitrary N: every gate is an uncertainty statement.

| Stage | Money | Enter when | Leave (demote) when |
|---|---|---|---|
| RESEARCH | none | hypothesis registered in `research/registry.jsonl` with a primary metric and burned-window check passed | – |
| PAPER | none | pre-registration committed; code implements it; tests pass | – |
| SHADOW | none (orders computed at live prices incl. A2 book fill) | PAPER look #1: primary metric's clustered 95% CI lower bound > 0 at the per-look alpha, both chronological halves positive, missing closes ≤ 20% | any look where the clustered 95% CI upper bound < 0 → reject |
| MICRO-LIVE | real, SIZING_v1.0 with PER_POSITION_CAP 0.5% and TOTAL_OPEN_CAP 3% | SHADOW on data **after** the PAPER decision (fresh window): same test passes again; **and** survives Benjamini-Hochberg (q=0.10) over every confirmatory test in the registry; **and** calibration: hit rate within the 95% CI of mean close-fair P, per probability bucket with n ≥ 50; **and** ≥ 95% of intended entries were fillable (A2); **and** simulated max drawdown at the planned size < 25% of the allocated bankroll | realized CLV CI upper bound < 0; or drawdown > 2× simulated 95th percentile; or a data-integrity pause; or fills < 90% of intended |
| LIVE | SIZING_v1.0 full caps (2% / 3% / 10%) | MICRO-LIVE window (fresh again) with positive CLV (clustered CI lower > 0) **and** realized ROI's CI not below −(2 × fees) **and** model/strategy parameters unchanged throughout | same as MICRO-LIVE; any model change sends the strategy back to PAPER |

**Sample adequacy** is decided by the CI width, not N: a stage requires the clustered 95% CI half-width of the primary metric ≤ half the estimated edge.

## Thresholds (replacing the HEURISTIC A/B/C in v2; derived only from v1 discovery-half data)
required_edge(grade, horizon) = z × SD_edge(grade, horizon) + execution buffer, where
- SD_edge = √(close-drift SD(horizon)² + devig-method half-range² + model SD², the last only for model-based strategies),
- z = 1.28 (P(true edge > 0) ≥ 0.90),
- execution buffer = median (VWAP − snapshot ask) observed at A2 fills + 1 tick,
- grades with calibration error (hit rate vs close-fair) outside CI in discovery data → no deployment.
