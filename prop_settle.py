"""Settle NFL (ESPN) and MLB (statsapi.mlb.com) player props from final box scores -> table prop_outcomes (one row per player/stat/date).
Graded tables read outcomes from here: copy_legs/copy_evals, copy_controls, stale_legs, demo_parlays, ledger legs.
Usage: python3 prop_settle.py [YYYYMMDD ...]  (default: yesterday and today, US dates)"""
import json, re, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import match, verify

ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.db"
SCHEMA = """CREATE TABLE IF NOT EXISTS prop_outcomes (game_date TEXT, event TEXT, player TEXT, stat TEXT, value REAL, ts TEXT,
  PRIMARY KEY (game_date, player, stat));"""


def box(event):
    s = verify.get(f"{verify.ESPN}/summary?event={event}")
    out = {}
    for t in s.get("boxscore", {}).get("players", []):
        for cat in t["statistics"]:
            lab = cat["labels"]
            for a in cat["athletes"]:
                v = dict(zip(lab, a["stats"]))
                d = out.setdefault(a["athlete"]["displayName"], {})
                num = lambda x: float(x) if re.fullmatch(r"-?\d+(\.\d+)?", str(x)) else None
                if cat["name"] == "passing":
                    c, att = (v.get("C/ATT") or "0/0").split("/")
                    d.update({"pass yards": num(v.get("YDS")), "pass tds": num(v.get("TD")), "interceptions": num(v.get("INT")),
                              "pass completions": float(c), "pass attempts": float(att)})
                elif cat["name"] == "rushing":
                    d.update({"rush yards": num(v.get("YDS")), "rush attempts": num(v.get("CAR")), "rush tds": num(v.get("TD"))})
                elif cat["name"] == "receiving":
                    d.update({"receptions": num(v.get("REC")), "receiving yards": num(v.get("YDS")), "rec tds": num(v.get("TD"))})
    for name in played(event, s):  # dressed and played but no stat line in a category = 0, not missing (2026-10-01 fix)
        d = out.setdefault(name, {})
        for k in ZERO_FILL:
            d.setdefault(k, 0.0)
    for d in out.values():  # combos PrizePicks uses
        if d.get("pass yards") is not None or d.get("rush yards") is not None:
            d["pass+rush yds"] = (d.get("pass yards") or 0) + (d.get("rush yards") or 0)
        d["rush+rec yds"] = (d.get("rush yards") or 0) + (d.get("receiving yards") or 0)
    return out


ZERO_FILL = ("receptions", "receiving yards", "rec tds", "rush yards", "rush attempts", "rush tds")
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"


def played(event, summary):
    """Names of players on the game roster with didNotPlay=False. ESPN's box score omits anyone with no stat in a category,
    so without this a 0-catch receiver looked identical to an inactive one (LESS legs never settled; H15 unscorable)."""
    names = []
    for c in summary["header"]["competitions"][0]["competitors"]:
        tid = c["team"]["id"]
        ids = {a["id"]: a["displayName"] for g in verify.get(f"{verify.ESPN}/teams/{tid}/roster").get("athletes", []) for a in g.get("items", [])}
        for e in verify.get(f"{CORE}/events/{event}/competitions/{event}/competitors/{tid}/roster").get("entries", []):
            if e.get("didNotPlay") is False and str(e["playerId"]) in ids:
                names.append(ids[str(e["playerId"])])
    return names


def settle_date(con, day):
    sb = verify.get(f"{verify.ESPN}/scoreboard?dates={day}")
    n = 0
    for e in sb.get("events", []):
        if e["competitions"][0]["status"]["type"]["name"] != "STATUS_FINAL":
            continue
        for player, stats in box(e["id"]).items():
            for stat, val in stats.items():
                if val is not None:
                    con.execute("INSERT OR REPLACE INTO prop_outcomes VALUES (?,?,?,?,?,?)", (day, e["id"], player, stat, val,
                                datetime.now(timezone.utc).isoformat(timespec="seconds")))
                    n += 1
    con.commit()
    return n


MLB = "https://statsapi.mlb.com/api/v1"
MLB_STATS = {"total bases": ("batting", "totalBases"), "home runs": ("batting", "homeRuns"),
             "pitcher strikeouts": ("pitching", "strikeOuts"), "pitching outs": ("pitching", "outs")}


def settle_mlb(con, day):
    """MLB official Stats API box scores (has totalBases directly). A player who didn't appear has no stats -> no row -> leg stays open."""
    n = 0
    for d in verify.get(f"{MLB}/schedule?sportId=1&date={day[:4]}-{day[4:6]}-{day[6:]}").get("dates", []):
        for g in d["games"]:
            if g["status"]["abstractGameState"] != "Final":
                continue
            b = verify.get(f"{MLB}/game/{g['gamePk']}/boxscore")
            for side in ("home", "away"):
                for p in b["teams"][side]["players"].values():
                    for stat, (grp, k) in MLB_STATS.items():
                        v = p["stats"].get(grp, {}).get(k)
                        if v is not None:
                            con.execute("INSERT OR REPLACE INTO prop_outcomes VALUES (?,?,?,?,?,?)", (day, str(g["gamePk"]), p["person"]["fullName"],
                                        stat, float(v), datetime.now(timezone.utc).isoformat(timespec="seconds")))
                            n += 1
    con.commit()
    return n


def outcome(con, player, stat, day):
    r = con.execute("SELECT value FROM prop_outcomes WHERE game_date=? AND stat=? AND player=?", (day, stat, player)).fetchone()
    if r:
        return r[0]
    for p, v in con.execute("SELECT player, value FROM prop_outcomes WHERE game_date=? AND stat=?", (day, stat)):
        if match.participant_score(player, p, "Baseball" if stat in MLB_STATS else "Football") >= match.AUTO_THRESHOLD:
            return v
    return None


def grade(value, line, side):
    if value is None:
        return None
    if value == line:
        return "push"
    return "win" if (value > line) == (side == "MORE") else "loss"


if __name__ == "__main__":
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    days = sys.argv[1:] or [(et - timedelta(days=1)).strftime("%Y%m%d"), et.strftime("%Y%m%d")]
    for d in days:
        print(d, "player-stat outcomes stored: NFL", settle_date(con, d), "| MLB", settle_mlb(con, d))
    assert grade(3, 2.5, "MORE") == "win" and grade(2, 2.5, "MORE") == "loss" and grade(2, 2.0, "LESS") == "push"
