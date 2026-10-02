"""NBA_PROP (H9, pre-registered 9a5b9a3). Walk-forward player-prop distributions for PTS/REB/AST/3PM.
  python3 nba_model.py validate   choose C1 vs C2 on VALIDATE (Jan-Feb 2026) by PTS log loss -> writes the choice
  python3 nba_model.py testA      NBA_PROP_v0.1 vs B0 on TEST (Mar 2026 -> playoffs): log loss + calibration
The model object is also what NBAAdapter.predict_player_prop calls."""

import research_log
research_log.require_fresh("H9_NBA_PROPS", "espn_nba", "2025-10-21", "2026-06-14")  # audit fix: never run on a window another experiment used
import csv, json, math, random, statistics, sys, zlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
STATS = ("pts", "reb", "ast", "tpm")
TRAIN_END, VAL_END = "2026-01-01", "2026-03-01"
DRAWS, MIN_GAMES = 200, 5
CHOICE = ROOT / "results/h9_choice.json"


def nb_sf(k, mu, r):
    """P(X >= k) for NegBin(mean mu, dispersion r); r large -> Poisson."""
    if k <= 0:
        return 1.0
    if mu <= 0:
        return 0.0
    p = r / (r + mu)
    pmf = p ** r  # P(X=0)
    cdf = pmf
    for x in range(1, k):
        pmf *= (x - 1 + r) / x * (1 - p)
        cdf += pmf
    return max(0.0, 1.0 - cdf)


def load():
    games = {g["id"]: g for g in csv.DictReader(open(ROOT / "data/raw/nba_games_2025_26.csv"))}
    rows = [r for r in csv.DictReader(open(ROOT / "data/raw/nba_player_games_2025_26.csv"))]
    for r in rows:
        r["min"] = float(r["min"] or 0)
        for s in STATS:
            r[s] = int(r[s] or 0)
    rows.sort(key=lambda r: (r["date"], r["game"]))
    return rows, games


def implied(g, team):
    """Pregame implied team points from ESPN pickcenter (spread is the home line: negative = home favoured)."""
    try:
        tot, sp = float(g["total"]), float(g["spread"])
    except (TypeError, ValueError):
        return None
    home = tot / 2 - sp / 2
    return home if team == g["home"] else tot - home


class Walk:
    """Replays the season; predict() before update() for every player-game, so nothing leaks."""

    def __init__(self, league_rate):
        self.hist = defaultdict(list)       # player -> [(min, {stat})]
        self.team_pts = defaultdict(list)   # team -> [points scored]
        self.league_rate = league_rate

    def predict(self, r, g, variant):
        h = [x for x in self.hist[r["player_id"]] if x[0] > 0]
        if len(h) < MIN_GAMES:
            return None
        rec = h[::-1]
        w10 = [0.8 ** k for k in range(min(10, len(rec)))]
        proj = sum(w * x[0] for w, x in zip(w10, rec)) / sum(w10)
        sd = max(3.0, statistics.pstdev([x[0] for x in rec[:10]]))
        w20 = [0.9 ** k for k in range(min(20, len(rec)))]
        out = {"proj_min": proj, "sd_min": sd, "b0": {s: statistics.mean(x[1][s] for x in h) for s in STATS}}
        scale = 1.0
        if variant == "C2":
            imp, tp = implied(g, r["team"]), self.team_pts[r["team"]]
            if imp and tp:
                scale = imp / statistics.mean(tp)
        for s in STATS:
            rate = (sum(w * x[1][s] for w, x in zip(w20, rec)) + 100 * self.league_rate[s]) / (sum(w * x[0] for w, x in zip(w20, rec)) + 100)
            out[s] = rate * (scale if s == "pts" else 1.0)
        return out

    def update(self, r):
        self.hist[r["player_id"]].append((r["min"], {s: r[s] for s in STATS}))


def prob_ge(pred, stat, k, r_disp, seed):
    """Mixture over DRAWS truncated-normal minute draws (fixed seed): minutes uncertainty -> stat uncertainty."""
    rnd = random.Random(seed)
    tot = 0.0
    for _ in range(DRAWS):
        m = max(0.0, rnd.gauss(pred["proj_min"], pred["sd_min"]))
        tot += nb_sf(k, m * pred[stat], r_disp[stat])
    return tot / DRAWS


def dispersion(pairs):
    """Method of moments: var = mu + mu^2/r  ->  r = sum mu^2 / sum((y-mu)^2 - mu); clamp to [0.5, 1000]."""
    num = sum(mu * mu for mu, _ in pairs)
    den = sum((y - mu) ** 2 - mu for mu, y in pairs)
    return min(1000.0, max(0.5, num / den)) if den > 0 else 1000.0


