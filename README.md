# quant-betting

The goal is to buy probabilities for less than they're worth, and to be able to find out that we can't.
It's stdlib Python 3.9 only (no pip installs) with SQLite, and git is the audit trail.

## Runbook
```
python3 snapshot.py                 # pull Kalshi prices locally (Pinnacle comes from the cloud)
python3 cloud_sync.py               # pull Pinnacle rows from the Cloudflare D1 collector
python3 status.py                   # collector health: last ok, cadence, failures, gaps, STALE flag
python3 verify.py "Player|stat|line|SIDE" ... --payout X --slip-verified --starters-confirmed --stake N
python3 ledger.py add-pp SNAPSHOT_ID [--stake N]   # log from a frozen pre-bet snapshot
python3 postmortem.py BET_ID        # fixed-format postmortem vs the frozen snapshot
python3 registry.py                 # discover + match every sport (runs 05:00/15:00 via launchd)
python3 quality.py [TICKER]         # A-D market quality + research edge scan
python3 movement.py [TICKER]        # cross-source lead/lag
python3 market_only.py scan|settle|report   # MARKET_ONLY_v1 paper strategy (scan runs every 2 min, settle every 30)
python3 board.py                    # "run the board": Kalshi ask vs Pinnacle no-vig, after fees
python3 ledger.py add TICKER --stake 20 --mode paper
python3 ledger.py settle            # closing price, CLV, result, P/L
python3 ledger.py report            # dashboard
python3 backtest_nfl.py             # E1-E3
python3 kalshi_history.py fetch && python3 kalshi_history.py analyze   # H4
python3 odds.py                     # self-check of all price math
```

## Capability audit (verified 2026-09-27, not assumed)

**AVAILABLE NOW**
- Python 3.9 (stdlib only), Node, SQLite, git, and a bash shell on this Mac; files persist in `~/Projects/quant-betting`.
- Web search and page fetch; **YouTube transcripts** via `tools/yt_transcript.py`. I read transcripts, not video frames: charts shown on screen are invisible to me.
- **Kalshi public market data** (no key): markets, order books, series fee multipliers, settled results, and hourly candles back to Apr 2025 via `/historical`.
- **Pinnacle moneylines** via the public guest API its website uses (unofficial; can break or be rotated at any time).
- **nflverse** NFL results and closing lines 2006-present; **ESPN** scoreboards (DraftKings line); **MLB StatsAPI**.
- Browser automation (in-app browser and your Chrome).

**AVAILABLE WITH SETUP**
- **Always-on Pinnacle collection (DEPLOYED 2026-09-27)**: Cloudflare Worker `quant-betting-collector` (cron every minute, adaptive 2/5/10/30-min cadence per league, heartbeat per pull) writes to D1 `quant-betting`. Health: https://quant-betting-collector.<account>.workers.dev/ . Code: `cloud/`. Free plan.
- **Laptop collector** (launchd, every 2 min while awake): Kalshi (Kalshi blocks Cloudflare) + `cloud_sync.py` to pull the cloud rows into `data/market.db`.
- **PrizePicks lines**: `api.prizepicks.com` returns 403 to scripts (bot protection). Options: pull through a real browser session, or log lines by hand. There's **no historical PrizePicks line archive**, so PrizePicks modeling starts with forward collection.
- **Kalshi order placement**: needs your API key (RSA). Not wanted until a strategy passes validation.
- Paid odds history (The Odds API etc.): needs a key and money; not needed yet.
- numpy/pandas/scikit-learn/LightGBM: one `pip install` away; deliberately not used until a model needs them.

**NOT AVAILABLE**
- Me running on my own. Between your messages I don't exist. Anything "continuous" is the launchd job, not me. Re-run me before game time.
- Official injury/lineup feeds with historical timestamps (so no leak-free historical injury features yet).
- Sharp *opening* line history (nflverse is closing only).

## Architecture
```
snapshot.py ──► data/market.db:snapshots (raw, append-only, timestamped; last pre-start row = close)
                     │
data/raw/*  ──► backtest_nfl.py / kalshi_history.py ──► results/*.txt ──► EXPERIMENTS.md (pre-registered)
                     │
board.py (fair P from Pinnacle no-vig, range across devig methods; Kalshi ask + exact fee) ──► PASS / PAPER / BET
                     │
ledger.py ──► data/market.db:bets (pre-event fields locked by SQL triggers, no deletes) ──► settle ──► CLV, P/L ──► report
```
- Raw vs processed vs model output vs live market data are kept apart: `data/raw/` (downloads, never edited), `results/` (model output), `data/market.db` (live snapshots and ledger).
- **Model versions**: `MODELS.md`. **Experiments**: `EXPERIMENTS.md`. **Known failures**: `FAILURES.md`. **Research**: `research/NOTES.md`.

## Decision labels
- **PASS**: all-in cost ≥ the lower bound of fair P, or not enough depth at the ask.
- **PAPER**: clears the rule, but the strategy hasn't passed its pre-registered test. Log it; don't fund it.
- **BET**: only for a strategy that has passed its test in EXPERIMENTS.md. None have yet.

## Why Kalshi vs Pinnacle first (sport/market choice)
It ranked best on data quality, live access, modelability, execution, and testability:
free real-time prices on both sides, exact fees from the API, automatic settlement, and a benchmark (the sharp close) that makes CLV measurable within days, not seasons.
Building our own outcome model first (E2) showed public-data Elo loses to the NFL closing market, so the market itself is the model and we look for a *venue* that's priced worse than it.
