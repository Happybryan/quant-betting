"""QUALITY_v0.1 - market-quality grade (A-D) for each outcome of a registry-matched Kalshi event, plus the
market-only price comparison that uses it. The grade is the WORST dimension (conservative), with reasons.
Thresholds and required edges are PROVISIONAL: MARKET_ONLY_v1 must fix them before any forward results are read,
and they are then re-estimated from data (directive 16, 50).
Usage: python3 quality.py [KALSHI_EVENT_TICKER ...]    (no args: scan every auto-matched event, research view)"""
import json, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import match, status
from odds import american_to_prob, devig_all, kalshi_fee

DB = Path(__file__).parent / "data" / "market.db"
VERSION = "QUALITY_v0.1"
# dimension -> (A, B, C) limits; worse than C = D. "lo" means bigger is better.
DIMS = {
    "pinnacle_limit": ("lo", 1000, 250, 50),        # max stake Pinnacle accepts ($): information behind the price
    "kalshi_spread": ("hi", 0.02, 0.04, 0.08),      # ask - bid
    "kalshi_ask_size": ("lo", 100, 25, 5),          # contracts at the ask
    "devig_spread": ("hi", 0.01, 0.02, 0.04),       # disagreement between mult/power/Shin fair P
    "source_gap": ("hi", 0.03, 0.06, 0.10),         # |Kalshi mid - Pinnacle fair|: large gaps are suspicious, not free money
    "pin_range_6h": ("hi", 0.02, 0.04, 0.08),       # range of Pinnacle fair P over the last 6h (stability)
}
REQUIRED_EDGE = {"A": 0.015, "B": 0.03, "C": 0.05, "D": None}   # HEURISTIC (audit 2026-09-28): not derived from data; D = never
# Pinnacle fair drift to close, SD by hours-to-start: latest version in config/drift_sd.json (drift.py appends monthly)
_DRIFT = json.loads((Path(__file__).parent / "config" / "drift_sd.json").read_text())[-1]
DRIFT_VERSION = _DRIFT["version"]
DRIFT_SD = [(float(k), v) for k, v in sorted(_DRIFT["sd"].items(), key=lambda kv: float(kv[0])) if v is not None]


def edge_uncertainty(fair, cost, hours):
    """Point edge, sd, 90% interval and P(true edge > 0). sd combines close-drift and devig-method disagreement.
    NOT included (unmeasurable per event): Pinnacle's own error vs the truth. So P(edge>0) is an UPPER bound."""
    import math, statistics
    drift = next(sd for h, sd in DRIFT_SD if (hours or 0) < h)
    sd = math.sqrt(drift ** 2 + ((fair["hi"] - fair["lo"]) / 2) ** 2)
    e = fair["mult"] - cost
    return dict(edge=e, sd=sd, lo90=e - 1.645 * sd, hi90=e + 1.645 * sd, p_pos=statistics.NormalDist().cdf(e / sd), breakeven=cost)
LEGACY = {v: k for k, v in status.LEGACY.items()}


def grade_dim(name, v):
    if v is None:
        return "C"  # unknown is never better than C
    d, a, b, c = DIMS[name]
    ok = (lambda x, t: x >= t) if d == "lo" else (lambda x, t: x <= t)
    return "A" if ok(v, a) else "B" if ok(v, b) else "C" if ok(v, c) else "D"


def pin_path(con, pid, sel, now, n_outcomes):
    """Pinnacle no-vig P path for one outcome over the last 6h, rebuilt from change-only rows."""
    rows = con.execute("""SELECT ts, selection, price FROM snapshots WHERE source='pinnacle' AND event_key=? AND price IS NOT NULL
                          ORDER BY ts""", (pid,)).fetchall()
    cur, path = {}, []
    for i, (ts, s, p) in enumerate(rows):
        cur[s] = p
        last_of_pull = i + 1 == len(rows) or rows[i + 1][0] != ts
        if sel in cur and len(cur) == n_outcomes and last_of_pull:  # full outcome set, after the whole pull applied
            names = list(cur)
            fair = devig_all([american_to_prob(cur[n]) for n in names])
            path.append((ts, fair[names.index(sel)]["mult"]))
    cutoff = (now - timedelta(hours=6)).isoformat()
    return [p for t, p in path if t >= cutoff] or [p for _, p in path[-1:]]


