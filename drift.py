"""Pinnacle fair-price drift to close, SD by hours-to-start. Versioned: each run APPENDS a new version to
config/drift_sd.json (never overwrites); quality.py uses the latest. Run monthly (launchd, day 1) or by hand.
Usage: python3 drift.py [--min-n 50]"""
import collections, json, sqlite3, statistics, sys
from datetime import datetime, timezone
from pathlib import Path
from odds import american_to_prob, devig_all

ROOT = Path(__file__).parent
CFG = ROOT / "config" / "drift_sd.json"
EDGES = [(1, "<1h"), (6, "1-6h"), (24, "6-24h"), (72, "24-72h"), (1e9, ">72h")]


def estimate(con):
    ev = collections.defaultdict(list)
    for k, ts, s, p, st in con.execute("SELECT event_key, ts, selection, price, start_time FROM snapshots WHERE source='pinnacle' AND price IS NOT NULL ORDER BY event_key, ts"):
        ev[k].append((ts, s, p, st))
    buckets, n_ev = collections.defaultdict(list), 0
    for rs in ev.values():
        n, st = len({s for _, s, _, _ in rs}), rs[0][3]
        if n < 2 or not st:
            continue
        cur, path = {}, []
        for i, (ts, s, p, _) in enumerate(rs):
            cur[s] = p
            if len(cur) == n and (i + 1 == len(rs) or rs[i + 1][0] != ts):
                names = sorted(cur)
                path.append((ts, devig_all([american_to_prob(cur[x]) for x in names])[0]["mult"]))
        start = datetime.fromisoformat(st.replace("Z", "+00:00"))
        pre = [x for x in path if x[0] <= start.isoformat()]
        if len(pre) < 2:
            continue
        n_ev += 1
        for t, f in pre[:-1]:
            h = (start - datetime.fromisoformat(t)).total_seconds() / 3600
            buckets[next(e for e, _ in EDGES if h < e)].append(pre[-1][1] - f)
    return n_ev, buckets


if __name__ == "__main__":
    min_n = int(sys.argv[sys.argv.index("--min-n") + 1]) if "--min-n" in sys.argv else 50
    n_ev, b = estimate(sqlite3.connect(ROOT / "data" / "market.db", timeout=60))
    hist = json.loads(CFG.read_text()) if CFG.exists() else []
    prev = hist[-1]["sd"] if hist else {}
    sd = {str(e): (statistics.pstdev(b[e]) if len(b[e]) >= min_n else prev.get(str(e))) for e, _ in EDGES}
    hist.append(dict(version=f"DRIFT_v{len(hist) + 1}", created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     n_events=n_ev, n_by_bucket={lab: len(b[e]) for e, lab in EDGES}, sd=sd))
    CFG.write_text(json.dumps(hist, indent=1))
    print(hist[-1])
