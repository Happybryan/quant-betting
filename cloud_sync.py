"""Pull new rows from the Cloudflare D1 collector (Pinnacle, 24/7) into data/market.db. Incremental by cloud id.
Usage: python3 cloud_sync.py        (launchd runs it every 2 min alongside the local Kalshi collector)"""
import json, sqlite3, subprocess
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.db"
CLOUD = ROOT / "cloud"
COLS = {"snapshots": "ts,source,league,event_key,start_time,selection,bid,ask,price,bid_size,ask_size,extra",
        "collector_runs": "ts,league,source,ok,rows_seen,rows_written,error,seconds"}
PAGE = 2000


def d1(sql):
    out = subprocess.run(["npx", "wrangler", "d1", "execute", "quant-betting", "--remote", "--json", "--command", sql],
                         cwd=CLOUD, capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise RuntimeError(out.stderr[-500:])
    return json.loads(out.stdout)[0]["results"]


def main():
    con = sqlite3.connect(DB, timeout=60)
    con.executescript((ROOT / "schema.sql").read_text())
    con.execute("CREATE TABLE IF NOT EXISTS cloud_sync (tbl TEXT PRIMARY KEY, last_id INTEGER NOT NULL)")
    for tbl, cols in COLS.items():
        last = (con.execute("SELECT last_id FROM cloud_sync WHERE tbl=?", (tbl,)).fetchone() or [0])[0]
        n = 0
        while True:
            rows = d1(f"SELECT id,{cols} FROM {tbl} WHERE id > {last} ORDER BY id LIMIT {PAGE}")
            if not rows:
                break
            if tbl == "collector_runs":  # Kalshi rows from the cloud are cloud attempts, not the laptop's collector
                for r in rows:
                    if r["source"].startswith("kalshi"):
                        r["source"] = "cloud_" + r["source"]
            con.executemany(f"INSERT INTO {tbl} ({cols}) VALUES ({','.join('?' * len(cols.split(',')))})",
                            [[r[c] for c in cols.split(",")] for r in rows])
            last = rows[-1]["id"]
            con.execute("INSERT OR REPLACE INTO cloud_sync VALUES (?,?)", (tbl, last))
            con.commit()  # rows + cursor commit together: a crash can't duplicate or skip
            n += len(rows)
            if len(rows) < PAGE:
                break
        print(f"cloud_sync {tbl}: +{n} (cloud id {last})")


def push_wanted(con):
    """Per-league cloud jobs for big sports (Soccer): the leagues the registry has matched to Kalshi. Uploaded only on change."""
    rows = con.execute("""SELECT DISTINCT json_extract(s.extra,'$.league_id'), s.league FROM snapshots s JOIN reg_league_map m
                          ON m.pinnacle_league = s.league WHERE s.source='pinnacle' AND m.n_matched > 0
                          AND json_extract(s.extra,'$.sport') = 'Soccer' AND json_extract(s.extra,'$.league_id') IS NOT NULL""").fetchall()
    sql = "DELETE FROM wanted_leagues;" + "".join(f"INSERT INTO wanted_leagues VALUES ({int(i)}, '{n.replace(chr(39), chr(39) * 2)}', 'Soccer');" for i, n in rows)
    mark = ROOT / "logs" / "wanted_leagues.sql"
    if mark.exists() and mark.read_text() == sql:
        return
    d1(sql)
    mark.write_text(sql)
    print(f"cloud_sync: pushed {len(rows)} wanted soccer leagues")


if __name__ == "__main__":
    main()
    push_wanted(sqlite3.connect(DB, timeout=60))
