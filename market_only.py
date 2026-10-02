"""MARKET_ONLY_v1 - implementation of the pre-registered strategy (MARKET_ONLY_v1.md, commit 9246342).
  python3 market_only.py scan      gate every eligible outcome; log ENTRY (passes) and OBSERVATION (0.25-1h pre-start, all)
  python3 market_only.py settle    close (Pinnacle, coverage-checked) + Kalshi settlement for started events
  python3 market_only.py report    scorecards: pooled, by sport/league/grade/edge bucket/time-to-start, halves, looks
Nothing here may deviate from the pre-registration; if it must, that is MARKET_ONLY_v2."""
import hashlib, json, math, sqlite3, statistics, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import match, quality, registry, status
from odds import american_to_prob, devig_all

ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.db"
STRATEGY = "MARKET_ONLY_v1"
def _cfg_hash():
    """Hash of the matcher/rules/quality configuration actually in use (aliases, thresholds, flags), so an unversioned edit
    can never hide inside a version label."""
    import hashlib, rules
    cfg = json.dumps([match.ALIASES, match.AUTO_THRESHOLD, sorted(match.STOP), match.FLAG_PATTERNS, rules.PINNACLE,
                      quality.DIMS, quality.REQUIRED_EDGE, quality.DRIFT_VERSION, quality.DRIFT_SD], sort_keys=True, default=str)
    return hashlib.sha256(cfg.encode()).hexdigest()[:10]


VERSIONS = f"MARKET_ONLY_v1|REGISTRY_v1.0|MATCH_v1.0|RULES_v1.0|QUALITY_v0.1|DATA_v1.3|paper-taker|cfg:{_cfg_hash()}"
CONTRACTS = 100
LOOKS, ALPHA = (50, 100, 200, 400), 0.0125
PIN_JOB = {"Esports": "E Sports", "MMA": "Mixed Martial Arts", "Rugby": "Rugby Union"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS mo_entries (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('ENTRY','OBSERVATION')),
  strategy TEXT NOT NULL, versions TEXT NOT NULL, kalshi_event TEXT, market_ticker TEXT NOT NULL, pinnacle_id TEXT,
  sport TEXT, league TEXT, outcome TEXT, pin_outcome TEXT, n_outcomes INTEGER, start_time TEXT, hours_to_start REAL,
  grade TEXT, fair_mult REAL, fair_lo REAL, fair_hi REAL, bid REAL, ask REAL, ask_size REAL, cost REAL, edge REAL,
  required_edge REAL, gate TEXT, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
  close_fair REAL, close_ts TEXT, close_status TEXT, ev_close REAL, result TEXT, payout REAL, pnl REAL, settled_ts TEXT,
  close_source TEXT, kalshi_close_mid REAL, kalshi_close_ts TEXT,
  UNIQUE (kind, market_ticker));
CREATE TRIGGER IF NOT EXISTS mo_pre_immutable BEFORE UPDATE OF ts, kind, strategy, versions, kalshi_event, market_ticker,
  pinnacle_id, sport, league, outcome, pin_outcome, n_outcomes, start_time, hours_to_start, grade, fair_mult, fair_lo, fair_hi,
  bid, ask, ask_size, cost, edge, required_edge, gate, payload, sha256 ON mo_entries
BEGIN SELECT RAISE(ABORT, 'pre-event fields are immutable'); END;
CREATE TRIGGER IF NOT EXISTS mo_no_delete BEFORE DELETE ON mo_entries BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
now_utc = lambda: datetime.now(timezone.utc)


