"""H4: favorite-longshot bias on Kalshi game-winner contracts (spec in EXPERIMENTS.md, written before running).
  python3 kalshi_history.py fetch     download settled markets + pre-game hourly candles (cached, resumable)
  python3 kalshi_history.py analyze   ROI after taker fees by ask bucket, discovery vs confirmation half"""
import json, math, random, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw" / "kalshi_candles"
K = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ["KXMLBGAME", "KXNBAGAME", "KXNHLGAME", "KXNFLGAME", "KXWNBAGAME", "KXNCAAFGAME"]
ENTRY_OFFSET_H, WINDOW_H = 4, 28


def get(path):
    for i in range(6):
        try:
            return json.load(urllib.request.urlopen(K + path, timeout=30))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503) or i == 5:
                raise
        except (urllib.error.URLError, TimeoutError):
            if i == 5:
                raise
        time.sleep(2 ** i + random.random())


def ts(s):
    # py3.9 fromisoformat rejects fractional seconds like ".68"; they don't matter at hourly resolution
    return int(datetime.fromisoformat(s[:19] + "+00:00").timestamp())


def list_markets(series):
    out = {}
    for base in (f"/historical/markets?series_ticker={series}", f"/markets?series_ticker={series}&status=settled"):
        c = ""
        while True:
            d = get(f"{base}&limit=1000&cursor={c}")
            for m in d.get("markets", []):
                out[m["ticker"]] = m | {"_historical": base.startswith("/historical")}
            c = d.get("cursor")
            if not c or not d.get("markets"):
                break
    return list(out.values())


def fetch_one(series, m):
    f = RAW / f"{m['ticker']}.json"
    # H4 anchors on occurrence_datetime - 4h; H4b (older markets without it) on expected_expiration_time - 5h
    anchor, off, study = ((m["occurrence_datetime"], ENTRY_OFFSET_H, "H4") if m.get("occurrence_datetime")
                          else (m.get("expected_expiration_time"), ENTRY_OFFSET_H + 1, "H4b"))
    if f.exists() or not anchor:
        return
    occ = ts(anchor)
    q = f"start_ts={occ - (WINDOW_H + off - ENTRY_OFFSET_H) * 3600}&end_ts={occ - off * 3600}&period_interval=60"
    path = (f"/historical/markets/{m['ticker']}/candlesticks?{q}" if m["_historical"]
            else f"/series/{series}/markets/{m['ticker']}/candlesticks?{q}")
    try:
        c = get(path).get("candlesticks", [])
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return  # not cached; a rerun picks it up
        c = {"error": e.code}
    except (urllib.error.URLError, TimeoutError, OSError):
        return  # network hiccup: not cached, a rerun picks it up
    f.write_text(json.dumps({"market": {k: m.get(k) for k in ("ticker", "event_ticker", "occurrence_datetime", "result",
                                                                "yes_sub_title", "open_time")}, "series": series, "candles": c,
                                "anchor": anchor, "offset_h": off, "study": study}))


def fetch():
    RAW.mkdir(parents=True, exist_ok=True)
    for s in SERIES:
        ms = list_markets(s)
        mults = get(f"/series/{s}")["series"].get("fee_multiplier")
        (RAW.parent / f"kalshi_{s}_markets.json").write_text(json.dumps({"fee_multiplier": mults, "n": len(ms)}))
        with ThreadPoolExecutor(3) as ex:  # ponytail: fixed 3 workers + backoff; Kalshi public limit is ~20 req/s
            list(ex.map(lambda m: fetch_one(s, m), ms))
        print(s, len(ms), "markets", flush=True)


