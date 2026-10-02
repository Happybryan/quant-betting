# Model versions

| Version | Status | What | Evidence |
|---|---|---|---|
| NFL_ELO_v1 | **Rejected** 2026-09-27 | 538-style Elo, K=20, HFA=55 (tuned on 2006-15), MOV multiplier, 1/3 season reversion | E2: loses to closing market, test ROI −8.1% |
| NFL_BLEND_v1 | **Rejected** 2026-09-27 | logistic(market logit, Elo logit), fit 2006-20 | E3: no out-of-sample gain over market |
| KALSHI_VS_PINNACLE_v0.1 | **Paper only** since 2026-09-27 | fair P = Pinnacle no-vig ML (multiplicative; lower bound = min with power devig); edge = fair P − Kalshi ask − fee/contract | H1 forward test pending |
| PP_PROP_v0.1 | Paper only since 2026-09-27 | leg fair P = Pinnacle no-vig at the same line | H5 pending |
| VERIFY_v1.0 | Active 2026-09-27 | multi-layer gate (VERIFY.md): Pinnacle + Kalshi + DK line, news, weather, environment, 2 models, movement, correlation, stress test | tested on 5 legs (outputs in results/verify/) |
| ENV_PASSTD_v0.1 | **Not validated (H7)**, info only | λ = implied team pts × league pass TDs/pt (0.0648, 2025 REG), Poisson | Poisson fit verified on 2025 |
| STAT_PASSTD_v0.2 | **Not validated (H7)**, info only | player pass TDs per implied point (last 17 games, ≥15 att) shrunk k=6 to league, × tonight's implied pts, Poisson | hyperparameters unvalidated |
| PP_PROP_v0.2 | Active 2026-09-27 | leg fair P = consensus (Pinnacle no-vig + Kalshi mid); conservative = worst of (power-devig low, Kalshi bid/ask side) | FORWARD_TEST.md |

## Decision-system versions (recorded on every bet)
| Component | Version | What changed | Why |
|---|---|---|---|
| Data | DATA_v1.0 → **DATA_v1.1** | `collector_runs` heartbeat; adaptive cadence (2/5/10/30 min by time to next game); Kalshi NFL prop ladders; change-only props | "not collected" was indistinguishable from "unchanged" (fabricated CLV, FAILURES.md) |
| Verification | VERIFY_v1.0 → **VERIFY_v2.0** | 18-item gate, data-quality grade A/B/C/F, frozen hashed pre-bet snapshot, re-verify on movement, collector-health gate, price path + gap reporting | directive 2026-09-27; history model must run BEFORE the bet |
| Data | DATA_v1.1 → **DATA_v1.2** | Pinnacle collected 24/7 by a Cloudflare Worker + D1 (change-only + "gone" rows), `cloud_sync.py`; Kalshi local only | the laptop slept through every kickoff on 2026-09-27; Kalshi blocks Cloudflare |
| Data | DATA_v1.2 → **DATA_v1.3** | cloud collects Pinnacle game lines for every sport (sport-level, 2/3-way); laptop collects Kalshi for every registry-matched series (41); job-level health HEALTHY/DELAYED/STALE/FAILED/RETIRED; registry refresh 05:00/15:00 | directive Phase 2 |
| Quality | new **QUALITY_v0.1** | market-quality grade A-D = worst of 6 dimensions; required edge by grade (provisional) | directive 16; thresholds to be fixed in MARKET_ONLY_v1 |
| Movement | new **MOVEMENT_v0.1** | Pinnacle-move → Kalshi follow/lead/no-follow + lag, with timing resolution | directive 18-19; measurement only |
| Strategy | new **MARKET_ONLY_v1** (pre-registered, commit 9246342, before code) | paper; gate of 14 checks; edge ≥ 1.5/3/5pp by grade A/B/C; ENTRY + unfiltered OBSERVATION; EV@close primary; 4 looks at α=0.0125; Holm per sport | directive Phase 3 |
| Sizing | new **SIZING_v1.0** | quarter Kelly on conservative P; caps 2% position / 3% event / 10% open; real stake 0 until the strategy's go-live rule passes | directive 28-29 |
| Execution | none → **EXEC_v1.0** | one OFFICIAL SLIP, alternatives never shown with it; AWAITING USER CONFIRMATION until the screenshot matches | backup leg was read as a 3-leg entry |

Changing a version means a new row with: what changed, why, expected benefit, and test results. Versions are never edited in place.
