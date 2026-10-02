"""VERIFY_v2.0 - multi-layer pre-bet verification for PrizePicks NFL legs (protocol in VERIFY.md).
Every check prints its evidence. Anything that can't be fetched shows as MANUAL, never as passed.

  python3 verify.py "Jacoby Brissett|pass tds|1.5|LESS" "Daniel Jones|pass tds|1.5|LESS" \
      [--payout 3 --entry power] [--slip-verified] [--starters-confirmed]

--payout/--entry come from YOUR slip screenshot. Without them there is no final EV: AWAITING SLIP VERIFICATION."""
import argparse, csv, hashlib, json, math, sqlite3, time, urllib.request
import status as collector_status
import versions
from datetime import datetime, timedelta, timezone
from pathlib import Path
from odds import american_to_prob, devig_range

ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.db"
PIN = "https://guest.api.arcadia.pinnacle.com/0.1"
PIN_H = {"X-API-Key": "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R", "User-Agent": "Mozilla/5.0"}
KAL = "https://api.elections.kalshi.com/trade-api/v2"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
# PP stat -> (Pinnacle description suffix, Kalshi series, DraftKings/ESPN type, nflverse column or None)
STATS = {
    "pass tds": ("Total Touchdown Passes", "KXNFLPASSTDS", "Total Passing Touchdowns (incl. overtime)", "passing_tds"),
    "pass yards": ("Total Passing Yards", "KXNFLPASSYDS", "Total Passing Yards (incl. overtime)", None),
    "receptions": ("Total Receptions", "KXNFLREC", "Total Receptions (incl. overtime)", "receptions"),
    "receiving yards": ("Total Receiving Yards", "KXNFLRECYDS", "Total Receiving Yards (incl. overtime)", None),
    "rush yards": ("Total Rushing Yards", "KXNFLRSHYDS", "Total Rushing Yards (incl. overtime)", None),
    "pass attempts": ("Total Pass Attempts", "KXNFLPASSATT", None, None),
    "pass completions": ("Total Pass Completions", "KXNFLPASSCOMP", None, None),
    "rush attempts": ("Total Rush Attempts", "KXNFLRSHATT", None, None),
    "interceptions": ("Total Interceptions", "KXNFLPASSINT", None, None),
}
VALIDATED = {"Environment model (team total)": False,        # H7 2026-09-28: beats baseline, 1 calibration bucket misses
             "Statistical model (player history)": False}    # H7 2026-09-28: no significant gain over baseline
SHRINK_K = 6          # ponytail: prior weight in games for the stat model; unvalidated, tune in H5 review
MARGIN_A = 0.03       # conservative P must beat break-even by 3pp for Level A
STRESS = (0.02, 0.04, 0.06)
ESPN_ABBR = {"WAS": "WSH", "LA": "LAR"}
_cache = {}


def get(url, headers=None):
    if url not in _cache:
        for i in range(3):
            try:
                _cache[url] = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers or {"User-Agent": "curl/8.1.2"}), timeout=30))
                break
            except Exception:
                if i == 2:
                    raise
                time.sleep(2)
    return _cache[url]


def poisson_cdf(k, lam):
    return sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(int(k) + 1))


def side_p(p_more, side):
    return p_more if side == "MORE" else 1 - p_more


# ---------- sources ----------
def pinnacle(player, stat, line):
    desc = f"{player} {STATS[stat][0]}"
    allm = {m["id"]: m for m in get(f"{PIN}/leagues/889/matchups", PIN_H)}
    sp = next((m for m in allm.values() if (m.get("special") or {}).get("description") == desc), None)
    if not sp:
        return None
    mk = next((k for k in get(f"{PIN}/leagues/889/markets/straight", PIN_H) if k["matchupId"] == sp["id"]), None)
    if not mk:
        return None
    names = {p["id"]: p["name"] for p in sp["participants"]}
    pr = {names[p["participantId"]]: p for p in mk["prices"] if p.get("participantId") in names}
    o, u = pr["Over"], pr["Under"]
    (pm, lo, hi), _ = devig_range([american_to_prob(o["price"]), american_to_prob(u["price"])])
    return dict(line=o["points"], over=o["price"], under=u["price"], raw_over=american_to_prob(o["price"]),
                raw_under=american_to_prob(u["price"]), p_more=pm, p_more_lo=lo, p_more_hi=hi,
                limit=(mk.get("limits") or [{}])[0].get("amount"), start=sp.get("startTime"),
                game=(sp.get("parent") or {}).get("id"), special_id=sp["id"], desc=desc)


def pinnacle_game(game_id):
    """Spread / total / moneyline for the parent game, plus participant names."""
    allm = {m["id"]: m for m in get(f"{PIN}/leagues/889/matchups", PIN_H)}
    g = allm.get(game_id)
    if not g:
        return None
    teams = {p["alignment"]: p["name"] for p in g["participants"]}
    out = dict(teams=teams)
    for k in get(f"{PIN}/leagues/889/markets/straight", PIN_H):
        if k["matchupId"] != game_id or k["period"] != 0 or k.get("isAlternate"):
            continue
        if k["type"] == "spread":
            out["spread"] = {p["designation"]: (p["points"], p["price"]) for p in k["prices"]}
        elif k["type"] == "total":
            out["total"] = {p["designation"]: (p["points"], p["price"]) for p in k["prices"]}
        elif k["type"] == "moneyline":
            out["ml"] = {p["designation"]: p["price"] for p in k["prices"]}
    return out