def assess(con, ticker, now=None, health=None):
    now = now or datetime.now(timezone.utc)
    row = con.execute("""SELECT k.match_confidence, k.equivalence, p.source_event_id, e.sport, k.source_league, e.start_utc
                         FROM reg_sources k JOIN reg_sources p ON p.event_id=k.event_id AND p.source='pinnacle'
                         JOIN reg_events e ON e.event_id=k.event_id WHERE k.source='kalshi' AND k.source_event_id=?""", (ticker,)).fetchone()
    if not row:
        return dict(ticker=ticker, verdict="PASS", why="no registry match")
    conf, eq, pid, sport, series, start = row
    if conf < match.AUTO_THRESHOLD or eq not in ("EXACT", "NEAR_EQUIVALENT"):
        return dict(ticker=ticker, verdict="PASS", why=f"identity {conf} / rules {eq}")
    pin = {s: (p, json.loads(x)) for s, p, x in con.execute(
        """SELECT selection, price, extra FROM snapshots WHERE rowid IN (SELECT max(rowid) FROM snapshots WHERE source='pinnacle'
           AND event_key=? GROUP BY selection)""", (pid,)) if p is not None}
    label = LEGACY.get(series, series)
    kts = con.execute("SELECT max(ts) FROM snapshots WHERE source='kalshi' AND league=?", (label,)).fetchone()[0]
    kal = {s: (b, a, sz, json.loads(x)) for s, b, a, sz, x in con.execute(
        "SELECT selection, bid, ask, ask_size, extra FROM snapshots WHERE source='kalshi' AND league=? AND ts=? AND event_key=?", (label, kts, ticker))}
    if len(pin) < 2 or not kal:
        return dict(ticker=ticker, verdict="PASS", why="missing prices (Pinnacle %d outcomes, Kalshi %d)" % (len(pin), len(kal)))
    health = health or {(h["league"], h["source"]): h for h in status.health(con, now)}
    pin_league = con.execute("SELECT pinnacle_league FROM reg_league_map WHERE kalshi_series=?", (series,)).fetchone()
    pin_job = (f"SPORT:Soccer|{pin_league[0]}" if sport == "Soccer" and pin_league else
               f"SPORT:{sport}" if sport != "Esports" else "SPORT:E Sports")
    jobs = [(pin_job, "pinnacle"), (label, "kalshi")]
    stale = [f"{j[0]}/{j[1]} {health[j]['state'] if health.get(j) else 'NO_HEARTBEAT'}" for j in jobs
             if not health.get(j) or health[j]["state"] != "HEALTHY"]  # missing heartbeat = stale (FAILURES F-daily-1)
    names = list(pin)
    fair = dict(zip(names, devig_all([american_to_prob(pin[n][0]) for n in names])))
    hours = (datetime.fromisoformat(start.replace("Z", "+00:00")) - now).total_seconds() / 3600 if start else None
    out = []
    for kname, (bid, ask, size, kx) in kal.items():
        pname = next((n for n in names if (kname.lower() in ("tie", "draw") and n.lower() == "draw") or
                      (kname.lower() not in ("tie", "draw") and match.participant_score(kname, n, sport) >= match.AUTO_THRESHOLD)), None)
        if not pname or not ask or not bid or not 0 < ask < 1:
            out.append(dict(outcome=kname, verdict="PASS", why="outcome not mapped or no two-sided Kalshi quote"))
            continue
        f = fair[pname]
        mult = kx.get("fee_multiplier") or 1
        cost = ask + kalshi_fee(ask, 100, mult) / 100
        path = pin_path(con, pid, pname, now, len(names))
        dims = dict(pinnacle_limit=pin[pname][1].get("limit"), kalshi_spread=round(ask - bid, 4), kalshi_ask_size=size,
                    devig_spread=f["hi"] - f["lo"], source_gap=abs((bid + ask) / 2 - f["mult"]), pin_range_6h=max(path) - min(path))
        grades = {d: grade_dim(d, v) for d, v in dims.items()}
        g = max(grades.values())  # 'D' > 'C' > 'B' > 'A' alphabetically: worst dimension wins
        reasons = [f"{d}={dims[d] if not isinstance(dims[d], float) else round(dims[d], 3)}:{gr}" for d, gr in grades.items() if gr == g and g != "A"]
        if stale:
            g, reasons = "D", reasons + ["STALE DATA: " + ", ".join(stale)]
        if hours is not None and not 0.25 <= hours <= 168:
            g, reasons = "D", reasons + [f"time to start {hours:.1f}h outside 0.25-168h"]
        edge = f["lo"] - cost
        need = REQUIRED_EDGE[g]
        verdict = "RESEARCH-CANDIDATE" if need is not None and edge >= need else "PASS"
        unc = edge_uncertainty(f, cost, hours)
        out.append(dict(outcome=kname, pin=pname, fair=f, bid=bid, ask=ask, cost=cost, edge=edge, grade=g, need=need, uncertainty=unc,
                        reasons=reasons, dims=dims, dim_grades=grades, verdict=verdict, market_ticker=kx.get("ticker"),
                        ask_size=size, fee_mult=mult, stale=stale))
    return dict(ticker=ticker, sport=sport, series=series, hours=hours, eq=eq, conf=conf, outcomes=out, pid=pid,
                start=start, kalshi_ts=kts, n_outcomes=len(names))


