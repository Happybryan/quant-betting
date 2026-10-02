"""DATA HEALTH (DATA_v1.3): every collector job, its expected cadence, and a status.
HEALTHY <= 1.5x cadence | DELAYED <= 3x | STALE > 3x | FAILED = last attempt failed. Jobs with no attempt in 24h are
listed as RETIRED (e.g. the old league-level moneyline job), never silently counted as healthy.
Usage: python3 status.py [--all]   (exit 1 if any job with data is STALE/FAILED)"""
import json, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(__file__).parent / "data" / "market.db"
# jobs replaced by newer ones (kept in history, never monitored): league-level moneylines -> SPORT:<sport>; SPORT:Soccer -> per league
RETIRED = {("SPORT:Soccer", "pinnacle")}  # superseded by per-league soccer jobs  # NFL/NCAAF/MLB/NHL/NBA/WNBA 'pinnacle' = laptop fallback, monitored again
LEGACY = {"NFL": "KXNFLGAME", "NCAAF": "KXNCAAFGAME", "MLB": "KXMLBGAME", "NHL": "KXNHLGAME", "NBA": "KXNBAGAME", "WNBA": "KXWNBAGAME"}


def _cadence(mins, floor=2):
    return 30 if mins is None else max(floor, 2 if mins <= 60 else 5 if mins <= 180 else 10 if mins <= 720 else 30)


def next_start(con, league, source, now):
    """Next scheduled start the job cares about (mirrors the collectors' cadence inputs)."""
    iso = now.isoformat()
    if league.startswith("SPORT:") and "|" in league:  # per-league job, e.g. SPORT:Soccer|England - Premier League
        q = ("SELECT min(start_time) FROM snapshots WHERE source='pinnacle' AND price IS NOT NULL AND start_time > ? AND league = ?",
             (iso, league.split("|", 1)[1]))
    elif league.startswith("SPORT:"):
        q = ("SELECT min(start_time) FROM snapshots WHERE source='pinnacle' AND price IS NOT NULL AND start_time > ? "
             "AND json_extract(extra,'$.sport') = ?", (iso, league[6:]))
    elif source == "pinnacle_prop":
        q = ("SELECT min(start_time) FROM snapshots WHERE source='pinnacle_prop' AND league=? AND start_time > ?", (league, iso))
    else:  # Kalshi series or legacy label: registry start times
        q = ("""SELECT min(e.start_utc) FROM reg_sources s JOIN reg_events e USING(event_id) WHERE s.source='kalshi'
                AND s.source_league=? AND e.start_utc > ?""", (LEGACY.get(league, league), iso))
    try:
        t = con.execute(*q).fetchone()[0]
    except sqlite3.OperationalError:
        return None
    return (datetime.fromisoformat(t.replace("Z", "+00:00")) - now).total_seconds() / 60 if t else None


def health(con, now=None):
    now = now or datetime.now(timezone.utc)
    day = (now - timedelta(days=1)).isoformat()
    out = []
    for league, source in con.execute("SELECT DISTINCT league, source FROM collector_runs WHERE source NOT LIKE 'cloud_%'"):
        last_any, last_ok_flag = con.execute("SELECT ts, ok FROM collector_runs WHERE league=? AND source=? ORDER BY ts DESC LIMIT 1", (league, source)).fetchone()
        ok = con.execute("SELECT max(ts) FROM collector_runs WHERE league=? AND source=? AND ok=1", (league, source)).fetchone()[0]
        fails = con.execute("SELECT count(*), max(error) FROM collector_runs WHERE league=? AND source=? AND ok=0 AND ts > ?", (league, source, day)).fetchone()
        seen = con.execute("SELECT rows_seen FROM collector_runs WHERE league=? AND source=? AND ok=1 ORDER BY ts DESC LIMIT 1", (league, source)).fetchone()
        due = _cadence(next_start(con, league, source, now), 5 if league == "SPORT:Soccer" else 2)
        age = (now - datetime.fromisoformat(ok)).total_seconds() / 60 if ok else None
        if last_any < day or (league, source) in RETIRED:
            state = "RETIRED"
        elif not last_ok_flag:
            state = "FAILED"
        else:
            state = "HEALTHY" if age <= 1.5 * due + 1 else "DELAYED" if age <= 3 * due + 2 else "STALE"
        out.append(dict(league=league, source=source, last_ok=ok, age_min=age, due_min=due, state=state,
                        fails_24h=fails[0], last_error=fails[1], rows_seen=seen[0] if seen else None))
    return out


def gaps(con, since):
    """Periods > 15 min with no successful run at all (laptop asleep / collector down)."""
    ts = [r[0] for r in con.execute("SELECT DISTINCT ts FROM collector_runs WHERE ok=1 AND ts > ? ORDER BY ts", (since,))]
    ts += [r[0] for r in con.execute("SELECT DISTINCT ts FROM snapshots WHERE ts > ? AND ts < coalesce((SELECT min(ts) FROM collector_runs), '9')", (since,))]
    ts = sorted(set(ts))
    return [(a, b) for a, b in zip(ts, ts[1:]) if (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() > 900]


if __name__ == "__main__":
    con = sqlite3.connect(DB, timeout=60)
    now = datetime.now(timezone.utc)
    rows = sorted(health(con, now), key=lambda r: (r["state"] == "RETIRED", r["source"], r["league"]))
    print(f"DATA HEALTH {now.isoformat(timespec='seconds')}")
    print(f"{'job (league/source)':38} {'last ok (UTC)':17} {'age':>6} {'expect':>7} {'rows':>6} {'fails24h':>8}  status")
    for r in rows:
        if r["state"] == "RETIRED" and "--all" not in sys.argv:
            continue
        print(f"{(r['league'] + ' / ' + r['source'])[:38]:38} {(r['last_ok'] or '-')[5:16]:17} {(r['age_min'] or 0):5.0f}m {r['due_min']:5d}m "
              f"{r['rows_seen'] or 0:6} {r['fails_24h']:8}  {r['state']}" + (f"  ({(r['last_error'] or '')[:50]})" if r["state"] == "FAILED" else ""))
    print("retired jobs hidden: " + str(sum(r["state"] == "RETIRED" for r in rows)) + " (python3 status.py --all)")
    bad = [r for r in rows if r["state"] in ("STALE", "FAILED") and r["rows_seen"]]
    print("OVERALL: " + ("DEGRADED: " + ", ".join(f"{r['league']}/{r['source']} {r['state']}" for r in bad) if bad else "HEALTHY"))
    if "--alert" in sys.argv:  # notify only when the set of degraded jobs CHANGES (no spam every 2 min)
        state = Path(__file__).parent / "logs" / "last_degraded.txt"
        cur = "\n".join(sorted(f"{r['league']}/{r['source']} {r['state']}" for r in bad))
        if cur != (state.read_text() if state.exists() else ""):
            import alerts
            alerts.notify("Collector health changed", cur.replace("\n", "; ") or "all jobs HEALTHY again")
            state.write_text(cur)
    sys.exit(1 if bad else 0)