def gate(a, o, now):
    """The 14 pre-registered checks. Returns (dict name->bool, all_pass)."""
    kal_age = (now - datetime.fromisoformat(a["kalshi_ts"])).total_seconds() / 60 if a.get("kalshi_ts") else 1e9
    g = {
        "EVENT MATCH VERIFIED": a["conf"] >= match.AUTO_THRESHOLD,
        "MARKET EQUIVALENCE VERIFIED": a["eq"] in ("EXACT", "NEAR_EQUIVALENT"),
        "RULES VERIFIED": a["eq"] in ("EXACT", "NEAR_EQUIVALENT"),
        "CURRENT PRICES": kal_age <= 5 and not o["stale"],
        "VIG REMOVED": o["fair"]["lo"] <= o["fair"]["mult"],
        "SOURCE QUALITY ACCEPTABLE": o["dim_grades"]["pinnacle_limit"] != "D",
        "MARKET QUALITY ACCEPTABLE": o["grade"] in ("A", "B", "C"),
        "LIQUIDITY": (o["ask_size"] or 0) >= CONTRACTS,
        "SPREAD": o["dim_grades"]["kalshi_spread"] != "D",
        "MOVEMENT CHECKED": o["dim_grades"]["pin_range_6h"] != "D",
        "EXECUTION COSTS": o["cost"] > o["ask"],
        "EDGE SURVIVES CONSERVATIVE": o["need"] is not None and o["edge"] >= o["need"],
        "COLLECTOR HEALTHY": not o["stale"],
        "TIMING": a["hours"] is not None and 0.25 <= a["hours"] <= 168,
    }
    return g, all(g.values())


def book_fill(ticker, n=CONTRACTS):
    """Live Kalshi order book at decision time -> VWAP YES cost for n contracts (asks = 1 - NO bids)."""
    try:
        ob = registry.get(f"{registry.KAL}/markets/{ticker}/orderbook")["orderbook_fp"]
    except Exception as e:
        return dict(error=repr(e)[:120])
    asks = sorted((round(1 - float(p), 4), float(q)) for p, q in (ob.get("no_dollars") or []))
    need, cost, levels = n, 0.0, []
    for price, qty in asks:
        take = min(need, qty)
        cost += take * price; need -= take; levels.append((price, qty))
        if need <= 0:
            break
    return dict(vwap=(cost / n) if need <= 0 else None, fillable=need <= 0, top_levels=levels[:5])