def run(variant):
    """Returns (train-estimated params, list of per-player-game predictions with outcomes) for dates >= TRAIN_END."""
    rows, games = load()
    train = [r for r in rows if r["date"] < TRAIN_END]
    league_rate = {s: sum(r[s] for r in train) / sum(r["min"] for r in train) for s in STATS}
    w = Walk(league_rate)
    tr_pairs, tr_b0 = defaultdict(list), defaultdict(list)
    preds = []
    seen_games = set()
    for r in rows:
        g = games[r["game"]]
        p = w.predict(r, g, variant)
        if p:
            if r["date"] < TRAIN_END:
                for s in STATS:
                    # stat | minutes dispersion: use ACTUAL minutes (train only), so the minutes mixture isn't double-counted
                    tr_pairs[s].append((r["min"] * p[s], r[s]))
                    tr_b0[s].append((p["b0"][s], r[s]))
            if r["date"] >= TRAIN_END:
                preds.append((r, p))
        w.update(r)
        if r["game"] not in seen_games:  # team points after the game, for later C2 scaling
            seen_games.add(r["game"])
            for team, key in ((g["home"], "home_score"), (g["away"], "away_score")):
                if g.get(key):
                    w.team_pts[team].append(float(g[key]))
    disp = {s: dispersion(tr_pairs[s]) for s in STATS}
    disp_b0 = {s: dispersion(tr_b0[s]) for s in STATS}
    return dict(league_rate=league_rate, disp=disp, disp_b0=disp_b0), preds


def ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def score(params, preds, lo, hi, stats=STATS, with_b0=True):
    out = defaultdict(list)
    for r, p in preds:
        if not lo <= r["date"] < hi:
            continue
        for s in stats:
            mean = p["proj_min"] * p[s]
            k = int(math.floor(mean)) + 1  # line floor(mean)+0.5 -> P(X >= floor(mean)+1)
            seed = zlib.crc32(f"{r['player_id']}|{r['game']}|{s}".encode())  # stable across runs (hash() is salted)
            pm = prob_ge(p, s, k, params["disp"], seed)
            y = 1.0 if r[s] >= k else 0.0
            pb = nb_sf(k, p["b0"][s], params["disp_b0"][s]) if with_b0 else None
            out[s].append((pm, pb, y))
    return out


def validate():
    res = {}
    for v in ("C1", "C2"):
        params, preds = run(v)
        sc = score(params, preds, TRAIN_END, VAL_END, stats=("pts",), with_b0=False)["pts"]
        res[v] = sum(ll(pm, y) for pm, _, y in sc) / len(sc)
        print(f"VALIDATE {v}: PTS log loss {res[v]:.4f} (n={len(sc)}) | train dispersion {params['disp']}")
    choice = min(res, key=res.get)
    CHOICE.write_text(json.dumps(dict(choice=choice, validate_logloss=res, ts=datetime.now().isoformat(timespec="seconds"))))
    print(f"NBA_PROP_v0.1 = {choice} (recorded in {CHOICE.name} before TEST is opened)")


def testA():
    choice = json.loads(CHOICE.read_text())["choice"]
    params, preds = run(choice)
    sc = score(params, preds, VAL_END, "2026-12-31")
    rnd = random.Random(9)
    print(f"TEST A | NBA_PROP_v0.1 = {choice} | period {VAL_END} -> end of playoffs")
    for s in STATS:
        rows = sc[s]
        d = [ll(pm, y) - ll(pb, y) for pm, pb, y in rows]
        boots = sorted(sum(d[rnd.randrange(len(d))] for _ in d) / len(d) for _ in range(1000))
        print(f"  {s.upper():3} n={len(rows)} logloss model {sum(ll(pm, y) for pm, _, y in rows) / len(rows):.4f} | B0 "
              f"{sum(ll(pb, y) for _, pb, y in rows) / len(rows):.4f} | diff {sum(d) / len(d):+.4f} [{boots[25]:+.4f}, {boots[974]:+.4f}]")
        miss = 0
        for lo, hi in ((0, .3), (.3, .4), (.4, .5), (.5, .6), (.6, .7), (.7, 1)):
            b = [(pm, y) for pm, _, y in rows if lo <= pm < hi]
            if len(b) >= 100:
                pm_, y_ = statistics.mean(x[0] for x in b), statistics.mean(x[1] for x in b)
                ci = 1.96 * math.sqrt(pm_ * (1 - pm_) / len(b))
                miss += abs(y_ - pm_) > ci
                print(f"      {lo:.1f}-{hi:.1f} n={len(b):5d} pred {pm_:.3f} actual {y_:.3f} (±{ci:.3f}){' MISS' if abs(y_ - pm_) > ci else ''}")
        ok = boots[974] < 0 and miss == 0
        print(f"    -> {s.upper()}: {'OUTCOME-VALIDATED' if ok else 'not validated'}")


if __name__ == "__main__":
    {"validate": validate, "testA": testA}[sys.argv[1]]()
