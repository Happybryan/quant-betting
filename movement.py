"""MOVEMENT_v0.1 - cross-source movement and lead/lag for registry-matched events (directive 18-19).
For every Pinnacle no-vig move >= MOVE (between consecutive observations) it asks: did Kalshi's mid follow, lead, or
not react, and how many minutes later? Time resolution is bounded by collector cadence, and that bound is reported.
This only MEASURES; it claims no exploitable lag (fees, latency, fills are not modeled here).
Usage: python3 movement.py [KALSHI_EVENT_TICKER]     (no args: summary across all matched events)"""
import sqlite3, statistics, sys
from datetime import datetime
from pathlib import Path
import match, quality
from odds import american_to_prob, devig_all

DB = Path(__file__).parent / "data" / "market.db"
MOVE, FOLLOW_FRAC, WINDOW_MIN = 0.015, 0.5, 60
t = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))


def lead_lag(pin, kal):
    """pin, kal: [(iso_ts, prob)] sorted. Returns list of dicts, one per Pinnacle move."""
    out = []
    for (t0, p0), (t1, p1) in zip(pin, pin[1:]):
        d = p1 - p0
        if abs(d) < MOVE:
            continue
        before = [p for ts, p in kal if ts <= t0]
        if not before:
            continue
        base = before[-1]
        led = [ts for ts, p in kal if t0 < ts <= t1 and (p - base) * d > 0 and abs(p - base) >= FOLLOW_FRAC * abs(d)]
        after = [ts for ts, p in kal if t1 < ts and (t(ts) - t(t1)).total_seconds() <= WINDOW_MIN * 60
                 and (p - base) * d > 0 and abs(p - base) >= FOLLOW_FRAC * abs(d)]
        res = (t(t1) - t(t0)).total_seconds() / 60  # the Pinnacle move happened somewhere in (t0, t1]
        if led:
            out.append(dict(at=t1, move=d, result="KALSHI_FIRST_OR_SAME_WINDOW", lag_min=None, resolution_min=res))
        elif after:
            out.append(dict(at=t1, move=d, result="KALSHI_FOLLOWED", lag_min=(t(after[0]) - t(t1)).total_seconds() / 60, resolution_min=res))
        else:
            has_obs = any(t1 < ts and (t(ts) - t(t1)).total_seconds() <= WINDOW_MIN * 60 for ts, _ in kal)
            out.append(dict(at=t1, move=d, result="NO_FOLLOW" if has_obs else "UNOBSERVED (no Kalshi data after move)",
                            lag_min=None, resolution_min=res))
    return out


def opportunities(con, ticker, contracts=100):
    """For each Pinnacle move: at the first Kalshi observation AFTER it (before Kalshi reacted), could you have bought the side
    whose fair value rose, at the ask, with size, and still had edge after the taker fee? Measurement only."""
    from odds import kalshi_fee
    out = []
    for ks, (pp, _) in paths(con, ticker).items():
        full = con.execute("""SELECT ts, bid, ask, ask_size, json_extract(extra,'$.fee_multiplier') FROM snapshots WHERE source='kalshi'
                              AND event_key=? AND selection=? AND ask IS NOT NULL ORDER BY ts""", (ticker, ks)).fetchall()
        for (t0, p0), (t1, p1) in zip(pp, pp[1:]):
            if p1 - p0 < MOVE:  # only upward fair moves create a buy-YES opportunity on this side
                continue
            nxt = next((f for f in full if f[0] > t1), None)
            if not nxt:
                continue
            ts, bid, ask, size, mult = nxt
            fee = kalshi_fee(ask, contracts, mult or 1) / contracts
            out.append(dict(outcome=ks, pin_move=p1 - p0, delay_min=(t(ts) - t(t1)).total_seconds() / 60, ask=ask, size=size,
                            edge_after_fee=p1 - ask - fee, fillable=(size or 0) >= contracts))
    return out


