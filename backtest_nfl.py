"""NFL moneyline experiments on nflverse closing lines (data/raw/nfl_games.csv).
E1  Is the devigged closing moneyline calibrated? (favourite-longshot bias test)
E2  NFL_ELO_v1 baseline vs the closing market.
E3  NFL_BLEND_v1 challenger: logistic blend of market + Elo. Does it beat the market out of sample,
    and does its estimated edge buy anything at the closing price (with vig)?
Splits are fixed in advance: train 2006-2015, validate 2016-2020, test 2021-2025 (touched once).
Usage: python3 backtest_nfl.py > results/nfl_backtest.txt"""

import research_log
research_log.require_fresh("E2_NFL_ELO", "nflverse_games", "2006", "2025")  # audit fix: never run on a window another experiment used
import csv, math, random
from pathlib import Path
from odds import american_to_prob, devig_mult

ROOT = Path(__file__).parent
TRAIN, VAL, TEST = range(2006, 2016), range(2016, 2021), range(2021, 2026)
lg = lambda p: math.log(p / (1 - p))
sig = lambda z: 1 / (1 + math.exp(-z))


def load():
    rows = [r for r in csv.DictReader(open(ROOT / "data/raw/nfl_games.csv")) if r["home_score"]]
    rows.sort(key=lambda r: (r["gameday"], r["gametime"]))
    for r in rows:
        hs, as_ = int(r["home_score"]), int(r["away_score"])
        r["y"] = 1.0 if hs > as_ else 0.0 if hs < as_ else 0.5
        r["mov"] = hs - as_
        r["season"] = int(r["season"])
        r["neutral"] = r["location"] == "Neutral"
        if r["home_moneyline"] and r["away_moneyline"]:
            hml, aml = float(r["home_moneyline"]), float(r["away_moneyline"])
            r["hml"], r["aml"] = hml, aml
            r["p_mkt"] = devig_mult([american_to_prob(hml), american_to_prob(aml)])[0]
    return rows


def run_elo(rows, k, hfa, revert=1 / 3):
    """FiveThirtyEight-style NFL Elo. p_elo is written BEFORE the game updates ratings (no leakage)."""
    elo, season = {}, None
    for r in rows:
        if r["season"] != season:
            season = r["season"]
            elo = {t: 1505 + (1 - revert) * (e - 1505) for t, e in elo.items()}
        h, a = elo.setdefault(r["home_team"], 1505), elo.setdefault(r["away_team"], 1505)
        diff = h - a + (0 if r["neutral"] else hfa)
        p = 1 / (1 + 10 ** (-diff / 400))
        r["p_elo"] = p
        mult = math.log(abs(r["mov"]) + 1) * 2.2 / ((diff if r["y"] == 1 else -diff) * 0.001 + 2.2)
        shift = k * mult * (r["y"] - p)
        elo[r["home_team"]], elo[r["away_team"]] = h + shift, a - shift
    return rows


def logloss(ps, ys):
    return -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(ps, ys)) / len(ys)


def brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)


def inv(m):
    n = len(m)
    a = [row[:] + [float(i == j) for j in range(n)] for i, row in enumerate(m)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(a[r][c]))
        a[c], a[piv] = a[piv], a[c]
        d = a[c][c]
        a[c] = [v / d for v in a[c]]
        for r in range(n):
            if r != c:
                f = a[r][c]
                a[r] = [v - f * w for v, w in zip(a[r], a[c])]
    return [row[n:] for row in a]


def logit_fit(X, y, iters=30):
    """Newton-Raphson logistic regression. Returns (beta, standard errors)."""
    k = len(X[0])
    b = [0.0] * k
    for _ in range(iters):
        g, H = [0.0] * k, [[0.0] * k for _ in range(k)]
        for x, t in zip(X, y):
            p = sig(sum(bi * xi for bi, xi in zip(b, x)))
            w = p * (1 - p)
            for i in range(k):
                g[i] += (t - p) * x[i]
                for j in range(k):
                    H[i][j] += w * x[i] * x[j]
        Hi = inv(H)
        b = [bi + sum(Hi[i][j] * g[j] for j in range(k)) for i, bi in enumerate(b)]
    return b, [math.sqrt(Hi[i][i]) for i in range(k)]


def calib_table(ps, ys, title):
    print(f"\n{title}\n  bucket     n   predicted  actual   diff")
    edges = [0, .2, .3, .4, .5, .55, .6, .65, .7, .75, .8, 1.01]
    for lo, hi in zip(edges, edges[1:]):
        b = [(p, y) for p, y in zip(ps, ys) if lo <= p < hi]
        if len(b) >= 20:
            pm, ym = sum(p for p, _ in b) / len(b), sum(y for _, y in b) / len(b)
            se = math.sqrt(pm * (1 - pm) / len(b))
            print(f"  {lo:.2f}-{min(hi,1):.2f} {len(b):5d}   {pm:.3f}    {ym:.3f}  {ym-pm:+.3f} (±{1.96*se:.3f})")


def paired_boot(pa, pb, ys, n=2000, seed=7):
    """95% CI of logloss(A) - logloss(B) by resampling games. Negative = A better."""
    rnd = random.Random(seed)
    la = [-(y * math.log(p) + (1 - y) * math.log(1 - p)) for p, y in zip(pa, ys)]
    lb = [-(y * math.log(p) + (1 - y) * math.log(1 - p)) for p, y in zip(pb, ys)]
    d = [a - b for a, b in zip(la, lb)]
    stats = sorted(sum(d[rnd.randrange(len(d))] for _ in d) / len(d) for _ in range(n))
    return sum(d) / len(d), stats[int(.025 * n)], stats[int(.975 * n)]


