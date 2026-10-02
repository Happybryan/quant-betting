"""Kalshi collector for GitHub Actions (runs while the Mac sleeps; Kalshi blocks Cloudflare egress, not GitHub).
Writes into a checkout of the `gh-data` branch (path = argv[1]):
  state.json.gz                  last seen value per (source, league, event_key, selection)
  runs/YYYY-MM-DD/HHMMSS.json.gz {ts, runs:[{league, source, ok, seen, error}], changed:[rows], removed:[keys]}
Only changes are stored (repo stays small); gh_sync.py on the Mac rebuilds full snapshots from them.
Series list: snapshot.LEAGUES + config/kalshi_series.json (exported from the Mac's registry by gh_sync.py)."""
import gzip, json, sys
from datetime import datetime, timezone
from pathlib import Path
import snapshot

ROOT = Path(__file__).parent


def key(r):
    return "\t".join((r[1], r[2], r[3] or "", r[5]))


def val(r):
    return [r[4], r[6], r[7], r[9], r[10], r[11]]  # start, bid, ask, bid_size, ask_size, extra


def main(out):
    out = Path(out)
    now = datetime.now(timezone.utc)
    ts = now.isoformat(timespec="seconds")
    sp = out / "state.json.gz"
    state = json.loads(gzip.decompress(sp.read_bytes())) if sp.exists() else {}
    cfg = ROOT / "config" / "kalshi_series.json"
    series = {lg: s for lg, (s, _) in snapshot.LEAGUES.items()}
    series.update(json.loads(cfg.read_text()) if cfg.exists() else {})
    jobs = [(lg, "kalshi", snapshot.kalshi_rows, s) for lg, s in series.items()] + [("NFL", "kalshi_prop", snapshot.kalshi_prop_rows, None)]
    runs, changed, removed = [], [], []
    for league, source, fn, arg in jobs:
        try:
            rows = fn(league, arg, ts)
        except Exception as e:  # one dead series must not kill the run
            runs.append(dict(league=league, source=source, ok=0, seen=None, error=repr(e)[:300]))
            continue
        seen = set()
        for r in rows:
            k = key(r)
            seen.add(k)
            if state.get(k) != val(r):
                state[k] = val(r)
                changed.append(list(r))
        prefix = f"{source}\t{league}\t"
        for k in [k for k in state if k.startswith(prefix) and k not in seen]:  # closed/settled markets
            del state[k]
            removed.append(k)
        runs.append(dict(league=league, source=source, ok=1, seen=len(rows), error=None))
    f = out / "runs" / now.strftime("%Y-%m-%d") / (now.strftime("%H%M%S") + ".json.gz")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(gzip.compress(json.dumps(dict(ts=ts, runs=runs, changed=changed, removed=removed)).encode()))
    sp.write_bytes(gzip.compress(json.dumps(state).encode()))
    print(f"{ts}: {sum(r['ok'] for r in runs)}/{len(runs)} jobs ok, {len(changed)} changed, {len(removed)} removed, state {len(state)}")


if __name__ == "__main__":
    main(sys.argv[1])
