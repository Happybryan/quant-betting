"""DAILY_PAPER_v1 (rules: DAILY_PAPER_v1.md, fixed before the first pick). Best-available daily paper picks: a PrizePicks 2-pick and
a Kalshi single, logged even when EV < 0. Separate scorecard; never evidence for going live.
  python3 daily_paper.py pick      (launchd 15:00 ET)
  python3 daily_paper.py settle
  python3 daily_paper.py report"""
import hashlib, json, math, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import match, pp_board, prop_settle, quality, registry, status, stale_scan, sgp

ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.db"
PAYOUT = 3.0  # v1 pre-registered assumption; kept for PRIZEPICKS_2PICK continuity
REAL_PAYOUT = {2: 2.0, 3: 5.0}  # v2 default from one lineup (2026-09-30); PP payouts vary per lineup, so use the app's number for real slips
SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_paper (id INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL, ts TEXT NOT NULL, pick TEXT NOT NULL,
  est_p REAL, est_ev REAL, flag TEXT, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
  close_value REAL, result TEXT, pnl REAL, settled_ts TEXT, UNIQUE (day, kind));
CREATE TRIGGER IF NOT EXISTS daily_pre_immutable BEFORE UPDATE OF day, kind, ts, pick, est_p, est_ev, flag, payload, sha256 ON daily_paper
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER IF NOT EXISTS daily_no_delete BEFORE DELETE ON daily_paper BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
now_utc = lambda: datetime.now(timezone.utc)


def log(con, day, kind, pick, p, ev, flag, payload):
    body = json.dumps(dict(payload, rules="DAILY_PAPER_v1"), sort_keys=True, default=str)
    try:
        con.execute("INSERT INTO daily_paper (day, kind, ts, pick, est_p, est_ev, flag, payload, sha256) VALUES (?,?,?,?,?,?,?,?,?)",
                    (day, kind, now_utc().isoformat(timespec="seconds"), pick, p, ev, flag, body, hashlib.sha256(body.encode()).hexdigest()))
        con.commit()
        print(f"[{kind}] {pick} | est P {p if p is None else round(p, 3)} | est EV {ev if ev is None else f'{ev * 100:+.1f}%'} | {flag}")
    except sqlite3.IntegrityError:
        print(f"[{kind}] already logged for {day}")


def pp_legs(con):
    pulls = pp_board.pull("americanfootball_nfl", 24) + pp_board.pull("baseball_mlb", 24)  # MLB since v1.1 (2026-09-30)
    pp_board.store(con, pulls)
    t = now_utc().isoformat(timespec="seconds")
    legs = []
    for e, d, _ in pulls:
        pp = next((b for b in d.get("bookmakers", []) if b["key"] == "prizepicks"), None)
        for m in (pp or {}).get("markets", []):
            stat = pp_board.MARKETS[m["key"]]
            for o in m["outcomes"]:
                if o["name"] != "Over":
                    continue
                p, src = stale_scan.p_at(con, o["description"], stat, o.get("point"), t)
                if p is None:
                    continue
                side, ps = ("MORE", p) if p >= 0.5 else ("LESS", 1 - p)
                legs.append(dict(event=e["id"], league=e["league"], start=e["commence_time"], player=o["description"], stat=stat, line=o.get("point"),
                                 side=side, p=ps, source=src))
    legs.sort(key=lambda l: -l["p"])
    return legs


def pp_best(legs):
    """Best 2-pick (2x) or 3-pick (5x) Power Play, one leg per game (independent). Bigger slip only if its EV beats the
    smaller by >= 0.05 per $1 ("some money over no money"). -> (ev, n, joint, legs) or None."""
    top = list({l["event"]: l for l in reversed(legs)}.values())  # best leg per game
    top.sort(key=lambda l: -l["p"])
    best = None
    for n in sorted(REAL_PAYOUT):
        if len(top) < n:
            break
        joint = math.prod(l["p"] for l in top[:n])
        ev = joint * REAL_PAYOUT[n] - 1
        if not best or ev >= best[0] + 0.05:
            best = (ev, n, joint, top[:n])
    return best


