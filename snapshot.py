"""DATA_v1.1 collector -> data/market.db (append-only).
Sources: Kalshi game winners (full rows), Pinnacle moneylines (full rows), Pinnacle player props and Kalshi NFL
prop ladders (change-only rows). Every source/league pull writes a collector_runs heartbeat row (ok or error), so
"price unchanged" and "not collected" can always be told apart.
Adaptive cadence: launchd fires every 2 min; each league is pulled only when due (see interval_for).
Usage: python3 snapshot.py [--force]"""
import json, sqlite3, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.db"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
PINNACLE = "https://guest.api.arcadia.pinnacle.com/0.1"
# ponytail: the public key Pinnacle's own website ships to every browser. Unofficial; breaks if they rotate it.
PIN_HEADERS = {"X-API-Key": "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R", "User-Agent": "Mozilla/5.0"}
# league -> (kalshi series, pinnacle league id). Two-way game-winner markets only (no soccer 3-way yet).
LEAGUES = {
    "NFL": ("KXNFLGAME", 889), "NCAAF": ("KXNCAAFGAME", 880), "MLB": ("KXMLBGAME", 246),
    "NHL": ("KXNHLGAME", 1456), "NBA": ("KXNBAGAME", 487), "WNBA": ("KXWNBAGAME", 578),
}