def kalshi(player, stat, line):
    """Ladder market 'Player: N+ ...' where N = line + 0.5 gives P(MORE line)."""
    n = int(line + 0.5)
    cursor, hit = "", None
    while True:
        d = get(f"{KAL}/markets?series_ticker={STATS[stat][1]}&status=open&limit=1000&cursor={cursor}")
        for m in d["markets"]:
            if m["title"].startswith(f"{player}: {n}+"):
                hit = m
        cursor = d.get("cursor")
        if hit or not cursor or not d["markets"]:
            break
    if not hit:
        return None
    bid, ask = float(hit["yes_bid_dollars"] or 0), float(hit["yes_ask_dollars"] or 0)
    if not 0 < bid < ask < 1:
        return dict(ticker=hit["ticker"], bid=bid, ask=ask, usable=False)
    return dict(ticker=hit["ticker"], bid=bid, ask=ask, p_more=(bid + ask) / 2, spread=ask - bid,
                volume=float(hit.get("volume_fp") or 0), usable=True, title=hit["title"])


def espn_event(team, start):
    """ESPN files games by US date; a 00:15Z kickoff is the previous US day, so try both."""
    t = datetime.fromisoformat(start.replace("Z", "+00:00"))
    for day in {t.strftime("%Y%m%d"), (t - timedelta(hours=8)).strftime("%Y%m%d")}:
        for e in get(f"{ESPN}/scoreboard?dates={day}")["events"]:
            c = e["competitions"][0]
            if abs((datetime.fromisoformat(e["date"].replace("Z", "+00:00")) - t).total_seconds()) < 6 * 3600 and \
                    any(x["team"]["abbreviation"] == ESPN_ABBR.get(team, team) for x in c["competitors"]):
                return e
    return None


def draftkings_line(event_id, player, stat):
    typ = STATS[stat][2]
    if not typ:
        return None
    items = get(f"{CORE}/events/{event_id}/competitions/{event_id}/odds/100/propBets?limit=2000").get("items", [])
    for x in items:
        if x["type"]["name"] != typ:
            continue
        ref = x["athlete"]["$ref"]
        if get(ref.replace("http://", "https://")).get("displayName") == player:
            return dict(line=x["current"]["target"]["value"], open=x["open"]["target"]["value"], updated=x.get("lastUpdated"))
    return None


def player_rows(player):
    rows = []
    for y in (2025, 2026):
        f = ROOT / f"data/raw/nfl_player_week_{y}.csv"
        if f.exists():
            rows += [r for r in csv.DictReader(open(f)) if r["season_type"] == "REG"]
    return [r for r in rows if r["player_display_name"] == player], rows


def implied_points():
    """(season, week, team) -> implied team points from nflverse closing spread/total."""
    out = {}
    for r in csv.DictReader(open(ROOT / "data/raw/nfl_games.csv")):
        if r["total_line"] and r["spread_line"]:
            t, sp = float(r["total_line"]), float(r["spread_line"])  # spread_line > 0 = home favoured
            out[(r["season"], r["week"], r["home_team"])] = t / 2 + sp / 2
            out[(r["season"], r["week"], r["away_team"])] = t / 2 - sp / 2
    return out


def stat_model(player, stat, line, team_pts=None):
    """STAT_v0.2. Pass TDs: player's TDs per implied team point over his last 17 qualifying games, shrunk to the
    league rate (k games), times tonight's implied team total, Poisson. Other counts: v0.1 per-game rate, no matchup.
    Poisson fit for QB pass TDs checked on 2025 (P<=1 empirical 0.551 vs Poisson 0.566). Hyperparameters unvalidated."""
    col = STATS[stat][3]
    if not col:
        return None
    mine, allr = player_rows(player)
    qual = (lambda r: float(r["attempts"] or 0) >= 15) if col == "passing_tds" else (lambda r: float(r["targets"] or 0) >= 1)
    if not mine:
        return None
    pos = mine[-1]["position"]
    games = sorted((r for r in mine if qual(r)), key=lambda r: (r["season"], int(r["week"])))[-17:]
    if not games:
        return None
    if col == "passing_tds" and team_pts:
        imp = implied_points()
        g = [(float(r[col] or 0), imp[(r["season"], r["week"], r["team"])]) for r in games if (r["season"], r["week"], r["team"]) in imp]
        lg = [(float(r[col] or 0), imp[(r["season"], r["week"], r["team"])]) for r in allr
              if r["season"] == "2025" and r["position"] == pos and qual(r) and (r["season"], r["week"], r["team"]) in imp]
        if g and lg:
            prior = sum(t for t, _ in lg) / sum(p for _, p in lg)
            avg_pts = sum(p for _, p in g) / len(g)
            rate = (sum(t for t, _ in g) + SHRINK_K * avg_pts * prior) / (sum(p for _, p in g) + SHRINK_K * avg_pts)
            lam = rate * team_pts
            return dict(lam=lam, n=len(g), raw=sum(t for t, _ in g) / sum(p for _, p in g), prior=prior,
                        p_more=1 - poisson_cdf(math.floor(line), lam), version="STAT_v0.2 per implied point",
                        note=f"his TDs/implied pt {sum(t for t, _ in g) / sum(p for _, p in g):.4f} (league {prior:.4f}) x {team_pts:.1f} pts")
    league = [float(r[col] or 0) for r in allr if r["season"] == "2025" and r["position"] == pos and qual(r)]
    m, prior = sum(float(r[col] or 0) for r in games) / len(games), sum(league) / len(league)
    lam = (len(games) * m + SHRINK_K * prior) / (len(games) + SHRINK_K)
    return None  # STAT_v0.1 (per-game rate) RETIRED 2026-09-28: blind to role changes (Ferguson 0.11 vs market 0.59). Needs a backtested role-aware model.


