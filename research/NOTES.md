# Research library: claims → methods → verdicts

Transcripts: `python3 tools/yt_transcript.py VIDEO_ID` writes to `research/videos/`.
YouTube is a source of hypotheses; data decides.

## Assigned videos (all four transcripts pulled and read, 2026-09-27)

| Video | Claim / method | Reproducible? | Verdict |
|---|---|---|---|
| Wagered On Tilt: *Sports Modelling Python Monte Carlo* (0WE4WOFgT-o) | Simulate QB yards/attempt 10,000× from Normal(mean, sd) of past games; report the average. | Yes, trivially. | **No predictive value.** Simulating a normal and taking the mean just returns the input mean. There's no opponent, no regression to the mean, and no price comparison. The author also says not to use it against a book. Keep only the mechanics: Monte Carlo is useful for *distributions of non-linear things* (parlays, correlated legs, bankroll paths), not for recovering a mean. |
| sentdex: *Monte Carlo Simulation and Python 3 - Simple Bettor* (1TgOQvZ88hw) | 100 bettors at a 49% win rate over 100 then 10,000 bets. Identical strategies show wildly different short-run results, and most go broke long-run. | Yes. | **Keep as a principle:** short-run records (including YouTube "70% winners") are dominated by variance. It justifies the n ≥ 100 and CI requirements in EXPERIMENTS.md and CLV as a faster signal. Build a bankroll-path simulator once there's a validated edge to feed it; before that it simulates garbage. |
| Wagered On Tilt: *Sports Wagering Model Monte Carlo 2* (YwDaYCPw9lE) | Adjusted score = (team off / league avg) × (opp def / league avg) × league avg, split home/away; simulate each team's score from an independent Normal; P(win) = share of sims. | Yes. It's the multiplicative-strength idea behind Poisson/Dixon-Coles. | **Weak as given.** There's no backtest, no comparison to prices, independent scores (ignores shared pace/game script), no shrinkage of small-sample averages, and no out-of-sample test. Its core, team strength from ratings, is what E2 tested with Elo, and **Elo lost to the NFL closing market** (EXPERIMENTS.md). A better-specified version (shrunk ratings, a Poisson/NB goal model for NHL/soccer) is a legitimate future challenger but must beat the market in the same harness. |
| OddsJam: *Closing Line Value* (3-UycYYSxNA) | Devig Pinnacle; a bet is good if its price beats the Pinnacle no-vig *closing* line; track % of bets beating the close. EV = p·profit − (1−p)·stake. | Yes. EV math verified in `odds.py` self-check (their Blue Jays −155 @ 63.45% example: +4.4%). | **Adopted**, with a caveat: it's a sales video for their tool. Pinnacle no-vig as fair price is now `KALSHI_VS_PINNACLE_v0.1`; CLV vs Pinnacle close is in `ledger.py settle`. Unverified in the video: their record, sample size, and whether their books limited them. |

## Academic / technical sources (checked 2026-09-27)