def real_pick(con, day, legs):
    """DAILY_PAPER v2: pp_best() at Bryan's real payouts."""
    best = pp_best(legs)
    if not best:
        return log(con, day, "PRIZEPICKS_REAL", "NO_PICK", None, None, "NO_PICK: fewer than 2 games with a priceable leg", dict(n=len(legs)))
    ev, n, joint, top = best
    pick = " + ".join(f"{l['player']} {l['side']} {l['line']} {l['stat']}" for l in top)
    log(con, day, "PRIZEPICKS_REAL", pick, joint, ev, ("NEGATIVE_EV" if ev < 0 else "POSITIVE_EV") + f" | {n}-pick Power {REAL_PAYOUT[n]}x REAL",
        dict(legs=top, payout_real=REAL_PAYOUT[n]))


def prizepicks_pick(con, day, legs, kind="PRIZEPICKS_2PICK"):
    if len(legs) < 2:
        return log(con, day, "PRIZEPICKS_2PICK", "NO_PICK", None, None, "NO_PICK: fewer than 2 priceable PrizePicks legs", dict(n=len(legs)))
    rows = [r for r in __import__("csv").DictReader(open(ROOT / "data/raw/nfl_player_week_2025.csv"))] + \
           [r for r in __import__("csv").DictReader(open(ROOT / "data/raw/nfl_player_week_2026.csv"))]
    info = {r["player_display_name"]: (r["position"], r["team"]) for r in rows}
    best = None
    for i in range(len(legs)):
        for j in range(i + 1, len(legs)):
            a, b = legs[i], legs[j]
            if a["event"] != b["event"]:
                joint, note = a["p"] * b["p"], "different games: independent"
            else:
                ia, ib = info.get(a["player"]), info.get(b["player"])
                c = ia and ib and sgp.lookup(a["stat"], ia[0], b["stat"], ib[0], ia[1] == ib[1])
                if not c:
                    continue
                band = 2 * c["se"] + 0.10
                joint = min(sgp.joint(a["p"], b["p"], a["side"], b["side"], r) for r in (c["rho"] - band, c["rho"], c["rho"] + band))
                note = f"same game: SGP rho {c['rho']:+.3f} at worse end"
            if not best or joint > best[0]:
                best = (joint, a, b, note)
    if not best:
        return log(con, day, "PRIZEPICKS_2PICK", "NO_PICK", None, None, "NO_PICK: no modelable pair", dict(n=len(legs)))
    joint, a, b, note = best
    ev = joint * PAYOUT - 1
    pick = f"{a['player']} {a['side']} {a['line']} {a['stat']} + {b['player']} {b['side']} {b['line']} {b['stat']}"
    log(con, day, kind, pick, joint, ev, ("NEGATIVE_EV" if ev < 0 else "POSITIVE_EV") + " | payout 3x ASSUMED",
        dict(legs=[a, b], correlation=note, payout_assumed=PAYOUT))


def kalshi_cands(con):
    """Every fresh A-C graded Kalshi outcome starting in 15 min..24 h, any sport (registry-matched to Pinnacle)."""
    now = now_utc()
    health = {(h["league"], h["source"]): h for h in status.health(con, now)}
    out = []
    for (t,) in con.execute("""SELECT source_event_id FROM reg_sources WHERE source='kalshi' AND match_confidence >= ?
                               AND equivalence IN ('EXACT','NEAR_EQUIVALENT')""", (match.AUTO_THRESHOLD,)).fetchall():
        a = quality.assess(con, t, now, health)
        if a.get("hours") is None or not 0.25 <= a["hours"] <= 24:
            continue
        out += [(a, o) for o in a.get("outcomes", []) if "edge" in o and o["grade"] in ("A", "B", "C") and not o["stale"]]
    return out


