"""H9 Test B (pre-registered 9a5b9a3): NBA_PROP_v0.1 vs Kalshi NBA player-prop prices in the TEST period.
  python3 h9_market.py fetch      settled KXNBAPTS/REB/AST/3PT markets + hourly candles (wide window; cached)
  python3 h9_market.py analyze    pre-tip price = last candle <= ESPN tip - 1h; blend + betting tests as registered"""

import research_log
research_log.require_fresh("H9_NBA_PROPS", "kalshi_nbaprops", "2026-03-01", "2026-06-14")  # audit fix: never run on a window another experiment used
import csv, json, math, random, re, sys, time, urllib.error, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import match, nba_model as nm

ROOT = Path(__file__).parent
RAW = ROOT / "data/raw/kalshi_nbaprops"
K = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = {"KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "tpm"}
TEST_START = nm.VAL_END


def get(path):
    for i in range(9):  # waits out ~4 min of network outage in total
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(K + path, headers={"User-Agent": "curl/8.1.2"}), timeout=30))
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == 5:
                raise
            time.sleep(2 ** i + 1)
        except OSError:  # URLError (incl. DNS failures) + socket.timeout
            if i == 8:
                raise
            time.sleep(min(60, 3 * 2 ** i))


def fetch():
    RAW.mkdir(parents=True, exist_ok=True)
    for series in SERIES:
        ms = {}
        for base in (f"/historical/markets?series_ticker={series}", f"/markets?series_ticker={series}&status=settled"):
            c = ""
            while True:
                d = get(f"{base}&limit=1000&cursor={c}")
                for m in d.get("markets", []):
                    ms[m["ticker"]] = m | {"_hist": base.startswith("/historical")}
                c = d.get("cursor")
                if not c or not d.get("markets"):
                    break
        n = 0
        for t, m in ms.items():
            f = RAW / f"{t}.json"
            anchor = m.get("occurrence_datetime") or m.get("expected_expiration_time")
            if f.exists() or not anchor or anchor[:10] < TEST_START:
                continue
            hi = int(datetime.fromisoformat(anchor[:19] + "+00:00").timestamp())  # py3.9: no fractional seconds
            path = (f"/historical/markets/{t}/candlesticks" if m["_hist"] else f"/series/{series}/markets/{t}/candlesticks")
            try:
                c = get(f"{path}?start_ts={hi - 36 * 3600}&end_ts={hi}&period_interval=60").get("candlesticks", [])
            except urllib.error.HTTPError as e:
                c = {"error": e.code}
            except OSError:
                continue  # network failure after retries: not cached, the next run picks it up
            f.write_text(json.dumps(dict(series=series, market={k: m.get(k) for k in ("ticker", "event_ticker", "yes_sub_title",
                                                                                        "floor_strike", "result")}, candles=c)))
            n += 1
            time.sleep(0.2)
        print(f"{series}: {len(ms)} settled markets, newly cached {n}", flush=True)


CLUSTER = False


def mult_cache():
    return {s: get(f"/series/{s}")["series"].get("fee_multiplier") or 1 for s in SERIES}


