"""H12 stale-line scanner (pre-registered be2c575). Run every 2 min by launchd; it decides itself whether to spend credits.
 1. Pull the PrizePicks board (Odds API) when an NFL game starts within 3h and the last pull is >= 20 min old (60-credit floor).
 2. For every current PP standard prop: P(side) at the PP line NOW vs WHEN PP last set that line (our first sighting).
    Logs legs with P_now >= 0.607 and delta >= 2pp (lowest grid value; discovery picks delta later). Immutable rows.
 3. Executability: a leg is EXECUTABLE if a PP pull >= 10 min after detection still shows the same line.
Usage: python3 stale_scan.py [--force-pull] [--report]"""
import hashlib, json, math, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pp_board
from odds import american_to_prob, devig_range

ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.db"
MIN_P, MIN_DELTA, POLL_MIN, EXEC_MIN = 3 ** -0.5 + 0.03, 0.02, 20, 10
KAL = {"pass yards": "passing yards", "receiving yards": "receiving yards", "rush yards": "rushing yards", "receptions": "receptions",
       "pass tds": "passing touchdowns"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS stale_legs (id INTEGER PRIMARY KEY, detect_ts TEXT NOT NULL, event_key TEXT, start_time TEXT, player TEXT,
  stat TEXT, pp_line REAL, side TEXT, pp_since TEXT, p_then REAL, p_now REAL, delta REAL, source TEXT, sharp_move_first_seen TEXT,
  latency_min REAL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
  exec_status TEXT, exec_checked_ts TEXT, close_p REAL, close_status TEXT, result TEXT,
  UNIQUE (event_key, player, stat, pp_line, side));
CREATE TRIGGER IF NOT EXISTS stale_pre_immutable BEFORE UPDATE OF detect_ts, event_key, start_time, player, stat, pp_line, side,
  pp_since, p_then, p_now, delta, source, sharp_move_first_seen, latency_min, payload, sha256 ON stale_legs
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER IF NOT EXISTS stale_no_delete BEFORE DELETE ON stale_legs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""


def p_at(con, player, stat, line, t):
    """P(MORE) at `line` as of time t: Pinnacle no-vig if its line == line, else Kalshi ladder rung N = floor(line)+1. (P, source).
    Whole-number lines (e.g. 2.0) are UNPRICEABLE here: landing exactly on the line is a push (PrizePicks drops the leg), so
    P(LESS wins) != 1 - P(MORE wins). Treating them as .5 lines inflated LESS (bug found 2026-09-28 via an implausible +10% EV)."""
    if line is None or float(line).is_integer():
        return None, None
    import verify
    suffix = pp_board.PIN_SUFFIX.get(stat) or (verify.STATS.get(stat) or [None])[0]
    if not suffix:
        return None, None
    desc = f"{player} {suffix}"
    r = {}
    # only THIS game's prices (start no earlier than 6h before t): an older game's line once priced a TNF leg off last week's
    # Pinnacle price (2026-10-01). Gone-markers (NULL price) are kept so a pulled market isn't priced from its last quote.
    for sel, price, pts in con.execute("""SELECT selection, price, json_extract(extra,'$.points') FROM snapshots WHERE source='pinnacle_prop'
            AND selection IN (?,?) AND ts <= ? AND julianday(start_time) >= julianday(?) - 0.25 ORDER BY ts""", (desc + "|Over", desc + "|Under", t, t)):
        r[sel.rsplit("|", 1)[1]] = (price, pts)
    if len(r) == 2 and None not in (r["Over"][0], r["Under"][0]) and r["Over"][1] == r["Under"][1] == line:
        return devig_range([american_to_prob(r["Over"][0]), american_to_prob(r["Under"][0])])[0][0], "pinnacle"
    if stat in KAL:
        n = int(math.floor(line)) + 1
        k = con.execute("""SELECT bid, ask FROM snapshots WHERE source='kalshi_prop' AND selection LIKE ? AND lower(selection) LIKE ?
                           AND ts <= ? AND julianday(start_time) >= julianday(?) - 0.25 AND bid IS NOT NULL AND ask IS NOT NULL
                           ORDER BY ts DESC LIMIT 1""", (f"{player}%: {n}+%", f"%{KAL[stat].split()[0]}%", t, t)).fetchone()
        if k:
            return (k[0] + k[1]) / 2, "kalshi"
    return None, None


def first_move(con, player, stat, since):
    """First Pinnacle change for this prop after `since` (when the sharp market moved, as we observed it)."""
    desc = f"{player} {pp_board.PIN_SUFFIX[stat]}"
    r = con.execute("SELECT min(ts) FROM snapshots WHERE source='pinnacle_prop' AND selection IN (?,?) AND ts > ?", (desc + "|Over", desc + "|Under", since)).fetchone()
    return r[0]


def move_window(con, first_seen, league="NFL"):
    """The move happened in (last successful props pull BEFORE first_seen, first_seen]. Returns (window_start, window_end)."""
    if not first_seen:
        return None, None
    prev = con.execute("""SELECT max(ts) FROM collector_runs WHERE league=? AND source='pinnacle_prop' AND ok=1 AND ts < ?""",
                       (league, first_seen)).fetchone()[0]
    return prev, first_seen


def maybe_pull(con, force):
    now = datetime.now(timezone.utc)
    soon = con.execute("""SELECT count(*) FROM snapshots WHERE source='pinnacle' AND league='NFL' AND price IS NOT NULL
                          AND start_time > ? AND start_time < ?""", (now.isoformat(), (now + timedelta(hours=3)).isoformat())).fetchone()[0]
    last = con.execute("SELECT max(ts) FROM collector_runs WHERE source='prizepicks' AND ok=1").fetchone()[0]
    due = force or (soon and (not last or (now - datetime.fromisoformat(last)).total_seconds() >= POLL_MIN * 60))
    if not due:
        return False
    pulls = pp_board.pull("americanfootball_nfl", 3.2)
    rows = pp_board.store(con, pulls)
    con.execute("INSERT INTO collector_runs VALUES (?,?,?,?,?,?,?,?)", (now.isoformat(timespec="seconds"), "NFL", "prizepicks", 1,
                sum(len(b["markets"]) for _, d, _ in pulls for b in d.get("bookmakers", []) if b["key"] == "prizepicks"), len(rows), None, 0))
    con.commit()
    return True


def detect(con):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = con.execute("""SELECT event_key, start_time, selection, json_extract(extra,'$.points'), ts FROM snapshots WHERE rowid IN
            (SELECT max(rowid) FROM snapshots WHERE source='prizepicks' GROUP BY event_key, selection) AND start_time > ?""", (now,)).fetchall()
    n = 0
    for ev, st, sel, line, since in cur:
        player, stat = sel.split("|")
        pn, src = p_at(con, player, stat, line, now)
        pt, src_t = p_at(con, player, stat, line, since)
        if pn is None or pt is None or src != src_t:
            continue  # need the same source at both times, or the delta is apples-to-oranges
        for side, a, b in (("MORE", pn, pt), ("LESS", 1 - pn, 1 - pt)):
            if a >= MIN_P and a - b >= MIN_DELTA:
                mv = first_move(con, player, stat, since)
                ws, we = move_window(con, mv)
                lat = lambda t: (datetime.fromisoformat(now) - datetime.fromisoformat(t)).total_seconds() / 60 if t else None
                payload = json.dumps(dict(ev=ev, player=player, stat=stat, line=line, side=side, p_now=a, p_then=b, source=src,
                                          since=since, detect=now, move_window_start=ws, move_window_end=we,
                                          latency_min_lower=lat(we), latency_max=lat(ws)), sort_keys=True)
                try:
                    con.execute("""INSERT INTO stale_legs (detect_ts, event_key, start_time, player, stat, pp_line, side, pp_since, p_then, p_now,
                        delta, source, sharp_move_first_seen, latency_min, payload, sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (now, ev, st, player, stat, line, side, since, b, a, a - b, src, mv,
                                 (datetime.fromisoformat(now) - datetime.fromisoformat(mv)).total_seconds() / 60 if mv else None,
                                 payload, hashlib.sha256(payload.encode()).hexdigest()))
                    n += 1
                    leg_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                    con.commit()
                    import subprocess
                    subprocess.Popen([sys.executable, str(ROOT / "stale_followup.py"), str(leg_id)], cwd=ROOT, start_new_session=True,
                                     stdout=open(ROOT / "logs" / "stale_followup.log", "a"), stderr=subprocess.STDOUT)
                    import alerts
                    alerts.notify("STALE PrizePicks line (paper, H12)", f"{player} {side} {line} {stat}: sharp {a:.0%} (was {b:.0%})")
                except sqlite3.IntegrityError:
                    pass
    con.commit()
    return n


def executability(con):
    for i, ev, sel_p, stat, line, dts in con.execute("SELECT id, event_key, player, stat, pp_line, detect_ts FROM stale_legs WHERE exec_status IS NULL").fetchall():
        later = con.execute("""SELECT ts FROM collector_runs WHERE source='prizepicks' AND ok=1 AND ts >= ? ORDER BY ts LIMIT 1""",
                            ((datetime.fromisoformat(dts) + timedelta(minutes=EXEC_MIN)).isoformat(),)).fetchone()
        if not later:
            continue
        ln = con.execute("""SELECT json_extract(extra,'$.points') FROM snapshots WHERE source='prizepicks' AND event_key=? AND selection=?
                            AND ts <= ? ORDER BY ts DESC LIMIT 1""", (ev, f"{sel_p}|{stat}", later[0])).fetchone()
        st = "EXECUTABLE" if ln and ln[0] == line else "NOT_EXECUTABLE (PP moved)"
        con.execute("UPDATE stale_legs SET exec_status=?, exec_checked_ts=? WHERE id=?", (st, later[0], i))
    con.commit()


def report(con):
    import prop_settle
    con.executescript(open(ROOT / "stale_followup.py").read().split('SCHEMA = """')[1].split('"""')[0])
    legs = con.execute("SELECT * FROM stale_legs ORDER BY id").fetchall()
    cols = [c[0] for c in con.execute("SELECT * FROM stale_legs LIMIT 0").description]
    legs = [dict(zip(cols, l)) for l in legs]
    print(f"H12 stale legs logged: {len(legs)}")
    print("SAMPLING CAVEAT: PrizePicks is polled every ~20 min near kickoff (props) and Pinnacle every 2-5 min. '0 detected' means 0 "
          "OBSERVABLE at this sampling frequency, NOT 0 existed. Short-lived opportunities are systematically missed.")
    if not legs:
        return
    print("\nSURVIVAL CURVE (share of detected opportunities still executable at the same line):")
    for off in (0, 1, 2, 5, 10):
        r = con.execute("SELECT count(*), sum(available) FROM stale_survival WHERE offset_min=? AND available IS NOT NULL", (off,)).fetchone()
        print(f"   +{off:2} min: {r[1] or 0}/{r[0]}" + (f" = {(r[1] or 0) / r[0]:.0%}" if r[0] else ""))
    print("\nTHREE SEPARATE MEASUREMENTS (never combined):")
    clv = [l["close_p"] - l["p_now"] for l in legs if l["close_p"] is not None]
    print(f"  1. Sharp/closing-line value (close P - detection P): n={len(clv)}" + (f", mean {sum(clv) / len(clv) * 100:+.2f}pp" if clv else ""))
    for off in (1, 2, 5, 10):
        ev = [r[0] for r in con.execute("SELECT ev_leg FROM stale_survival WHERE offset_min=? AND available=1 AND ev_leg IS NOT NULL", (off,))]
        print(f"  2. Est. EV per leg at realistic entry +{off}m (P - 0.577, only if still available): n={len(ev)}" + (f", mean {sum(ev) / len(ev) * 100:+.2f}pp" if ev else ""))
    hits = []
    for l in legs:
        day = (datetime.fromisoformat(l["start_time"].replace("Z", "+00:00")) - timedelta(hours=4)).strftime("%Y%m%d")
        g = prop_settle.grade(prop_settle.outcome(con, l["player"], l["stat"], day), l["pp_line"], l["side"])
        if g in ("win", "loss"):
            hits.append((g == "win", l["p_now"]))
    print(f"  3. Realized hit rate: n={len(hits)}" + (f", {sum(h for h, _ in hits) / len(hits):.3f} vs mean detection P {sum(p for _, p in hits) / len(hits):.3f} vs break-even 0.577" if hits else ""))
    print("  NOTE: positive (1) does NOT prove positive PrizePicks EV; only (2) at executable moments, confirmed by (3), speaks to that.")


if __name__ == "__main__":
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    pulled = maybe_pull(con, "--force-pull" in sys.argv)
    n = detect(con)
    executability(con)
    print(f"stale_scan: pulled={pulled} new_stale_legs={n}")
    if "--report" in sys.argv:
        report(con)