def get(url, headers=None, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": "quant-betting/0.1"})
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def kalshi_rows(league, series, ts):
    mult = get(f"{KALSHI}/series/{series}")["series"].get("fee_multiplier")
    rows, cursor = [], ""
    while True:
        d = get(f"{KALSHI}/markets?series_ticker={series}&status=open&limit=1000&cursor={cursor}")
        for m in d["markets"]:
            f = lambda k: float(m[k]) if m.get(k) not in (None, "") else None
            rows.append((ts, "kalshi", league, m["event_ticker"], m.get("occurrence_datetime"), m["yes_sub_title"],
                         f("yes_bid_dollars"), f("yes_ask_dollars"), None, f("yes_bid_size_fp"), f("yes_ask_size_fp"),
                         json.dumps({"ticker": m["ticker"], "fee_multiplier": mult, "volume": f("volume_fp"),
                                     "open_interest": f("open_interest_fp"), "last": f("last_price_dollars")})))
        cursor = d.get("cursor")
        if not cursor:
            return rows


def pinnacle_rows(league, lid, ts):
    """Game moneylines (source 'pinnacle') plus player props (source 'pinnacle_prop')."""
    allm = {m["id"]: m for m in get(f"{PINNACLE}/leagues/{lid}/matchups", PIN_HEADERS) if not m.get("isLive")}
    matchups = {i: m for i, m in allm.items() if m.get("type") == "matchup" and not m.get("parentId")}
    rows = []
    for k in get(f"{PINNACLE}/leagues/{lid}/markets/straight", PIN_HEADERS):
        sp = allm.get(k["matchupId"])
        if sp and (sp.get("special") or {}).get("category") == "Player Props" and k.get("status") in (None, "open"):
            names = {p["id"]: p["name"] for p in sp["participants"]}
            for pr in k["prices"]:
                side = names.get(pr.get("participantId"))
                if side in ("Over", "Under") and pr.get("points") is not None:
                    rows.append((ts, "pinnacle_prop", league, str(sp["id"]), sp.get("startTime"),
                                 f"{sp['special']['description']}|{side}", None, None, pr["price"], None, None,
                                 json.dumps({"points": pr["points"], "game": (sp.get("parent") or {}).get("id"),
                                             "limit": (k.get("limits") or [{}])[0].get("amount")})))
            continue
        g = matchups.get(k["matchupId"])
        if not g or k["type"] != "moneyline" or k["period"] != 0 or k.get("isAlternate") or k.get("status") != "open":
            continue
        names = {p["alignment"]: p["name"] for p in g["participants"]}
        for pr in k["prices"]:
            if pr.get("designation") in names:
                rows.append((ts, "pinnacle", league, str(g["id"]), g["startTime"], names[pr["designation"]],
                             None, None, pr["price"], None, None,
                             json.dumps({"side": pr["designation"], "limit": (k.get("limits") or [{}])[0].get("amount")})))
    return rows


KALSHI_PROP_SERIES = ["KXNFLPASSTDS", "KXNFLPASSYDS", "KXNFLREC", "KXNFLRECYDS", "KXNFLRSHYDS",
                      "KXNFLPASSCOMP", "KXNFLPASSATT", "KXNFLRSHATT", "KXNFLPASSINT"]
CHANGE_ONLY = ("pinnacle_prop", "kalshi_prop")


def kalshi_prop_rows(league, _, ts):
    rows = []
    for series in KALSHI_PROP_SERIES:
        cursor = ""
        while True:
            d = get(f"{KALSHI}/markets?series_ticker={series}&status=open&limit=1000&cursor={cursor}")
            for m in d["markets"]:
                f = lambda k: float(m[k]) if m.get(k) not in (None, "") else None
                rows.append((ts, "kalshi_prop", league, m["event_ticker"], m.get("occurrence_datetime"), m["title"],
                             f("yes_bid_dollars"), f("yes_ask_dollars"), None, f("yes_bid_size_fp"), f("yes_ask_size_fp"),
                             json.dumps({"ticker": m["ticker"], "series": series, "volume": f("volume_fp")})))
            cursor = d.get("cursor")
            if not cursor or not d["markets"]:
                break
    return rows


def interval_for(con, league, now):
    """Minutes between pulls: 2 in the final hour before any game in the league, 5 within 3h, 10 within 12h, else 30."""
    nxt = con.execute("""SELECT min(start_time) FROM snapshots WHERE source='pinnacle' AND league=? AND start_time > ?
                         AND price IS NOT NULL""", (league, now.isoformat())).fetchone()[0]
    if not nxt:
        return 30
    mins = (datetime.fromisoformat(nxt.replace("Z", "+00:00")) - now).total_seconds() / 60
    return 2 if mins <= 60 else 5 if mins <= 180 else 10 if mins <= 720 else 30


def only_changed(con, rows):
    """Prop sources store a row only when line/price/bid/ask moved. With collector_runs this is lossless:
    the price at time T = last change row before T, valid only if a successful run covers T."""
    key = lambda r: (r[6], r[7], r[8], json.loads(r[11]).get("points"))
    out = []
    for src in {r[1] for r in rows}:
        mine = [r for r in rows if r[1] == src]
        if src not in CHANGE_ONLY:
            out += mine
            continue
        last = {(e, sel): (b, a, p, pts) for e, sel, b, a, p, pts in con.execute(
            """SELECT event_key, selection, bid, ask, price, json_extract(extra,'$.points') FROM snapshots
               WHERE rowid IN (SELECT max(rowid) FROM snapshots WHERE source=? GROUP BY event_key, selection)""", (src,))}
        out += [r for r in mine if last.get((r[3], r[5])) != key(r)]
    return out


def kalshi_jobs(con):
    """Legacy six + every Kalshi series the registry has matched to Pinnacle at least once (DATA_v1.3)."""
    jobs = {label: series for label, (series, _) in LEAGUES.items()}
    try:
        for (series,) in con.execute("SELECT kalshi_series FROM reg_league_map WHERE n_matched > 0"):
            if series not in jobs.values():
                jobs[series] = series
    except sqlite3.OperationalError:
        pass  # registry not built yet
    return jobs


def main():
    now = datetime.now(timezone.utc)
    ts = now.isoformat(timespec="seconds")
    force = "--force" in sys.argv
    con = sqlite3.connect(DB, timeout=60)
    con.executescript((ROOT / "schema.sql").read_text())
    import status
    for league, series in kalshi_jobs(con).items():
        lid = LEAGUES.get(league, (None, None))[1]
        last = con.execute("SELECT max(ts) FROM collector_runs WHERE league=? AND source='kalshi' AND ok=1", (league,)).fetchone()[0]
        due = status._cadence(status.next_start(con, league, "kalshi", now))
        if not force and last and (now - datetime.fromisoformat(last)).total_seconds() < due * 60 - 30:
            continue
        # Pinnacle is collected 24/7 by the Cloudflare Worker (cloud/) and pulled in by cloud_sync.py.
        # Kalshi blocks Cloudflare egress, so Kalshi stays here (runs only while this Mac is awake).
        jobs = [("kalshi", kalshi_rows, series)] + ([("pinnacle", pinnacle_rows, lid)] if "--with-pinnacle" in sys.argv and lid else [])
        if league == "NFL":
            jobs.append(("kalshi_prop", kalshi_prop_rows, None))
        for name, fn, arg in jobs:
            t0 = time.time()
            try:
                seen = fn(league, arg, ts)
                rows = only_changed(con, seen)
                con.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                con.execute("INSERT INTO collector_runs VALUES (?,?,?,?,?,?,?,?)", (ts, league, name, 1, len(seen), len(rows), None, time.time() - t0))
                if name == "pinnacle":  # laptop FALLBACK for the cloud (CPU-limited): record the heartbeats the readers check
                    sport = {"NFL": "Football", "NCAAF": "Football", "MLB": "Baseball", "NHL": "Hockey", "NBA": "Basketball", "WNBA": "Basketball"}[league]
                    con.execute("INSERT INTO collector_runs VALUES (?,?,?,?,?,?,?,?)", (ts, league, "pinnacle_prop", 1, sum(r[1] == "pinnacle_prop" for r in seen), None, "laptop fallback", 0))
                    con.execute("INSERT INTO collector_runs VALUES (?,?,?,?,?,?,?,?)", (ts, f"SPORT:{sport}", "pinnacle", 1, sum(r[1] == "pinnacle" for r in seen), None, f"laptop fallback ({league} only)", 0))
                print(f"{ts} {league:6} {name:11} seen {len(seen):5} written {len(rows):5} (every {due} min)")
            except Exception as e:  # one dead source must not kill the rest of the pull
                con.execute("INSERT INTO collector_runs VALUES (?,?,?,?,?,?,?,?)", (ts, league, name, 0, None, None, repr(e)[:300], time.time() - t0))
                print(f"{ts} {league:6} {name:11} FAILED: {e!r}")
            con.commit()


if __name__ == "__main__":
    main()