def load():
    mult = {f.name[7:-13]: json.loads(f.read_text())["fee_multiplier"] for f in RAW.parent.glob("kalshi_*_markets.json")}
    rows = []
    for f in RAW.glob("*.json"):
        d = json.loads(f.read_text())
        m, c = d["market"], d["candles"]
        if m["result"] not in ("yes", "no") or not isinstance(c, list):
            continue
        cutoff = ts(d.get("anchor") or m["occurrence_datetime"]) - d.get("offset_h", ENTRY_OFFSET_H) * 3600
        # live endpoint says "close_dollars", /historical says "close"
        close = lambda x: (x.get("yes_ask") or {}).get("close_dollars") or (x.get("yes_ask") or {}).get("close")
        asks = [x for x in c if x["end_period_ts"] <= cutoff and close(x)]
        if not asks:
            continue
        ask = float(close(asks[-1]))
        if not 0 < ask < 1:
            continue
        fee = 0.07 * mult[d["series"]] * ask * (1 - ask)
        won = m["result"] == "yes"
        rows.append(dict(study=d.get("study", "H4"), series=d["series"], event=m["event_ticker"], date=d.get("anchor") or m["occurrence_datetime"], ask=ask,
                         won=won, ret=((1 if won else 0) - ask - fee) / (ask + fee)))
    return rows


def cluster_ci(rs, n=2000, seed=11):
    """ROI and 95% CI, bootstrapping whole events so both sides of one game resample together."""
    ev = {}
    for r in rs:
        ev.setdefault(r["event"], []).append(r["ret"])
    keys, rnd, stats = list(ev), random.Random(seed), []
    for _ in range(n):
        s = [ev[keys[rnd.randrange(len(keys))]] for _ in keys]
        stats.append(sum(map(sum, s)) / sum(map(len, s)))
    stats.sort()
    return sum(r["ret"] for r in rs) / len(rs), stats[int(.025 * n)], stats[int(.975 * n)]


def table(rows, title):
    print(f"\n{title}\n  ask bucket     n   avg ask  win%   ROI after fee   95% CI (event-clustered)")
    edges = [0, .2, .3, .4, .5, .6, .7, .8, 1]
    for lo, hi in zip(edges, edges[1:]):
        b = [r for r in rows if lo <= r["ask"] < hi]
        if len(b) >= 30:
            roi, clo, chi = cluster_ci(b)
            print(f"  {lo:.2f}-{hi:.2f} {len(b):6d}   {sum(r['ask'] for r in b)/len(b):.3f}  {sum(r['won'] for r in b)/len(b)*100:5.1f}   "
                  f"{roi*100:+6.1f}%        [{clo*100:+.1f}%, {chi*100:+.1f}%]")


def analyze(study="H4"):
    import research_log
    research_log.require_fresh({"H4": "H4_KALSHI_FLB", "H4b": "H4b_REPLICATION"}[study], "kalshi_game_winners",
                               *({"H4": ("2026-04", "2026-09-27"), "H4b": ("2025-04", "2026-04")}[study]))
    rows = sorted((r for r in load() if r["study"] == study), key=lambda r: r["date"])
    half = rows[len(rows) // 2]["date"]
    print(f"{study} Kalshi favorite-longshot test | {len(rows)} contract-sides, {len({r['event'] for r in rows})} events | "
          f"entry = last hourly yes_ask >= {ENTRY_OFFSET_H + (study == 'H4b')}h before {'expected_expiration_time' if study == 'H4b' else 'occurrence_datetime'} | split at {half[:10]}")
    for s in SERIES:
        n = [r for r in rows if r["series"] == s]
        if n:
            print(f"  {s}: {len(n)} sides, {n[0]['date'][:10]} .. {n[-1]['date'][:10]}")
    table([r for r in rows if r["date"] < half], "DISCOVERY half (all sports)")
    table([r for r in rows if r["date"] >= half], "CONFIRMATION half (all sports)")
    table(rows, "POOLED (all sports)")
    for s in SERIES:
        sub = [r for r in rows if r["series"] == s]
        if len(sub) >= 300:
            table(sub, f"POOLED {s}")


if __name__ == "__main__":
    {"fetch": fetch, "analyze": lambda: analyze(sys.argv[2] if len(sys.argv) > 2 else "H4")}[sys.argv[1]]()
