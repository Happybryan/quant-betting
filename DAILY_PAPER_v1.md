# DAILY_PAPER_v1: rules (fixed 2026-09-28, before the first pick)

**Purpose:** a daily, fully logged track record of the system's best-available picks. It is **not** a strategy test and can never be used for a go-live decision (GO_LIVE.md governs that). Its own scorecard only.

## Every day at 15:00 ET (launchd), for events starting in the next 24h
1. **PrizePicks 2-pick (paper, 1 unit).** Pull the real PrizePicks board (Odds API `us_dfs`, standard lines; NFL now, more sports as pricing paths exist). For each prop, P(side) is the sharp probability **at the PrizePicks line** (Pinnacle no-vig if its line equals PP's, else the Kalshi rung at the PP line; otherwise unpriceable). Pick the **two highest-P legs**; if both are in the same game, the joint uses SGP_CORR_v1 at the worse end of its band (unmodeled pairs are skipped). Payout **assumed 3x (2-pick Power)** and labeled unverified. Est. EV = joint × 3 − 1.
2. **Kalshi single (paper, 100 contracts).** Among registry-matched outcomes (confidence ≥ 90, rules EXACT/NEAR) with FRESH data, quality grade A-C and 0.25-24h to start: the **highest edge** = conservative fair (min of the devig methods) − (ask + taker fee).
3. **Both are logged even when EV < 0** (flagged NEGATIVE_EV). If no candidate exists: a NO_PICK row with the reason.

## Settlement and scorecard
PrizePicks legs: ESPN box scores (prop_settle.py). Kalshi: its own settlement. Close: Pinnacle at the PP line / Pinnacle no-vig for the Kalshi outcome (15-min coverage rule, CLV_DEFINITION.md). Reported: picks, W-L, paper P/L, mean est. EV, closing value, hit rate vs mean P. Rows are immutable pre-event; results append only.

## Amendment v1.1 (written 2026-09-29, effective with the 2026-09-30 15:00 pick; no result of any MLB prop seen before writing)
- PRIZEPICKS_2PICK candidate pool = NFL legs + MLB legs (Odds API `baseball_mlb`, us_dfs: total bases, home runs, pitcher strikeouts, pitching outs).
- MLB legs priced exactly like NFL: Pinnacle no-vig at the SAME line only; whole-number lines unpriceable (push).
- Same-game MLB pairs are skipped (no validated MLB correlation model); cross-game pairs are treated as independent.
- MLB settlement: statsapi.mlb.com final box scores. A player with no appearance has no stat row and the leg stays unsettled (PrizePicks voids it).
- Everything else in v1 unchanged. Separate scorecard; never go-live evidence.

## v2 addition: PRIZEPICKS_REAL (written 2026-10-01 01:15 ET, first pick 2026-10-01 15:00; v1 rows unchanged)
- Same priced legs as v1 (Pinnacle no-vig at the same line; whole-number lines unpriceable). One leg per game (independence).
- Candidates: 2-pick Power at **2x** and 3-pick Power at **5x**: Bryan's actual payouts (screenshot 2026-09-30).
- Choose the size with the higher EV; the bigger slip only if its EV beats the smaller by >= 0.05 per $1.
- Settled with the real payout. This is the slip Bryan uses for free Bonus Lineups.

## v3 addition: BET_OF_THE_DAY (written 2026-10-02 ~02:40 UTC, first pick 2026-10-02 15:00 ET; earlier kinds unchanged)
- Exactly one pick per day, any sport: highest EV per $1 among Kalshi singles (all registry-matched sports; YES ask >= 0.20 only, H4b ban) and the best PrizePicks Power slip (pp_best, real payouts).
- Labels: REAL BET = EV > 0 AND Kalshi grade A/B AND P(edge > 0) >= 0.80. CHECK PAYOUT = PrizePicks slip with EV > 0 at the default payout: Bryan screenshots the builder, real only if the shown multiplier beats `breakeven_x`. PAPER PICK = everything else (logged and scored, never bet).
- Runner-ups (top 3) stored in the payload. Settled like its venue. Scored by sport in the report.
