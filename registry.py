"""REGISTRY_v1.0 - universal market registry.
Discovers every open sports event on Kalshi (winner markets) and Pinnacle (all sports), assigns canonical event IDs,
matches events across platforms with MATCH_v1.0 confidence, infers the league map from matched events (no
hard-coding), checks settlement equivalence with RULES_v1.0, and writes the SPORT COVERAGE MATRIX.
Usage: python3 registry.py            -> refresh registry + print coverage summary
       python3 registry.py --show N   -> also print N example matches"""
import csv, hashlib, json, re, sqlite3, sys, time, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import match, rules
from odds import american_to_prob, devig_all

ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.db"
KAL = "https://api.elections.kalshi.com/trade-api/v2"
PIN = "https://guest.api.arcadia.pinnacle.com/0.1"
PIN_H = {"X-API-Key": "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R", "User-Agent": "Mozilla/5.0"}
# canonical sport names; Kalshi tag -> canonical, Pinnacle sport name -> canonical
SPORT = {"E Sports": "Esports", "Mixed Martial Arts": "MMA", "Rugby Union": "Rugby", "Rugby League": "Rugby"}
CLOUD_LEAGUES = {"NFL", "NCAA", "MLB", "NHL", "NBA", "WNBA"}  # Pinnacle leagues the cloud collector covers today
TAXONOMY = {"GAME": "GAME_WINNER", "MATCH": "MATCH_WINNER"}  # Kalshi series suffix -> canonical (2- or 3-way decided by outcomes)
SCHEMA = """
CREATE TABLE IF NOT EXISTS reg_events (event_id TEXT PRIMARY KEY, sport TEXT, league TEXT, start_utc TEXT,
  participants TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS reg_sources (event_id TEXT, source TEXT, source_event_id TEXT, source_league TEXT,
  market_type TEXT, outcomes TEXT, match_confidence INTEGER, match_reason TEXT, equivalence TEXT, eq_reasons TEXT,
  updated TEXT, PRIMARY KEY (source, source_event_id));
CREATE TABLE IF NOT EXISTS reg_league_map (kalshi_series TEXT PRIMARY KEY, sport TEXT, pinnacle_league TEXT,
  n_kalshi_events INTEGER, n_matched INTEGER, share REAL, updated TEXT);
"""


def get(url, headers=None, pause=0.0):
    for i in range(4):
        try:
            time.sleep(pause)
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers or {"User-Agent": "curl/8.1.2"}), timeout=60))
        except urllib.error.HTTPError as e:
            if e.code not in (403, 429) or i == 3:  # Pinnacle throttles with 403
                raise
            time.sleep(8 * (i + 1))
        except OSError:  # timeouts, resets, DNS blips (crashed the 05:00 build twice)
            if i == 3:
                raise
            time.sleep(8 * (i + 1))


# ---------- discovery ----------
def kalshi_events():
    series = get(f"{KAL}/series?category=Sports&limit=5000")["series"]
    meta = {s["ticker"]: s for s in series}
    out, cursor = [], ""
    while True:
        d = get(f"{KAL}/events?status=open&with_nested_markets=true&limit=200&cursor={cursor}", pause=0.25)
        for e in d["events"]:
            s = meta.get(e["series_ticker"])
            suffix = next((k for k in TAXONOMY if e["series_ticker"].endswith(k)), None)
            if not s or not suffix or not e.get("markets"):
                continue
            outs = [m["yes_sub_title"] for m in e["markets"]]
            parts = [o for o in outs if o.lower() not in ("tie", "draw")]
            dm = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", e["event_ticker"])
            if not dm or len(parts) != 2:
                continue
            date = datetime.strptime("".join(dm.groups()), "%y%b%d").date()
            rt = e["markets"][0]["rules_primary"] + " " + e["markets"][0]["rules_secondary"]
            tm = re.search(r"scheduled for (\w{3} \d{1,2}, \d{4}) at (\d{1,2}:\d{2} [AP]M) (EDT|EST|ET)", rt)
            t_utc = None
            if tm:
                t_utc = datetime.strptime(f"{tm.group(1)} {tm.group(2)}", "%b %d, %Y %I:%M %p").replace(tzinfo=match.ET).astimezone(timezone.utc).isoformat()
            tags = s.get("tags") or []
            out.append(dict(source_id=e["event_ticker"], series=e["series_ticker"], league=s["title"],
                            sport=SPORT.get(tags[0], tags[0]) if tags else "Unknown", date=date, time_utc=t_utc,
                            participants=parts, n_outcomes=len(outs), market_type=TAXONOMY[suffix],
                            rules=rt, prices={m["yes_sub_title"]: (m.get("yes_bid_dollars"), m.get("yes_ask_dollars")) for m in e["markets"]},
                            fee_mult=s.get("fee_multiplier")))
        cursor = d.get("cursor")
        if not cursor or not d["events"]:
            return out, series


