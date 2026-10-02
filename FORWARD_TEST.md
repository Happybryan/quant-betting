# Forward test rules, FIXED 2026-09-27 before the next board

These definitions are committed before any bet under them. Classification is written at entry time and never changes after a result.

## Active versions
Model `PP_PROP_v0.2` · Data `DATA_v1.1` · Verification `VERIFY_v2.0` · Execution `EXEC_v1.0` (see `versions.py`).
A version change starts a new cohort. Cohorts are always reported separately and never merged after the fact.

## What counts
| Class | Definition (decided before the event) | Ledger |
|---|---|---|
| **OFFICIAL** | Every leg is **LEVEL A** in a VERIFY_v2.0 pre-bet snapshot (data quality A, all 18 gate items ✓, conservative P ≥ break-even + 3pp, robust to −4pp), logged with `ledger.py add-pp` before the first leg starts, and the slip screenshot matches the OFFICIAL SLIP | mode paper during phase 1, stake as shown |
| **SHADOW** | Legs that pass every *automated* check (data quality B only because the slip/starters aren't confirmed), logged before start for research | mode paper, stake 0 |
| **PASS** | Everything else. Still recorded automatically: every verify run is frozen in `prebet_snapshots` | none |
| **PRE_VERIFICATION_PROTOCOL** | Entry #1 and legs #2-4 (2026-09-27). Permanent. Shown in ALL SYSTEM RECOMMENDATIONS, never deleted, never re-scored | as logged |

## Required data quality
OFFICIAL: A. SHADOW: B. C/F: never logged as a bet.

## Evaluation (no magic sample size)
- **Primary metric: CLV** = Pinnacle no-vig closing P − entry P, only where the collector covered the 15 min before start. Missing closes are counted and never filled in.
- Checkpoints every **25 legs with a valid close**. At each one, report mean CLV with a 95% CI, calibration (hit rate vs mean model P, CI), and ROI (secondary; it needs far more data).
- Legs needed to detect a true mean CLV of δ with 80% power: n ≈ (2.8·σ/δ)², with σ estimated from our own CLV data at each checkpoint (e.g. σ = 3pp and δ = 1pp gives ~71). This is recomputed, not assumed.
- **Go-live (real money) requires:** mean CLV 95% CI lower bound > 0, AND the hit rate's 95% CI includes the mean model P, AND ≤ 20% missing closes.
- **Stop rule:** at any checkpoint with ≥ 50 valid closes, if the mean CLV 95% CI upper bound < 0, the strategy is rejected.
- If more than 20% of closes are missing, the test is **paused** as a data-integrity failure until the collector is fixed.

## Capital
No real money and no scaling until the go-live rule passes. Current evidence of a live edge: **none**.

## Amendment 2026-09-28 (before any SHADOW/OFFICIAL entry exists)
- SHADOW = verify level "LEVEL B - SHADOW": every automated check ok, no bear-case item, not fragile, conservative P ≥ hypothetical 2-pick 3x break-even (0.577) + 3pp. Only the human gates (slip, line type, payout, stale line, starters) are pending.
- Eligible stats until more models are validated: **pass TDs only** (the only stat with both an environment model and a matchup-aware history model). Receptions and yards legs are ineligible: HISTORICAL MODEL ✗.