def kalshi_payload(a, o):
    return dict(ticker=o["market_ticker"], pinnacle_id=a["pid"], pin_outcome=o["pin"], start=a["start"], sport=a["sport"], series=a["series"],
                ask=o["ask"], cost=o["cost"], fair=o["fair"], edge=o["edge"], uncertainty=o.get("uncertainty"), grade=o["grade"])


def kalshi_pick(con, day, cands):
    best = max(cands, key=lambda c: c[1]["edge"], default=None)
    if not best:
        return log(con, day, "KALSHI_SINGLE", "NO_PICK", None, None, "NO_PICK: no fresh A-C outcome starting within 24h", {})
    a, o = best
    log(con, day, "KALSHI_SINGLE", f"{o['outcome']} YES ({a['sport']}, {o['market_ticker']}) @ ask {o['ask']:.2f}", o["fair"]["lo"],
        o["edge"] / o["cost"], ("NEGATIVE_EV" if o["edge"] < 0 else "POSITIVE_EV") + f" | grade {o['grade']}",
        kalshi_payload(a, o))


def bet_of_the_day(con, day, legs, cands):
    """BET_OF_THE_DAY (v3, 2026-10-02): exactly one pick a day, any sport: the highest EV per $1 among Kalshi singles
    (YES asks >= 0.20 only: the H4b longshot ban) and the best PrizePicks Power slip. Labelled REAL BET only when EV > 0 AND
    (Kalshi: grade A/B and P(edge > 0) >= 0.80 | PrizePicks: never automatic, payouts vary per lineup -> CHECK PAYOUT with the
    break-even multiplier). Everything else is a PAPER PICK. Real stakes still follow GO_LIVE.md / promo caps."""
    opts = []
    for a, o in cands:
        if o["ask"] >= 0.20:
            ev = o["edge"] / o["cost"]
            sure = (o.get("uncertainty") or {}).get("p_pos", 0)
            real = ev > 0 and o["grade"] in ("A", "B") and sure >= 0.80
            opts.append((ev, "REAL BET" if real else "PAPER PICK", f"Kalshi: {o['outcome']} YES ({a['sport']}) @ {o['ask']:.2f}",
                         o["fair"]["lo"], dict(kalshi_payload(a, o), venue="kalshi")))
    pb = pp_best(legs)
    if pb:
        ev, n, joint, top = pb
        opts.append((ev, "CHECK PAYOUT" if ev > 0 else "PAPER PICK",
                     f"PrizePicks {n}-pick Power: " + " + ".join(f"{l['player']} {l['side']} {l['line']} {l['stat']}" for l in top),
                     joint, dict(venue="prizepicks", legs=top, payout_real=REAL_PAYOUT[n], breakeven_x=round(1 / joint, 2))))
    if not opts:
        return log(con, day, "BET_OF_THE_DAY", "NO_PICK", None, None, "NO_PICK: nothing priceable in the next 24h", {})
    opts.sort(key=lambda x: -x[0])
    ev, label, pick, p, payload = opts[0]
    payload["runners_up"] = [dict(pick=x[2], ev=round(x[0], 4), label=x[1]) for x in opts[1:4]]
    log(con, day, "BET_OF_THE_DAY", pick, p, ev, label + (" | NEGATIVE_EV" if ev < 0 else " | POSITIVE_EV"), payload)