def pinnacle_events():
    sports = [s for s in get(f"{PIN}/sports", PIN_H) if s.get("matchupCount") and s["name"] not in NON_SPORTS]
    out = []
    for s in sports:
        try:
            ms = [m for m in get(f"{PIN}/sports/{s['id']}/matchups", PIN_H, pause=1.5)
                  if m.get("type") == "matchup" and not m.get("parentId") and not m.get("isLive")]
            straight = get(f"{PIN}/sports/{s['id']}/markets/straight?primaryOnly=true", PIN_H, pause=1.5)
            SOURCE_HEALTH.append((f"pinnacle/{s['name']}", "HEALTHY", len(ms)))
        except Exception as e:  # rule 63: a failed source is recorded and skipped, never guessed around
            SOURCE_HEALTH.append((f"pinnacle/{s['name']}", "FAILED", repr(e)[:80]))
            continue
        ml = {}
        for k in straight:
            if k["type"] == "moneyline" and k["period"] == 0 and not k.get("isAlternate"):
                ml[k["matchupId"]] = {p["designation"]: p["price"] for p in k["prices"] if "designation" in p}  # some sports price by participantId
        for m in ms:
            names = {p["alignment"]: p["name"] for p in m["participants"] if p.get("alignment") in ("home", "away")}
            if len(names) != 2:
                continue
            out.append(dict(id=str(m["id"]), sport=SPORT.get(s["name"], s["name"]), league=m["league"]["name"],
                            start=m["startTime"], participants=[names["home"], names["away"]], ml=ml.get(m["id"], {})))
    return out


# ---------- registry ----------
def canonical_id(sport, start_iso, participants):
    key = f"{sport}|{match.et_date(start_iso)}|{'|'.join(sorted(match.core(p) for p in participants))}"
    return "EV-" + hashlib.sha1(key.encode()).hexdigest()[:12]


def build():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    kal, series = kalshi_events()
    pin = pinnacle_events()
    by_sport = defaultdict(list)
    for p in pin:
        by_sport[p["sport"]].append(p)
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    matches = []
    for k in kal:
        best, conf, why = match.match_event(k, by_sport.get(k["sport"], []))
        eq, eqr = ("NOT_COMPARABLE", ["unmatched"])
        if best:
            n_pin = len(best["ml"]) if best["ml"] else 0
            eq, eqr = rules.equivalence(k["sport"], k["rules"], k["n_outcomes"], n_pin) if n_pin else ("NOT_COMPARABLE", ["no Pinnacle moneyline posted"])
        matches.append((k, best, conf, why, eq, eqr))
    for p in pin:
        eid = canonical_id(p["sport"], p["start"], p["participants"])
        con.execute("INSERT OR REPLACE INTO reg_events VALUES (?,?,?,?,?,?)", (eid, p["sport"], p["league"], p["start"], json.dumps(p["participants"]), now))
        con.execute("INSERT OR REPLACE INTO reg_sources VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (eid, "pinnacle", p["id"], p["league"], "MONEYLINE_3WAY" if len(p["ml"]) == 3 else "MONEYLINE_2WAY",
                     json.dumps(list(p["ml"])), 100, "source of record", None, None, now))
    for k, best, conf, why, eq, eqr in matches:
        eid = canonical_id(best["sport"], best["start"], best["participants"]) if best and conf >= match.AUTO_THRESHOLD else \
            canonical_id(k["sport"], f"{k['date']}T12:00:00-04:00", k["participants"])
        if not (best and conf >= match.AUTO_THRESHOLD):
            con.execute("INSERT OR IGNORE INTO reg_events VALUES (?,?,?,?,?,?)", (eid, k["sport"], k["league"], k["time_utc"], json.dumps(k["participants"]), now))
        con.execute("INSERT OR REPLACE INTO reg_sources VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (eid, "kalshi", k["source_id"], k["series"], f"{k['market_type']}_{k['n_outcomes']}WAY",
                     json.dumps(k["participants"]), conf, why, eq, json.dumps(eqr), now))
    # league map inferred from matched events
    per = defaultdict(Counter)
    tot = Counter(k["series"] for k in kal)
    for k, best, conf, *_ in matches:
        if best and conf >= match.AUTO_THRESHOLD:
            per[k["series"]][best["league"]] += 1
    lmap = {}
    for s_, n in tot.items():
        lg, c = per[s_].most_common(1)[0] if per[s_] else (None, 0)
        sp = next(k["sport"] for k in kal if k["series"] == s_)
        lmap[s_] = (lg, c, n)
        con.execute("INSERT OR REPLACE INTO reg_league_map VALUES (?,?,?,?,?,?,?)", (s_, sp, lg, n, c, c / n if n else 0, now))
    con.commit()
    return kal, pin, matches, lmap, series


