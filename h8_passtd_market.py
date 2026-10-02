"""H8 (EXPERIMENTS.md, pre-registered f129183): NFL pass-TD models vs Kalshi KXNFLPASSTDS prices.
  python3 h8_passtd_market.py fetch      markets + pre-kickoff hourly candles (cached in data/raw/kalshi_passtds/)
  python3 h8_passtd_market.py analyze    primary (rung 2+) log-loss test + betting test, exactly as registered"""

import research_log
research_log.require_fresh("H8_PASSTD_VS_MARKET", "kalshi_passtds", "2025-12", "2026-09-27")  # audit fix: never run on a window another experiment used
import csv, json, math, random, re, sys, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
import backtest_passtd as bt, match
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
RAW = ROOT / "data/raw/kalshi_passtds"
K = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXNFLPASSTDS"
ALIAS = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "LVR": "LV"}


def get(path):
    for i in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(K + path, headers={"User-Agent": "curl/8.1.2"}), timeout=30))
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == 4:
                raise
            time.sleep(2 ** i + 1)


def games():
    out = {}
    for g in csv.DictReader(open(ROOT / "data/raw/nfl_games.csv")):
        if g["gametime"]:
            kick = datetime.strptime(f"{g['gameday']} {g['gametime']}", "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("America/New_York"))
            out.setdefault(g["gameday"], []).append(dict(season=g["season"], week=g["week"], home=g["home_team"], away=g["away_team"],
                                                        kick=kick.astimezone(timezone.utc)))
    return out


def find_game(event_ticker, gm):
    m = re.search(r"-(\d{2}[A-Z]{3}\d{2})([A-Z]+)$", event_ticker)
    day = datetime.strptime(m.group(1), "%y%b%d").strftime("%Y-%m-%d")
    code = m.group(2)
    for g in gm.get(day, []):
        variants = {g["away"] + g["home"], g["home"] + g["away"]}
        variants |= {v.replace(a, b) for v in list(variants) for b, a in ALIAS.items()}
        if code in variants:
            return g
    return None


def fetch():
    RAW.mkdir(parents=True, exist_ok=True)
    ms = {}
    for base in (f"/historical/markets?series_ticker={SERIES}", f"/markets?series_ticker={SERIES}&status=settled"):
        c = ""
        while True:
            d = get(f"{base}&limit=1000&cursor={c}")
            for m in d.get("markets", []):
                ms[m["ticker"]] = m | {"_hist": base.startswith("/historical")}
            c = d.get("cursor")
            if not c or not d.get("markets"):
                break
    gm = games()
    n = 0
    for t, m in ms.items():
        f = RAW / f"{t}.json"
        if f.exists():
            continue
        g = find_game(m["event_ticker"], gm)
        rec = dict(market={k: m.get(k) for k in ("ticker", "event_ticker", "yes_sub_title", "floor_strike", "result")}, game=None, candles=None)
        if g:
            rec["game"] = {k: (v.isoformat() if k == "kick" else v) for k, v in g.items()}
            lo, hi = int((g["kick"] - timedelta(hours=25)).timestamp()), int((g["kick"] - timedelta(hours=1)).timestamp())
            path = (f"/historical/markets/{t}/candlesticks" if m["_hist"] else f"/series/{SERIES}/markets/{t}/candlesticks")
            try:
                rec["candles"] = get(f"{path}?start_ts={lo}&end_ts={hi}&period_interval=60").get("candlesticks", [])
            except urllib.error.HTTPError as e:
                rec["candles"] = {"error": e.code}
            time.sleep(0.25)
        f.write_text(json.dumps(rec))
        n += 1
    print(f"markets {len(ms)}, newly cached {n}")


def load():
    lam = bt.lambdas()
    rows, skipped = [], {"no game": 0, "no candle": 0, "no player": 0, "not yes/no": 0}
    for f in sorted(RAW.glob("*.json")):
        d = json.loads(f.read_text())
        m, g, c = d["market"], d["game"], d["candles"]
        if m["result"] not in ("yes", "no"):
            skipped["not yes/no"] += 1
            continue
        if not g:
            skipped["no game"] += 1
            continue
        cl = lambda x, side: (x.get(side) or {}).get("close_dollars") or (x.get(side) or {}).get("close")
        cs = [x for x in (c if isinstance(c, list) else []) if cl(x, "yes_bid") and cl(x, "yes_ask")]
        if not cs:
            skipped["no candle"] += 1
            continue
        bid, ask = float(cl(cs[-1], "yes_bid")), float(cl(cs[-1], "yes_ask"))
        name, rung = m["yes_sub_title"].split(":")[0], int(round(float(m["floor_strike"]) + 0.5))
        p = lam.get((match.core(name), g["season"], g["week"]))
        if not p or p["team"] not in (g["home"], g["away"]):
            skipped["no player"] += 1
            continue
        ge = lambda l: 1 - sum(math.exp(-l) * l ** k / math.factorial(k) for k in range(rung))
        rows.append(dict(t=g["kick"], rung=rung, y=1.0 if m["result"] == "yes" else 0.0, bid=bid, ask=ask, mid=(bid + ask) / 2,
                         M1=ge(p["lam1"]), M2=ge(p["lam2"]), M0=ge(p["lam0"])))
    return sorted(rows, key=lambda r: r["t"]), skipped


lg = lambda p: math.log(min(max(p, 1e-4), 1 - 1e-4) / (1 - min(max(p, 1e-4), 1 - 1e-4)))
sig = lambda z: 1 / (1 + math.exp(-z))
fee = lambda p: 0.07 * p * (1 - p)  # KXNFLPASSTDS fee multiplier checked at analysis time (see output)


def analyze():
    rows, skipped = load()
    mult = get(f"/series/{SERIES}")["series"].get("fee_multiplier")
    print(f"H8 | usable markets {len(rows)} | excluded {skipped} | fee multiplier {mult}")
    prim = [r for r in rows if r["rung"] == 2 and 0 < r["mid"] < 1]
    ll = lambda p, y: -(y * math.log(min(max(p, 1e-6), 1 - 1e-6)) + (1 - y) * math.log(1 - min(max(p, 1e-6), 1 - 1e-6)))
    print(f"\nPRIMARY (rung 2+, n={len(prim)}): log loss")
    rnd = random.Random(8)
    base = [ll(r["mid"], r["y"]) for r in prim]
    print(f"  market mid        {sum(base) / len(base):.4f}")
    verdict = {}
    for m in ("M1", "M2"):
        bl = [ll(sig(0.75 * lg(r["mid"]) + 0.25 * lg(r[m])), r["y"]) for r in prim]
        raw = [ll(r[m], r["y"]) for r in prim]
        d = [a - b for a, b in zip(bl, base)]
        boots = sorted(sum(d[rnd.randrange(len(d))] for _ in d) / len(d) for _ in range(2000))
        verdict[m] = boots[1949] < 0
        print(f"  {m} alone         {sum(raw) / len(raw):.4f}")
        print(f"  blend 0.75mkt+0.25{m} {sum(bl) / len(bl):.4f}  diff vs market {sum(d) / len(d):+.4f} [{boots[50]:+.4f}, {boots[1949]:+.4f}] -> "
              f"{'ADDS INFORMATION' if verdict[m] else 'no demonstrated information beyond the market'}")
    print("\nBETTING TEST (all rungs; edge >= 3pp after fee; YES at ask / NO at 1-bid)")
    for m in ("M1", "M2"):
        bets = []
        for r in rows:
            if r[m] - r["ask"] - (mult or 1) * fee(r["ask"]) >= 0.03:
                cost = r["ask"] + (mult or 1) * fee(r["ask"]); bets.append((r["t"], r["rung"], (r["y"] - cost) / cost))
            elif (1 - r[m]) - (1 - r["bid"]) - (mult or 1) * fee(1 - r["bid"]) >= 0.03:
                cost = (1 - r["bid"]) + (mult or 1) * fee(1 - r["bid"]); bets.append((r["t"], r["rung"], ((1 - r["y"]) - cost) / cost))
        if not bets:
            print(f"  {m}: no bets"); continue
        ret = [b[2] for b in bets]
        mean = sum(ret) / len(ret)
        se = (sum((x - mean) ** 2 for x in ret) / (len(ret) - 1) / len(ret)) ** 0.5 if len(ret) > 1 else float("nan")
        h = len(ret) // 2
        halves = (sum(ret[:h]) / max(h, 1), sum(ret[h:]) / max(len(ret) - h, 1))
        ok = verdict[m] and len(ret) >= 50 and mean - 1.96 * se > 0 and min(halves) > 0
        print(f"  {m}: {len(ret)} bets, ROI {mean * 100:+.1f}% [{(mean - 1.96 * se) * 100:+.1f}, {(mean + 1.96 * se) * 100:+.1f}] | "
              f"halves {halves[0] * 100:+.1f}% / {halves[1] * 100:+.1f}% | by rung " +
              ", ".join(f"{k}+: n{sum(1 for b in bets if b[1] == k)} {sum(b[2] for b in bets if b[1] == k) / max(1, sum(1 for b in bets if b[1] == k)) * 100:+.0f}%" for k in sorted({b[1] for b in bets})))
        print(f"  DECISION {m}: {'MARKET-VALIDATED gate check' if ok else 'stays INFORMATION-ONLY'}")


if __name__ == "__main__":
    {"fetch": fetch, "analyze": analyze}[sys.argv[1]]()
