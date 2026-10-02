"""Permanent betting ledger (data/market.db, table `bets`; pre-event fields are locked by DB triggers).
  python3 ledger.py add TICKER --stake 20 [--mode paper|live]    record a Kalshi position priced by board.py
  python3 ledger.py add-pp SNAPSHOT_ID --stake 10 [--mode paper|live]
                                                                  record a PrizePicks entry FROM a frozen pre-bet snapshot
  python3 ledger.py settle                                        fill close/CLV/result/PnL for finished Kalshi games
  python3 ledger.py report                                        two views (ALL / CURRENT PROTOCOL) + CLV scorecard
Classification (protocol, versions) is written at entry time and can never change afterwards."""
import argparse, json, sqlite3, urllib.request
from datetime import datetime, timezone
from pathlib import Path
import board, snapshot, versions
import statistics

DB = Path(__file__).parent / "data" / "market.db"
MAX_SNAPSHOT_AGE_MIN = 10
LEAGUE_SPORT = {"NFL": "Football", "NCAAF": "Football", "MLB": "Baseball", "NHL": "Hockey", "NBA": "Basketball", "WNBA": "Basketball"}  # a price older than this is not a price


def now():
    return datetime.now(timezone.utc)


def add(a, con):
    kts, pts, rows, _ = board.build(a.stake)
    age = (now() - datetime.fromisoformat(min(kts, pts))).total_seconds() / 60
    if age > MAX_SNAPSHOT_AGE_MIN:
        raise SystemExit(f"latest snapshot is {age:.0f} min old; run snapshot.py first")
    r = next((r for r in rows if r["ticker"] == a.ticker), None)
    if not r:
        raise SystemExit(f"{a.ticker} not on the matched board")
    if board.action(r) == "PASS" and not a.force:
        raise SystemExit(f"board says {board.action(r)} (edge {r['edge']*100:.1f}pp); --force to record anyway")
    con.execute("""INSERT INTO bets (ts,mode,platform,sport,league,game,start_time,market,selection,ticker,price,stake,
        contracts,fee,model_p,p_low,p_high,market_p,fair_price,edge,ev,model_version,data_version,assumptions,sources,
        protocol,verify_version,exec_version)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'KALSHI_BOARD_v0.1/' || ?,'KALSHI_BOARD_v0.1',?)""",
        (now().isoformat(timespec="seconds"), a.mode, "kalshi", r["league"], r["league"], r["game"], r["start"],
         "game winner", r["team"], r["ticker"], r["ask"], round(r["ask"] * r["contracts"] + r["fee"], 2), r["contracts"],
         r["fee"], r["p"], r["p_lo"], r["p_hi"], r["ask"], r["cost_pc"], r["edge"], r["ev_pct"], board.MODEL,
         f"kalshi@{kts} pinnacle@{pts}",
         "fair P = Pinnacle no-vig ML (mult; range vs power devig); taker fill at snapshot ask; NFL tie pays 0.50 on Kalshi but voids at Pinnacle",
         f"kalshi {r['ticker']}; pinnacle ML {r['pin_price']:+.0f}", versions.EXEC, versions.EXEC))
    con.commit()
    print(f"recorded {a.mode} {r['team']} @ {r['ask']:.2f} x{r['contracts']} edge {r['edge']*100:.1f}pp")


def covered(con, start_iso, league, source, minutes=15):
    """A close is valid only if the collector had a successful run for this source within `minutes` before start."""
    t0 = (datetime.fromisoformat(start_iso.replace("Z", "+00:00")).timestamp() - minutes * 60)
    lo = datetime.fromtimestamp(t0, timezone.utc).isoformat()
    return con.execute("SELECT max(ts) FROM collector_runs WHERE league=? AND source=? AND ok=1 AND ts>=? AND ts<=?",
                       (league, source, lo, start_iso.replace("Z", "+00:00"))).fetchone()[0]


def prop_close(con, desc, side, start_iso, league="NFL", entry_line=None):
    """Closing no-vig P for a Pinnacle prop side, or (None, 'MISSING', last_obs) if the collector missed the close."""
    rows = con.execute("""SELECT ts, selection, price, json_extract(extra,'$.points') FROM snapshots WHERE source='pinnacle_prop'
                          AND selection IN (?,?) AND ts<=? AND price IS NOT NULL ORDER BY ts""",
                       (desc + "|Over", desc + "|Under", start_iso.replace("Z", "+00:00"))).fetchall()
    last, pts = {}, None
    for ts, sel, price, p in rows:
        last[sel.rsplit("|", 1)[1]] = (ts, price)
        pts = p
    if entry_line is not None and pts is not None and float(pts) != float(entry_line):
        return None, "LINE_CHANGED", (rows[-1][0], None)  # CLV_DEFINITION.md: not comparable, never interpolated
    if len(last) < 2:
        return None, "MISSING", None
    pm = board.devig_range([board.american_to_prob(last["Over"][1]), board.american_to_prob(last["Under"][1])])[0][0]
    p = pm if side == "MORE" else 1 - pm
    run = covered(con, start_iso, league, "pinnacle_prop")
    return (p, "ok", run) if run else (None, "MISSING", (max(last["Over"][0], last["Under"][0]), p))


