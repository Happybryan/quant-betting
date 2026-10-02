"""Statistical validity audit, part 1: effective sample sizes and game-clustered uncertainty for H4/H4b, H8, H9-A.
Clustering: resample whole games (or events) with replacement; every prop from a resampled game comes along.
Usage: python3 audit_stats.py > results/audit_stats.txt"""
import json, math, random, re, statistics
from collections import defaultdict
from pathlib import Path
import kalshi_history as kh, h8_passtd_market as h8, nba_model as nm

B = 1000


def cluster_ci(values, clusters, seed=1):
    by = defaultdict(list)
    for v, c in zip(values, clusters):
        by[c].append(v)
    keys, rnd, stats = list(by), random.Random(seed), []
    for _ in range(B):
        s = [by[keys[rnd.randrange(len(keys))]] for _ in keys]
        stats.append(sum(map(sum, s)) / sum(map(len, s)))
    stats.sort()
    return statistics.mean(values), stats[int(.025 * B)], stats[int(.975 * B)]


def naive_ci(values, seed=1):
    rnd = random.Random(seed)
    st = sorted(sum(values[rnd.randrange(len(values))] for _ in values) / len(values) for _ in range(B))
    return st[int(.025 * B)], st[int(.975 * B)]


def h4():
    for study in ("H4", "H4b"):
        rows = [r for r in kh.load() if r["study"] == study]
        print(f"{study}: contract-sides {len(rows)} | events {len({r['event'] for r in rows})} | dates {len({r['date'][:10] for r in rows})} "
              f"| series {len({r['series'] for r in rows})}  (CIs in the published result were already event-clustered)")


def h8a():
    rows, _ = h8.load()
    gid = lambda r: r["t"]  # kickoff time identifies the game slot; props in the same game share it
    prim = [r for r in rows if r["rung"] == 2 and 0 < r["mid"] < 1]
    print(f"\nH8: prop markets {len(rows)} | rung-2 {len(prim)} | distinct kickoff slots {len({gid(r) for r in rows})} | "
          f"rungs per player-game avg {len(rows) / max(1, len({(r['t'], r['M1']) for r in rows})):.1f}")
    ll = lambda p, y: -(y * math.log(min(max(p, 1e-6), 1 - 1e-6)) + (1 - y) * math.log(1 - min(max(p, 1e-6), 1 - 1e-6)))
    lg = lambda q: math.log(min(max(q, 1e-4), 1 - 1e-4) / (1 - min(max(q, 1e-4), 1 - 1e-4)))
    sig = lambda z: 1 / (1 + math.exp(-z))
    for m in ("M1", "M2"):
        d = [ll(sig(0.75 * lg(r["mid"]) + 0.25 * lg(r[m])), r["y"]) - ll(r["mid"], r["y"]) for r in prim]
        mean, lo, hi = cluster_ci(d, [gid(r) for r in prim])
        nlo, nhi = naive_ci(d)
        print(f"  {m} blend-vs-market log loss diff {mean:+.4f} | naive CI [{nlo:+.4f}, {nhi:+.4f}] | game-clustered CI [{lo:+.4f}, {hi:+.4f}]")
        bets, cl = [], []
        for r in rows:
            f = lambda q: 0.07 * q * (1 - q)
            if r[m] - r["ask"] - f(r["ask"]) >= 0.03:
                c = r["ask"] + f(r["ask"]); bets.append((r["y"] - c) / c); cl.append(gid(r))
            elif (1 - r[m]) - (1 - r["bid"]) - f(1 - r["bid"]) >= 0.03:
                c = 1 - r["bid"] + f(1 - r["bid"]); bets.append((1 - r["y"] - c) / c); cl.append(gid(r))
        mean, lo, hi = cluster_ci(bets, cl)
        print(f"  {m} betting ROI {mean * 100:+.1f}% | bets {len(bets)} in {len(set(cl))} game slots | game-clustered CI [{lo * 100:+.1f}, {hi * 100:+.1f}]")


def h9a():
    choice = json.loads(nm.CHOICE.read_text())["choice"]
    params, preds = nm.run(choice)
    test = [(r, p) for r, p in preds if r["date"] >= nm.VAL_END]
    print(f"\nH9 Test A: player-games {len(test)} | games {len({r['game'] for r, _ in test})} | players {len({r['player_id'] for r, _ in test})} "
          f"| dates {len({r['date'][:10] for r, _ in test})} | prop observations {4 * len(test)} (4 stats per player-game)")
    for s in nm.STATS:
        d, cl = [], []
        for r, p in test:
            k = int(math.floor(p["proj_min"] * p[s])) + 1
            pm = nm.prob_ge(p, s, k, params["disp"], nm.zlib.crc32(f"{r['player_id']}|{r['game']}|{s}".encode()))
            pb = nm.nb_sf(k, p["b0"][s], params["disp_b0"][s])
            y = 1.0 if r[s] >= k else 0.0
            d.append(nm.ll(pm, y) - nm.ll(pb, y)); cl.append(r["game"])
        mean, lo, hi = cluster_ci(d, cl)
        nlo, nhi = naive_ci(d)
        print(f"  {s.upper():3} model-vs-B0 log loss diff {mean:+.4f} | naive CI [{nlo:+.4f}, {nhi:+.4f}] | game-clustered CI [{lo:+.4f}, {hi:+.4f}]"
              f" | design effect ~{((hi - lo) / max(nhi - nlo, 1e-9)) ** 2:.1f}")


if __name__ == "__main__":
    h4(); h8a(); h9a()