def paths(con, ticker):
    row = con.execute("""SELECT p.source_event_id, e.sport, k.source_league FROM reg_sources k JOIN reg_sources p ON
                         p.event_id=k.event_id AND p.source='pinnacle' JOIN reg_events e ON e.event_id=k.event_id
                         WHERE k.source='kalshi' AND k.source_event_id=? AND k.match_confidence>=?""", (ticker, match.AUTO_THRESHOLD)).fetchone()
    if not row:
        return {}
    pid, sport, series = row
    label = quality.LEGACY.get(series, series)
    prow = con.execute("SELECT ts, selection, price FROM snapshots WHERE source='pinnacle' AND event_key=? AND price IS NOT NULL ORDER BY ts", (pid,)).fetchall()
    n = len({s for _, s, _ in prow})
    cur, pin = {}, {}
    for i, (ts, s, p) in enumerate(prow):
        cur[s] = p
        last_of_pull = i + 1 == len(prow) or prow[i + 1][0] != ts  # evaluate once per pull, never mid-update
        if len(cur) == n and last_of_pull:
            names = list(cur)
            for nm, f in zip(names, devig_all([american_to_prob(cur[x]) for x in names])):
                pin.setdefault(nm, []).append((ts, f["mult"]))
    kal = {}
    for ts, s, b, a in con.execute("""SELECT ts, selection, bid, ask FROM snapshots WHERE source='kalshi' AND league=? AND event_key=?
                                      AND bid IS NOT NULL AND ask IS NOT NULL ORDER BY ts""", (label, ticker)):
        kal.setdefault(s, []).append((ts, (b + a) / 2))
    out = {}
    for ks, kp in kal.items():
        pn = next((nm for nm in pin if (ks.lower() in ("tie", "draw") and nm.lower() == "draw") or
                   (ks.lower() not in ("tie", "draw") and match.participant_score(ks, nm, sport) >= match.AUTO_THRESHOLD)), None)
        if pn:
            out[ks] = (pin[pn], kp)
    return out


if __name__ == "__main__":
    con = sqlite3.connect(DB, timeout=60)
    tickers = sys.argv[1:] or [x for (x,) in con.execute(
        "SELECT source_event_id FROM reg_sources WHERE source='kalshi' AND match_confidence>=? AND equivalence IN ('EXACT','NEAR_EQUIVALENT')",
        (match.AUTO_THRESHOLD,))]
    allm = []
    for tk in tickers:
        for outcome, (pp, kp) in paths(con, tk).items():
            ms = lead_lag(pp, kp)
            allm += ms
            if sys.argv[1:]:
                print(f"{tk} {outcome}: {len(pp)} Pinnacle obs, {len(kp)} Kalshi obs")
                for m in ms:
                    print(f"   {m['at'].isoformat()[11:16]}Z Pinnacle {m['move'] * 100:+.1f}pp -> {m['result']}"
                          + (f" after {m['lag_min']:.0f} min" if m["lag_min"] is not None else "") + f" (move timing resolution {m['resolution_min']:.0f} min)")
    opp = [o for tk in tickers for o in opportunities(con, tk)]
    good = [o for o in opp if o["edge_after_fee"] > 0 and o["fillable"]]
    print(f"LATENCY TEST: {len(opp)} upward Pinnacle moves with a later Kalshi quote; {len(good)} left a fillable positive edge after fees"
          + (f" (median edge {statistics.median(o['edge_after_fee'] for o in good) * 100:.1f}pp, median delay {statistics.median(o['delay_min'] for o in good):.0f} min)" if good else ""))
    for o in sorted(good, key=lambda o: -o["edge_after_fee"])[:5]:
        print(f"   {o['outcome'][:22]:22} Pinnacle +{o['pin_move'] * 100:.1f}pp -> Kalshi ask {o['ask']:.2f} {o['delay_min']:.0f} min later, edge {o['edge_after_fee'] * 100:+.1f}pp, size {o['size']}")
    from collections import Counter
    print(f"{quality.VERSION.replace('QUALITY', 'MOVEMENT')} across {len(tickers)} events: {len(allm)} Pinnacle moves >= {MOVE * 100:.1f}pp")
    print("results:", dict(Counter(m["result"] for m in allm)))
    lags = [m["lag_min"] for m in allm if m["lag_min"] is not None]
    if lags:
        print(f"Kalshi follow lag: median {statistics.median(lags):.0f} min, n={len(lags)} (bounded below by collector cadence; NOT evidence of an exploitable delay)")
    print(f"median move-timing resolution: {statistics.median([m['resolution_min'] for m in allm]):.0f} min" if allm else "no moves observed yet: need more collection time")
