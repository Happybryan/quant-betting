"""Universal calibration engine: any (group, predicted p, outcome) rows -> per-group, per-bucket predicted vs actual with a
Wilson 95% CI, flagging buckets whose actual rate excludes the prediction. Never pools groups silently.
Sources wired in: MARKET_ONLY observations/entries (Pinnacle close vs Kalshi settlement), ledger legs. Usage: python3 calibration.py"""
import math, sqlite3
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).parent / "data" / "market.db"
BUCKETS = [(0, .2), (.2, .35), (.35, .5), (.5, .65), (.65, .8), (.8, 1.01)]


def wilson(k, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def table(rows, title, min_n=10):
    """rows: iterable of (group, p, y). Prints and returns the list of miscalibrated (group, bucket)."""
    g = defaultdict(list)
    for grp, p, y in rows:
        g[grp].append((p, y))
    bad = []
    print(f"\n{title}")
    for grp in sorted(g, key=str):
        xs = g[grp]
        print(f"  {grp}: n={len(xs)}  Brier {sum((p - y) ** 2 for p, y in xs) / len(xs):.4f}")
        for lo, hi in BUCKETS:
            b = [(p, y) for p, y in xs if lo <= p < hi]
            if len(b) < min_n:
                if b:
                    print(f"     {lo:.2f}-{min(hi, 1):.2f} n={len(b):4} (too few to judge)")
                continue
            k = sum(y for _, y in b)
            wl, wh = wilson(k, len(b))
            mp = sum(p for p, _ in b) / len(b)
            miss = not wl <= mp <= wh
            bad += [(grp, (lo, hi))] if miss else []
            print(f"     {lo:.2f}-{min(hi, 1):.2f} n={len(b):4} predicted {mp:.3f} actual {k / len(b):.3f} [{wl:.3f}, {wh:.3f}]{'  MISCALIBRATED' if miss else ''}")
    return bad


if __name__ == "__main__":
    con = sqlite3.connect(DB, timeout=60)
    mo = con.execute("""SELECT sport, close_fair, payout FROM mo_entries WHERE close_fair IS NOT NULL AND payout IS NOT NULL""").fetchall()
    table(((s, p, 1.0 if pay >= 1 else 0.0) for s, p, pay in mo), f"Pinnacle close (no-vig) vs Kalshi settlement, by sport ({len(mo)} settled)")
    legs = con.execute("SELECT league, model_p, result FROM bets WHERE stake=0 AND result IN ('win','loss')").fetchall()
    table(((f"{lg} (model P at entry)", p, 1.0 if r == "win" else 0.0) for lg, p, r in legs), f"Ledger legs ({len(legs)} settled)", min_n=1)