def env_model(stat, line, team_pts):
    """Pass-TD expectation from the implied team total x league pass-TDs-per-point (2025 REG), Poisson."""
    if STATS[stat][3] != "passing_tds" or team_pts is None:
        return None
    games = [r for r in csv.DictReader(open(ROOT / "data/raw/nfl_games.csv")) if r["season"] == "2025" and r["game_type"] == "REG" and r["home_score"]]
    pts = sum(int(r["home_score"]) + int(r["away_score"]) for r in games)
    ptd = sum(float(r["passing_tds"] or 0) for r in csv.DictReader(open(ROOT / "data/raw/nfl_player_week_2025.csv")) if r["season_type"] == "REG")
    ratio = ptd / pts
    lam = team_pts * ratio
    return dict(ratio=ratio, lam=lam, p_more=1 - poisson_cdf(math.floor(line), lam))


def pin_history(desc, side):
    """Our collector's first-seen vs latest no-vig P for this prop (movement)."""
    con = sqlite3.connect(DB, timeout=60)
    rows = con.execute("""SELECT ts, selection, price, json_extract(extra,'$.points') FROM snapshots WHERE source='pinnacle_prop'
                          AND selection IN (?, ?) AND price IS NOT NULL ORDER BY ts""", (desc + "|Over", desc + "|Under")).fetchall()
    by_ts = {}
    for ts, sel, price, pts in rows:
        by_ts.setdefault(ts, {})[sel.rsplit("|", 1)[1]] = (price, pts)
    last, snaps = {}, []
    for ts in sorted(by_ts):
        last.update(by_ts[ts])
        if len(last) == 2 and last["Over"][1] == last["Under"][1]:
            pm = devig_range([american_to_prob(last["Over"][0]), american_to_prob(last["Under"][0])])[0][0]
            snaps.append((ts, last["Over"][1], side_p(pm, side)))
    return snaps


