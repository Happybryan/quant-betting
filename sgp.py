"""SGP_CORR_v1: same-game correlation for NFL player props (Gaussian copula on per-player standardized residuals).
Estimation data: nflverse REG 2024-2025 (parameter estimation, not a confirmatory test; registered as SGP_CORR_v1).
  python3 sgp.py fit      -> config/sgp_corr.json (rho, n, se by (stat, pos, stat, pos, same/opp team))
  joint(pA, pB, sideA, sideB, rho) -> P(A and B)"""
import csv, json, math, statistics, sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).parent
CFG = ROOT / "config" / "sgp_corr.json"
N = NormalDist()
COL = {"pass tds": "passing_tds", "pass yards": "passing_yards", "receptions": "receptions", "receiving yards": "receiving_yards",
       "rush yards": "rushing_yards", "pass attempts": "attempts", "pass completions": "completions", "rush attempts": "carries",
       "interceptions": "passing_interceptions"}
POS = {"QB", "RB", "WR", "TE"}
MIN_N = 300


def key(sa, pa, sb, pb, rel):
    a, b = (sa, pa), (sb, pb)
    return "|".join((*min(a, b), *max(a, b), rel))


def fit():
    rows = []
    for y in (2024, 2025):
        rows += [r for r in csv.DictReader(open(ROOT / f"data/raw/nfl_player_week_{y}.csv")) if r["season_type"] == "REG" and r["position"] in POS]
    by_ps = defaultdict(list)
    for r in rows:
        by_ps[(r["player_id"], r["season"])].append(r)
    z = {}  # (player, season, week) -> {stat: z}
    for (pid, s), g in by_ps.items():
        if len(g) < 6:
            continue
        for stat, col in COL.items():
            xs = [float(r[col] or 0) for r in g]
            sd = statistics.pstdev(xs)
            if sd > 0:
                m = statistics.mean(xs)
                for r in g:
                    z.setdefault((pid, s, r["week"]), {})[stat] = (float(r[col] or 0) - m) / sd
    games = defaultdict(list)
    for r in rows:
        if (r["player_id"], r["season"], r["week"]) in z:
            games[r["game_id"]].append(r)
    acc = defaultdict(list)
    for g in games.values():
        for a, b in combinations(g, 2):
            rel = "same" if a["team"] == b["team"] else "opp"
            za, zb = z[(a["player_id"], a["season"], a["week"])], z[(b["player_id"], b["season"], b["week"])]
            for sa, va in za.items():
                for sb, vb in zb.items():
                    acc[key(sa, a["position"], sb, b["position"], rel)].append(va * vb)
    out = {k: dict(rho=max(-0.95, min(0.95, statistics.mean(v))), n=len(v), se=1 / math.sqrt(len(v))) for k, v in acc.items() if len(v) >= MIN_N}
    CFG.write_text(json.dumps(dict(version="SGP_CORR_v1", data="nflverse REG 2024-2025", min_n=MIN_N, pairs=out), indent=0))
    print(f"SGP_CORR_v1: {len(out)} (stat,pos,stat,pos,relation) cells with n >= {MIN_N}")


def lookup(sa, pa, sb, pb, same_team):
    cfg = json.loads(CFG.read_text())["pairs"]
    return cfg.get(key(sa, pa, sb, pb, "same" if same_team else "opp"))


def bvn(a, b, rho, steps=400):
    """P(X <= a, Y <= b) for standard bivariate normal with correlation rho (Simpson integration over x)."""
    if abs(rho) < 1e-9:
        return N.cdf(a) * N.cdf(b)
    lo, hi = -8.0, a
    h = (hi - lo) / steps
    s = 0.0
    r = math.sqrt(1 - rho * rho)
    for i in range(steps + 1):
        x = lo + i * h
        w = 1 if i in (0, steps) else (4 if i % 2 else 2)
        s += w * N.pdf(x) * N.cdf((b - rho * x) / r)
    return s * h / 3


def joint(pA, pB, sideA, sideB, rho):
    """P(A and B) where A = our side of leg A (prob pA). Latent z: MORE wins in the upper tail, LESS in the lower tail,
    so the effective correlation between the two WIN events flips sign when exactly one leg is LESS."""
    eff = rho * (1 if sideA == sideB else -1)
    return bvn(N.inv_cdf(pA), N.inv_cdf(pB), eff)


if __name__ == "__main__":
    if sys.argv[1:] == ["fit"]:
        fit()
    assert abs(joint(0.6, 0.6, "MORE", "MORE", 0.0) - 0.36) < 1e-3
    assert joint(0.6, 0.6, "MORE", "MORE", 0.5) > 0.36 > joint(0.6, 0.6, "MORE", "LESS", 0.5)
    print("sgp ok")
