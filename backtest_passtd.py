"""H7 (EXPERIMENTS.md): walk-forward validation of the NFL pass-TD gate models. Parameters are fixed in advance.
Every prediction for game G uses only data from before G (prior games, prior season league rates) plus G's closing
spread/total (available before kickoff). Usage: python3 backtest_passtd.py > results/h7_passtd.txt"""

import research_log
research_log.require_fresh("H7_PASSTD_VS_OUTCOMES", "nflverse_weekly", "2025", "2026-wk2")  # audit fix: never run on a window another experiment used
import csv, math, random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
K, N_HIST, LINE = 6, 17, 1.5


def pois_ge2(lam):
    return 1 - math.exp(-lam) * (1 + lam)


def load():
    rows = []
    for y in (2024, 2025, 2026):
        rows += [r for r in csv.DictReader(open(ROOT / f"data/raw/nfl_player_week_{y}.csv")) if r["season_type"] == "REG"]
    imp = {}
    for g in csv.DictReader(open(ROOT / "data/raw/nfl_games.csv")):
        if g["total_line"] and g["spread_line"] and g["game_type"] == "REG":
            t, sp = float(g["total_line"]), float(g["spread_line"])
            imp[(g["season"], g["week"], g["home_team"])] = t / 2 + sp / 2
            imp[(g["season"], g["week"], g["away_team"])] = t / 2 - sp / 2
    pts = defaultdict(int)
    for g in csv.DictReader(open(ROOT / "data/raw/nfl_games.csv")):
        if g["game_type"] == "REG" and g["home_score"]:
            pts[g["season"]] += int(g["home_score"]) + int(g["away_score"])
    return rows, imp, pts


def main():
    rows, imp, pts = load()
    qual = [r for r in rows if float(r["attempts"] or 0) >= 15]
    for r in qual:
        r["td"] = float(r["passing_tds"] or 0)
        r["key"] = (int(r["season"]), int(r["week"]))
        r["imp"] = imp.get((r["season"], r["week"], r["team"]))
    league = {}  # season -> (mean TDs per qualifying game, pass TDs per point)
    for s in ("2024", "2025"):
        g = [r for r in qual if r["season"] == s]
        league[int(s) + 1] = (sum(r["td"] for r in g) / len(g), sum(float(r["passing_tds"] or 0) for r in rows if r["season"] == s) / pts[s])
    hist = defaultdict(list)
    preds = []
    for r in sorted(qual, key=lambda r: r["key"]):
        s = r["key"][0]
        if s in league and r["imp"]:
            mean_prior, ratio_prior = league[s]
            h = [x for x in hist[r["player_id"]] if x["imp"]][-N_HIST:]
            p0 = pois_ge2(mean_prior)
            p1 = pois_ge2(r["imp"] * ratio_prior)
            prior_rate = league_rate_per_implied[s]
            avg_pts = (sum(x["imp"] for x in h) / len(h)) if h else r["imp"]
            rate = (sum(x["td"] for x in h) + K * avg_pts * prior_rate) / (sum(x["imp"] for x in h) + K * avg_pts)
            p2 = pois_ge2(rate * r["imp"])
            preds.append(dict(y=1.0 if r["td"] >= 2 else 0.0, M0=p0, M1=p1, M2=p2, season=s, n_hist=len(h)))
        hist[r["player_id"]].append(r)
    report(preds)


def lambdas():
    """(normalized name, season, week) -> team + lambdas for M0/M1/M2, for EVERY QB game with >=1 attempt.
    History used for M2 = prior qualifying games (>=15 att) only; the target game itself is never filtered on its own stats."""
    import match
    rows, imp, pts = load()
    rate = {}
    for s in ("2024", "2025"):
        g = [r for r in rows if r["season"] == s and float(r["attempts"] or 0) >= 15 and imp.get((r["season"], r["week"], r["team"]))]
        rate[int(s) + 1] = (sum(float(r["passing_tds"] or 0) for r in g) / len(g),
                            sum(float(r["passing_tds"] or 0) for r in rows if r["season"] == s) / pts[s],
                            sum(float(r["passing_tds"] or 0) for r in g) / sum(imp[(r["season"], r["week"], r["team"])] for r in g))
    hist, out = defaultdict(list), {}
    for r in sorted((r for r in rows if float(r["attempts"] or 0) >= 1), key=lambda r: (int(r["season"]), int(r["week"]))):
        s, i = int(r["season"]), imp.get((r["season"], r["week"], r["team"]))
        if s in rate and i:
            mean_p, ratio_p, per_imp = rate[s]
            h = hist[r["player_id"]][-N_HIST:]
            avg = sum(x[1] for x in h) / len(h) if h else i
            m2 = (sum(x[0] for x in h) + K * avg * per_imp) / (sum(x[1] for x in h) + K * avg)
            out[(match.core(r["player_display_name"]), r["season"], r["week"])] = dict(team=r["team"], lam0=mean_p, lam1=i * ratio_p, lam2=m2 * i)
        if float(r["attempts"] or 0) >= 15 and i:
            hist[r["player_id"]].append((float(r["passing_tds"] or 0), i))
    return out


def ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def report(preds):
    ys = [p["y"] for p in preds]
    print(f"H7 pass-TD validation | test games {len(preds)} (2025 REG + 2026 REG, QB >= 15 att) | base rate TDs>=2: {sum(ys) / len(ys):.3f}")
    print(f"{'model':6} {'logloss':>8} {'brier':>7}   vs M0 logloss diff [95% CI]")
    rnd = random.Random(7)
    for m in ("M0", "M1", "M2"):
        l = [ll(p[m], p["y"]) for p in preds]
        b = sum((p[m] - p["y"]) ** 2 for p in preds) / len(preds)
        d = [ll(p[m], p["y"]) - ll(p["M0"], p["y"]) for p in preds]
        boots = sorted(sum(d[rnd.randrange(len(d))] for _ in d) / len(d) for _ in range(2000))
        print(f"{m:6} {sum(l) / len(l):8.4f} {b:7.4f}   {sum(d) / len(d):+.4f} [{boots[50]:+.4f}, {boots[1949]:+.4f}]" if m != "M0" else
              f"{m:6} {sum(l) / len(l):8.4f} {b:7.4f}   (baseline)")
    for m in ("M1", "M2"):
        print(f"\ncalibration {m}: bucket  n   predicted  actual   (95% CI half-width)")
        worst = 0
        for lo, hi in ((0, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .6), (.6, 1)):
            b = [p for p in preds if lo <= p[m] < hi]
            if b:
                pm, ym = sum(p[m] for p in b) / len(b), sum(p["y"] for p in b) / len(b)
                ci = 1.96 * math.sqrt(pm * (1 - pm) / len(b))
                flag = " MISS" if len(b) >= 50 and abs(ym - pm) > ci else ""
                worst += bool(flag)
                print(f"   {lo:.1f}-{hi:.1f} {len(b):5d}   {pm:.3f}     {ym:.3f}   (±{ci:.3f}){flag}")
        print(f"   buckets (n>=50) outside CI: {worst}")


league_rate_per_implied = {}
if __name__ == "__main__":
    rows, imp, pts = load()
    qual = [r for r in rows if float(r["attempts"] or 0) >= 15]
    for s in ("2024", "2025"):
        g = [r for r in qual if r["season"] == s and imp.get((r["season"], r["week"], r["team"]))]
        league_rate_per_implied[int(s) + 1] = sum(float(r["passing_tds"] or 0) for r in g) / sum(imp[(r["season"], r["week"], r["team"])] for r in g)
    main()