# ---------- the protocol ----------
def verify_leg(player, stat, line, side, a):
    line, R = float(line), dict(player=player, stat=stat, line=line, side=side, checks=[], bear=[], inputs={})
    chk = lambda name, status, note: R["checks"].append((name, status, note))
    now = datetime.now(timezone.utc)

    # 1 user market
    chk("Exact PrizePicks line confirmed", "ok" if a.slip_verified else "manual", f"{player} {stat} {line} {side}" + ("" if a.slip_verified else " (from your message, not a slip)"))
    chk("Line type standard (not goblin/demon)", "ok" if a.slip_verified else "manual", "needs slip screenshot")
    chk("Actual payout structure", "ok" if a.payout else "manual", f"{a.entry} {a.payout}x" if a.payout else "AWAITING SLIP VERIFICATION")

    con = sqlite3.connect(DB, timeout=60)
    # Gate on the cloud Pinnacle feed (closing-line source). Kalshi is fetched live below; its history is laptop-only.
    health = [h for h in collector_status.health(con, now)
              if (h["league"], h["source"]) in (("NFL", "pinnacle_prop"), ("SPORT:Football", "pinnacle"))]
    bad = [f"{h['league']}/{h['source']} {h['state']} ({(h['age_min'] or 0):.0f}m old)" for h in health if h["state"] in ("STALE", "FAILED")]
    chk("Data collector healthy", "fail" if bad or not health else "ok",
        ("DATA STALE: " + ", ".join(bad)) if bad else ", ".join(f"{h['source']} last ok {(h['age_min'] or 0):.0f}m ago (cadence {h['due_min']}m)" for h in health))
    R["inputs"]["collector"] = health

    # 2-5 prices
    pin = pinnacle(player, stat, line)
    R["inputs"]["pinnacle"] = pin
    if not pin:
        chk("Pinnacle price", "fail", "no Pinnacle market for this prop")
        return R
    start = datetime.fromisoformat(pin["start"].replace("Z", "+00:00"))
    R.update(start=pin["start"], game=pin["game"])
    if start <= now:
        chk("Event not started", "fail", f"started {pin['start']}; not bettable")
    else:
        chk("Event not started", "ok", f"starts {pin['start']} ({(start - now).total_seconds() / 3600:.1f}h)")
    chk("Current price refresh", "ok", f"Pinnacle + Kalshi fetched live at {now.isoformat(timespec='seconds')} (this run)")
    p_pin = side_p(pin["p_more"], side)
    p_pin_lo = min(side_p(pin["p_more_lo"], side), side_p(pin["p_more_hi"], side))
    chk("Pinnacle price + vig removed", "ok" if pin["line"] == line else "warn",
        f"line {pin['line']} O {pin['over']:+.0f}/U {pin['under']:+.0f} raw {pin['raw_over']:.3f}/{pin['raw_under']:.3f} "
        f"(overround {pin['raw_over'] + pin['raw_under'] - 1:.3f}) -> no-vig {side} {p_pin:.3f} (method range low {p_pin_lo:.3f})")
    kal = kalshi(player, stat, line)
    R["inputs"]["kalshi"] = kal
    if kal and kal["usable"]:
        p_kal = side_p(kal["p_more"], side)
        p_kal_cons = kal["bid"] if side == "MORE" else 1 - kal["ask"]
        chk("Kalshi exchange price", "ok", f"{kal['title']} bid {kal['bid']:.2f}/ask {kal['ask']:.2f} vol {kal['volume']:.0f} -> {side} mid {p_kal:.3f} (conservative {p_kal_cons:.3f})")
    else:
        p_kal = p_kal_cons = None
        chk("Kalshi exchange price", "warn", "no usable Kalshi ladder at this exact line" + (f" ({kal['ticker']} bid {kal['bid']} ask {kal['ask']})" if kal else ""))

    mine, _ = player_rows(player)
    team = mine[-1]["team"] if mine else None
    R.update(team=team, pos=mine[-1]["position"] if mine else None)
    ev = espn_event(team, pin["start"]) if team else None
    dk = draftkings_line(ev["id"], player, stat) if ev else None
    R["inputs"]["draftkings"], R["inputs"]["espn_event"] = dk, ev and ev["id"]
    lines = {"PrizePicks": line, "Pinnacle": pin["line"]} | ({"DraftKings": dk["line"]} if dk else {})
    outliers = [k for k, v in lines.items() if v != line]
    chk("Line consensus", "ok" if not outliers else "warn",
        ", ".join(f"{k} {v}" for k, v in lines.items()) + (f" | OUTLIER vs PP: {outliers}" if outliers else " | all agree") + (" | DK prices not exposed, line only" if dk else " | DK line unavailable"))

    sources = [x for x in (p_pin, p_kal) if x is not None]
    if len(sources) >= 2 and abs(p_pin - p_kal) > 0.05:
        R["bear"].append(f"Pinnacle {p_pin:.3f} vs Kalshi {p_kal:.3f} disagree by {abs(p_pin - p_kal) * 100:.1f}pp")
    R["p_consensus"] = sum(sources) / len(sources)
    R["p_conservative"] = min([p_pin_lo] + ([p_kal_cons] if p_kal_cons is not None else []))
    chk("Market consensus", "ok" if len(sources) >= 2 else "warn",
        f"{len(sources)} priced source(s): consensus {R['p_consensus']:.3f}, conservative {R['p_conservative']:.3f}"
        + (" (single source: Pinnacle only)" if len(sources) < 2 else ""))

    lim, vol = pin["limit"], (kal or {}).get("volume", 0)
    quality = "HIGH" if (lim or 0) >= 1000 and len(sources) >= 2 and vol >= 5000 else "MEDIUM" if (lim or 0) >= 250 or vol >= 5000 else "LOW"
    chk("Market quality / limits", "ok" if quality != "LOW" else "warn", f"{quality}: Pinnacle limit ${lim}, Kalshi volume {vol:.0f}, {len(sources)} priced sources")
    if quality == "LOW":
        R["bear"].append("low-information market: small limits / thin exchange volume")

    # 6-7 news + weather
    if ev:
        summ = get(f"{ESPN}/summary?event={ev['id']}")
        inj = [(i["athlete"]["displayName"], i["athlete"].get("position", {}).get("abbreviation"), i.get("status"))
               for t in summ.get("injuries", []) if t["team"]["abbreviation"] == ESPN_ABBR.get(team, team) for i in t.get("injuries", [])]
        me = [x for x in inj if x[0] == player]
        R["inputs"]["injuries"] = inj
        chk("Injury status (ESPN)", "fail" if me and me[0][2] in ("Out", "Doubtful", "Injured Reserve") else "warn" if me else "ok",
            f"{player}: {me[0][2] if me else 'not on injury report'}")
        # Only teammates with real recent usage (last 2 team games: >=10 att, >=3 tgt or >=5 car). Backups/long-term IR are noise.
        teamrows = [r for r in player_rows(player)[1] if r["team"] == team]
        wk = sorted({(r["season"], int(r["week"])) for r in teamrows})[-2:]
        used = {r["player_display_name"] for r in teamrows if (r["season"], int(r["week"])) in wk and
                (float(r["attempts"] or 0) >= 10 or float(r["targets"] or 0) >= 3 or float(r["carries"] or 0) >= 5)}
        key = [f"{n} {p} {s}" for n, p, s in inj if s in ("Out", "Doubtful", "Questionable") and n in used and n != player]
        chk("Relevant teammate injuries", "ok" if not key else "warn", "; ".join(key[:8]) or "no injured teammate with recent usage (OL usage not measurable: unchecked)")
        if key:
            R["bear"].append("teammate injuries: " + "; ".join(key[:4]))
        gi = summ.get("gameInfo", {})
        w, venue = gi.get("weather"), gi.get("venue", {})
        R["inputs"]["weather"] = dict(weather=w, venue=venue.get("fullName"))
        bad = w and ((w.get("gust") or 0) >= 20 or (w.get("precipitation") or 0) >= 50 or (w.get("temperature") or 60) <= 25)
        chk("Weather / venue", "warn" if bad else "ok",
            f"{venue.get('fullName')}: " + (f"{w.get('temperature')}F, gust {w.get('gust')} mph, precip {w.get('precipitation')}%" if w else "no weather listed (likely indoor/roof)"))
    else:
        chk("Injury status (ESPN)", "manual", "ESPN event not found")
        chk("Weather / venue", "manual", "ESPN event not found")
    last = mine[-1] if mine else None
    vol = lambda r: f"{r['attempts']} att" if stat.startswith("pass") or stat == "interceptions" else f"{r['targets']} tgt, {r['carries']} car"
    chk("Starter status", "ok" if a.starters_confirmed else "warn",
        ("confirmed by you" if a.starters_confirmed else "PROXY only: last game " + (f"{last['season']} wk{last['week']} {vol(last)}" if last else "n/a")
         + ". Official NFL inactives post ~90 min before kickoff"))

    # 8 game environment
    g = pinnacle_game(pin["game"])
    R["inputs"]["game_lines"] = g
    team_pts = None
    if g and "spread" in g and "total" in g and ev:
        espn_name = next((c["team"]["displayName"] for c in ev["competitions"][0]["competitors"] if c["team"]["abbreviation"] == ESPN_ABBR.get(team, team)), None)
        des = next((d for d, n in g["teams"].items() if n == espn_name), None)
        if des:
            total = g["total"]["over"][0]
            team_pts = total / 2 - g["spread"][des][0] / 2
            chk("Game environment", "ok", f"{g['teams']['away']} @ {g['teams']['home']}: total {total}, {espn_name} spread {g['spread'][des][0]:+}, implied team total {team_pts:.1f}")
    if team_pts is None:
        chk("Game environment", "manual", "could not derive implied team total")

    # 9/12 independent models
    env = env_model(stat, line, team_pts)
    sm = stat_model(player, stat, line, team_pts)
    R["inputs"].update(team_pts=team_pts, env_model=env, stat_model=sm)
    for name, mdl in (("Environment model (team total)", env), ("Statistical model (player history)", sm)):
        if not mdl:
            chk(name, "manual", "no validated model for this stat yet")
            continue
        p = side_p(mdl["p_more"], side)
        gap = p - R["p_consensus"]
        detail = (f"lambda {mdl['lam']:.2f} (league pass TDs/pt {mdl['ratio']:.4f})" if "ratio" in mdl else
                  f"lambda {mdl['lam']:.2f}, {mdl['n']} games, {mdl['note']}, k={SHRINK_K} [{mdl['version']}]")
        chk(name, ("ok" if abs(gap) <= 0.05 else "warn") if VALIDATED[name] else "warn",
            f"{side} {p:.3f} vs market {R['p_consensus']:.3f} ({gap * 100:+.1f}pp); {detail}" + ("" if VALIDATED[name] else " | NOT VALIDATED (H7): information only"))
        if abs(gap) > 0.05:
            R["bear"].append(f"{name} disagrees with market by {gap * 100:+.1f}pp; find out why before trusting either")
        R["p_" + name.split()[0].lower()] = p

    # 10-11 movement + stale line
    hist = pin_history(pin["desc"], side) + [(now.isoformat(timespec="seconds"), pin["line"], p_pin)]
    R["inputs"]["price_path"] = hist
    gaps = collector_status.gaps(con, hist[0][0])
    cover = (" | UNOBSERVED collector gaps: " + "; ".join(f"{a[11:16]}-{b[11:16]}Z" for a, b in gaps)) if gaps else " | continuous coverage"
    mv = hist[-1][2] - hist[0][2]
    hrs = max((datetime.fromisoformat(hist[-1][0]) - datetime.fromisoformat(hist[0][0])).total_seconds() / 3600, 1e-9)
    steps = [(b[0], b[2] - a[2]) for a, b in zip(hist, hist[1:])]
    big = max(steps, key=lambda x: abs(x[1])) if steps else None
    chk("Market movement (Pinnacle, our collector)", "warn" if mv < -0.02 or gaps else "ok",
        f"first seen {hist[0][0][11:16]}Z {side} {hist[0][2]:.3f} -> now {hist[-1][2]:.3f} ({mv * 100:+.1f}pp, {mv * 100 / hrs:+.2f}pp/h"
        + (f", largest step {big[1] * 100:+.1f}pp by {big[0][11:16]}Z" if big and big[1] else "") + ")" + cover)
    if mv < -0.02:
        R["bear"].append(f"market moved against {side} by {mv * 100:.1f}pp since first seen (cause unverified; see postmortem catalyst check)")
    prev = con.execute("SELECT id, ts, payload FROM prebet_snapshots ORDER BY id DESC").fetchall()
    for pid, pts, payload in prev:
        old = next((l for l in json.loads(payload)["legs"] if (l["player"], l["stat"], l["line"], l["side"]) == (player, stat, line, side)), None)
        if old and "p_consensus" in old:
            d = p_pin - side_p(old["inputs"]["pinnacle"]["p_more"], side)
            chk("Moved since previous analysis", "warn" if abs(d) > 0.02 else "ok",
                f"pre-bet snapshot #{pid} at {pts[11:16]}Z had Pinnacle {side} {side_p(old['inputs']['pinnacle']['p_more'], side):.3f}; now {p_pin:.3f} ({d * 100:+.1f}pp)"
                + (" -> old analysis STALE, this run is the re-verification at the current price" if abs(d) > 0.02 else ""))
            break
    if dk:
        chk("Market movement (DraftKings line)", "warn" if dk["open"] != dk["line"] else "ok", f"open {dk['open']} -> current {dk['line']} (updated {dk['updated']})")
    chk("Stale-line check", "manual", "PrizePicks line timestamp not observable; compare against a fresh board just before entry")

    if R["p_conservative"] - 0.5 > 0.12:
        R["bear"].append(f"edge vs a coin-flip line is large ({(R['p_consensus'] - .5) * 100:.0f}pp). Confirm this is a standard pick with LESS available")
    R["grade"] = data_grade(R, a)
    return R


