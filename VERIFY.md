# Pre-bet verification protocol (VERIFY_v2.0)

`python3 verify.py "Player|stat|line|MORE/LESS" ... --payout X --entry power [--slip-verified] [--starters-confirmed]`
Each run is saved to `results/verify/<UTC timestamp>.txt`.

## Automated (runs every time)
| Layer | Source | Notes |
|---|---|---|
| Event not started | Pinnacle start time | started = REJECT |
| Price #1 + no-vig | Pinnacle props | raw implied, overround, multiplicative no-vig, power-devig low bound |
| Price #2 | Kalshi player-prop ladders ("N+") | mid = P(MORE); conservative = bid (MORE) / 1 − ask (LESS) |
| Line consensus | PrizePicks vs Pinnacle vs DraftKings (via ESPN; line + open line only, no prices) | outliers flagged |
| Consensus / conservative P | mean of priced sources / worst of (power-devig low, Kalshi conservative) | sources are never averaged across disagreement > 5pp without a flag |
| Market quality | Pinnacle limit, Kalshi volume, source count | HIGH / MEDIUM / LOW |
| Injuries | ESPN game injury report (player + skill/OL teammates) | |
| Weather / venue | ESPN game info | warn at gust ≥ 20, precip ≥ 50%, ≤ 25°F |
| Game environment | Pinnacle spread + total → implied team total | |
| ENV model | implied team pts × league pass-TDs-per-point (2025 REG), Poisson | pass TDs only |
| STAT model | STAT_v0.2 (pass TDs): player TDs per implied point, shrunk, × tonight's implied pts; v0.1 (receptions): per-game rate, no matchup | disagreement > 5pp → bear case |
| Movement | our collector's Pinnacle history; DraftKings open → current line | against us > 2pp → bear case |
| Correlation | same Pinnacle game id | same game → REJECT (no joint model yet) |
| Sensitivity / stress | break-even per leg from the ACTUAL payout; EV at consensus, conservative, −2/−4/−6pp | EV < 0 at −4pp → FRAGILE |

## Manual gates (a bet can't reach Level A without them)
1. **Slip screenshot**: exact line, MORE/LESS selectable, standard (no goblin/demon icon), entry type, payout multiplier. → `--slip-verified --payout X`
2. **Starters**: official NFL inactives (~90 min before kickoff). → `--starters-confirmed`
3. **Stale line**: re-read the PrizePicks board right before entry; if any leg's line changed, re-run.

## Levels
- **REJECT**: any hard fail, same-game legs, or conservative P ≤ break-even.
- **C (no money)**: any manual gate unresolved.
- **B (no money)**: gates resolved, but warnings, open bear-case items, fragility, or margin < 3pp.
- **A (deployable)**: everything resolved, no open contradiction, conservative P ≥ break-even + 3pp, robust to −4pp.

Unresolved STAT-model disagreements keep a leg at B. The two models are there to catch problems, not to be averaged together.

## VERIFY_v2.0 additions
- **Gate (18 items)**: MARKET VERIFIED, PAYOUT VERIFIED, MULTI-BOOK, NO-VIG, NEWS, STARTER/ROLE, WEATHER, GAME-ENVIRONMENT, STATISTICAL MODEL (team total), MARKET MODEL, HISTORICAL MODEL (player), MARKET MOVEMENT, CORRELATION, BEAR CASE, SENSITIVITY, CURRENT PRICE REFRESH, DATA COLLECTOR HEALTHY, PRE-BET SNAPSHOT SAVED. Any ✗ means no deployment.
- **Data quality**: A / B / C / F (see `data_grade` in verify.py). OFFICIAL requires A.
- **Frozen pre-bet snapshot**: every run writes all inputs, model outputs, checks, versions and args to `prebet_snapshots` (DB triggers block updates and deletes) plus `results/prebet/<id>_<sha>.json`. Ledger entries reference the snapshot id and sha.
- **Re-verification**: if a leg's Pinnacle P moved > 2pp since the last snapshot of the same leg, the old analysis is flagged STALE and this run is the re-verification at the current price.
- **Collector health**: verification fails if NFL Pinnacle or Kalshi-prop data is STALE (`python3 status.py`).
- **OFFICIAL SLIP** is printed only if every leg is LEVEL A and `--stake` is given, then `STATUS: AWAITING USER CONFIRMATION OF ACTUAL SLIP`.
