-- Cloud mirror of the local collector tables (DATA_v1.2). Every source is change-only; a row with extra.gone=true
-- marks a market that disappeared. collector_runs proves coverage.
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, source TEXT NOT NULL, league TEXT NOT NULL, event_key TEXT NOT NULL,
  start_time TEXT, selection TEXT NOT NULL, bid REAL, ask REAL, price REAL, bid_size REAL, ask_size REAL, extra TEXT
);
CREATE TABLE IF NOT EXISTS collector_runs (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, league TEXT NOT NULL, source TEXT NOT NULL,
  ok INTEGER NOT NULL, rows_seen INTEGER, rows_written INTEGER, error TEXT, seconds REAL
);
CREATE INDEX IF NOT EXISTS runs_league ON collector_runs(league, ok, ts);
-- change-detection state: last stored value per market
CREATE TABLE IF NOT EXISTS latest (
  source TEXT NOT NULL, league TEXT NOT NULL, event_key TEXT NOT NULL, selection TEXT NOT NULL,
  k TEXT NOT NULL, start_time TEXT, ts TEXT NOT NULL, PRIMARY KEY (source, event_key, selection)
);
CREATE INDEX IF NOT EXISTS latest_league ON latest(source, league);

-- next scheduled start per collector job (drives adaptive cadence)
CREATE TABLE IF NOT EXISTS job_next (job TEXT PRIMARY KEY, next_start TEXT);

-- leagues the laptop registry wants collected per-league (uploaded by cloud_sync.py)
CREATE TABLE IF NOT EXISTS wanted_leagues (league_id INTEGER PRIMARY KEY, name TEXT, sport TEXT);
CREATE TABLE IF NOT EXISTS last_ok (job TEXT PRIMARY KEY, ts TEXT);