def data_grade(R, a):
    """A: healthy+fresh collector, >=2 priced sources, news+weather+environment fetched, slip verified, starters confirmed.
    B: A minus slip/starter confirmation. C: single source or news/environment missing. F: stale/started/unpriceable."""
    st = {n: s for n, s, _ in R["checks"]}
    if any(s == "fail" for s in st.values()) or "p_consensus" not in R:
        return "F"
    core = st.get("Market consensus") == "ok" and all(st.get(k) in ("ok", "warn") for k in ("Injury status (ESPN)", "Weather / venue", "Game environment"))
    if not core:
        return "C"
    return "A" if a.slip_verified and a.payout and a.starters_confirmed else "B"


def joint_prob(legs, ps):
    """Joint P(all legs). Different games: product. 2 legs in the same game: SGP_CORR_v1 Gaussian copula with rho taken at the
    WORSE end of rho +- (2 SE + 0.1 structural). Anything else in the same game: None (not modeled -> reject)."""
    games = [l.get("game") for l in legs]
    if len(set(games)) == len(games):
        return math.prod(ps), None
    if len(legs) != 2:
        return None, "3+ legs with a same-game pair: correlation not modeled"
    import sgp
    a, b = legs
    if not (a.get("pos") and b.get("pos") and a.get("team") and b.get("team")):
        return None, "same game but a player has no recent stats (position/team unknown): correlation not modeled"
    c = sgp.lookup(a["stat"], a.get("pos"), b["stat"], b.get("pos"), a.get("team") == b.get("team"))
    if not c:
        return None, f"no SGP_CORR_v1 estimate for {a['stat']}/{a.get('pos')} x {b['stat']}/{b.get('pos')}"
    band = 2 * c["se"] + 0.10
    j = min(sgp.joint(ps[0], ps[1], a["side"], b["side"], r) for r in (c["rho"] - band, c["rho"], c["rho"] + band))
    return j, f"same game: rho {c['rho']:+.3f} (n={c['n']}), evaluated at the worse of rho +-{band:.2f}"


