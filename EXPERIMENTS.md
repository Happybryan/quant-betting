# Experiment log

Rules: the hypothesis, test and decision rule are written BEFORE the test runs, and committed to git.
Results are appended below, never edited into the hypothesis. The git history is the audit trail.

---

## E1-E3 · NFL moneyline: market calibration, Elo baseline, blend challenger
- **Written:** 2026-09-27, before first run. Code: `backtest_nfl.py`. Output: `results/nfl_backtest.txt`.
- **Hypotheses.** E1: devigged NFL closing moneylines are miscalibrated (favorite-longshot bias).
  E2: a tuned 538-style Elo (NFL_ELO_v1) predicts as well as the closing market. E3: a logistic blend of market plus Elo (NFL_BLEND_v1) beats the market out of sample.
- **Rationale.** Elo is the standard public baseline. If it can't beat the close, more features on the same public box-score data probably can't either.
- **Data.** nflverse `games.csv`, 1999-2026. Closing moneylines exist from 2006; 1999-2005 is Elo warm-up only.
  Limitation: the price source is not a single sharp book, and there are no opening lines, so this only tests *closing* prices.
- **Test.** Splits fixed in advance: train 2006-15, validate 2016-20, test 2021-25 (evaluated once). Metrics: log loss, Brier, paired bootstrap CI vs market, and a 1-unit betting sim at the actual closing price (vig included), broken out by estimated-edge bucket.
- **Decision rule.** A model is kept only if its test log loss beats the market (95% CI excludes 0) AND its betting-sim ROI is positive and rises with the edge bucket.
- **Result (2026-09-27).**
  - E1: calibration slope 1.024 (95% CI ±0.091) on 2006-20. **No detectable favorite-longshot bias** in NFL closing moneylines. The test-period buckets are all within noise.
  - E2: Elo loses to the market in every period (test log loss 0.6411 vs 0.6108; diff +0.030, CI [+0.020, +0.042]). Betting Elo "edges" at the close: test ROI **-8.1%** over 1,185 bets, validate -6.1%. Bigger estimated edges did NOT earn more; the 8%+ bucket lost -8.2%.
  - E3: the blend puts weight 0.12 on Elo (CI crosses 0) and doesn't beat the market out of sample (Δlogloss +0.0008, CI [-0.0007, +0.0024]). Test betting ROI -6.5%.
- **Decision.** Reject NFL_ELO_v1 and NFL_BLEND_v1 as betting models. The closing NFL moneyline beats public Elo. Any NFL edge has to come from *timing* (beating the price before it closes) or from venues priced worse than the sharp close, not from Elo on box scores.

---

## H1 · Kalshi game-winner asks vs Pinnacle no-vig (KALSHI_VS_PINNACLE_v0.1), forward test
- **Written:** 2026-09-27, before any positions.
- **Hypothesis.** When the Kalshi taker ask plus fee sits below Pinnacle's no-vig probability (using the *lower* of the multiplicative and power devig estimates), the position has positive expected value. Evidence of that: positive CLV against the Pinnacle no-vig close, and outcomes in line with Pinnacle's probabilities.
- **Rationale.** Pinnacle is widely treated as the sharpest moneyline (see research/NOTES.md). Kalshi is a separate order book with retail flow, so the two can drift apart.
- **Test.** Paper-log every PAPER signal from `board.py` via `ledger.py add --mode paper`, then `ledger.py settle`.
  Evaluate at n ≥ 100 settled paper positions: mean CLV vs Pinnacle close, ROI with CI, and hit-rate vs mean model P.
- **Decision rule.** Go live at small size only if mean CLV > 0 with the 95% CI excluding 0. Otherwise reject.
- **First observation (2026-09-27 11:35 UTC, one snapshot, 52 matched sides).** Kalshi tracked Pinnacle within about 1-4pp on every side. After taker fees, 0 of 52 sides cleared the rule, and the best was +0.4pp raw with its lower devig bound ≤ 0. One snapshot proves nothing; this needs the scheduled collector.

---