def coverage(kal, pin, matches, lmap, series):
    rows = []
    kal_series = {k["series"]: k for k in kal}
    for s_, (lg, nm, n) in sorted(lmap.items(), key=lambda x: (kal_series[x[0]]["sport"], x[0])):
        k0 = kal_series[s_]
        ms = [m for m in matches if m[0]["series"] == s_]
        auto = [m for m in ms if m[1] and m[2] >= match.AUTO_THRESHOLD]
        eqs = Counter(m[4] for m in auto)
        eq = eqs.most_common(1)[0][0] if eqs else "-"
        rules_ok = eq in ("EXACT", "NEAR_EQUIVALENT")
        cloud = lg in CLOUD_LEAGUES
        status = "DISCOVERED"
        if lg:
            status = "INGESTION READY"
        if auto and nm / n >= 0.5:
            status = "MATCHING READY"
        if status == "MATCHING READY" and rules_ok and cloud:
            status = "MARKET-ONLY PAPER READY"
        rows.append(dict(sport=k0["sport"], league=k0["league"], kalshi_series=s_, kalshi_open_events=n,
                         prizepicks="unknown (API blocked)", pinnacle=lg or "not found", pinnacle_matched=nm,
                         secondary="not investigated", historical=f"Kalshi yes; Pinnacle {'since 2026-09-27' if cloud else 'no (not collected)'}",
                         live="yes" if lg else "Kalshi only", rules=eq, matcher_tested="yes" if k0["sport"] in TESTED_SPORTS else "no",
                         market_only_ready="yes" if status == "MARKET-ONLY PAPER READY" else "no",
                         player_model="NFL pass TDs (v0.2, unvalidated)" if s_ == "KXNFLGAME" else "none",
                         model_validated="no", status=status))
    kal_leagues = {lg for lg, *_ in lmap.values() if lg}
    for lg, spt in sorted({(p["league"], p["sport"]) for p in pin} - {(l, None) for l in ()}):
        if lg not in kal_leagues:
            rows.append(dict(sport=spt, league=lg, kalshi_series="-", kalshi_open_events=0, prizepicks="unknown (API blocked)",
                             pinnacle=lg, pinnacle_matched=0, secondary="not investigated", historical="no",
                             live="Pinnacle only", rules="-", matcher_tested="-", market_only_ready="no",
                             player_model="none", model_validated="no", status="DISCOVERED (no Kalshi market)"))
    return rows


NON_SPORTS = {"Politics", "Entertainment"}
SOURCE_HEALTH = []
TESTED_SPORTS = {"Soccer", "Football", "Basketball", "Baseball", "Hockey", "Tennis", "Esports"}  # see test_engine.py

if __name__ == "__main__":
    t0 = time.time()
    kal, pin, matches, lmap, series = build()
    rows = coverage(kal, pin, matches, lmap, series)
    (ROOT / "results").mkdir(exist_ok=True)
    with open(ROOT / "results/coverage_matrix.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(), w.writerows(rows)
    auto = [m for m in matches if m[1] and m[2] >= match.AUTO_THRESHOLD]
    print(f"REGISTRY_v1.0 built in {time.time() - t0:.0f}s: Kalshi winner events {len(kal)} in {len(lmap)} series | "
          f"Pinnacle events {len(pin)} | auto-matched {len(auto)} | below threshold/ambiguous "
          f"{sum(1 for m in matches if m[1] and m[2] < match.AUTO_THRESHOLD)} | unmatched {sum(1 for m in matches if not m[1])}")
    print("source health:", "; ".join(f"{a} {b} ({c})" for a, b, c in SOURCE_HEALTH))
    print("equivalence of auto-matched:", dict(Counter(m[4] for m in auto)))
    print("coverage status counts:", dict(Counter(r["status"] for r in rows)))
    if "--show" in sys.argv:
        n = int(sys.argv[sys.argv.index("--show") + 1])
        for k, best, conf, why, eq, eqr in sorted(matches, key=lambda m: -m[2])[:n]:
            print(f"  [{conf:3}] {k['source_id']:32} {' vs '.join(k['participants'])[:40]:40} -> "
                  f"{(best['league'] + ': ' + ' vs '.join(best['participants']))[:60] if best else '-':60} {eq}")