def entry_math(legs, a):
    print("\n" + "=" * 90 + "\nENTRY: correlation, sensitivity, stress test")
    _, note = joint_prob(legs, [l["p_consensus"] for l in legs])
    corr = note is not None and joint_prob(legs, [l["p_consensus"] for l in legs])[0] is None
    print(f"  Correlation: {note or 'all legs in different games: independence assumed (shared weather/news ignored)'}"
          + ("  -> NOT MODELED: REJECT" if corr else ""))
    n = len(legs)
    payout = a.payout
    label = f"{a.entry} {payout}x (from slip)" if payout else "HYPOTHETICAL 3x - AWAITING SLIP VERIFICATION, not a final EV"
    payout = payout or 3.0
    be_leg = payout ** (-1 / n)
    print(f"  Payout: {label}. Break-even joint {1 / payout:.3f}, per leg {be_leg:.3f} (independent-leg equivalent)")
    base = [l["p_consensus"] for l in legs]
    cons = [l["p_conservative"] for l in legs]
    jp = lambda ps: joint_prob(legs, ps)[0] or 0.0
    ev = lambda ps: jp(ps) * payout - 1
    print(f"  {'scenario':28} {'legs':28} {'joint':>6} {'EV':>7}")
    rows = [("consensus", base), ("conservative (worst source)", cons)] + [(f"consensus -{int(s * 100)}pp per leg", [p - s for p in base]) for s in STRESS]
    for name, ps in rows:
        print(f"  {name:28} {' '.join(f'{p:.3f}' for p in ps):28} {jp(ps):6.3f} {ev(ps) * 100:+6.1f}%")
    fragile = ev([p - 0.04 for p in base]) < 0
    print(f"  Robustness: {'FRAGILE (EV < 0 if each leg is 4pp too high)' if fragile else 'ROBUST to a 4pp per-leg overestimate'}")
    # entry-level break-even on the CONSERVATIVE joint; legs still need the per-leg margin in level()
    if not corr and jp(cons) * payout - 1 <= 0:
        fragile = True
    return corr, fragile, be_leg


