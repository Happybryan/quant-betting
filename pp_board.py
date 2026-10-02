"""PrizePicks board via The Odds API (region us_dfs; standard lines only; goblins/demons live in *_alternate markets, not
requested). Saves every pull (raw JSON + change-only `prizepicks` rows) and compares each prop to Pinnacle no-vig at the
SAME line. Legs whose better side clears the 2-pick break-even + 3pp go to verify.py.
  python3 pp_board.py [--sport americanfootball_nfl] [--hours 30] [--verify]
Credits: 1 per market per event (5 markets -> 5/event). Free tier 500/month: budget-checked before every call."""
import json, os, sqlite3, subprocess, sys, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from odds import american_to_prob, devig_range

ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.db"
RAW = ROOT / "data" / "raw" / "odds_api"
API = "https://api.the-odds-api.com/v4"
SPORT_MARKETS = {
    "americanfootball_nfl": {"player_pass_tds": "pass tds", "player_pass_yds": "pass yards", "player_receptions": "receptions",
                             "player_reception_yds": "receiving yards", "player_rush_yds": "rush yards"},
    "baseball_mlb": {"batter_total_bases": "total bases", "batter_home_runs": "home runs", "pitcher_strikeouts": "pitcher strikeouts",
                     "pitcher_outs": "pitching outs"}}
MARKETS = {k: v for m in SPORT_MARKETS.values() for k, v in m.items()}
LEAGUE = {"americanfootball_nfl": "NFL", "baseball_mlb": "MLB"}
PIN_SUFFIX = {"pass tds": "Total Touchdown Passes", "pass yards": "Total Passing Yards", "receptions": "Total Receptions",
              "receiving yards": "Total Receiving Yards", "rush yards": "Total Rushing Yards",
              "total bases": "Total Bases", "home runs": "Total Home Runs", "pitcher strikeouts": "Total Strikeouts",
              "pitching outs": "Total Pitching Outs"}
BREAKEVEN_2 = 3 ** -0.5
MIN_REMAINING = 60  # never spend the last 60 free credits automatically


def key():
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("ODDS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("ODDS_API_KEY missing in .env")


def get(path):
    r = urllib.request.urlopen(f"{API}{path}{'&' if '?' in path else '?'}apiKey={key()}", timeout=30)
    return json.load(r), int(r.headers.get("x-requests-remaining", -1))


def pull(sport, hours):
    events, left = get(f"/sports/{sport}/events")  # free
    now = datetime.now(timezone.utc)
    events = [e for e in events if now < datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) < now + timedelta(hours=hours)]
    RAW.mkdir(parents=True, exist_ok=True)
    out = []
    for e in events:
        mk = SPORT_MARKETS[sport]
        if left != -1 and left - len(mk) < MIN_REMAINING:
            print(f"STOP: only {left} credits left (floor {MIN_REMAINING})"); break
        d, left = get(f"/sports/{sport}/events/{e['id']}/odds?regions=us_dfs&markets={','.join(mk)}&oddsFormat=american")
        ts = now.isoformat(timespec="seconds")
        (RAW / f"{sport}_{e['id']}_{now:%Y%m%dT%H%M%SZ}.json").write_text(json.dumps(d))
        out.append((dict(e, league=LEAGUE[sport]), d, ts))
    print(f"{len(out)} events pulled; credits remaining {left}")
    return out


def store(con, pulls):
    con.executescript((ROOT / "schema.sql").read_text())
    last = {(e, s): p for e, s, p in con.execute(
        "SELECT event_key, selection, json_extract(extra,'$.points') FROM snapshots WHERE rowid IN (SELECT max(rowid) FROM snapshots WHERE source='prizepicks' GROUP BY event_key, selection)")}
    rows = []
    for e, d, ts in pulls:
        for b in d.get("bookmakers", []):
            if b["key"] != "prizepicks":
                continue
            for m in b["markets"]:
                for o in m["outcomes"]:
                    if o["name"] != "Over":
                        continue
                    sel = f"{o['description']}|{MARKETS[m['key']]}"
                    if last.get((e["id"], sel)) != o.get("point"):
                        rows.append((ts, "prizepicks", e.get("league", "NFL"), e["id"], e["commence_time"], sel, None, None, None, None, None,
                                     json.dumps({"points": o.get("point"), "market": m["key"]})))
    con.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return rows


def pinnacle(con, player, stat):
    desc = f"{player} {PIN_SUFFIX[stat]}"
    r = {s.rsplit("|", 1)[1]: (p, pts) for s, p, pts in con.execute(
        """SELECT selection, price, json_extract(extra,'$.points') FROM snapshots WHERE rowid IN (SELECT max(rowid) FROM snapshots
           WHERE source='pinnacle_prop' AND selection IN (?,?) GROUP BY selection) AND price IS NOT NULL""", (desc + "|Over", desc + "|Under"))}
    return r if len(r) == 2 and r["Over"][1] == r["Under"][1] else None


def scan(con, pulls, verify):
    cands, report = [], []
    for e, d, ts in pulls:
        pp = next((b for b in d.get("bookmakers", []) if b["key"] == "prizepicks"), None)
        for m in (pp or {}).get("markets", []):
            stat = MARKETS[m["key"]]
            for o in m["outcomes"]:
                if o["name"] != "Over":
                    continue
                player, line = o["description"], o.get("point")
                pin = pinnacle(con, player, stat)
                if not pin:
                    report.append((player, stat, line, "no Pinnacle market", None)); continue
                (po, lo_o, _), (pu, lo_u, _) = devig_range([american_to_prob(pin["Over"][0]), american_to_prob(pin["Under"][0])])
                pl = pin["Over"][1]
                if pl != line:
                    better = "MORE" if line < pl else "LESS"
                    report.append((player, stat, line, f"Pinnacle line {pl}: {better} at PP is better than Pinnacle's {better} "
                                   f"({(po if better == 'MORE' else pu):.3f}) but the size of the gain is unpriced", None))
                    continue
                side, p, lo = ("MORE", po, lo_o) if po >= pu else ("LESS", pu, lo_u)
                report.append((player, stat, line, f"same line: {side} fair {p:.3f}", p))
                if lo >= BREAKEVEN_2 + 0.03:
                    cands.append(f"{player}|{stat}|{line}|{side}")
    print(f"\nPrizePicks board vs Pinnacle ({len(report)} props):")
    for pl, st, ln, msg, p in sorted(report, key=lambda x: -(x[4] or 0)):
        print(f"  {pl[:22]:22} {st:15} {ln!s:>6}  {msg}")
    print(f"\ncandidates clearing 2-pick break-even {BREAKEVEN_2:.3f} + 3pp at the SAME line: {cands or 'NONE'}")
    for c in cands if verify else []:
        out = subprocess.run([sys.executable, "verify.py", c, "--note", "PP_BOARD (Odds API line)"], cwd=ROOT, capture_output=True, text=True).stdout
        print("  ", c, "->", next((l for l in out.splitlines() if l.startswith("PRE-BET GATE")), "verify error"))
    return cands


if __name__ == "__main__":
    sport = sys.argv[sys.argv.index("--sport") + 1] if "--sport" in sys.argv else "americanfootball_nfl"
    hours = float(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 30
    con = sqlite3.connect(DB, timeout=60)
    p = pull(sport, hours)
    print(f"stored {len(store(con, p))} new/changed PrizePicks lines")
    scan(con, p, "--verify" in sys.argv)
