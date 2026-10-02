"""H12 survival follow-up for ONE stale leg: re-checks the exact actionable PrizePicks opportunity at detection +1, +2, +5, +10 min.
Spawned (detached) by stale_scan.py at detection. Each check pulls only that prop's market for that event: 1 Odds API credit.
Per check it records: target vs actual check time, whether the same player/stat/LINE is still offered (standard market), the observed
line, and the sharp P of our side at that moment (latest Pinnacle/Kalshi in the DB, with its age). Usage: python3 stale_followup.py LEG_ID"""
import json, sqlite3, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pp_board, stale_scan

ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.db"
OFFSETS = (1, 2, 5, 10)
MARKET = {v: k for k, v in pp_board.MARKETS.items()}
SCHEMA = """
CREATE TABLE IF NOT EXISTS stale_survival (leg_id INTEGER NOT NULL, offset_min INTEGER NOT NULL, target_ts TEXT, check_ts TEXT,
  available INTEGER, observed_line REAL, p_side REAL, p_source TEXT, p_age_min REAL, ev_leg REAL, credits_left INTEGER, note TEXT,
  PRIMARY KEY (leg_id, offset_min));
CREATE TRIGGER IF NOT EXISTS survival_immutable BEFORE UPDATE ON stale_survival BEGIN SELECT RAISE(ABORT, 'immutable'); END;
"""


def sharp_now(con, player, stat, line, side, t):
    p, src = stale_scan.p_at(con, player, stat, line, t)
    if p is None:
        return None, None, None
    last = con.execute("SELECT max(ts) FROM snapshots WHERE source IN ('pinnacle_prop','kalshi_prop') AND ts <= ?", (t,)).fetchone()[0]
    age = (datetime.fromisoformat(t) - datetime.fromisoformat(last)).total_seconds() / 60 if last else None
    return (p if side == "MORE" else 1 - p), src, age


def record(con, leg, offset, target, available, line_seen, credits, note=""):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    p, src, age = sharp_now(con, leg["player"], leg["stat"], leg["pp_line"], leg["side"], now)
    con.execute("INSERT OR IGNORE INTO stale_survival VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (leg["id"], offset, target, now, available, line_seen, p, src, age,
                 (p - stale_scan.MIN_P + 0.03) if p is not None else None, credits, note))  # ev_leg = p - 0.577 (2-pick 3x break-even)
    con.commit()


def main(leg_id):
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    leg = dict(con.execute("SELECT * FROM stale_legs WHERE id=?", (leg_id,)).fetchone())
    t0 = datetime.fromisoformat(leg["detect_ts"])
    record(con, leg, 0, leg["detect_ts"], 1, leg["pp_line"], None, "detection pull (available by definition)")
    for off in OFFSETS:
        target = t0 + timedelta(minutes=off)
        time.sleep(max(0.0, (target - datetime.now(timezone.utc)).total_seconds()))
        try:
            d, left = pp_board.get(f"/sports/americanfootball_nfl/events/{leg['event_key']}/odds?regions=us_dfs&markets={MARKET[leg['stat']]}&oddsFormat=american")
        except Exception as e:
            record(con, leg, off, target.isoformat(timespec="seconds"), None, None, None, f"check failed: {e!r}"[:200]); continue
        seen = None
        for b in d.get("bookmakers", []):
            if b["key"] == "prizepicks":
                for m in b["markets"]:
                    for o in m["outcomes"]:
                        if o.get("description") == leg["player"] and o["name"] == "Over":
                            seen = o.get("point")
        record(con, leg, off, target.isoformat(timespec="seconds"), int(seen == leg["pp_line"]), seen, left,
               "" if seen == leg["pp_line"] else ("prop removed" if seen is None else "line moved"))
        if left is not None and left < pp_board.MIN_REMAINING:
            record(con, leg, 99, None, None, None, left, "stopped: credit floor"); break


if __name__ == "__main__":
    main(int(sys.argv[1]))