GATE = [("MARKET VERIFIED", ["Exact PrizePicks line confirmed", "Line type standard (not goblin/demon)"]),
        ("PAYOUT VERIFIED", ["Actual payout structure"]), ("MULTI-BOOK CHECK", ["Market consensus"]),
        ("NO-VIG CALCULATION", ["Pinnacle price + vig removed"]), ("NEWS CHECK", ["Injury status (ESPN)", "Relevant teammate injuries"]),
        ("STARTER/ROLE CHECK", ["Starter status"]), ("WEATHER CHECK", ["Weather / venue"]), ("GAME-ENVIRONMENT CHECK", ["Game environment"]),
        ("STATISTICAL MODEL (team total)", ["Environment model (team total)"]), ("MARKET MODEL", ["Market consensus", "Kalshi exchange price"]),
        ("HISTORICAL MODEL (player)", ["Statistical model (player history)"]), ("MARKET MOVEMENT CHECK", ["Market movement (Pinnacle, our collector)"]),
        ("CURRENT PRICE REFRESH", ["Current price refresh"]), ("DATA COLLECTOR HEALTHY", ["Data collector healthy"])]


def gate(R, corr, fragile, snap_id):
    st = {n: s for n, s, _ in R["checks"]}
    rows = [(g, all(st.get(k) == "ok" for k in ks)) for g, ks in GATE]
    rows += [("CORRELATION CHECK", not corr), ("BEAR CASE", not R["bear"]), ("SENSITIVITY ANALYSIS", not fragile),
             ("PRE-BET SNAPSHOT SAVED", snap_id is not None)]
    return rows


def level(R, be_leg, corr, fragile, a):
    st = {s for _, s, _ in R["checks"]}
    if "fail" in st or corr:
        return "REJECT"
    if R.get("grade") != "A" and R["p_conservative"] > be_leg and "manual" not in st and a.payout and a.slip_verified and a.starters_confirmed:
        return f"LEVEL B - data quality {R.get('grade')}, no money"
    if R["p_conservative"] <= be_leg:
        return "REJECT (conservative P below break-even)"
    if "manual" in st or not a.payout or not a.slip_verified or not a.starters_confirmed:
        # SHADOW (FORWARD_TEST.md): every automated check ok; only the human gates are pending
        human = {"Exact PrizePicks line confirmed", "Line type standard (not goblin/demon)", "Actual payout structure",
                 "Stale-line check", "Starter status"}
        auto_ok = all(s_ == "ok" for n, s_, _ in R["checks"] if n not in human)
        if auto_ok and not R["bear"] and not fragile and R["p_conservative"] >= be_leg + MARGIN_A:
            return "LEVEL B - SHADOW (automated checks pass; slip/starters pending, no money)"
        return "LEVEL C - INTERESTING (NO MONEY): unresolved manual checks"
    if fragile or "warn" in st or R["bear"] or R["p_conservative"] < be_leg + MARGIN_A:
        return "LEVEL B - VERIFIED CANDIDATE: warnings/bear case open, no money yet"
    return "LEVEL A - DEPLOYABLE" if R.get("grade") == "A" else f"LEVEL B - data quality {R.get('grade')}, no money"


def freeze(legs, a, entry, stamp):
    """Immutable pre-bet snapshot: every input, model output, check and version, hashed. Returns (id, sha)."""
    payload = json.dumps(dict(ts=stamp, versions=dict(model=versions.MODEL, data=versions.DATA, verify=versions.VERIFY, exec=versions.EXEC),
                              args=vars(a), legs=legs, entry=entry), default=str, sort_keys=True)
    sha = hashlib.sha256(payload.encode()).hexdigest()
    con = sqlite3.connect(DB, timeout=60)
    con.executescript((ROOT / "schema.sql").read_text())
    cur = con.execute("INSERT INTO prebet_snapshots (ts, versions, legs, payload, sha256) VALUES (?,?,?,?,?)",
                      (stamp, versions.PROTOCOL + "|" + versions.MODEL + "|" + versions.DATA,
                       "; ".join(f"{l['player']} {l['stat']} {l['line']} {l['side']}" for l in legs), payload, sha))
    con.commit()
    (ROOT / "results/prebet").mkdir(parents=True, exist_ok=True)
    (ROOT / f"results/prebet/{cur.lastrowid:05d}_{sha[:12]}.json").write_text(payload)
    return cur.lastrowid, sha


