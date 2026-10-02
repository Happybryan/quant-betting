"""Import the GitHub Actions Kalshi collector (gh_collect.py) into data/market.db.
  python3 gh_sync.py            pull branch gh-data into data/gh-data, import new runs
  python3 gh_sync.py --export   write config/kalshi_series.json (registry-matched series) for the next publish
Kalshi game rows are rebuilt as FULL snapshots (quality.assess reads all rows at one ts); prop ladders stay change-only.
A league's GitHub run is skipped when the Mac's own collector ran that league within 15 min (no duplicate snapshots)."""
import gzip, json, sqlite3, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path
import snapshot

ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.db"
REPO = "https://github.com/Happybryan/quant-betting.git"
CLONE = ROOT / "data" / "gh-data"
STATE = ROOT / "data" / "gh_state.json"


def export(con):
    rows = con.execute("SELECT kalshi_series FROM reg_league_map WHERE n_matched > 0").fetchall()
    legacy = {s for s, _ in snapshot.LEAGUES.values()}
    cfg = {s: s for s, in rows if s not in legacy}
    (ROOT / "config" / "kalshi_series.json").write_text(json.dumps(cfg, indent=1, sort_keys=True) + "\n")
    print(f"exported {len(cfg)} series")


def pull():
    if CLONE.exists():
        subprocess.run(["git", "-C", str(CLONE), "fetch", "-q", "--depth", "1", "origin", "gh-data"], check=True)
        subprocess.run(["git", "-C", str(CLONE), "reset", "-q", "--hard", "FETCH_HEAD"], check=True)
    else:
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "gh-data", REPO, str(CLONE)], check=True)


def local_ran(con, league, source, ts):
    t = datetime.fromisoformat(ts)
    lo, hi = (t - timedelta(minutes=15)).isoformat(timespec="seconds"), (t + timedelta(minutes=15)).isoformat(timespec="seconds")
    return con.execute("""SELECT 1 FROM collector_runs WHERE league=? AND source=? AND ok=1 AND ts BETWEEN ? AND ?
                          AND (error IS NULL OR error != 'github') LIMIT 1""", (league, source, lo, hi)).fetchone() is not None


def import_job(con, run, j, state, ts):
    """One GitHub job -> snapshots + collector_runs. Skipped where the Mac/cloud already covered it within 15 min."""
    src, lg = j["source"], j["league"]
    if src == "pinnacle":  # league = SPORT:<sport>; soccer heartbeats are per league (SPORT:Soccer|<league>), like the Worker
        sport = lg.split(":", 1)[1]
        labels = {l: (f"SPORT:{sport}|{l}" if sport == "Soccer" else lg) for l in (j.get("leagues") or [])}
        keep = {l for l, lab in labels.items() if not local_ran(con, lab, "pinnacle", ts)}
        rows = [tuple(r) for r in run["changed"] if r[1] == "pinnacle" and r[2] in keep and json.loads(r[11]).get("sport") == sport]
        rows += [(ts, "pinnacle", k.split("\t")[1], k.split("\t")[2], None, k.split("\t")[3], None, None, None, None, None,
                  json.dumps({"gone": True})) for k in j.get("removed", []) if k.split("\t")[1] in keep]
        con.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        beats = {labels[l] for l in keep} if j["ok"] else ({lg} if sport != "Soccer" else set())
        for lab in beats:
            con.execute("INSERT INTO collector_runs VALUES (?,?,?,?,?,?,?,?)", (ts, lab, "pinnacle", j["ok"], j["seen"], len(rows), j["error"] or "github", 0))
        return len(rows)
    if local_ran(con, lg, src, ts):
        return 0
    rows = []
    if j["ok"] and src == "kalshi":  # full snapshot of this league at ts (quality.assess reads every row at one ts)
        pre = f"kalshi\t{lg}\t"
        for k, v in state.items():
            if k.startswith(pre):
                _, l, ev, sel = k.split("\t", 3)
                rows.append((ts, "kalshi", l, ev, v[0], sel, v[1], v[2], None, v[3], v[4], v[5]))
    elif j["ok"]:
        rows = [tuple(r) for r in run["changed"] if r[1] == src and r[2] == lg]
    con.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO collector_runs VALUES (?,?,?,?,?,?,?,?)", (ts, lg, src, j["ok"], j["seen"], len(rows), j["error"] or "github", 0))
    return len(rows)


def main():
    con = sqlite3.connect(DB, timeout=60)
    if "--export" in sys.argv:
        return export(con)
    pull()
    st = json.loads(STATE.read_text()) if STATE.exists() else {"last": "", "state": {}}
    state, n_runs, n_rows = st["state"], 0, 0
    for f in sorted((CLONE / "runs").glob("*/*.json.gz")):
        name = f.parent.name + "/" + f.name
        if name <= st["last"]:
            continue
        run = json.loads(gzip.decompress(f.read_bytes()))
        ts = run["ts"]
        for r in run["changed"]:
            state["\t".join((r[1], r[2], r[3] or "", r[5]))] = [r[4], r[6], r[7], r[9], r[10], r[11]]
        for j in run["runs"]:
            for k in j.get("removed", []):
                state.pop(k, None)
        for k in run.get("removed", []):  # pre-2026-10-02 run format
            state.pop(k, None)
        for j in run["runs"]:
            n_rows += import_job(con, run, j, state, ts)
        st["last"] = name
        n_runs += 1
    con.commit()
    STATE.write_text(json.dumps(st))
    print(f"gh_sync: {n_runs} GitHub runs imported, {n_rows} rows")


if __name__ == "__main__":
    main()
