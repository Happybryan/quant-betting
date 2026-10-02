"""COPY-BET DISCOVERY (H10). Community entries are a SENSOR, never the model; every leg goes through verify.py.
  python3 copybets.py add --entry "Jalen Hurts|pass tds|1.5|LESS; DeVonta Smith|receptions|5.5|MORE" \
                          [--creator NAME] [--copies N] [--payout 3] [--sport NFL] [--line-type standard]
  python3 copybets.py pool       repeated legs: appearances, distinct entries/creators, COPY_CONSENSUS_SCORE
  python3 copybets.py verify     run every unique pooled leg through verify.py (flagged COPY_DERIVED), record levels
PrizePicks is blocked for scripts, so entries arrive from Bryan's screenshots/text."""
import argparse, json, math, sqlite3, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS copy_entries (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, creator TEXT, copies INTEGER, sport TEXT,
  payout REAL, entry_type TEXT, raw TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS copy_legs (entry_id INTEGER NOT NULL, player TEXT NOT NULL, stat TEXT NOT NULL, line REAL NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('MORE','LESS')), line_type TEXT);
CREATE TABLE IF NOT EXISTS copy_evals (ts TEXT NOT NULL, player TEXT, stat TEXT, line REAL, side TEXT, consensus REAL,
  snapshot_id INTEGER, level TEXT, grade TEXT, p_consensus REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS copy_controls (ts TEXT NOT NULL, copy_player TEXT, game TEXT, player TEXT, stat TEXT, line REAL,
  side TEXT, market_p REAL, seed INTEGER);
CREATE TRIGGER IF NOT EXISTS copy_controls_immutable BEFORE UPDATE ON copy_controls BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS copy_evals_immutable BEFORE UPDATE ON copy_evals BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
NFL_STATS = {"pass tds", "pass yards", "receptions", "receiving yards", "rush yards", "pass attempts", "pass completions",
             "rush attempts", "interceptions"}


def con_():
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    return con


def add(a):
    con = con_()
    legs = [l.strip().split("|") for l in a.entry.split(";") if l.strip()]
    for l in legs:
        if len(l) != 4 or l[3].upper() not in ("MORE", "LESS"):
            raise SystemExit(f"bad leg {l}: use Player|stat|line|MORE/LESS")
    cur = con.execute("INSERT INTO copy_entries (ts, creator, copies, sport, payout, entry_type, raw) VALUES (?,?,?,?,?,?,?)",
                      (datetime.now(timezone.utc).isoformat(timespec="seconds"), a.creator, a.copies, a.sport, a.payout,
                       f"{len(legs)}-pick", a.entry))
    con.executemany("INSERT INTO copy_legs VALUES (?,?,?,?,?,?)",
                    [(cur.lastrowid, p.strip(), s.strip().lower(), float(ln), sd.upper(), a.line_type) for p, s, ln, sd in legs])
    con.commit()
    print(f"entry #{cur.lastrowid}: {len(legs)} legs recorded")


def pool_rows(con):
    """One row per distinct (player, stat, line, side), with consensus. COPY_CONSENSUS_SCORE is NOT a probability:
    log2(1 + distinct entries) + log2(1 + distinct creators). Same-creator repeats count once (herding guard)."""
    rows = con.execute("""SELECT l.player, l.stat, l.line, l.side, count(DISTINCT e.id), count(DISTINCT coalesce(e.creator, 'unknown#' || e.id)),
                                 coalesce(sum(e.copies), 0), min(e.ts), max(e.ts), group_concat(DISTINCT coalesce(l.line_type, '?'))
                          FROM copy_legs l JOIN copy_entries e ON e.id = l.entry_id GROUP BY l.player, l.stat, l.line, l.side""").fetchall()
    out = [dict(player=p, stat=s, line=ln, side=sd, entries=ne, creators=nc, copies=cp, first=f, last=la, line_types=lt,
                consensus=round(math.log2(1 + ne) + math.log2(1 + nc), 2)) for p, s, ln, sd, ne, nc, cp, f, la, lt in rows]
    # same player+stat on BOTH sides in the pool = the crowd disagrees with itself
    sides = {}
    for r in out:
        sides.setdefault((r["player"], r["stat"]), set()).add(r["side"])
    for r in out:
        r["crowd_split"] = len(sides[(r["player"], r["stat"])]) > 1
    return sorted(out, key=lambda r: -r["consensus"])


def pool(_):
    rows = pool_rows(con_())
    print(f"{'player':22} {'stat':16} {'line':>5} side  entries creators copies consensus  line types  notes")
    for r in rows:
        print(f"{r['player'][:22]:22} {r['stat'][:16]:16} {r['line']:5} {r['side']:4}  {r['entries']:7} {r['creators']:8} {r['copies']:6} "
              f"{r['consensus']:9}  {r['line_types']:10}  {'CROWD SPLIT (both sides present)' if r['crowd_split'] else ''}")


def add_controls(con, r, now, k=3):
    """H10 v2 control group: k random NON-copied Pinnacle props from the same game, side chosen at random (seeded),
    priced by Pinnacle no-vig at the same moment. Outcomes settle later from box scores, like the copy legs."""
    import random, zlib
    import verify
    from odds import american_to_prob, devig_mult
    pin = verify.pinnacle(r["player"], r["stat"], r["line"])
    if not pin:
        return
    game = str(pin["game"])
    copied = {(x[0], x[1]) for x in con.execute("SELECT player, stat FROM copy_legs")}
    props = {}
    for sel, price, pts in con.execute("""SELECT selection, price, json_extract(extra,'$.points') FROM snapshots WHERE rowid IN
            (SELECT max(rowid) FROM snapshots WHERE source='pinnacle_prop' AND json_extract(extra,'$.game')=? GROUP BY selection)
            AND price IS NOT NULL""", (int(game),)):
        desc, side = sel.rsplit("|", 1)
        props.setdefault(desc, {})[side] = (price, pts)
    pool = []
    for desc, sd in sorted(props.items()):
        stat = next((k for k, v in verify.STATS.items() if desc.endswith(" " + v[0])), None)
        if not stat or len(sd) != 2 or sd["Over"][1] != sd["Under"][1]:
            continue
        player = desc[: -len(verify.STATS[stat][0]) - 1]
        if (player, stat) not in copied:
            pool.append((player, stat, sd))
    seed = zlib.crc32(f"{r['player']}|{r['stat']}|{now}".encode())
    rnd = random.Random(seed)
    for player, stat, sd in rnd.sample(pool, min(k, len(pool))):
        pm = devig_mult([american_to_prob(sd["Over"][0]), american_to_prob(sd["Under"][0])])[0]
        side = rnd.choice(("MORE", "LESS"))
        con.execute("INSERT INTO copy_controls VALUES (?,?,?,?,?,?,?,?,?)", (now, r["player"], game, player, stat, sd["Over"][1], side,
                    pm if side == "MORE" else 1 - pm, seed))
    con.commit()


def verify_pool(a):
    con = con_()
    con.commit()
    for r in pool_rows(con):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if r["stat"] not in NFL_STATS:
            con.execute("INSERT INTO copy_evals VALUES (?,?,?,?,?,?,?,?,?,?,?)", (now, r["player"], r["stat"], r["line"], r["side"],
                        r["consensus"], None, "PASS", None, None, "no verified pricing path for this sport/stat yet"))
            con.commit()
            print(f"PASS  {r['player']} {r['stat']} {r['line']} {r['side']}: no verified pricing path for this sport/stat yet")
            continue
        leg = f"{r['player']}|{r['stat']}|{r['line']}|{r['side']}"
        out = subprocess.run([sys.executable, "verify.py", leg, "--note", f"COPY_DERIVED consensus={r['consensus']} entries={r['entries']}"],
                             cwd=ROOT, capture_output=True, text=True).stdout
        snap = next((int(l.split("#")[1].split()[0]) for l in out.splitlines() if l.startswith("PRE-BET SNAPSHOT #")), None)
        gate = next((l for l in out.splitlines() if l.startswith("PRE-BET GATE")), "")
        level = gate.split("   ")[-1].strip() if gate else "NOT READY"
        grade = gate.split("DATA QUALITY")[1].split()[0] if "DATA QUALITY" in gate else None
        pc = None
        if snap:
            p = json.loads(con.execute("SELECT payload FROM prebet_snapshots WHERE id=?", (snap,)).fetchone()[0])
            pc = p["legs"][0].get("p_consensus")
        why = [l.strip() for l in out.splitlines() if l.strip().startswith(("[!]", "[X]", "[ ]"))][:4]
        con.execute("INSERT INTO copy_evals VALUES (?,?,?,?,?,?,?,?,?,?,?)", (now, r["player"], r["stat"], r["line"], r["side"],
                    r["consensus"], snap, level, grade, pc, "; ".join(why)))
        con.commit()  # never hold a write lock while verify.py (a separate process) needs the DB
        if snap:
            add_controls(con, r, now)
        print(f"{level[:40]:40} {r['player']} {r['stat']} {r['line']} {r['side']} | consensus {r['consensus']} | market P "
              f"{'-' if pc is None else f'{pc:.3f}'} | snapshot #{snap}")
        for w in why:
            print(f"      {w[:130]}")
    con.commit()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    x = sp.add_parser("add")
    x.add_argument("--entry", required=True); x.add_argument("--creator"); x.add_argument("--copies", type=int)
    x.add_argument("--payout", type=float); x.add_argument("--sport", default="NFL"); x.add_argument("--line-type", default=None)
    sp.add_parser("pool"); sp.add_parser("verify")
    a = ap.parse_args()
    {"add": add, "pool": pool, "verify": verify_pool}[a.cmd](a)
