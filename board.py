"""KALSHI_VS_PINNACLE_v0.1 - price Kalshi game-winner contracts against Pinnacle's no-vig moneyline.
The "model" here is the sharp market itself; we don't claim to out-predict it, only to find Kalshi asks below it.
Uses the latest snapshot per source (run snapshot.py first). Usage: python3 board.py [--stake 20] [--all]"""
import argparse, json, re, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from odds import american_to_prob, devig_range, kalshi_fee

MODEL = "KALSHI_VS_PINNACLE_v0.1"
DB = Path(__file__).parent / "data" / "market.db"
MAX_START_GAP_H = 36   # kalshi occurrence_datetime is an expected *end*, not the start


def words(s):
    return re.sub(r"[.()']", "", s).lower().split()


def same_team(kalshi_name, pin_name):
    """'Chicago C' ~ 'Chicago Cubs', 'Fresno St.' ~ 'Fresno State', but 'Chicago C' !~ 'Chicago White Sox'."""
    k, p = words(kalshi_name), words(pin_name)
    return len(k) <= len(p) and all(p[i].startswith(w) if i == len(k) - 1 else p[i] == w for i, w in enumerate(k))


def latest(con, source):
    """Kalshi: full rows from the latest local pull. Pinnacle: change-only rows from the cloud collector, so take the
    latest row per market, dropping markets marked gone (price NULL) and games already started."""
    ts = con.execute("SELECT max(ts) FROM snapshots WHERE source=?", (source,)).fetchone()[0]
    if source != "pinnacle":
        return ts, con.execute("SELECT * FROM snapshots WHERE source=? AND ts=?", (source, ts)).fetchall()
    run = con.execute("SELECT max(ts) FROM collector_runs WHERE source='pinnacle' AND ok=1").fetchone()[0] or ts
    rows = con.execute("""SELECT * FROM snapshots WHERE rowid IN (SELECT max(rowid) FROM snapshots WHERE source='pinnacle'
                          GROUP BY event_key, selection) AND price IS NOT NULL AND start_time > ?""",
                       (datetime.now(timezone.utc).isoformat(),)).fetchall()
    return run, rows


def hours(a, b):
    f = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    return abs((f(a) - f(b)).total_seconds()) / 3600


def build(stake):
    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    kts, krows = latest(con, "kalshi")
    pts, prows = latest(con, "pinnacle")
    pin = {}
    for r in prows:
        pin.setdefault(r["event_key"], []).append(r)
    kal = {}
    for r in krows:
        kal.setdefault(r["event_key"], []).append(r)

    out, unmatched = [], []
    for ev, sides in kal.items():
        if len(sides) != 2:
            continue  # ties / 3-way markets are out of scope for v0.1
        match = None
        for pk, ps in pin.items():
            if len(ps) != 2 or ps[0]["league"] != sides[0]["league"] or hours(ps[0]["start_time"], sides[0]["start_time"]) > MAX_START_GAP_H:
                continue
            pairs = [(k, p) for k in sides for p in ps if same_team(k["selection"], p["selection"])]
            if len(pairs) == 2 and pairs[0][1] is not pairs[1][1]:
                match = pairs  # both teams must map one-to-one or we don't trust the join
                break
        if not match:
            unmatched.append(ev)
            continue
        fair = devig_range([american_to_prob(p["price"]) for _, p in match])
        for (k, p), (pt, lo, hi) in zip(match, fair):
            ask, x = k["ask"], json.loads(k["extra"])
            if not ask or ask >= 1:
                continue
            n = max(1, int(stake // ask))
            fee = kalshi_fee(ask, n, x["fee_multiplier"])
            cost_pc = ask + fee / n
            out.append(dict(league=k["league"], game=ev, team=k["selection"], ticker=x["ticker"], start=p["start_time"],
                            ask=ask, bid=k["bid"], ask_size=k["ask_size"], pin_price=p["price"], p=pt, p_lo=lo, p_hi=hi,
                            contracts=n, fee=fee, cost_pc=cost_pc, edge=pt - cost_pc, edge_lo=lo - cost_pc,
                            ev_pct=(pt - cost_pc) / cost_pc, spread=(ask - k["bid"]) if k["bid"] else None))
    return kts, pts, out, unmatched


def action(r):
    """Pre-registered rule for v0.1 (see EXPERIMENTS.md H1). Unvalidated strategy -> PAPER, never BET."""
    if r["ask"] < 0.20:
        return "PASS(<20c)"  # H4 + H4b: taker longshots under 20c lost ~33%
    if r["edge_lo"] <= 0:
        return "PASS"
    if (r["ask_size"] or 0) < r["contracts"]:
        return "PASS(depth)"
    return "PAPER"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stake", type=float, default=20.0, help="dollars per position used for fee rounding")
    ap.add_argument("--all", action="store_true", help="show every matched side, not just the top 15")
    a = ap.parse_args()
    kts, pts, rows, unmatched = build(a.stake)
    rows.sort(key=lambda r: -r["edge"])
    print(f"{MODEL} | kalshi snapshot {kts} | pinnacle snapshot {pts} | stake ${a.stake:.0f}")
    print(f"matched sides {len(rows)} | unmatched kalshi events {len(unmatched)}\n")
    print(f"{'ACTION':11} {'LEAGUE':5} {'TEAM':18} {'ASK':>5} {'FEE/c':>6} {'PIN':>6} {'FAIR P':>13} {'EDGE':>6} {'EV%':>6} {'SPRD':>5}")
    for r in rows if a.all else rows[:15]:
        print(f"{action(r):11} {r['league']:5} {r['team'][:18]:18} {r['ask']:5.2f} {r['fee']/r['contracts']:6.3f} {r['pin_price']:+6.0f} "
              f"{r['p_lo']:.3f}-{r['p_hi']:.3f} {r['edge']*100:5.1f}pp {r['ev_pct']*100:5.1f}% {(r['spread'] or 0)*100:4.0f}c")
    print("\nEDGE = Pinnacle no-vig P minus Kalshi all-in cost per contract. FAIR P range = multiplicative vs power devig.")
    print("PAPER = passes the rule but the strategy is unvalidated: log it (ledger.py add --mode paper), don't fund it.")


if __name__ == "__main__":
    assert same_team("Chicago C", "Chicago Cubs") and not same_team("Chicago C", "Chicago White Sox")
    assert same_team("Fresno St.", "Fresno State") and same_team("New Orleans", "New Orleans Saints")
    assert not same_team("New York J", "New York Giants")
    main()
