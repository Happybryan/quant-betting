"""EXPLORATORY backtest of everything collected since 2026-09-27 (forward data, small n). Hypothesis generation only:
nothing here is confirmatory, and any idea it suggests must be pre-registered and tested on NEW data.
Scores closing prices (last quote before start) against settled results:
  A. Pinnacle no-vig player props (NFL, MLB): calibration, Over/Under bias, by stat
  B. Kalshi NFL prop ladders ("N+"): favourite-longshot pattern by price bucket
  C. PrizePicks standard lines we pulled: MORE hit rate
NFL caveat: ESPN box scores omit players with no stat in a category, so a 0-catch receiver has no row. Missing
outcomes are reported two ways: excluded, and counted as 0 (the Under side). The truth lies between.
  python3 explore_since_start.py"""
import re, sqlite3
from datetime import datetime, timedelta
from calibration import wilson
from odds import american_to_prob, devig_mult

DB = "data/market.db"
PIN = {"Total Receiving Yards": "receiving yards", "Total Receptions": "receptions", "Total Rushing Yards": "rush yards",
       "Total Rush Attempts": "rush attempts", "Total Passing Yards": "pass yards", "Total Touchdown Passes": "pass tds",
       "Total Pass Completions": "pass completions", "Total Pass Attempts": "pass attempts", "Total Interceptions": "interceptions",
       "Total Bases": "total bases", "Total Home Runs": "home runs", "Total Strikeouts": "pitcher strikeouts",
       "Total Pitching Outs": "pitching outs"}
KAL = {"passing yards": "pass yards", "receiving yards": "receiving yards", "rushing yards": "rush yards", "receptions": "receptions",
       "passing touchdowns": "pass tds", "passing attempts": "pass attempts", "passing completions": "pass completions",
       "rushing attempts": "rush attempts", "passing interceptions": "interceptions"}


def et_day(start):
    return (datetime.fromisoformat(start.replace("Z", "+00:00")) - timedelta(hours=4)).strftime("%Y%m%d")


def outcomes(con):
    out = {}
    for d, p, s, v in con.execute("SELECT game_date, player, stat, value FROM prop_outcomes"):
        out[(d, p, s)] = v
    return out, {d for d, in con.execute("SELECT DISTINCT game_date FROM prop_outcomes")}


def pinnacle_closes(con):
    """last two-sided quote at the same line with ts <= start, per (event, player, stat)."""
    last = {}
    for ts, ev, start, sel, price, pts, lg in con.execute(
            """SELECT ts, event_key, start_time, selection, price, json_extract(extra,'$.points'), league FROM snapshots
               WHERE source='pinnacle_prop' AND league IN ('NFL','MLB') AND ts <= start_time ORDER BY ts"""):
        m = re.fullmatch(r"(.+) (Total [A-Za-z ]+)\|(Over|Under)", sel)
        if not m or m.group(2) not in PIN:
            continue
        last[(ev, m.group(1), PIN[m.group(2)], m.group(3))] = (price, pts, start, lg)
    rows = []
    for (ev, pl, st, side), (po, pts, start, lg) in last.items():
        if side != "Over" or (ev, pl, st, "Under") not in last:
            continue
        pu, ptsu, _, _ = last[(ev, pl, st, "Under")]
        if None in (po, pu) or pts != ptsu:
            continue
        p_over = devig_mult([american_to_prob(po), american_to_prob(pu)])[0]
        rows.append(dict(league=lg, player=pl, stat=st, line=pts, p=p_over, day=et_day(start)))
    return rows


def kalshi_closes(con):
    """Kalshi ladder rungs; the rows' start_time is the market close (~kickoff + 3h), so the close quote is the last before start - 3h."""
    last = {}
    for ts, start, sel, bid, ask in con.execute("""SELECT ts, start_time, selection, bid, ask FROM snapshots WHERE source='kalshi_prop'
                                                     AND bid IS NOT NULL AND ask IS NOT NULL ORDER BY ts"""):
        kick = datetime.fromisoformat(start.replace("Z", "+00:00")) - timedelta(hours=3)
        if datetime.fromisoformat(ts) > kick:
            continue
        m = re.fullmatch(r"(.+): (\d+)\+ (.+)", sel)
        if m and m.group(3) in KAL:
            last[(m.group(1), KAL[m.group(3)], int(m.group(2)), kick.isoformat())] = (bid + ask) / 2
    return [dict(player=p, stat=s, n=n, p=mid, day=et_day(k)) for (p, s, n, k), mid in last.items()]


