"""ESPN NBA 2025-26 box scores -> data/raw/nba_player_games_2025_26.csv + nba_games_2025_26.csv (cached per game).
Resumable. Usage: python3 nba_data.py"""
import csv, json, time, urllib.error, urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = ROOT / "data/raw/espn_nba"
E = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"


def get(url):
    for i in range(4):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "curl/8.1.2"}), timeout=30))
        except OSError:  # URLError, socket.timeout, ConnectionReset are all OSError
            if i == 3:
                raise
            time.sleep(3 * (i + 1))


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    d = date(2025, 10, 21)
    while d <= date(2026, 6, 30):
        for ev in get(f"{E}/scoreboard?dates={d:%Y%m%d}").get("events", []):
            f = CACHE / f"{ev['id']}.json"
            if f.exists() or ev["competitions"][0]["status"]["type"]["name"] != "STATUS_FINAL":
                continue
            s = get(f"{E}/summary?event={ev['id']}")
            f.write_text(json.dumps(dict(id=ev["id"], date=ev["date"], season_type=ev.get("season", {}).get("type"),
                                         box=s.get("boxscore", {}).get("players", []), pick=s.get("pickcenter", []),
                                         teams=[(c["team"]["abbreviation"], c["homeAway"], c.get("score")) for c in ev["competitions"][0]["competitors"]])))
            time.sleep(0.2)
        d += timedelta(days=1)
    players, games = [], []
    for f in sorted(CACHE.glob("*.json")):
        g = json.loads(f.read_text())
        home = next(t for t, ha, _ in g["teams"] if ha == "home")
        away = next(t for t, ha, _ in g["teams"] if ha == "away")
        pk = next((p for p in g["pick"] if p.get("overUnder") is not None), {})
        games.append(dict(id=g["id"], date=g["date"], season_type=g["season_type"], home=home, away=away,
                          total=pk.get("overUnder"), spread=pk.get("spread"),
                          home_fav=(pk.get("homeTeamOdds") or {}).get("favorite"), **{f"{ha}_score": sc for _, ha, sc in g["teams"]}))
        for t in g["box"]:
            st = t["statistics"][0]
            lab = st["labels"]
            for a in st["athletes"]:
                if a.get("didNotPlay") or not a.get("stats"):
                    continue
                v = dict(zip(lab, a["stats"]))
                if v.get("MIN") in (None, "", "--"):
                    continue
                players.append(dict(game=g["id"], date=g["date"], season_type=g["season_type"], team=t["team"]["abbreviation"],
                                    player_id=a["athlete"]["id"], player=a["athlete"]["displayName"],
                                    pos=(a["athlete"].get("position") or {}).get("abbreviation"), min=v["MIN"], pts=v["PTS"],
                                    reb=v["REB"], ast=v["AST"], tpm=v["3PT"].split("-")[0]))
    for name, rows in (("nba_player_games_2025_26.csv", players), ("nba_games_2025_26.csv", games)):
        with open(ROOT / "data/raw" / name, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader(), w.writerows(rows)
    print(f"games {len(games)}, player-games {len(players)}")


if __name__ == "__main__":
    main()