- **Bürgi, Deng & Whelan (2025), *Makers and Takers: The Economics of the Kalshi Prediction Market*** ([paper](https://www.karlwhelan.com/Papers/Kalshi.pdf), [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5502658)). Over 300k Kalshi contracts: prices are informative and sharpen toward close, but there's a **favorite-longshot bias**. Low-price contracts lose heavily and high-price contracts earn small positive returns; the bias is **much worse for takers than makers**. → Tested directly as **H4**. Also implies we should test resting maker orders (lower fee coefficient 0.0175 vs 0.07).
- **Walsh & Joshi (2024), *Machine learning for sports betting: should model selection be based on accuracy or calibration?*** ([arXiv](https://arxiv.org/abs/2303.06021)). Selecting models on calibration rather than accuracy produced far better betting returns, and Kelly only works with a calibrated model. A corrigendum fixed feature-engineering errors, but the authors say the conclusion stands. Their headline ROIs come from one NBA setting and shouldn't be taken as transferable. → We select on log loss / calibration, never accuracy.
- **Hubáček, Šourek & Železný (2019), *Exploiting sports-betting market using machine learning*, IJF 35(2)** ([PDF](http://ida.felk.cvut.cz/zelezny/pubs/ijf.2019.pdf)). An accurate model that is *correlated with the bookmaker's* still loses to the margin; profit requires decorrelating from the book. → Explains E2/E3: Elo is mostly a noisier copy of the market. Any future model should be judged on the information it adds *beyond* the market (the E3 blend coefficient), not on standalone accuracy.
- Kalshi fee formula: taker `ceil(0.07·M·C·P·(1−P))`, maker coefficient 0.0175, M per series from `GET /series/{ticker}` (`fee_multiplier`). Sources: [Kalshi fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf) (PDF not machine-readable from here), consistent third-party write-ups ([pm.wiki](https://pm.wiki/learn/kalshi-fees-explained)), and the live API multiplier.

## Hypothesis backlog (untested, in rough priority)
1. Kalshi maker orders: does resting at bid+1¢ (lower fee) earn positive returns against the Pinnacle close, net of adverse selection? Needs forward order data.
2. Kalshi reaction lag: does Kalshi lag Pinnacle after news (lineups, injuries)? Needs higher-frequency snapshots near game time.
3. MLB starting-pitcher model (strikeout props): only after PrizePicks line history exists (see README, PrizePicks access).
4. NHL/soccer Poisson / Dixon-Coles with shrinkage, as a market challenger.

## 2026-09-30: Bryan's real PrizePicks payouts (screenshot, $10 Bonus Lineup, MLB)
- 2-pick Power = **2x** (not the 3x assumed in DAILY_PAPER_v1 / pp_board BREAKEVEN_2); 2-pick Flex = 1x (2/2), 0.05x (1/2).
- At 2x the per-leg break-even for a 2-pick Power is 0.5**0.5 = **70.7%** (vs 57.7% at 3x). Standard-line PP legs sit at 50-55% vs Pinnacle, so real-money 2-picks are deeply negative for him.
- DAILY_PAPER_v1 keeps its pre-registered 3x assumption (no retroactive change); any v2 must use his actual payout table (get 3-6 pick Power/Flex payouts from a screenshot).
- 3-pick (same screenshot flow): Power **5x**; Flex 3/3 = 2x, 2/3 = 0.05x. Power 3-pick per-leg break-even = 0.2**(1/3) = 58.5%.
- CORRECTION (Bryan, 2026-10-01): PP payouts are NOT fixed per pick count. They vary by lineup (players/lines picked); he has had a 3-leg at 10x. The 2x/5x seen 2026-09-30 are that lineup's payouts only. REAL_PAYOUT in daily_paper.py is a default; for real/promo slips use the multiplier the app shows for that exact lineup (screenshot before submit).
- Candidate hypothesis (NOT registered yet): legs that raise the lineup multiplier may be overpaid vs their sharp-market probability. Needs per-lineup payout data (screenshots only; the Odds API doesn't expose it).
- 2026-10-01 builder screenshots (H13 data): Yastrzemski MORE 0.5 TB + Whiteheart LESS 0.5 rec = 2-pick Power 3x / Flex 2x,0.5x. Adding Whiteheart Anytime TD (demon) to that = 3-pick 29.5x (PP prices TD and 0-catch as independent; true joint ~0.2%). Yaz MORE + Whiteheart TD demon = 2-pick Power 14x / Flex 8x,0.75x (sharp: 0.532 x Kalshi TD 0.065 = 3.5% -> EV 14x = -51%).
- "Guarantee Pick: Your Choice" promo: pick a popular projection lowered to 0.5 (e.g. Rodgers 0.5 pass attempts), +1 more pick; $25 max entry; expires 11:59pm ET same day. Near-lock leg -> lineup EV ~ P(other legs) x payout.

## 2026-10-01: exploratory backtest of all forward data 09-27..10-01 (explore_since_start.py)
- Pinnacle MLB props are well calibrated (n=617, Brier 0.18): HR props priced 13.3% hit 13.1%; total bases 50.7% vs 52.2%. Validates Pinnacle as the "truth" anchor.
- NFL props (one Sunday + MNF, n~600, game-clustered): within noise overall. Receptions Over -4.6 to -10.8pp; QB Overs +9 to +17pp on n=30 (few games: likely noise).
- Kalshi NFL ladders (n~520): YES rungs at 5-35% hit +1.5 to +3pp above mid, the OPPOSITE of the game-market longshot loss (H4b). Fees and the ask/mid gap eat part of it.
- PrizePicks standard lines: MORE hit 29/64 = 45%.
- Settlement gap: ESPN NFL box omits zero-stat players, so 0 catches can't be told from inactive. Fix before H15 can be scored.
- Registered H14/H15/H16 as forward tests from 2026-10-02 (discovery window burned).
- Settlement fix (same day): NFL players with didNotPlay=False on ESPN's game roster are zero-filled for receiving/rushing stats; DNP players stay missing (= PrizePicks void). Re-settled 09-27/09-28. Discovery number for H15 resolves to receptions Over 0.414 vs 0.491 (-7.7pp, n=162). Still discovery data only.
