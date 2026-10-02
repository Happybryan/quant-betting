"""Always-on collector for GitHub Actions (runs while the Mac sleeps; Kalshi blocks Cloudflare egress, not GitHub).
Jobs: Kalshi game winners (snapshot.LEAGUES + config/kalshi_series.json), Kalshi NFL prop ladders, and Pinnacle
moneylines for EVERY sport (no CPU limit here, unlike the free Cloudflare Worker, which keeps dying on small sports).
Writes into a checkout of the `gh-data` branch (path = argv[1]):
  state.json.gz                  {"v": last value per key, "jobs": keys each job saw last run}
  runs/YYYY-MM-DD/HHMMSS.json.gz {ts, runs:[{league, source, ok, seen, error, removed}], changed:[rows]}
Only changes are stored; gh_sync.py on the Mac rebuilds Kalshi full snapshots and writes Pinnacle gone-markers."""
import gzip, json, sys, time, urllib.error
from datetime import datetime, timezone
from pathlib import Path
import snapshot

ROOT = Path(__file__).parent
SHORT = {889: "NFL", 880: "NCAAF", 246: "MLB", 1456: "NHL", 487: "NBA", 578: "WNBA"}
NON_SPORTS = {"Politics", "Entertainment"}


def key(r):
    return "\t".join((r[1], r[2], r[3] or "", r[5]))


def val(r):
    if r[1] == "pinnacle":
        return [r[4], r[8]]  # start, price (limit churn alone is not a change)
    return [r[4], r[6], r[7], r[9], r[10], r[11]]  # start, bid, ask, bid_size, ask_size, extra


def pin_get(url):
    """Pinnacle answers some sports with 403 (darts/snooker/golf..., sometimes just bursts): pace every call, retry
    once after 15s, then let the job fail. The next run (~10 min) tries again; a long backoff stalled whole runs."""
    for i in range(2):
        time.sleep(2)
        try:
            return snapshot.get(url, snapshot.PIN_HEADERS, tries=1)
        except urllib.error.HTTPError as e:
            if e.code != 403 or i == 1:
                raise
            time.sleep(15)


def pinnacle_sport(sid, sname, ts):
    """Mirror of the Worker's pinnacleSport(): full-game moneylines for one Pinnacle sport. Soccer heartbeats are per league."""
    P = snapshot.PINNACLE
    ms = {m["id"]: m for m in pin_get(f"{P}/sports/{sid}/matchups")
          if m.get("type") == "matchup" and not m.get("parentId") and not m.get("isLive")}
    rows = []
    for k in pin_get(f"{P}/sports/{sid}/markets/straight?primaryOnly=true"):
        m = ms.get(k["matchupId"])
        if not m or k["type"] != "moneyline" or k["period"] != 0 or k.get("isAlternate") or k.get("status") not in (None, "open"):
            continue
        names = {p["alignment"]: p["name"] for p in m["participants"]}
        league = SHORT.get(m["league"]["id"], m["league"]["name"])
        limit = (k.get("limits") or [{}])[0].get("amount")
        for pr in k["prices"]:
            if pr.get("designation"):
                rows.append((ts, "pinnacle", league, str(m["id"]), m["startTime"],
                             names.get(pr["designation"]) or ("Draw" if pr["designation"] == "draw" else pr["designation"]),
                             None, None, pr["price"], None, None,
                             json.dumps({"side": pr["designation"], "limit": limit, "sport": sname, "league_id": m["league"]["id"]})))
    return rows


def jobs_list():
    cfg = ROOT / "config" / "kalshi_series.json"
    series = {lg: s for lg, (s, _) in snapshot.LEAGUES.items()}
    series.update(json.loads(cfg.read_text()) if cfg.exists() else {})
    jobs = [(lg, "kalshi", lambda ts, lg=lg, s=s: snapshot.kalshi_rows(lg, s, ts)) for lg, s in series.items()]
    jobs.append(("NFL", "kalshi_prop", lambda ts: snapshot.kalshi_prop_rows("NFL", None, ts)))
    try:
        sports = [s for s in snapshot.get(f"{snapshot.PINNACLE}/sports", snapshot.PIN_HEADERS)
                  if s.get("matchupCount") and s["name"] not in NON_SPORTS]
    except Exception as e:
        print(f"pinnacle sports list failed: {e!r}")
        sports = []
    jobs += [(f"SPORT:{s['name']}", "pinnacle", lambda ts, s=s: pinnacle_sport(s["id"], s["name"], ts)) for s in sports]
    return jobs


def main(out):
    out = Path(out)
    now = datetime.now(timezone.utc)
    ts = now.isoformat(timespec="seconds")
    sp = out / "state.json.gz"
    st = json.loads(gzip.decompress(sp.read_bytes())) if sp.exists() else {}
    if "v" not in st:  # pre-2026-10-02 format: flat dict of values
        st = {"v": st, "jobs": {}}
    state, seen_by, fails = st["v"], st["jobs"], st.setdefault("fails", {})
    runs, changed = [], []
    for league, source, fn in jobs_list():
        job = f"{source}|{league}"
        if fails.get(job, 0) and fails[job] % 6:  # failing job: retry about hourly, not every run
            fails[job] += 1
            continue
        try:
            rows = fn(ts)
        except Exception as e:  # one dead series/sport must not kill the run
            fails[job] = fails.get(job, 0) + 1
            runs.append(dict(league=league, source=source, ok=0, seen=None, error=repr(e)[:300], removed=[]))
            continue
        fails.pop(job, None)
        keys = set()
        for r in rows:
            k = key(r)
            keys.add(k)
            if state.get(k) != val(r):
                state[k] = val(r)
                changed.append(list(r))
        removed = sorted(set(seen_by.get(job, [])) - keys)  # closed/settled markets
        for k in removed:
            state.pop(k, None)
        seen_by[job] = sorted(keys)
        runs.append(dict(league=league, source=source, ok=1, seen=len(rows), error=None, removed=removed,
                         leagues=sorted({r[2] for r in rows}) if source == "pinnacle" else None))
    f = out / "runs" / now.strftime("%Y-%m-%d") / (now.strftime("%H%M%S") + ".json.gz")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(gzip.compress(json.dumps(dict(ts=ts, runs=runs, changed=changed)).encode()))
    sp.write_bytes(gzip.compress(json.dumps(st).encode()))
    print(f"{ts}: {sum(r['ok'] for r in runs)}/{len(runs)} jobs ok, {len(changed)} changed, "
          f"{sum(len(r['removed']) for r in runs)} removed, state {len(state)}")


if __name__ == "__main__":
    main(sys.argv[1])
