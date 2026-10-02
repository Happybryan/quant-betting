# CLV definition (frozen 2026-09-28; changes require a new version of this file)

| Platform / strategy | "Entry" | "Close" | Validity | Stored |
|---|---|---|---|---|
| Kalshi MARKET_ONLY | all-in cost per contract: snapshot ask + taker fee (100-lot); A2 also stores the live-book VWAP | **Pinnacle** multiplicative no-vig P of the same outcome at the **last complete Pinnacle pull at or before the scheduled start** | a successful run of that Pinnacle job (sport job, or `SPORT:Soccer|<league>`) within **15 min before start**, else CLOSE MISSING | `close_fair`, `close_ts`, `close_source`, `ev_close` = close_fair − cost |
| Kalshi (secondary) | same | Kalshi **mid** (bid+ask)/2 from the last local Kalshi pull ≤ start | pull within 15 min before start, else NULL | `kalshi_close_mid`, `kalshi_close_ts` |
| PrizePicks legs (ledger / copy / controls) | Pinnacle no-vig P at logging (PrizePicks has no observable price) | Pinnacle no-vig P for the same player/stat/line at the last prop change ≤ start | a successful `<league>/pinnacle_prop` run within 15 min before start; if Pinnacle's line moved away from the entry line, the close is **LINE_CHANGED** (not comparable), never interpolated | `close_fair_p`, `close_status`, `last_obs_*` |
| Scheduled start | Pinnacle `startTime` (UTC) | – | Kalshi's `occurrence_datetime` is NOT a start time (≈ start + 3h) | – |

Missing closes are counted and reported, never filled in. A test with > 20% missing closes pauses.
