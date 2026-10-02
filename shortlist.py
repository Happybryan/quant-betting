"""Shortlist: which players to look up on PrizePicks. Scans cloud-collected Pinnacle props for upcoming NFL games,
keeps plausible standard lines where one side is priced 0.60-0.70 no-vig, then runs verify.py on each as a single
leg (hypothetical 2-pick 3x break-even; payout not verified). Output = players + exact line/side to check.
Usage: python3 shortlist.py [--hours 26] [--top 12]"""
import argparse, sqlite3, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import verify
from odds import american_to_prob, devig_range

ROOT = Path(__file__).parent
SUFFIX = {v[0]: k for k, v in verify.STATS.items()}   # Pinnacle description suffix -> our stat key


def candidates(hours):
    con = sqlite3.connect(ROOT / "data/market.db")
    now = datetime.now(timezone.utc)
    rows = con.execute("""SELECT event_key, selection, price, json_extract(extra,'$.points'), start_time FROM snapshots
        WHERE rowid IN (SELECT max(rowid) FROM snapshots WHERE source='pinnacle_prop' GROUP BY event_key, selection)
        AND price IS NOT NULL AND start_time > ? AND start_time < ?""",
                       (now.isoformat(), (now + timedelta(hours=hours)).isoformat())).fetchall()
    props = {}
    for ev, sel, price, pts, st in rows:
        desc, side = sel.rsplit("|", 1)
        props.setdefault((ev, desc), {})[side] = (price, pts, st)
    out = []
    for (ev, desc), s in props.items():
        stat = next((v for k, v in SUFFIX.items() if desc.endswith(" " + k)), None)
        if not stat or len(s) != 2 or s["Over"][1] != s["Under"][1]:
            continue
        (po, _, _), (pu, _, _) = devig_range([american_to_prob(s["Over"][0]), american_to_prob(s["Under"][0])])
        side, p = ("MORE", po) if po > pu else ("LESS", pu)
        if 0.60 <= p <= 0.70:
            out.append((p, desc[: -len(verify.STATS[stat][0]) - 1], stat, s["Over"][1], side, s["Over"][2]))
    return sorted(out, reverse=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=26)
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args()
    c = candidates(a.hours)
    print(f"{len(c)} plausible standard-line props priced 0.60-0.70 by Pinnacle; verifying the top {min(a.top, len(c))}")
    legs = [f"{pl}|{st}|{ln}|{sd}" for _, pl, st, ln, sd, _ in c[: a.top]]
    for leg in legs:  # one verify run per leg -> one frozen snapshot per candidate
        r = subprocess.run([sys.executable, "verify.py", leg, "--note", "SHORTLIST"], cwd=ROOT, capture_output=True, text=True)
        lvl = [l for l in r.stdout.splitlines() if l.startswith("PRE-BET GATE")]
        snap = [l for l in r.stdout.splitlines() if l.startswith("PRE-BET SNAPSHOT")]
        print(f"{leg:55} {lvl[0].split('DATA QUALITY')[1].strip() if lvl else 'ERROR ' + r.stderr[-200:]}  [{snap[0].split()[2] if snap else '-'}]")