def score(rows, out, days, miss_as_zero):
    res = []
    for r in rows:
        if r["day"] not in days:
            continue
        v = out.get((r["day"], r["player"], r["stat"]))
        if v is None and r.get("league") == "MLB":
            continue  # MLB box includes every player who appeared with 0s: missing = didn't play = void
        if v is None:
            if not miss_as_zero:
                continue
            v = 0.0
        if "line" in r:
            if v == r["line"]:
                continue  # push
            res.append((r, r["p"], int(v > r["line"])))
        else:
            res.append((r, r["p"], int(v >= r["n"])))
    return res


def calib(res, title, edges=(0, .2, .35, .45, .55, .65, .8, 1.01)):
    print(f"\n{title}: n={len(res)}")
    if not res:
        return
    brier = sum((p - y) ** 2 for _, p, y in res) / len(res)
    base = sum((0.5 - y) ** 2 for _, p, y in res) / len(res)
    print(f"  Brier {brier:.4f} vs coin-flip {base:.4f} | mean P {sum(p for _, p, _ in res) / len(res):.3f} | hit rate {sum(y for *_, y in res) / len(res):.3f}")
    for lo, hi in zip(edges, edges[1:]):
        b = [(p, y) for _, p, y in res if lo <= p < hi]
        if len(b) >= 10:
            k = sum(y for _, y in b); w = wilson(k, len(b))
            print(f"  P {lo:.2f}-{min(hi, 1):.2f}: n {len(b):4} | market {sum(p for p, _ in b) / len(b):.3f} | actual {k / len(b):.3f} "
                  f"[{w[0]:.3f}, {w[1]:.3f}]")


def by_stat(res, title):
    print(f"\n{title} (actual - market, Over side):")
    g = {}
    for r, p, y in res:
        g.setdefault((r.get("league", "NFL"), r["stat"]), []).append((p, y))
    for (lg, st), b in sorted(g.items(), key=lambda x: -len(x[1])):
        if len(b) >= 20:
            k = sum(y for _, y in b); w = wilson(k, len(b))
            mp = sum(p for p, _ in b) / len(b)
            print(f"  {lg:3} {st:18} n {len(b):4} | market {mp:.3f} actual {k / len(b):.3f} [{w[0]:.3f}, {w[1]:.3f}] diff {k / len(b) - mp:+.3f}")


if __name__ == "__main__":
    con = sqlite3.connect(DB, timeout=60)
    out, days = outcomes(con)
    print("settled days:", sorted(days))
    pin = pinnacle_closes(con)
    for lg in ("MLB", "NFL"):
        rows = [r for r in pin if r["league"] == lg]
        for z in ((False,) if lg == "MLB" else (False, True)):
            res = score(rows, out, days, z)
            calib(res, f"A. Pinnacle {lg} props, P(Over) at close" + ("" if lg == "MLB" else (" [missing=0]" if z else " [missing excluded]")))
            by_stat(res, f"   {lg} by stat" + ("" if lg == "MLB" else (" [missing=0]" if z else " [missing excluded]")))
    kal = kalshi_closes(con)
    for z in (False, True):
        calib(score(kal, out, days, z), "B. Kalshi NFL ladder rungs, YES mid at close" + (" [missing=0]" if z else " [missing excluded]"),
              edges=(0, .05, .1, .2, .35, .5, .65, .8, .9, 1.01))
    pp = []
    for ts, start, sel, pts in con.execute("""SELECT ts, start_time, selection, json_extract(extra,'$.points') FROM snapshots
                                              WHERE source='prizepicks' AND ts <= start_time AND json_extract(extra,'$.points') IS NOT NULL ORDER BY ts"""):
        pl, st = sel.split("|")
        pp.append(dict(player=pl, stat=st, line=pts, p=0.5, day=et_day(start), league="MLB" if st in ("total bases", "home runs", "pitcher strikeouts", "pitching outs") else "NFL"))
    last = {(r["player"], r["stat"], r["day"]): r for r in pp}
    res = score(list(last.values()), out, days, False)
    k = sum(y for *_, y in res)
    print(f"\nC. PrizePicks standard lines (last pre-game line): n={len(res)} | MORE hit {k}/{len(res)}" + (f" = {k / len(res):.3f}" if res else ""))