def add_pp(a, con):
    """Log a PrizePicks entry exactly as frozen in a pre-bet snapshot. Refuses anything that wasn't Level A,
    unless --mode paper (paper tests may log B/C levels; their level is recorded, never upgraded later)."""
    row = con.execute("SELECT id, ts, payload, sha256 FROM prebet_snapshots WHERE id=?", (a.snapshot,)).fetchone()
    if not row:
        raise SystemExit("no such pre-bet snapshot")
    sid, sts, payload, sha = row
    p = json.loads(payload)
    legs, entry = p["legs"], p["entry"]
    if any(l["start"] <= now().isoformat() for l in legs if "start" in l):
        raise SystemExit("a leg has already started; entries must be logged before the event")
    lvl = [l.get("level", "") for l in legs]
    if not all(x.startswith(("LEVEL A", "LEVEL B - SHADOW")) for x in lvl):
        raise SystemExit(f"only LEVEL A (OFFICIAL) or LEVEL B (SHADOW, stake 0) legs can be logged; got {lvl}")
    if (a.mode == "live" or a.stake) and not all(x.startswith("LEVEL A") for x in lvl):
        raise SystemExit(f"entries with a stake (or live) require LEVEL A on every leg; got {lvl}")
    payout = entry.get("payout")
    joint = 1.0
    for l in legs:
        joint *= l["p_consensus"]
    common = dict(ts=now().isoformat(timespec="seconds"), mode=a.mode, platform="prizepicks", sport="NFL", league="NFL",
                  model_version=p["versions"]["model"], data_version=p["versions"]["data"], verify_version=p["versions"]["verify"],
                  exec_version=p["versions"]["exec"], protocol=f"{p['versions']['verify']}/{p['versions']['exec']}",
                  prebet_id=sid, prebet_sha=sha, sources="pre-bet snapshot #%d" % sid,
                  assumptions=f"levels {lvl}; payout {'from slip' if payout else 'UNVERIFIED'}")
    rows = [dict(game=" + ".join(str(l.get("game")) for l in legs), start_time=min(l["start"] for l in legs), market=f"PP {len(legs)}-pick {entry['entry']}",
                 selection=" + ".join(f"{l['player']} {l['side']} {l['line']} {l['stat']}" for l in legs), line="/".join(str(l["line"]) for l in legs),
                 price=payout or 0, stake=a.stake, model_p=joint, market_p=(1 / payout) if payout else None,
                 edge=(joint - 1 / payout) if payout else None, ev=(joint * payout - 1) if payout else None)]
    for l in legs:
        rows.append(dict(game=str(l.get("game")), start_time=l["start"], market=f"PP leg {l['stat']}",
                         selection=f"{l['player']} {l['side']} {l['line']}", line=str(l["line"]), price=entry.get("be_leg") or 0, stake=0,
                         model_p=l["p_consensus"], p_low=l["p_conservative"], market_p=entry.get("be_leg"),
                         edge=(l["p_consensus"] - entry["be_leg"]) if entry.get("be_leg") else None))
    for r in rows:
        r |= common
        con.execute(f"INSERT INTO bets ({','.join(r)}) VALUES ({','.join('?' * len(r))})", list(r.values()))
    con.commit()
    print(f"logged {a.mode} entry + {len(legs)} legs from pre-bet snapshot #{sid} ({sha[:12]}), protocol {common['protocol']}")


def closing(con, b):
    """Last snapshot at or before start: Kalshi ask and Pinnacle no-vig P for the same team."""
    k = con.execute("""SELECT ask FROM snapshots WHERE source='kalshi' AND json_extract(extra,'$.ticker')=? AND ts<=?
                       ORDER BY ts DESC LIMIT 1""", (b["ticker"], b["start_time"])).fetchone()
    ts = con.execute("SELECT max(ts) FROM snapshots WHERE source='pinnacle' AND ts<=? AND league=?",
                     (b["start_time"], b["league"])).fetchone()[0]
    fair = None
    for key, in con.execute("SELECT DISTINCT event_key FROM snapshots WHERE source='pinnacle' AND ts=? AND league=?", (ts, b["league"])):
        ps = con.execute("SELECT selection, price, start_time FROM snapshots WHERE source='pinnacle' AND ts=? AND event_key=?", (ts, key)).fetchall()
        if len(ps) == 2 and ps[0][2] == b["start_time"] and any(board.same_team(b["selection"], p[0]) for p in ps):
            probs = board.devig_range([board.american_to_prob(p[1]) for p in ps])
            fair = next(pr[0] for p, pr in zip(ps, probs) if board.same_team(b["selection"], p[0]))
    return (k[0] if k else None), fair