def show(a):
    if "outcomes" not in a:
        return print(f"{a['ticker']}: {a['verdict']} ({a['why']})")
    print(f"{a['ticker']} | {a['sport']} | starts in {a['hours']:.1f}h | match {a['conf']} | rules {a['eq']}")
    for o in a["outcomes"]:
        if "fair" not in o:
            print(f"   {o['outcome'][:22]:22} {o['verdict']} ({o['why']})")
            continue
        print(f"   {o['outcome'][:22]:22} fair {o['fair']['lo']:.3f}-{o['fair']['hi']:.3f} | K {o['bid']:.2f}/{o['ask']:.2f} cost {o['cost']:.3f} | "
              f"edge {o['edge'] * 100:+5.1f}pp | grade {o['grade']} need {'-' if o['need'] is None else f'{o[chr(110)+chr(101)+chr(101)+chr(100)]*100:.1f}pp'} "
              f"| {o['verdict']}" + (f"  [{'; '.join(o['reasons'])}]" if o["reasons"] else ""))


if __name__ == "__main__":
    con = sqlite3.connect(DB, timeout=60)
    now = datetime.now(timezone.utc)
    health = {(h["league"], h["source"]): h for h in status.health(con, now)}
    tickers = sys.argv[1:] or [t for (t,) in con.execute(
        "SELECT source_event_id FROM reg_sources WHERE source='kalshi' AND match_confidence >= ? AND equivalence IN ('EXACT','NEAR_EQUIVALENT')",
        (match.AUTO_THRESHOLD,))]
    res = [assess(con, t, now, health) for t in tickers]
    if sys.argv[1:]:
        for a in res:
            show(a)
    else:
        from collections import Counter
        outs = [o for a in res if "outcomes" in a for o in a["outcomes"] if "grade" in o]
        print(f"{VERSION} research scan {now.isoformat(timespec='seconds')}: {len(res)} matched events, {len(outs)} priced outcomes")
        print("grades:", dict(sorted(Counter(o["grade"] for o in outs).items())), "| passes w/o pricing:",
              sum(1 for a in res if "outcomes" not in a))
        print("capping dimensions:", dict(Counter(r.split("=")[0] for o in outs for r in o["reasons"]).most_common(8)))
        best = sorted(outs, key=lambda o: -o["edge"])[:8]
        print("top edges (conservative fair minus Kalshi all-in cost) - RESEARCH ONLY, MARKET_ONLY_v1 not yet registered:")
        for o in best:
            print(f"   {o['edge'] * 100:+5.1f}pp grade {o['grade']} need {'-' if o['need'] is None else round(o['need'] * 100, 1)} "
                  f"{o['outcome'][:24]:24} {o['verdict']}  {'; '.join(o['reasons'])[:90]}")