def analyze():
    choice = json.loads(nm.CHOICE.read_text())["choice"]
    params, preds = nm.run(choice)
    rows, games = nm.load()
    tip = {g: datetime.fromisoformat(games[g]["date"].replace("Z", "+00:00")) for g in games}
    by = {}  # (normalized player, ET date) -> (row, pred)
    for r, p in preds:
        by[(match.core(r["player"]), match.et_date(r["date"]))] = (r, p)
    data, skip = [], defaultdict(int)
    for f in RAW.glob("*.json"):
        d = json.loads(f.read_text())
        m, c = d["market"], d["candles"]
        if m["result"] not in ("yes", "no"):
            skip["not yes/no"] += 1; continue
        dm = re.search(r"-(\d{2}[A-Z]{3}\d{2})", m["event_ticker"])
        day = datetime.strptime(dm.group(1), "%y%b%d").date()
        name, n = m["yes_sub_title"].split(":")[0], int(round(float(m["floor_strike"]) + 0.5))
        hit = by.get((match.core(name), day))
        if not hit:
            skip["no model prediction (player/date)"] += 1; continue
        r, p = hit
        cut = int((tip[r["game"]] - timedelta(hours=1)).timestamp())
        cl = lambda x, side: (x.get(side) or {}).get("close_dollars") or (x.get(side) or {}).get("close")
        cs = [x for x in (c if isinstance(c, list) else []) if x["end_period_ts"] <= cut and x["end_period_ts"] >= cut - 24 * 3600
              and cl(x, "yes_bid") and cl(x, "yes_ask")]
        if not cs:
            skip["no pre-tip candle"] += 1; continue
        bid, ask = float(cl(cs[-1], "yes_bid")), float(cl(cs[-1], "yes_ask"))
        stat = SERIES[d["series"]]
        pm = nm.prob_ge(p, stat, n, params["disp"], nm.zlib.crc32(f"{m['ticker']}".encode()))
        data.append(dict(t=tip[r["game"]], stat=stat, rung=n, y=1.0 if m["result"] == "yes" else 0.0, bid=bid, ask=ask, mid=(bid + ask) / 2, pm=pm))
    data.sort(key=lambda x: x["t"])
    if CLUSTER:
        return data, mult_cache(), choice
    print(f"H9 TEST B | NBA_PROP_v0.1 = {choice} | usable markets {len(data)} | excluded {dict(skip)}")
    lg = lambda q: math.log(min(max(q, 1e-4), 1 - 1e-4) / (1 - min(max(q, 1e-4), 1 - 1e-4)))
    sig = lambda z: 1 / (1 + math.exp(-z))
    mult = {s: get(f"/series/{s}")["series"].get("fee_multiplier") or 1 for s in SERIES}
    rnd = random.Random(9)
    for stat in sorted({x["stat"] for x in data}):
        rows = [x for x in data if x["stat"] == stat and 0 < x["mid"] < 1]
        base = [nm.ll(x["mid"], x["y"]) for x in rows]
        bl = [nm.ll(sig(0.75 * lg(x["mid"]) + 0.25 * lg(x["pm"])), x["y"]) for x in rows]
        dd = [a - b for a, b in zip(bl, base)]
        boots = sorted(sum(dd[rnd.randrange(len(dd))] for _ in dd) / len(dd) for _ in range(1000))
        fm = mult[next(s for s, v in SERIES.items() if v == stat)]
        fee = lambda q: fm * 0.07 * q * (1 - q)
        bets = []
        for x in rows:
            if x["pm"] - x["ask"] - fee(x["ask"]) >= 0.03:
                c_ = x["ask"] + fee(x["ask"]); bets.append((x["y"] - c_) / c_)
            elif (1 - x["pm"]) - (1 - x["bid"]) - fee(1 - x["bid"]) >= 0.03:
                c_ = (1 - x["bid"]) + fee(1 - x["bid"]); bets.append(((1 - x["y"]) - c_) / c_)
        mean = sum(bets) / len(bets) if bets else float("nan")
        se = (sum((b - mean) ** 2 for b in bets) / (len(bets) - 1) / len(bets)) ** 0.5 if len(bets) > 1 else float("nan")
        h = len(bets) // 2
        halves = (sum(bets[:h]) / max(h, 1), sum(bets[h:]) / max(len(bets) - h, 1)) if bets else (float("nan"),) * 2
        ok = boots[974] < 0 and len(bets) >= 50 and mean - 1.96 * se > 0 and min(halves) > 0
        print(f"  {stat.upper():3} n={len(rows)} | market {sum(base) / len(base):.4f} model {sum(nm.ll(x['pm'], x['y']) for x in rows) / len(rows):.4f} "
              f"blend diff {sum(dd) / len(dd):+.4f} [{boots[25]:+.4f}, {boots[974]:+.4f}] | bets {len(bets)} ROI {mean * 100:+.1f}% "
              f"[{(mean - 1.96 * se) * 100:+.1f}, {(mean + 1.96 * se) * 100:+.1f}] halves {halves[0] * 100:+.1f}/{halves[1] * 100:+.1f}% "
              f"-> {'MARKET-VALIDATED' if ok else 'not market-validated'}")


if __name__ == "__main__":
    {"fetch": fetch, "analyze": analyze}[sys.argv[1]]()