def record(con, kind, a, o, g, now):
    if kind == "ENTRY":
        o = dict(o, live_book=book_fill(o["market_ticker"]))
    payload = json.dumps(dict(assessment={k: v for k, v in a.items() if k != "outcomes"}, outcome=o, gate=g,
                              kind=kind, ts=now.isoformat(timespec="seconds"), versions=VERSIONS), default=str, sort_keys=True)
    sha = hashlib.sha256(payload.encode()).hexdigest()
    try:
        con.execute("""INSERT INTO mo_entries (ts, kind, strategy, versions, kalshi_event, market_ticker, pinnacle_id, sport, league,
            outcome, pin_outcome, n_outcomes, start_time, hours_to_start, grade, fair_mult, fair_lo, fair_hi, bid, ask, ask_size, cost,
            edge, required_edge, gate, payload, sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (now.isoformat(timespec="seconds"), kind, STRATEGY, VERSIONS, a["ticker"], o["market_ticker"], a["pid"], a["sport"],
                     a["series"], o["outcome"], o["pin"], a["n_outcomes"], a["start"], a["hours"], o["grade"], o["fair"]["mult"],
                     o["fair"]["lo"], o["fair"]["hi"], o["bid"], o["ask"], o["ask_size"], o["cost"], o["edge"], o["need"],
                     json.dumps(g), payload, sha))
        return True
    except sqlite3.IntegrityError:
        return False  # already logged (one per market per kind, ever)


def scan():
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    now = now_utc()
    health = {(h["league"], h["source"]): h for h in status.health(con, now)}
    tickers = [t for (t,) in con.execute("""SELECT source_event_id FROM reg_sources WHERE source='kalshi' AND match_confidence >= ?
                                            AND equivalence IN ('EXACT','NEAR_EQUIVALENT')""", (match.AUTO_THRESHOLD,))]
    n_eval = n_entry = n_obs = 0
    fails = defaultdict(int)
    first = {t: quality.assess(con, t, now, health) for t in tickers}
    # Plumbing, not a rule change: refresh Kalshi (the pre-registered <=5 min requirement) for leagues where an outcome is
    # within 3pp of its required edge, or inside the OBSERVATION window, then re-assess those events.
    import snapshot
    refresh = {a["series"] for a in first.values() for o in a.get("outcomes", []) if "fair" in o and (
        (o["need"] is not None and o["edge"] >= o["need"] - 0.03) or (a["hours"] is not None and 0.25 <= a["hours"] <= 1.0))}
    ts = now.isoformat(timespec="seconds")
    for series in sorted(refresh):
        label = quality.LEGACY.get(series, series)
        try:
            rows = snapshot.kalshi_rows(label, series, ts)
            con.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.execute("INSERT INTO collector_runs VALUES (?,?,?,?,?,?,?,?)", (ts, label, "kalshi", 1, len(rows), len(rows), None, 0))
        except Exception as e:
            con.execute("INSERT INTO collector_runs VALUES (?,?,?,?,?,?,?,?)", (ts, label, "kalshi", 0, None, None, repr(e)[:300], 0))
    con.commit()
    if refresh:
        health = {(h["league"], h["source"]): h for h in status.health(con, now)}
    print(f"refreshed Kalshi for {len(refresh)} near-threshold / observation-window leagues")
    for t in tickers:
        a = quality.assess(con, t, now, health) if first[t].get("series") in refresh else first[t]
        for o in a.get("outcomes", []):
            if "fair" not in o or not o.get("market_ticker"):
                continue
            n_eval += 1
            g, ok = gate(a, o, now)
            for k, v in g.items():
                fails[k] += not v
            if ok:  # amendment A2: the live book must fill 100 contracts with the edge intact at the VWAP
                from odds import kalshi_fee
                bk = book_fill(o["market_ticker"])
                vw = bk.get("vwap")
                ok = bool(bk.get("fillable") and vw and o["fair"]["lo"] - vw - kalshi_fee(vw, CONTRACTS, o["fee_mult"]) / CONTRACTS >= o["need"])
                fails["A2 LIVE BOOK FILL"] += not ok
            if ok and record(con, "ENTRY", a, o, g, now):
                n_entry += 1
                import alerts
                alerts.notify("MARKET_ONLY_v1 paper ENTRY", f"{o['outcome']} ({a['sport']}) ask {o['ask']:.2f} edge {o['edge'] * 100:+.1f}pp grade {o['grade']}")
            if a["hours"] is not None and 0.25 <= a["hours"] <= 1.0:
                n_obs += record(con, "OBSERVATION", a, o, g, now)
    con.commit()
    print(f"{STRATEGY} scan {now.isoformat(timespec='seconds')}: {len(tickers)} eligible events, {n_eval} priced outcomes -> "
          f"{n_entry} new ENTRY, {n_obs} new OBSERVATION")
    print("gate failures (outcomes failing each check): " + ", ".join(f"{k} {v}" for k, v in sorted(fails.items(), key=lambda x: -x[1]) if v))


def close_fair(con, pid, pname, start):
    """Last complete Pinnacle pull at or before start -> (multiplicative fair for pname, ts)."""
    rows = con.execute("""SELECT ts, selection, price FROM snapshots WHERE source='pinnacle' AND event_key=? AND price IS NOT NULL
                          AND ts <= ? ORDER BY ts""", (pid, start.replace("Z", "+00:00"))).fetchall()
    n = len({s for _, s, _ in rows})
    cur, last = {}, (None, None)
    for i, (ts, s, p) in enumerate(rows):
        cur[s] = p
        if len(cur) == n and n >= 2 and pname in cur and (i + 1 == len(rows) or rows[i + 1][0] != ts):
            names = list(cur)
            last = (devig_all([american_to_prob(cur[x]) for x in names])[names.index(pname)]["mult"], ts)
    return last


def settle():
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    now = now_utc()
    n_close = n_res = 0
    for r in con.execute("SELECT * FROM mo_entries WHERE settled_ts IS NULL AND start_time <= ?", (now.isoformat(),)).fetchall():
        if r["close_status"] is None:
            p, ts = close_fair(con, r["pinnacle_id"], r["pin_outcome"], r["start_time"])
            st = r["start_time"].replace("Z", "+00:00")
            lo = (datetime.fromisoformat(st) - timedelta(minutes=15)).isoformat()
            pl = con.execute("SELECT pinnacle_league FROM reg_league_map WHERE kalshi_series=?", (r["league"],)).fetchone()
            job = f"SPORT:Soccer|{pl[0]}" if r["sport"] == "Soccer" and pl else "SPORT:" + PIN_JOB.get(r["sport"], r["sport"])
            cov = con.execute("SELECT max(ts) FROM collector_runs WHERE league=? AND source='pinnacle' AND ok=1 AND ts>=? AND ts<=?",
                              (job, lo, st)).fetchone()[0]
            ok = p is not None and cov is not None
            kc = con.execute("""SELECT bid, ask, ts FROM snapshots WHERE source='kalshi' AND json_extract(extra,'$.ticker')=? AND ts<=?
                                ORDER BY ts DESC LIMIT 1""", (r["market_ticker"], st)).fetchone()
            kok = kc and (datetime.fromisoformat(st) - datetime.fromisoformat(kc[2])).total_seconds() <= 900 and kc[0] and kc[1]
            con.execute("""UPDATE mo_entries SET close_fair=?, close_ts=?, close_status=?, ev_close=?, close_source=?, kalshi_close_mid=?,
                           kalshi_close_ts=? WHERE id=?""",
                        (p if ok else None, ts, "ok" if ok else "MISSING", (p - r["cost"]) if ok else None,
                         f"pinnacle {job} run {cov}" if ok else None, ((kc[0] + kc[1]) / 2) if kok else None, kc[2] if kok else None, r["id"]))
            n_close += 1
        if r["start_time"] > (now - timedelta(minutes=90)).isoformat():
            continue  # results only after the event can plausibly be over; spares Kalshi requests
        try:
            m = registry.get(f"{registry.KAL}/markets/{r['market_ticker']}")["market"]
        except Exception:
            continue
        if m.get("status") in ("finalized", "settled") and (m.get("result") or m.get("settlement_value_dollars")):
            pay = {"yes": 1.0, "no": 0.0}.get(m.get("result"))
            if pay is None:
                pay = float(m.get("settlement_value_dollars") or 0)
            con.execute("UPDATE mo_entries SET result=?, payout=?, pnl=?, settled_ts=? WHERE id=?",
                        (m.get("result") or "value", pay, pay * CONTRACTS - r["cost"] * CONTRACTS, now.isoformat(timespec="seconds"), r["id"]))
            n_res += 1
    con.commit()
    print(f"{STRATEGY} settle {now.isoformat(timespec='seconds')}: closes recorded {n_close}, results recorded {n_res}")


def cluster_ci(rows, key="kalshi_event", B=1000, seed=3):
    """Event-clustered bootstrap CI of mean EV@close (outcomes of one event are not independent). Amendment A1."""
    import random
    by = defaultdict(list)
    for r in rows:
        if r["ev_close"] is not None:
            by[r[key]].append(r["ev_close"])
    if len(by) < 2:
        return None
    ks, rnd, st = list(by), random.Random(seed), []
    for _ in range(B):
        s = [by[ks[rnd.randrange(len(ks))]] for _ in ks]
        st.append(sum(map(sum, s)) / sum(map(len, s)))
    st.sort()
    return st[int(.025 * B)], st[int(.975 * B)], st[int(ALPHA * B)], len(ks)


def mean_ci(x):
    if len(x) < 2:
        return (x[0] if x else float("nan")), float("nan"), float("nan"), float("nan")
    m, s = statistics.mean(x), statistics.stdev(x)
    se = s / math.sqrt(len(x))
    z_one = statistics.NormalDist().inv_cdf(1 - ALPHA)
    return m, m - 1.96 * se, m + 1.96 * se, m - z_one * se  # last: one-sided lower bound at the per-look alpha


def table(rows, key, title):
    groups = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    print(f"  by {title}:")
    for k in sorted(groups, key=str):
        g = groups[k]
        ev = [r["ev_close"] for r in g if r["ev_close"] is not None]
        res = [r for r in g if r["result"] is not None]
        m, lo, hi, _ = mean_ci(ev)
        print(f"    {str(k)[:26]:26} n {len(g):4} | valid close {len(ev):4} EV@close {m * 100 if ev else float('nan'):+6.2f}pp [{lo * 100:+.2f}, {hi * 100:+.2f}]"
              + (f" | settled {len(res)} win {sum(r['payout'] >= 1 for r in res)} P/L ${sum(r['pnl'] for r in res):+.0f}" if res else ""))


def report():
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    for kind in ("ENTRY", "OBSERVATION"):
        rows = con.execute("SELECT * FROM mo_entries WHERE kind=? ORDER BY ts", (kind,)).fetchall()
        closed = [r for r in rows if r["close_status"]]
        miss = sum(r["close_status"] == "MISSING" for r in closed)
        ev = [r["ev_close"] for r in rows if r["ev_close"] is not None]
        print(f"\n=== {STRATEGY} {kind}S: {len(rows)} logged, {len(closed)} past start ({miss} CLOSE MISSING = "
              f"{(miss / len(closed) * 100) if closed else 0:.0f}%), {sum(r['result'] is not None for r in rows)} settled ===")
        if not rows:
            continue
        m, lo, hi, lb = mean_ci(ev)
        print(f"  pooled EV@close: {m * 100 if ev else float('nan'):+.2f}pp, naive 95% CI [{lo * 100:+.2f}, {hi * 100:+.2f}], n={len(ev)}")
        cc = cluster_ci(rows)
        if cc:
            print(f"  EVENT-CLUSTERED 95% CI [{cc[0] * 100:+.2f}, {cc[1] * 100:+.2f}] over {cc[3]} events; one-sided lower bound at alpha {ALPHA}: {cc[2] * 100:+.2f}pp (PRIMARY per amendment A1)")
        if kind == "ENTRY":
            look = max([l for l in LOOKS if len(ev) >= l], default=None)
            print(f"  looks reached: {look or 'none yet'} (next at {next((l for l in LOOKS if len(ev) < l), '-')}); "
                  + (f"one-sided lower bound at alpha {ALPHA}: {lb * 100:+.2f}pp -> {'SIGNIFICANT' if lb > 0 else 'not significant'}" if look else "no inference before the first look"))
            half = len(ev) // 2
            if half:
                print(f"  discovery half {statistics.mean(ev[:half]) * 100:+.2f}pp | confirmation half {statistics.mean(ev[half:]) * 100:+.2f}pp")
            if look and len(ev) >= 100 and hi < 0:
                print("  STOP RULE TRIGGERED: 95% CI upper bound < 0 -> reject MARKET_ONLY_v1")
            if closed and miss / len(closed) > 0.20:
                print("  DATA INTEGRITY PAUSE: > 20% missing closes")
        table(rows, lambda r: r["sport"], "sport")
        table(rows, lambda r: r["grade"], "quality grade")
        table(rows, lambda r: next(f"{a}-{b}pp" for a, b in ((-99, 0), (0, 2), (2, 4), (4, 6), (6, 8), (8, 99)) if a <= r["edge"] * 100 < b), "estimated edge bucket")
        table(rows, lambda r: "<1h" if r["hours_to_start"] < 1 else "1-6h" if r["hours_to_start"] < 6 else "6-24h" if r["hours_to_start"] < 24 else ">24h", "time to start")
        table(rows, lambda r: "ask>=500" if (r["ask_size"] or 0) >= 500 else "100-499" if (r["ask_size"] or 0) >= 100 else "<100", "liquidity")
        res = [r for r in rows if r["result"] is not None and r["close_fair"] is not None]
        if res:
            print(f"  calibration: win rate {sum(r['payout'] >= 1 for r in res) / len(res):.3f} vs mean close fair {statistics.mean(r['close_fair'] for r in res):.3f} (n={len(res)})")


if __name__ == "__main__":
    {"scan": scan, "settle": settle, "report": report}[sys.argv[1]]()