class Tee:
    """Every verification run is saved to results/verify/ as the audit record."""
    def __init__(self, path):
        self.f, self.out = open(path, "w"), __import__("sys").stdout
    def write(self, s):
        self.f.write(s); self.out.write(s)
    def flush(self):
        self.f.flush(); self.out.flush()


def main():
    import sys
    (ROOT / "results/verify").mkdir(parents=True, exist_ok=True)
    sys.stdout = Tee(ROOT / f"results/verify/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.txt")
    ap = argparse.ArgumentParser()
    ap.add_argument("legs", nargs="+", help='"Player|stat|line|MORE/LESS"')
    ap.add_argument("--payout", type=float), ap.add_argument("--entry", default="power")
    ap.add_argument("--slip-verified", action="store_true"), ap.add_argument("--starters-confirmed", action="store_true")
    ap.add_argument("--stake", type=float, help="dollars for the official slip")
    ap.add_argument("--note", default="", help="free text stored in the pre-bet snapshot (e.g. TEST)")
    ap.add_argument("--bankroll", type=float, help="PrizePicks balance, for SIZING_v1.0")
    ap.add_argument("--slip", default="", help="the slip exactly as displayed (legs, line types, payout, promo), stored in the snapshot")
    a = ap.parse_args()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"{versions.VERIFY} run {stamp} | model {versions.MODEL} | data {versions.DATA} | exec {versions.EXEC}")
    legs = []
    for s in a.legs:
        player, stat, line, side = s.split("|")
        R = verify_leg(player, stat.lower(), line, side.upper(), a)
        legs.append(R)
        print("\n" + "=" * 90 + f"\n{player} | {stat} {line} {side}")
        for name, status, note in R["checks"]:
            print(f"  [{ {'ok': 'x', 'warn': '!', 'fail': 'X', 'manual': ' '}[status]}] {name:40} {note}")
        print("  BEAR CASE: " + ("; ".join(R["bear"]) if R["bear"] else "nothing specific found by the automated checks; still read the checklist above"))
    priced = all("p_consensus" in l for l in legs)
    corr, fragile, be_leg = entry_math(legs, a) if priced else (None, None, None)
    for R in legs:
        R["level"] = level(R, be_leg, corr, fragile, a) if priced else "NOT READY (unpriced leg)"
    snap_id, sha = freeze(legs, a, dict(corr=corr, fragile=fragile, be_leg=be_leg, payout=a.payout, entry=a.entry, stake=a.stake), stamp)
    print(f"\nPRE-BET SNAPSHOT #{snap_id} saved (sha256 {sha[:16]}...), results/prebet/{snap_id:05d}_{sha[:12]}.json")
    for R in legs:
        print(f"\nPRE-BET GATE: {R['player']} {R['stat']} {R['line']} {R['side']}   DATA QUALITY {R['grade'] if 'grade' in R else 'F'}   {R['level']}")
        if priced:
            for name, ok in gate(R, corr, fragile, snap_id):
                print(f"  {'✓' if ok else '✗'} {name}")
    deploy = priced and all(R["level"].startswith("LEVEL A") for R in legs) and a.stake and a.slip  # payout must come from a real slip
    print("\n" + "=" * 90)
    if deploy:
        print("OFFICIAL SLIP")
        for i, R in enumerate(legs, 1):
            print(f"  Leg {i}: {R['player']} {R['side']} {R['line']} {R['stat']}")
        print(f"  Entry type: {len(legs)}-pick {a.entry.title()}\n  Displayed payout: {a.payout}x\n  Stake: ${a.stake:.2f}")
        import sizing, alerts
        joint = joint_prob(legs, [R["p_conservative"] for R in legs])[0]
        real, paper, why = sizing.stake("PP_PROP_v0.2", a.bankroll or a.stake * 50, sizing.kelly_prizepicks(joint, a.payout))
        print(f"  Sizing (SIZING_v1.0, conservative joint {joint:.3f}): real ${real:.2f} | paper ${paper:.2f} | {why}")
        print(f"  Pre-bet snapshot: #{snap_id}\nSTATUS: AWAITING USER CONFIRMATION OF ACTUAL SLIP")
        alerts.notify("LEVEL A slip ready", " + ".join(f"{R['player']} {R['side']} {R['line']}" for R in legs))
    else:
        why = sorted({R["level"] for R in legs if not R["level"].startswith("LEVEL A")}) + ([] if a.stake else ["no --stake given"]) + \
              ([] if a.slip else ["no --slip (actual displayed slip) given"])
        print("NO OFFICIAL SLIP. " + " | ".join(why))
    print("Legend: [x] ok  [!] warning  [X] fail  [ ] manual/unresolved")


if __name__ == "__main__":
    assert abs(poisson_cdf(1, 1.2) - math.exp(-1.2) * 2.2) < 1e-12
    assert side_p(0.6, "LESS") == 0.4
    main()