def settle():
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    for i, kind, pick, payload in con.execute("SELECT id, kind, pick, payload FROM daily_paper WHERE result IS NULL AND pick != 'NO_PICK'").fetchall():
        p = json.loads(payload)
        if "ticker" in p:  # KALSHI_SINGLE, or a Kalshi BET_OF_THE_DAY
            try:
                m = registry.get(f"{registry.KAL}/markets/{p['ticker']}")["market"]
            except Exception:
                continue
            if m.get("status") in ("finalized", "settled") and m.get("result") in ("yes", "no"):
                won = m["result"] == "yes"
                con.execute("UPDATE daily_paper SET result=?, pnl=?, settled_ts=? WHERE id=?",
                            ("win" if won else "loss", round((1.0 if won else 0.0) * 100 - p["cost"] * 100, 2), now_utc().isoformat(timespec="seconds"), i))
        else:
            gs = []
            for l in p["legs"]:
                day = (datetime.fromisoformat(l["start"].replace("Z", "+00:00")) - timedelta(hours=4)).strftime("%Y%m%d")
                gs.append(prop_settle.grade(prop_settle.outcome(con, l["player"], l["stat"], day), l["line"], l["side"]))
            if None in gs:
                continue
            won = all(g == "win" for g in gs if g != "push") and any(g == "win" for g in gs)
            res = "win" if won else ("push" if all(g == "push" for g in gs) else "loss")
            con.execute("UPDATE daily_paper SET result=?, pnl=?, settled_ts=? WHERE id=?",
                        (res + f" ({'/'.join(gs)})", (p.get("payout_real", PAYOUT) - 1) if res == "win" else (0 if res == "push" else -1), now_utc().isoformat(timespec="seconds"), i))
    con.commit()


def report():
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    rows = con.execute("SELECT day, kind, pick, est_p, est_ev, flag, result, pnl FROM daily_paper ORDER BY day, kind").fetchall()
    print(f"DAILY_PAPER_v1: {len(rows)} rows (separate scorecard; not go-live evidence)")
    for r in rows:
        print(f"  {r[0]} {r[1]:17} {r[2][:90]:90} P {r[3] if r[3] is None else round(r[3], 3)} EV {r[4] if r[4] is None else f'{r[4] * 100:+.1f}%'} {r[5]} -> {r[6] or 'open'}")
    for kind in ("PRIZEPICKS_2PICK", "PRIZEPICKS_REAL", "KALSHI_SINGLE", "BET_OF_THE_DAY"):
        s = [r for r in rows if r[1] == kind and r[6]]
        if s:
            print(f"  {kind}: settled {len(s)}, wins {sum(r[6].startswith('win') for r in s)}, paper P/L {sum(r[7] or 0 for r in s):+.2f} units, "
                  f"mean est EV {sum(r[4] for r in s) / len(s) * 100:+.1f}%")
    # By sport, every settled pick of every kind, as profit per $1 staked (Kalshi pnl is cents per contract, PrizePicks per 1-unit entry)
    g = {}
    for kind, pnl, payload in con.execute("SELECT kind, pnl, payload FROM daily_paper WHERE result IS NOT NULL AND pnl IS NOT NULL"):
        p = json.loads(payload)
        sport = p.get("sport") or "/".join(sorted({l.get("league", "NFL") for l in p.get("legs", [])})) or "?"
        roi = pnl / (p["cost"] * 100) if "ticker" in p else pnl
        g.setdefault(sport, []).append(roi)
    if g:
        print("  BY SPORT (profit per $1 staked; n is tiny until weeks pass):")
        for sport, v in sorted(g.items(), key=lambda x: -len(x[1])):
            print(f"    {sport:24} n {len(v):3} | wins {sum(x > 0 for x in v):3} | profit/$1 {sum(v) / len(v):+.2f}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "pick":
        con = sqlite3.connect(DB, timeout=60)
        con.executescript(SCHEMA)
        day = (now_utc() - timedelta(hours=4)).strftime("%Y-%m-%d")
        legs = []
        try:
            legs = pp_legs(con)
        except Exception as e:
            print(f"pp_legs failed: {e!r}")
        cands = []
        try:
            cands = kalshi_cands(con)
        except Exception as e:
            print(f"kalshi_cands failed: {e!r}")
        for f in (lambda c, d: prizepicks_pick(c, d, legs), lambda c, d: real_pick(c, d, legs), lambda c, d: kalshi_pick(c, d, cands),
                  lambda c, d: bet_of_the_day(c, d, legs, cands)):
            try:
                f(con, day)
            except Exception as e:
                print(f"{f.__name__} failed: {e!r}")
    else:
        {"settle": settle, "report": report}[cmd]()