def bet_sim(games, pkey, title):
    """Bet 1 unit on any side whose model EV at the actual closing price (vig in) clears 0. Ties refund."""
    print(f"\n{title}\n  est. edge    n  win%   exp.win%   ROI      units")
    buckets = [(0, .02), (.02, .04), (.04, .06), (.06, .08), (.08, 1)]
    tot = []
    for lo, hi in buckets:
        res = []
        for g in games:
            for p, price, won in ((g[pkey], g["hml"], g["y"]), (1 - g[pkey], g["aml"], 1 - g["y"])):
                edge = p - american_to_prob(price)
                if lo <= edge < hi and won != 0.5:
                    pay = price / 100 if price > 0 else 100 / -price
                    res.append((won, p, pay * won - (1 - won)))
        tot += res
        if res:
            n = len(res)
            roi = sum(r[2] for r in res) / n
            se = math.sqrt(sum((r[2] - roi) ** 2 for r in res) / (n - 1) / n) if n > 1 else 0
            print(f"  {lo*100:3.0f}-{min(hi,1)*100:3.0f}% {n:5d} {sum(r[0] for r in res)/n*100:5.1f}   {sum(r[1] for r in res)/n*100:5.1f}   "
                  f"{roi*100:+6.1f}% ±{1.96*se*100:4.1f}  {sum(r[2] for r in res):+7.1f}")
    if tot:
        print(f"  ALL      {len(tot):5d}   ROI {sum(r[2] for r in tot)/len(tot)*100:+.1f}%   units {sum(r[2] for r in tot):+.1f}")


def main():
    rows = load()
    priced = lambda seasons: [r for r in rows if r["season"] in seasons and "p_mkt" in r]
    print("NFL moneyline backtest | data: nflverse games.csv closing moneylines | ties count as y=0.5 in scoring")

    # E2: tune Elo on TRAIN only.
    best, g = None, priced(TRAIN)
    for k in (15, 20, 25, 30):
        for hfa in (20, 30, 40, 48, 55, 65):
            run_elo(rows, k, hfa)
            ll = logloss([r["p_elo"] for r in g], [r["y"] for r in g])
            if best is None or ll < best[0]:
                best = (ll, k, hfa)
    _, K, HFA = best
    run_elo(rows, K, HFA)
    print(f"\nNFL_ELO_v1 tuned on train: K={K} HFA={HFA} (grid K 15-30, HFA 20-65, season revert 1/3)")

    tr, va, te = priced(TRAIN), priced(VAL), priced(TEST)
    print(f"games with closing ML: train {len(tr)}  validate {len(va)}  test {len(te)}")
    print("\n                 logloss   brier")
    for name, g in (("train", tr), ("validate", va), ("TEST", te)):
        ys = [r["y"] for r in g]
        for m in ("p_mkt", "p_elo"):
            print(f"  {name:8} {m:6}  {logloss([r[m] for r in g], ys):.4f}   {brier([r[m] for r in g], ys):.4f}")

    # E1: market calibration. Fit on train+val; the test table is the honest read.
    X = [[1, lg(r["p_mkt"])] for r in tr + va]
    b, se = logit_fit(X, [r["y"] for r in tr + va])
    print(f"\nE1 calibration fit on 2006-2020: y ~ {b[0]:+.3f} (±{1.96*se[0]:.3f}) + {b[1]:.3f} (±{1.96*se[1]:.3f}) * logit(p_mkt)")
    print("   slope > 1 would mean favourites are underpriced (favourite-longshot bias); intercept > 0 = home underpriced")
    calib_table([r["p_mkt"] for r in te], [r["y"] for r in te], "E1 market calibration, TEST 2021-2025 (home side)")

    # E3: blend challenger fit on train+val, judged on test.
    X = [[1, lg(r["p_mkt"]), lg(r["p_elo"])] for r in tr + va]
    b, se = logit_fit(X, [r["y"] for r in tr + va])
    print(f"\nNFL_BLEND_v1 = sig({b[0]:+.3f} + {b[1]:.3f}*logit(p_mkt) + {b[2]:+.3f}*logit(p_elo))  "
          f"elo coef 95% CI {b[2]-1.96*se[2]:+.3f}..{b[2]+1.96*se[2]:+.3f}")
    for r in rows:
        if "p_mkt" in r:
            r["p_blend"] = sig(b[0] + b[1] * lg(r["p_mkt"]) + b[2] * lg(r["p_elo"]))
    ys = [r["y"] for r in te]
    for m in ("p_elo", "p_blend"):
        d, lo, hi = paired_boot([r[m] for r in te], [r["p_mkt"] for r in te], ys)
        print(f"  TEST logloss({m}) - logloss(market) = {d:+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]")

    bet_sim(te, "p_elo", "E2 betting sim, TEST: NFL_ELO_v1 vs closing price (vig included)")
    bet_sim(te, "p_blend", "E3 betting sim, TEST: NFL_BLEND_v1 vs closing price (vig included)")
    bet_sim(va, "p_elo", "E2 betting sim, VALIDATE 2016-2020: NFL_ELO_v1 (for time-stability check)")


if __name__ == "__main__":
    assert abs(logloss([.5, .5], [1, 0]) - math.log(2)) < 1e-12
    b, _ = logit_fit([[1, x] for x in (-2, -1, 0, 1, 2) * 40], [0, 0, 1, 0, 1] * 20 + [0, 1, 1, 1, 1] * 20)
    assert b[1] > 0
    main()