def settle(a, con):
    con.row_factory = sqlite3.Row
    for b in con.execute("SELECT * FROM bets WHERE result IS NULL AND platform='kalshi'").fetchall():  # PrizePicks settles by hand
        m = json.load(urllib.request.urlopen(f"{snapshot.KALSHI}/markets/{b['ticker']}", timeout=30))["market"]
        if m.get("status") not in ("finalized", "settled") or not m.get("result"):
            continue
        payout = {"yes": 1.0, "no": 0.0}.get(m["result"])
        if payout is None:  # e.g. tie settled at a fair price
            payout = float(m.get("settlement_value_dollars") or 0.5)
        close_ask, close_fair = closing(con, b)
        if not covered(con, b["start_time"], "SPORT:" + LEAGUE_SPORT.get(b["league"], b["league"]), "pinnacle"):
            close_ask = close_fair = None  # collector missed the close: MISSING, never substituted
        result = "win" if payout == 1 else "loss" if payout == 0 else "push"
        con.execute("UPDATE bets SET close_price=?, close_fair_p=?, clv=?, close_status=?, result=?, pnl=?, settled_ts=? WHERE id=?",
                    (close_ask, close_fair, (close_fair - b["fair_price"]) if close_fair else None, "ok" if close_fair else "MISSING", result,
                     round(payout * b["contracts"] - b["stake"], 2), now().isoformat(timespec="seconds"), b["id"]))
        print(f"settled #{b['id']} {b['selection']}: {result}")
    con.commit()



def report(a, con):
    con.row_factory = sqlite3.Row
    views = [("ALL SYSTEM RECOMMENDATIONS", "1=1"),
             (f"CURRENT PROTOCOL ({versions.PROTOCOL})", f"protocol = '{versions.PROTOCOL}'")]
    for title, where in views:
        print(f"\n=== {title} ===")
        for mode in ("paper", "live"):
            bs = con.execute(f"SELECT * FROM bets WHERE mode=? AND {where} ORDER BY ts", (mode,)).fetchall()
            if not bs:
                print(f"[{mode}] no bets")
                continue
            entries = [b for b in bs if b["stake"]]
            legs = [b for b in bs if not b["stake"]]
            s = [b for b in entries if b["result"]]
            pnl, staked = sum(b["pnl"] or 0 for b in s), sum(b["stake"] for b in s)
            eq = peak = dd = 0.0
            for b in s:
                eq += b["pnl"] or 0; peak = max(peak, eq); dd = max(dd, peak - eq)
            print(f"[{mode}] entries {len(entries)} settled {len(s)} W-L {sum(b['result']=='win' for b in s)}-{sum(b['result']=='loss' for b in s)}"
                  f" | staked ${staked:.2f} P/L ${pnl:+.2f} ROI {pnl / staked * 100 if staked else 0:+.1f}% | max DD ${dd:.2f}")
            sl = [b for b in legs if b["result"] in ("win", "loss")]
            if sl:
                print(f"        legs settled {len(sl)}: hit {sum(b['result']=='win' for b in sl)}/{len(sl)} vs mean model P {sum(b['model_p'] for b in sl) / len(sl):.3f}")
            for col in ("protocol", "model_version"):
                for k, n in con.execute(f"SELECT {col}, count(*) FROM bets WHERE mode=? AND {where} GROUP BY {col}", (mode,)):
                    print(f"        {col}={k}: {n} rows")
            clv_scorecard([b for b in bs])


def clv_scorecard(bs):
    """CLV = closing fair P - entry fair P (probability points). MISSING closes are counted, never filled in."""
    have = [b["clv"] for b in bs if b["clv"] is not None]
    miss = sum(1 for b in bs if b["close_status"] == "MISSING")
    print(f"        CLV scorecard: {len(have)} with valid close, {miss} CLOSE MISSING")
    if have:
        n = len(have)
        print(f"          beat close {sum(c > 0.0005 for c in have)}/{n}, tied {sum(abs(c) <= 0.0005 for c in have)}/{n}, lost {sum(c < -0.0005 for c in have)}/{n}"
              f" | mean {statistics.mean(have) * 100:+.2f}pp median {statistics.median(have) * 100:+.2f}pp")
        print("          distribution (pp): " + " ".join(f"{c * 100:+.1f}" for c in sorted(have)))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    q = sp.add_parser("add-pp"); q.add_argument("snapshot", type=int); q.add_argument("--stake", type=float, default=0)
    q.add_argument("--mode", choices=("paper", "live"), default="paper")
    p = sp.add_parser("add"); p.add_argument("ticker"); p.add_argument("--stake", type=float, default=20)
    p.add_argument("--mode", choices=("paper", "live"), default="paper"); p.add_argument("--force", action="store_true")
    sp.add_parser("settle"); sp.add_parser("report")
    a = ap.parse_args()
    con = sqlite3.connect(DB, timeout=60)
    con.executescript((Path(__file__).parent / "schema.sql").read_text())
    {"add": add, "add-pp": add_pp, "settle": settle, "report": report}[a.cmd](a, con)


if __name__ == "__main__":
    main()
