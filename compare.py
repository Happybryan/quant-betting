"""Market-only price comparison for ONE registry-matched Kalshi event, any number of outcomes.
Pinnacle fair P per outcome (multiplicative / power / Shin, and the spread across them) vs Kalshi bid/ask and taker fee.
Refuses (PASS) if the match confidence is below threshold or the rules are not EXACT/NEAR_EQUIVALENT.
Usage: python3 compare.py KALSHI_EVENT_TICKER"""
import json, sqlite3, sys
from pathlib import Path
import match, registry
from odds import american_to_prob, devig_all, kalshi_fee

DB = Path(__file__).parent / "data" / "market.db"


def compare(ticker, contracts=100):
    con = sqlite3.connect(DB, timeout=60)
    row = con.execute("""SELECT k.event_id, k.match_confidence, k.match_reason, k.equivalence, k.eq_reasons, p.source_event_id, e.sport
                         FROM reg_sources k JOIN reg_sources p ON p.event_id=k.event_id AND p.source='pinnacle'
                         JOIN reg_events e ON e.event_id=k.event_id WHERE k.source='kalshi' AND k.source_event_id=?""", (ticker,)).fetchone()
    if not row:
        return print(f"PASS: {ticker} has no registry match to a Pinnacle event")
    eid, conf, why, eq, eqr, pid, sport = row
    print(f"{ticker} -> {eid} (Pinnacle {pid}, {sport}) | match confidence {conf} ({why}) | rules {eq}: {'; '.join(json.loads(eqr))}")
    if conf < match.AUTO_THRESHOLD or eq not in ("EXACT", "NEAR_EQUIVALENT"):
        return print("PASS: identity or settlement not verified")
    e = registry.get(f"{registry.KAL}/events/{ticker}?with_nested_markets=true")
    series = registry.get(f"{registry.KAL}/series/{e['event']['series_ticker']}")["series"]
    mult = series.get("fee_multiplier")
    mk = {m["yes_sub_title"]: m for m in (e.get("markets") or e["event"].get("markets") or [])}  # nested form puts them under event
    g = next(m for m in registry.get(f"{registry.PIN}/matchups/{pid}/related", registry.PIN_H) if str(m["id"]) == pid)
    names = {p["alignment"]: p["name"] for p in g["participants"] if p.get("alignment") in ("home", "away")}
    ml = next(k for k in registry.get(f"{registry.PIN}/matchups/{pid}/markets/straight", registry.PIN_H)
              if k["type"] == "moneyline" and k["period"] == 0 and not k.get("isAlternate"))
    prices = {p["designation"]: p["price"] for p in ml["prices"]}
    order = [d for d in ("home", "away", "draw") if d in prices]
    fair = dict(zip(order, devig_all([american_to_prob(prices[d]) for d in order])))
    print(f"Pinnacle overround {sum(american_to_prob(prices[d]) for d in order) - 1:.3f} | Kalshi fee multiplier {mult}")
    print(f"{'outcome':24} {'Pin':>6} {'mult':>6} {'power':>6} {'shin':>6} {'K bid':>6} {'K ask':>6} {'cost/c':>7} {'edge vs low fair':>17}")
    for d in order:
        label = names.get(d, "Tie")
        km = next((m for n, m in mk.items() if (d == "draw" and n.lower() in ("tie", "draw")) or
                   (d != "draw" and match.participant_score(n, label, sport) >= match.AUTO_THRESHOLD)), None)
        f = fair[d]
        if not km:
            print(f"{label[:24]:24} {prices[d]:+6.0f} {f['mult']:6.3f} {f['power']:6.3f} {f['shin']:6.3f}   no Kalshi contract matched -> PASS")
            continue
        ask = float(km["yes_ask_dollars"] or 0)
        cost = ask + kalshi_fee(ask, contracts, mult) / contracts if 0 < ask < 1 else None
        edge = (f["lo"] - cost) if cost else None
        print(f"{label[:24]:24} {prices[d]:+6.0f} {f['mult']:6.3f} {f['power']:6.3f} {f['shin']:6.3f} {float(km['yes_bid_dollars'] or 0):6.2f} {ask:6.2f} "
              f"{cost if cost else float('nan'):7.3f} {(edge * 100 if edge is not None else float('nan')):+15.1f}pp")
    print("edge vs low fair = most conservative devig method minus Kalshi all-in taker cost (100-lot). Market-only, UNVALIDATED.")


if __name__ == "__main__":
    for t in sys.argv[1:]:
        compare(t)
        print()
