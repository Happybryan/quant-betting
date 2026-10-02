-- Raw live market data. Append-only; one row per selection per pull.
CREATE TABLE IF NOT EXISTS snapshots (
  ts          TEXT NOT NULL,           -- UTC ISO time of the pull
  source      TEXT NOT NULL,           -- kalshi | pinnacle
  league      TEXT NOT NULL,
  event_key   TEXT NOT NULL,           -- kalshi event_ticker | pinnacle matchupId
  start_time  TEXT,                    -- scheduled start (UTC)
  selection   TEXT NOT NULL,           -- team name as the source spells it
  bid REAL, ask REAL,                  -- kalshi: dollars
  price REAL,                          -- pinnacle: American odds
  bid_size REAL, ask_size REAL,
  extra       TEXT                     -- JSON: ticker, fee multiplier, volume...
);
CREATE INDEX IF NOT EXISTS snap_key ON snapshots(source, event_key, ts);

-- Permanent betting ledger. Pre-event fields can never change; rows can never be deleted.
CREATE TABLE IF NOT EXISTS bets (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL, mode TEXT NOT NULL CHECK (mode IN ('paper','live')),
  platform TEXT NOT NULL, sport TEXT, league TEXT, game TEXT NOT NULL, start_time TEXT,
  market TEXT NOT NULL, selection TEXT NOT NULL, line TEXT, ticker TEXT,
  price REAL NOT NULL, stake REAL NOT NULL, contracts REAL, fee REAL,
  model_p REAL, p_low REAL, p_high REAL, market_p REAL, fair_price REAL,
  edge REAL, ev REAL, model_version TEXT NOT NULL, data_version TEXT,
  assumptions TEXT, sources TEXT,
  -- filled at/after settlement
  close_price REAL, close_fair_p REAL, clv REAL,
  result TEXT CHECK (result IN ('win','loss','push','void') OR result IS NULL),
  pnl REAL, settled_ts TEXT, notes TEXT,
  -- added DATA_v1.1 / VERIFY_v2.0 (2026-09-27)
  protocol TEXT, verify_version TEXT, exec_version TEXT, prebet_id INTEGER, prebet_sha TEXT,
  close_status TEXT,             -- ok | MISSING (collector gap before start) | n/a
  last_obs_ts TEXT, last_obs_fair_p REAL  -- labeled last observation when the close is MISSING; never used as CLV
);
CREATE TRIGGER IF NOT EXISTS bets_pre_event_immutable
BEFORE UPDATE OF ts, mode, platform, sport, league, game, start_time, market, selection, line, ticker,
  price, stake, contracts, fee, model_p, p_low, p_high, market_p, fair_price, edge, ev,
  model_version, data_version, assumptions, sources, protocol, verify_version, exec_version, prebet_id, prebet_sha ON bets
BEGIN SELECT RAISE(ABORT, 'pre-event ledger fields are immutable'); END;
CREATE TRIGGER IF NOT EXISTS bets_no_delete BEFORE DELETE ON bets
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;

-- Collector heartbeat: one row per run, so "no change" can be told apart from "not collected".
CREATE TABLE IF NOT EXISTS collector_runs (
  ts TEXT NOT NULL, league TEXT NOT NULL, source TEXT NOT NULL,
  ok INTEGER NOT NULL, rows_seen INTEGER, rows_written INTEGER, error TEXT, seconds REAL
);
CREATE INDEX IF NOT EXISTS runs_ts ON collector_runs(league, source, ts);

-- Frozen pre-bet snapshots: everything verify.py saw, hashed. Never updated, never deleted.
CREATE TABLE IF NOT EXISTS prebet_snapshots (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, versions TEXT NOT NULL, legs TEXT NOT NULL,
  payload TEXT NOT NULL, sha256 TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS prebet_immutable BEFORE UPDATE ON prebet_snapshots
BEGIN SELECT RAISE(ABORT, 'pre-bet snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS prebet_no_delete BEFORE DELETE ON prebet_snapshots
BEGIN SELECT RAISE(ABORT, 'pre-bet snapshots are append-only'); END;