## H4 · Favorite-longshot bias on Kalshi sports game-winner contracts
- **Written:** 2026-09-27, before running `kalshi_history.py`.
- **Hypothesis.** Following Bürgi, Deng & Whelan (2025), Kalshi game-winner YES contracts bought at the pre-game taker ask show a favorite-longshot bias: contracts at ask ≥ 0.70 win more often than ask + fee requires, and contracts ≤ 0.30 win less.
- **Why it matters.** It directly tests the "bet the favorite as a safety net" idea on the venue we'd actually use.
- **Data.** Every settled market (historical + recent endpoints) in KXMLBGAME, KXNBAGAME, KXNHLGAME, KXNFLGAME, KXWNBAGAME, KXNCAAFGAME. Raw candles cached in `data/raw/kalshi_candles/`.
- **Entry price (leak-free).** The yes_ask close of the last hourly candle ending ≤ occurrence_datetime − 4h. occurrence_datetime is about start + 3h, so entry is at least ~1h before first pitch or kickoff. Markets with no ask in [occ − 28h, occ − 4h] are excluded. Results other than yes/no (ties, voids) are excluded.
- **Costs.** Taker fee 0.07 × M × P × (1 − P) per contract (100-lot amortized; M = the series' *current* fee_multiplier, an assumption because historical multipliers may have differed). Depth is unknown from candles, so fills are assumed at the ask.
- **Test.** ROI after fees by ask bucket (<0.20, 0.20-0.30, …, ≥0.80) and by sport. Split chronologically by market date: first half = discovery, second half = confirmation.
- **Decision rule.** A "buy YES in bucket B" rule advances to paper trading only if ROI after fees is > 0 in BOTH halves AND the pooled 95% CI lower bound is > 0. Otherwise it's rejected. Note that both sides of a game appear in the sample (not independent), so reported CIs are somewhat too narrow; the check will cluster by event.
- **Result (2026-09-27, `results/h4_kalshi_flb.txt`).** 6,551 contract-sides / 3,310 events (Apr-Sep 2026; MLB 4,194, NCAAF 1,067, WNBA 688, NHL 278, NBA 164, NFL 160). Older markets lack `occurrence_datetime`, so the spec excludes them (see H4b).
  - Favorites: 0.70-0.80 went −7.1% (discovery) and +5.8% (confirmation), pooled +0.9% [−5.9, +7.9]. 0.80-1.00 went −5.6% / +0.8%, pooled −0.3% [−3.5, +3.0]. **Fails**: not positive in both halves, and the CI includes 0.
  - Middle 0.30-0.70: every pooled bucket −1.7% to −5.1%, which is about the fee plus spread.
  - Longshots: <0.20 pooled **−32.4%** [−58.2, −4.2]; 0.20-0.30 **−20.9%** [−40.1, −0.2]. Both CIs exclude 0. This reproduces the taker longshot losses Bürgi, Deng & Whelan report.
- **Decision.** **Reject** "buy the favorite at the ask" as a strategy: after fees it's roughly break-even, not an edge. **Adopt a hard rule:** never buy Kalshi YES below 0.30 at the ask without an independent fair price showing value. Taker longshots lose 20-30%.

---

## H4b · Replication of H4 on older Kalshi markets (untouched data)
- **Written:** 2026-09-27, AFTER H4's result, BEFORE fetching any of this data.
- **Data.** Settled markets in the same six series that lack `occurrence_datetime`: roughly Apr 2025 through early 2026, and none of them used in H4.
- **Entry price.** yes_ask close of the last hourly candle ending ≤ `expected_expiration_time` − **5h** (1h more margin than H4, because the start offset on these older markets is unverified). Markets with no ask in [anchor − 29h, anchor − 5h] are excluded.
- **Test and decision rule.** Same buckets, fees and event-clustered CI as H4. H4b confirms a bucket's H4 sign only if its pooled 95% CI excludes 0 in the same direction. A favorite bucket would earn paper trading only if H4b's lower bound is > 0, which H4 already makes unlikely.

- **Process note (2026-09-27).** analyze was run once on a partial, buggy dataset (MLB Jul-Sep 2026 only, 1,884 sides) before the two parser bugs above were found. It showed no bucket passing. The decision rule and buckets are unchanged; the official result is the full-data run below.
- **Result (2026-09-27, `results/h4b_kalshi_flb.txt`).** 13,737 contract-sides / 6,883 events, Apr 2025 - Apr 2026, none of them in H4. (The results file's header wrongly says "4h before occurrence_datetime"; the code actually used expected_expiration_time − 5h. The label is fixed in code.)
  - Favorites: 0.70-0.80 pooled **−3.1%** [−6.9, +0.6]; 0.80-1.00 **−1.0%** [−3.4, +1.4]. **Confirms H4: no favorite edge.**
  - Longshots <0.20: **−33.9%** [−47.4, −18.5]. **Confirms H4.** 0.20-0.30: −3.9% [−15.8, +7.7], does **not** confirm H4's −20.9%.
  - Not pre-registered, flagged as data-mined: 0.40-0.50 went +2.4% / +3.1% across the two halves (pooled +2.7% [−0.9, +6.4]) while 0.50-0.70 went about −7.7% (CI excludes 0). That hints short underdogs were underpriced in 2025. **But H4's later 2026 data shows 0.40-0.50 at −3.3%**, so it didn't persist out of time. Not pursued; it's logged only so nobody "rediscovers" it later without that context.
- **Decision.** The favorite rule stays rejected (two independent samples). **Hard rule: never buy Kalshi YES under 0.20 at the ask** (confirmed twice). 0.20-0.30 is a caution zone, not banned. Caveat: older, thinner markets may have had wider asks, so the candle close ask can overstate cost versus a patient limit order.

---

## H5 · PrizePicks standard legs vs Pinnacle no-vig props (PP_PROP_v0.1), forward test
- **Written:** 2026-09-27, before the first logged entry.
- **Hypothesis.** When a PrizePicks standard leg's line exactly matches Pinnacle's line and Pinnacle's no-vig probability for our side is ≥ 0.60 (2-pick breakeven is 0.577 at 3x), legs hit at roughly Pinnacle's probability, so 2-pick entries earn positive ROI.
- **Test.** Log every qualifying leg (paper or live) before start. Per leg, record hit/miss plus the Pinnacle closing no-vig probability (CLV = close P − entry P). Evaluate at n ≥ 100 legs: hit rate vs mean model P, mean CLV with CI, and entry ROI.
- **Decision rule.** Keep if mean CLV ≥ 0 and the hit rate's 95% CI includes the mean model P, with the lower bound > 0.577. Stop if the hit rate's upper 95% bound drops below 0.577.
- **Known hazard.** The PrizePicks lines are user-transcribed, and payout, goblin/demon status and LESS availability aren't verified by me.
- **Amendment (2026-09-27, before any H5 result):** only legs that reach **Level A in `verify.py`** count as H5 entries from now on. Entry #1 (logged before VERIFY existed) stays in the ledger but is tagged under-verified and reported separately.
- **Entry #1 settled (2026-09-27, paper, pre-VERIFY, excluded from H5):** Jones LESS won (1 TD), Brissett LESS lost (2 TDs on 48 attempts, SF 29-27 shootout), entry lost (−$10 paper). CLV: Jones 0.0pp, Watson 0.0pp, Brissett **−3.2pp**, entry −2.1pp. The one leg that VERIFY later flagged twice (market moved against it; history model −13pp) is the one that lost. n = 1, logged as an observation only, not evidence.

---

## H6 · Does pre-entry market movement predict CLV and outcomes?
- **Written:** 2026-09-27, prompted by one observation (Brissett −3.2pp). One observation is not evidence, so **no decision rule is changed** by this.
- **Hypothesis.** Legs whose Pinnacle no-vig P for our side moved against us between first observation and entry perform worse after entry: lower CLV (entry → close) and a hit rate below model P.
- **Data.** Every OFFICIAL and SHADOW leg with a valid close. Movement = entry P − first-observed P (collector), bucketed: > +5, +2..+5, 0..+2, ≈0 (|Δ| < 0.5pp), 0..−2, −2..−5, < −5 (pp). Velocity (pp/h) and the time of the largest step are stored in the pre-bet snapshot's price path.
- **Catalysts.** `postmortem.py` checks ESPN team news in the movement window. Moves are labeled INFORMATION (verifiable catalyst) or UNKNOWN. A reason is never invented.
- **Test.** Per bucket: N, hit rate vs mean model P, mean CLV (CI), ROI. First look at 100 valid-close legs, no earlier.
- **Decision rule.** Add a movement filter only if the adverse buckets (0..−2, −2..−5, < −5 combined) show mean CLV below the ≈0 bucket with a 95% CI excluding 0 *and* the same sign in both chronological halves.

---

## H7 · Validation of the NFL pass-TD models (ENV_PASSTD_v0.1, STAT_PASSTD_v0.2)
- **Written:** 2026-09-28, before `backtest_passtd.py` exists.
- **Question.** Do the two pass-TD models used as verify-gate checks predict P(pass TDs ≥ 2) better than a naive baseline, and are they calibrated?
- **Models (parameters fixed in advance, nothing tuned on the test data).**
  - M0 baseline: Poisson with λ = the prior season's league mean pass TDs per qualifying QB game.
  - M1 ENV: λ = implied team points (nflverse closing spread/total) × the prior season's league pass-TDs-per-point.
  - M2 STAT_v0.2: the player's pass TDs per implied point over his last 17 prior qualifying games (any season), shrunk with k=6 to the prior-season league rate, × implied points.
- **Data.** nflverse weekly stats 2024-2026 + games.csv. Test population: every 2025 REG and 2026 QB game with ≥ 15 attempts (disclosed limitation: ≥ 15 attempts is known only after the game, so injury exits are excluded).
- **Metrics.** Log loss and Brier on the outcome TDs ≥ 2; paired bootstrap CI vs M0; calibration buckets (predicted vs actual, CI).
- **Decision rule.** A model is **VALIDATED** as a gate check only if its log loss beats M0 (paired 95% CI excluding 0) AND no calibration bucket with n ≥ 50 misses by more than its 95% CI. An unvalidated model is shown as information only and can never make HISTORICAL/STATISTICAL MODEL pass the verify gate.
- **Not tested here:** accuracy vs the betting market. There are no historical pass-TD prop prices in our data yet.
- **Result (2026-09-28, `results/h7_passtd.txt`).** 605 test games, base rate TDs≥2 = 0.448.
  - M1 ENV: log loss 0.6626 vs M0 0.6887 (diff −0.026, CI [−0.039, −0.014]), so it beats the baseline, but calibration bucket 0.5-0.6 (n=101) predicted 0.535 vs actual 0.634, outside the CI. **NOT VALIDATED.** It under-predicts high-total games (a possible v0.2 direction: pass-TD rate per point rising with the total; not acted on).
  - M2 STAT_v0.2: diff −0.015, CI [−0.037, +0.006], not better than the baseline; calibration OK. **NOT VALIDATED.**
- **Decision.** Both models are information-only in `verify.py` (`VALIDATED` map). No pass-TD leg can reach LEVEL A or SHADOW until a model passes a new pre-registered test. Next test: vs **Kalshi historical KXNFLPASSTDS markets** (812, Nov 2025-Jan 2026), the first test against actual market prices.

---

## H8 · NFL pass-TD models vs the market (Kalshi KXNFLPASSTDS)
- **Written:** 2026-09-28, before any Kalshi pass-TD price history was downloaded.
- **Question.** Do ENV_PASSTD_v0.1 (M1) or STAT_PASSTD_v0.2 (M2) add information beyond the market price, and would betting their disagreements with the market have been profitable after fees?
- **Data.** Every settled KXNFLPASSTDS market (historical + recent endpoints; about 1,217 with a yes/no result, Dec 2025 → Sep 2026; "scalar" settlements excluded). Rung N comes from `yes_sub_title` ("Name: N+"), with P(YES) = P(TDs ≥ N). Kickoff comes from nflverse `games.csv` (ET gameday + gametime), matched on the ticker's date and teams; the player is matched to nflverse by name among that game's teams (unmatched → excluded and counted).
- **Market price (leak-free).** The last hourly candle ending ≤ kickoff − 1h, within [kickoff − 25h, kickoff − 1h], with both bid and ask. Market P = (bid + ask) / 2.
- **Model P.** Exactly the H7 code (walk-forward; each game uses only prior data plus its closing spread/total). Poisson P(≥N).
- **Primary test (rung 2+ only).** Log loss of the market mid vs a fixed pre-specified blend: logit(p_blend) = 0.75·logit(market) + 0.25·logit(model) (no fitting). Paired bootstrap 95% CI. The model **adds information** only if the blend beats the market with the CI excluding 0.
- **Betting test (all rungs).** Buy YES at the ask if model P − ask − fee ≥ 0.03; buy NO at (1 − bid) if (1 − model P) − (1 − bid) − fee ≥ 0.03. Kalshi taker fee (series multiplier, 100-lot). ROI with a 95% CI; by rung; chronological halves.
- **Decision rule.** A model becomes a **market-validated** gate check only if the primary test passes AND the betting test's ROI CI lower bound is > 0 with ≥ 50 bets AND both halves are positive. Otherwise the NFL pass-TD models stay information-only, full stop.

---

## H9 · NBA player-prop models (NBA_PROP_v0.1): build, validate, test vs outcomes and vs Kalshi
- **Written:** 2026-09-28, before any NBA data was downloaded.
- **Data.** ESPN box scores for the 2025-26 NBA season (regular season + playoffs): minutes, PTS, REB, AST, 3PM per player-game, plus ESPN `pickcenter` pregame spread/total. Disclosed limitations: no reliable pre-game injury/lineup timestamps (the box-score `starter` flag is post-game, so **unused**); injuries are therefore not modeled in v0.1.
- **Splits (chronological, fixed now).** TRAIN = season start → 2025-12-31 (league rates, dispersion). VALIDATE = 2026-01-01 → 2026-02-28 (choose between the two candidates below, once). TEST = 2026-03-01 → end of playoffs (touched once).
- **Candidates (walk-forward; each prediction uses only games before it).**
  - B0 baseline: season-to-date mean of the stat, negative binomial with train-period dispersion.
  - C1 minutes model: projected minutes = 0.8^k-weighted mean of the last 10 played games; per-minute rate = 0.9^k-weighted over the last 20 played games, shrunk to the league per-minute rate with 100 minutes of prior weight. **Minutes uncertainty propagates:** minutes ~ Normal(proj, max(3, recent SD)) truncated at 0, 200 fixed-seed draws, stat | minutes ~ NegBin(mean = minutes × rate, train dispersion).
  - C2 = C1 × game environment: mean scaled by (pregame implied team points / team's season-to-date points per game), PTS only; REB/AST/3PM unscaled.
- **Model selection.** On VALIDATE only: the candidate (C1 or C2) with the lower PTS log loss at the line floor(mean) + 0.5 becomes NBA_PROP_v0.1. One choice, recorded before TEST is opened.
- **Test A (vs outcomes, TEST period).** For PTS, REB, AST, 3PM at lines = floor(v0.1 mean) + 0.5: log loss vs B0 (paired bootstrap CI) and calibration buckets.
- **Test B (vs market).** Kalshi historical NBA player-prop markets (points and any other stats present) that fall in TEST: the same design as H8 (pre-tip price = last hourly candle ≤ tip − 1h; blend 0.75·market + 0.25·model; betting test with edge ≥ 3pp after fees; halves).
- **Decision rule.** NBA_PROP_v0.1 becomes an **outcome-validated** gate check for a stat if Test A beats B0 (CI excluding 0) with no calibration bucket (n ≥ 100) outside its CI. It becomes **market-validated** (allowed to support LEVEL A) only if Test B's blend beats the market (CI excluding 0) AND betting ROI CI lower bound > 0 with ≥ 50 bets AND both halves positive. Anything less: information-only.
- **Result (2026-09-28, `results/h8_passtd_market.txt`).** 984 usable markets (excluded: 212 no player match, mostly playoff games that the pre-registered H7 code (REG only) does not cover; 17 no game; 4 no candle; 5 scalar). Primary (2+, n=244): market 0.6736, M1 alone 0.6739, blends not better than market (M1 diff −0.0023 CI [−0.0078, +0.0028]; M2 +0.0003). Betting: M1 +3.5% ROI CI [−25.8, +32.9], halves opposite in sign; M2 −12.3%.
- **Decision.** Both stay INFORMATION-ONLY. Finding: the one-line team-total model matches the market's accuracy alone, but adds nothing beyond it. No NFL pass-TD edge from these models.
- **H9 Test A result (2026-09-28, `results/h9_testA.txt`, TEST Mar 1 → Jun 14 2026, n=9,095 per stat).** Model = C1 (VALIDATE PTS log loss 0.6853 vs C2 0.6870; choice committed f344a59 before TEST).
  PTS −0.072 vs B0 (CI [−0.083, −0.061]), REB −0.022, AST −0.025, all better than baseline; 3PM +0.012 (worse). **Calibration misses in every stat**, with a consistent upward bias on MORE (e.g. AST 0.3-0.4 predicted 0.361 vs actual 0.310). **Decision: NOT VALIDATED for any stat**; NBA_PROP_v0.1 is information-only. Diagnosis for a v0.2 (to be pre-registered, re-estimated on TRAIN/VALIDATE only): the dispersion/mean is too optimistic on MORE (dispersion from actual-minutes pairs understates variance, or the projected mean runs high in the playoffs).

---

## H10 · Do PrizePicks copy/community entries carry information? (Bryan's hypothesis)
- **Written:** 2026-09-28, before any copy-bet data exists. Source: Bryan's experience that copy bets have been his best results. **Treated as a hypothesis, not a fact.**
- **Collection.** Bryan supplies screenshots/text of PrizePicks community entries (the API and PrizePicks site are blocked for this system). Every entry is broken into legs via `copybets.py`. Recorded: legs, lines, sides, line types, payout, copies/popularity, creator, timestamp.
- **COPY_CONSENSUS_SCORE** (not a probability): log2(1 + distinct entries containing the leg) + log2(1 + distinct creators). Stored separately from model and market probabilities.
- **Every leg is verified independently** (verify.py for NFL; market-only for others) and frozen in a pre-bet snapshot, flagged COPY_DERIVED.
- **Comparison.** COPY-DERIVED legs vs all other verified legs from the same period (verified via shortlist/verify): mean CLV (Pinnacle close − entry fair), hit rate vs mean market P, and ROI where bet.
- **Decision rule.** Copy consensus gets a quantitative role only if, at ≥ 100 copy-derived legs with a valid close, copy-derived legs show higher mean CLV than the rest (difference CI excluding 0) OR hit above their market P (CI excluding 0). Otherwise copy bets stay a discovery sensor with no special weight. Popularity never overrides a gate result.
- **H10 AMENDMENT v2 (2026-09-28 statistical audit; before any H10 leg has settled).** The win rate of copy legs is not the test. Primary test: logistic regression of the leg outcome on logit(Pinnacle no-vig P at entry) + COPY indicator (+ COPY_CONSENSUS_SCORE as a secondary term), over copy legs plus a **control group** of 3 randomly chosen non-copied Pinnacle props per copy leg from the same game, sides randomized (seeded, frozen in `copy_controls`). Copy information has incremental value only if the COPY coefficient > 0 with a 95% CI excluding 0, **game-clustered**, at ≥ 100 settled copy legs. Also reported: CLV of copy vs control legs.

---

## H12 · Stale PrizePicks lines after sharp moves
- **Written:** 2026-09-28, before any stale-line data exists (1 PrizePicks pull total).
- **Question.** When the sharp market moves materially while PrizePicks hasn't moved, does entering the stale PrizePicks side have positive out-of-sample EV after real-world constraints?
- **Universe.** Every PrizePicks STANDARD prop (Odds API `us_dfs`) with a Pinnacle prop for the same player/stat. NFL now; NBA from its season start. Goblins/demons excluded (different payouts).
- **Signal (decided at detection time t).** The PrizePicks line has been unchanged since before the sharp move, and the sharp probability of one side **at the PrizePicks line** has risen by ≥ Δ since PrizePicks last changed that line. P at the PP line: Pinnacle no-vig if Pinnacle's line equals PP's; otherwise the Kalshi ladder rung at the PP line; otherwise unpriceable (logged, excluded).
- **Entry (paper).** The side whose P at the PP line is ≥ 0.577 + 0.03 (2-pick Power 3x break-even plus margin; payout unverified → paper only).
- **Real-world constraints.**
  1. Detection latency = time from the first Pinnacle observation of the move to the PP pull that confirms PP unchanged.
  2. **Executability:** the leg counts only if the next PrizePicks pull, at least 10 minutes later (manual entry time), still shows the same line; otherwise it is logged NOT_EXECUTABLE.
  3. Credits: PP polled every 20 min within 3h of kickoff, never below a 60-credit floor. Missed opportunities are expected and not imputed.
  4. Same-game legs are never paired without SGP_CORR_v1.
- **Primary metric (fast).** EV@close per executable leg = Pinnacle close P of our side at the PP line − 0.577, game-clustered CI. **Secondary (slow, the truth check).** Hit rate vs mean close P and vs 0.577, game-clustered, once n allows. Also detection latency distribution and executable share.
- **Δ threshold.** Discovery half: Δ ∈ {2, 3, 4, 6}pp examined; ONE value is chosen there. Confirmation half: that Δ only.
- **Decision.** Positive only if, in the CONFIRMATION half: EV@close clustered CI lower bound > 0 at ≥ 50 executable legs, executable share ≥ 80%, and the hit rate is not significantly below mean close P. The claim must also survive BH (q=0.10) over the registry. Required n for the outcome check is reported, not assumed (≈1,000 legs to detect a 4pp hit-rate edge).
- **H12 AMENDMENT 1 (2026-09-28, before ANY stale leg was detected; 0 rows).**
  - **Executability is a survival curve, not a single 10-minute rule.** For every detection, `stale_followup.py` re-checks the exact actionable opportunity (same player, stat, line, standard market) at detection, +1, +2, +5 and +10 min (1 Odds API credit per check). Target vs actual check time, availability, the observed line and the sharp P of our side at that moment are logged permanently (`stale_survival`, immutable). Output: "% of detected stale opportunities still executable after X minutes."
  - **Sharp-move timing.** Stored per detection: the move window (last props pull showing the old price → first pull showing the new one) and the detection latency bounds (lower = detect − window end, upper = detect − window start).
  - **Three separate measurements, never combined:** (1) sharp/closing-line value = Pinnacle close P at the PP line − P at detection; (2) estimated EV per leg at each realistic entry moment = sharp P of our side at +1/+2/+5/+10 min − 0.577, counted only if the opportunity was still available at that moment; (3) realized hit rate from ESPN box scores. **Positive (1) alone is not evidence of positive PrizePicks EV.** Only (2) at executable moments, corroborated by (3), speaks to that.
  - **Sampling caveat.** Detection is limited by polling (PrizePicks every ~20 min near kickoff; Pinnacle props every 2-5 min; laptop-dependent). "0 stale lines detected" means 0 were OBSERVABLE at this sampling frequency, not that 0 existed; short-lived opportunities are systematically under-sampled, so the survival curve is conditional on being detected.
  - The executability decision threshold in the original H12 (≥ 80% executable) is evaluated at **+2 min and +5 min** from this curve; no other H12 threshold changes. **No H12 threshold changes after meaningful data accumulates.**
- **H9 Test B result (2026-09-28, `results/h9_testB.txt` + game-clustered re-check `results/h9_testB_clustered.txt`).** 39,959 Kalshi NBA prop markets (Mar-Jun 2026, ~300 tip-offs). Blend (0.75 market + 0.25 model) beats the market in log loss for **AST, REB, 3PM** (game-clustered CIs exclude 0: AST −0.0064 [−0.0088, −0.0042], REB −0.0052 [−0.0072, −0.0030], 3PM −0.0029 [−0.0048, −0.0010]); PTS no. **Betting test fails for every stat** (edge ≥ 3pp after fees): AST −8.2% [−14.1, −1.6], PTS −7.6% [−12.3, −3.1], REB −5.2% [−10.3, +0.5], 3PM −2.3% [−9.8, +6.1]; halves inconsistent.
- **Decision (pre-registered rule): NOT market-validated for any stat → information-only.** Finding worth recording: the model carries information the market does not fully price (AST/REB/3PM), but its MORE bias (Test A) makes its raw disagreements lose money. A calibrated v0.2 is the justified next step; it must be pre-registered now and tested only on 2026-27 regular-season data (the 2025-26 windows are burned).
