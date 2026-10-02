# HANDOFF: read this first in any new session (updated 2026-09-29 morning)

Bryan's rules: address him as **"Bryan"** at the start of every reply. Paper only; real money $0 until a strategy passes GO_LIVE.md. He will ask **"you learn anything?"**: that means settle and review (below). Give exact instructions, one slip, no backups.

## Immediate next step (was blocked by a transient tool error at the end of the last session)
Settle Monday Night Football 2026-09-28 (PHI @ CHI, kickoff 00:15Z) and report:
```bash
cd ~/Projects/quant-betting && python3 cloud_sync.py; python3 prop_settle.py 20260928; python3 daily_paper.py settle; python3 market_only.py settle; python3 daily_paper.py report; python3 market_only.py report
```
Then grade and compare (H10 v2): copy legs (`copy_evals` rows with reason 'priced at the PP line%', p_consensus = market P at the PP line) vs `copy_controls` (market_p) via `prop_settle.outcome(con, player, stat, '20260928')` + `prop_settle.grade(value, line, side)`. Report hit rate vs mean market P for each group; closing-line values were already measured before kickoff: copy legs +0.40pp mean (7 beat / 2 tied / 3 lost, n=12), controls +0.08pp (n=35). Also grade: demo_parlays #1 (built on lines PP didn't offer, flagged), daily_paper 2026-09-28 (PRIZEPICKS_2PICK is a flagged whole-number-line bug; PRIZEPICKS_2PICK_CORRECTED = Raymond LESS 24.5 rec yds + Ertz MORE 10.5 rec yds; KALSHI_SINGLE = Anadolu Efes YES @ 0.51, EuroLeague). stale_legs: 0 detected.

## What exists (all in git, stdlib Python 3.9, data in data/market.db)
- Collection: laptop launchd every 2 min (`snapshot.py --with-pinnacle` Kalshi for 41 series + Pinnacle fallback for the 6 US leagues, `cloud_sync.py`, `market_only.py scan`, `stale_scan.py`, `status.py --alert`); Cloudflare Worker `quant-betting-collector` + D1 `quant-betting` (Pinnacle all sports; **keeps dying on the free-plan 10 ms CPU limit**, so every non-NFL sport goes stale); registry job 05:00/15:00 (`registry.py`, `prop_settle.py`, `daily_paper.py settle`, `daily_paper.py pick` at 15:00, drift on the 1st).
- MLB props (since 2026-09-29): `pp_board.py --sport baseball_mlb`, settled via statsapi.mlb.com in `prop_settle.py`, in the daily pick from 2026-09-30 (DAILY_PAPER v1.1).
- PrizePicks lines: The Odds API (key in git-ignored `.env`, free 500 credits/month, 60-credit floor; about 479 left on 2026-09-28). PrizePicks' site and app are blocked for me (safety restriction); community posts only via Bryan's screenshots (AirDrop → ~/Downloads) → `copybets.py add/pool/verify`.
- Decision tools: `verify.py` (VERIFY_v2.1, 18-item gate, SGP same-game joint), `quality.py` (A-D + edge uncertainty), `compare.py`, `market_only.py`, `pp_board.py`, `stale_scan.py` + `stale_followup.py` (survival at +0/1/2/5/10 min), `daily_paper.py`, `ledger.py`, `postmortem.py`, `simulate.py`, `calibration.py`, `sgp.py`, `research_log.py`.
- Tests: `python3 -m unittest test_engine` (all passing at last run).

## Results so far (details in EXPERIMENTS.md)
E1-E3 NFL Elo/market: rejected. H4/H4b Kalshi favorites: no edge; YES < 0.20 loses ~33% (hard ban). H7/H8 NFL pass-TD models: not validated, no info beyond Kalshi. H9 NBA props: Test A not validated (MORE bias); **Test B: blend adds info for AST/REB/3PM (game-clustered) but betting loses → info-only**. MARKET_ONLY_v1: 0 entries (Kalshi ≈ Pinnacle). H12 stale PP lines: 0 detected (sampling-limited). PrizePicks same-line props sit at 50-52% vs Pinnacle.

## Open tasks, in priority order
1. Settle MNF + report (above).
2. Pre-register NBA_PROP_v0.2 (calibration fix; dispersion/mean re-estimated on TRAIN/VALIDATE only) before the NBA season (~Oct 20); test only on 2026-27 REG data.
3. Price PrizePicks line gaps via Kalshi ladders (currently "better but unpriced").
4. Collect Sunday's full NFL slate for H12 (watch credits).
5. Pending Bryan decisions: Cloudflare Workers Paid $5/mo (fixes cloud CPU), GitHub for always-on Kalshi, Odds API $30 plan (only if H12 shows executable stale lines).
6. Refactor NFL logic from verify.py into adapters.NFLAdapter (low priority).
