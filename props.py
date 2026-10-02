"""PP_PROP_v0.1 - price PrizePicks legs against Pinnacle's latest player-prop line (no-vig).
Usage: python3 props.py "Jaylen Waddle|receiving yards|59.5" "Bo Nix|pass attempts|33.5" ...
Only an exact line match gets a probability. A different line gets a direction hint, never a number
(turning a line gap into a probability needs a distribution model we haven't validated)."""
import sqlite3, sys
from pathlib import Path
from odds import american_to_prob, devig_range

DB = Path(__file__).parent / "data" / "market.db"
BREAKEVEN_2PICK_POWER = 3 ** -0.5  # 57.7% per leg if a 2-pick Power pays 3x and legs are independent


def latest_props(con):
    rows = con.execute("""SELECT event_key, selection, price, json_extract(extra,'$.points'), ts FROM snapshots
        WHERE rowid IN (SELECT max(rowid) FROM snapshots WHERE source='pinnacle_prop' GROUP BY event_key, selection)""")
    out = {}
    for ev, sel, price, pts, ts in rows:
        desc, side = sel.rsplit("|", 1)
        out.setdefault((ev, desc), {})[side] = (price, pts, ts)
    return out


def price_leg(props, player, stat, line):
    words = (player + " " + stat).lower().split()
    hits = [(d, s) for (ev, d), s in props.items() if all(w in d.lower() for w in words) and len(s) == 2]
    if len(hits) != 1:
        return f"{player} {stat} {line}: {'no Pinnacle market' if not hits else 'ambiguous: ' + '; '.join(d for d, _ in hits[:3])}"
    desc, s = hits[0]
    (op, pts, ts), (up, upts, _) = s["Over"], s["Under"]
    if pts != upts:
        return f"{desc}: Pinnacle over/under lines differ, skipped"
    (po, lo_o, hi_o), (pu, lo_u, hi_u) = devig_range([american_to_prob(op), american_to_prob(up)])
    if float(line) == pts:
        best, p, lo = ("MORE", po, lo_o) if po > pu else ("LESS", pu, lo_u)
        tag = "CANDIDATE(paper)" if lo > BREAKEVEN_2PICK_POWER else "PASS"
        return (f"{desc} {line}: fair MORE {po:.3f} / LESS {pu:.3f} (Pinnacle {op:+.0f}/{up:+.0f}, line last moved {ts[11:16]}Z) -> "
                f"{best} {p:.3f} vs 2-pick breakeven {BREAKEVEN_2PICK_POWER:.3f}: {tag}")
    better = "MORE" if float(line) < pts else "LESS"
    return (f"{desc}: PrizePicks {line} vs Pinnacle {pts} (fair MORE {po:.3f} at {pts}). "
            f"{better} at {line} is better than Pinnacle's {better} at {pts} ({'> ' if (po if better=='MORE' else pu) > .5 else ''}"
            f"{(po if better=='MORE' else pu):.3f}+), size of the gain unpriced")


if __name__ == "__main__":
    props = latest_props(sqlite3.connect(DB, timeout=60))
    for leg in sys.argv[1:]:
        print(price_leg(props, *leg.split("|")))
