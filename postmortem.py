"""Postmortem for a settled ledger row, in the fixed format. It compares reality to the FROZEN pre-bet snapshot.
It never regenerates a prediction with newer information, and never fills a missing close.
Usage: python3 postmortem.py BET_ID [BET_ID ...]"""
import json, sqlite3, sys
from datetime import datetime, timedelta
from pathlib import Path
import ledger, status, verify

DB = Path(__file__).parent / "data" / "market.db"
NEWS_WORDS = ("injur", "out", "questionable", "doubtful", "inactive", "starter", "start", "benched", "weather", "limited")


def parse_leg(b):
    """'Jacoby Brissett LESS 1.5' + market 'PP leg Pass TDs' -> (player, stat key, line, side)."""
    toks = b["selection"].split()
    side_i = next(i for i, t in enumerate(toks) if t in ("MORE", "LESS"))
    stat = b["market"].replace("PP leg ", "").replace(" (backup)", "").lower()
    return " ".join(toks[:side_i]), stat, float(toks[side_i + 1]), toks[side_i]


def catalysts(team, player, t0, t1):
    """ESPN team news published inside the movement window that mentions the player or a news keyword.
    Returns [] when nothing verifiable is found. Never invents a reason."""
    abbr = verify.ESPN_ABBR.get(team, team).lower()
    try:
        arts = verify.get(f"{verify.ESPN}/news?team={abbr}&limit=100").get("articles", [])
    except Exception as e:
        return [f"(news feed unavailable: {e!r})"]
    last = player.split()[-1].lower()
    out = []
    for x in arts:
        pub = x.get("published", "")
        if t0 <= pub.replace("Z", "+00:00") <= t1:
            h = (x.get("headline", "") + " " + x.get("description", "")).lower()
            if last in h or any(w in h for w in NEWS_WORDS):
                out.append(f"{pub[:16]}Z {x.get('headline')}")
    return out


def leg_report(con, b):
    player, stat, line, side = parse_leg(b)
    desc = f"{player} {verify.STATS[stat][0]}"
    start = b["start_time"].replace("Z", "+00:00")
    path = [p for p in verify.pin_history(desc, side) if p[0] <= start]
    p_close, cstat, info = ledger.prop_close(con, desc, side, b["start_time"], entry_line=line)
    gaps = [(a, c) for a, c in status.gaps(con, path[0][0] if path else b["ts"]) if a < start]
    rows = verify.player_rows(player)[0]
    team = rows[-1]["team"] if rows else None
    moves = [(y[0], y[2] - x[2]) for x, y in zip(path, path[1:]) if abs(y[2] - x[2]) > 0.001]
    cat = []
    if moves and team:
        t0 = (datetime.fromisoformat(path[0][0]) - timedelta(hours=2)).isoformat()
        cat = catalysts(team, player, t0, moves[-1][0])
    return dict(player=player, stat=stat, line=line, side=side, desc=desc, path=path, p_close=p_close, close_status=cstat,
                close_info=info, gaps=gaps, moves=moves, catalysts=cat)


def main(ids):
    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    for i in ids:
        b = con.execute("SELECT * FROM bets WHERE id=?", (i,)).fetchone()
        print("=" * 90 + f"\nPOSTMORTEM #{i}: {b['selection']}  [{b['mode']}, {b['protocol']}]")
        snap = con.execute("SELECT id, ts, sha256 FROM prebet_snapshots WHERE id=?", (b["prebet_id"],)).fetchone() if b["prebet_id"] else None
        print(f"RESULT:                 {b['result'] or 'unsettled'}" + (f"  P/L ${b['pnl']:+.2f}" if b["stake"] else ""))
        print(f"PRE-BET SNAPSHOT:       " + (f"#{snap['id']} {snap['ts']} sha {snap['sha256'][:16]}" if snap else
                                              "NONE: logged before VERIFY existed; only the ledger's pre-event fields are trustworthy"))
        print(f"ORIGINAL THESIS:        {b['assumptions']}")
        print(f"PRE-BET MODEL P:        {b['model_p']:.3f}  (model {b['model_version']}, logged {b['ts']})")
        if b["stake"]:  # entry row: summarize via its legs
            print(f"ENTRY PRICE:            {b['price']}x payout (break-even joint {1 / b['price']:.3f})" if b["price"] else "ENTRY PRICE: unverified payout")
            print(f"CLOSING PRICE / CLV:    {b['close_status'] or '-'}" + (f"  (labeled last observation {b['last_obs_ts']}: {b['last_obs_fair_p']:.3f}, NOT a close)" if b["last_obs_fair_p"] else ""))
            print("EXECUTION ERROR?:       " + ("YES: slip never verified; the backup-leg wording was misread as a 3-leg entry (EXEC none)"
                                              if b["exec_version"] in (None, "none") else "see legs"))
            continue
        L = leg_report(con, b)
        print(f"ENTRY PRICE:            fair {L['side']} {b['model_p']:.3f} at logging")
        print(f"CLOSING PRICE:          " + (f"{L['p_close']:.3f} (collector run {L['close_info'][11:16]}Z within 15 min of start)" if L["close_status"] == "ok"
                                             else ("LINE_CHANGED (Pinnacle moved off the entry line: not comparable)" if L["close_status"] == "LINE_CHANGED" else "CLOSE MISSING") + (f"  (labeled: last observed price {L['close_info'][1]:.3f}, last change {L['close_info'][0][11:16]}Z; NOT a close)" if L["close_info"] else "")))
        print(f"CLV:                    " + (f"{(L['p_close'] - b['model_p']) * 100:+.1f}pp" if L["p_close"] is not None else "not computable (no valid close)"))
        print("MARKET MOVEMENT:        " + ("  ".join(f"{t[11:16]}Z {p:.3f}" for t, _, p in L["path"]) or "no observations"))
        print("   changes observed:    " + (", ".join(f"{d * 100:+.1f}pp by {t[11:16]}Z" for t, d in L["moves"]) or "none observed"))
        print("   unobserved gaps:     " + ("; ".join(f"{a[11:16]}-{c[11:16]}Z" for a, c in L["gaps"]) or "none"))
        print("WHY IT MOVED:           " + ("; ".join(L["catalysts"]) if L["catalysts"] else
                                            "no verifiable catalyst found in ESPN team news for the window (movement cause UNKNOWN, not assumed)" if L["moves"] else "n/a"))
        print("WHAT CHANGED AFTER ENTRY: see movement above; the frozen pre-bet inputs are in the snapshot" if snap else
              "WHAT CHANGED AFTER ENTRY: movement above; no frozen inputs exist for this pre-protocol bet")
        print("DATA ERROR?:            " + ("YES: collector gaps before start; the close is missing" if L["close_status"] != "ok" or L["gaps"] else "no"))
        print("EXECUTION ERROR?:       " + ("unverifiable: slip never verified (pre-protocol)" if b["exec_version"] in (None, "none") else "no"))
        lose = 1 - b["model_p"]
        print(f"MODEL ERROR?:           not assessable from one outcome. Needs calibration across many legs (H5)")
        print(f"NORMAL VARIANCE?:       at P={b['model_p']:.3f} this side {'loses' if b['result'] == 'loss' else 'wins'} "
              f"{(lose if b['result'] == 'loss' else b['model_p']) * 100:.0f}% of the time; one result is consistent with either a good or bad estimate")
        sysfix = []
        if L["close_status"] != "ok" or L["gaps"]:
            sysfix.append("infrastructure: always-on collector (FAILURES.md)")
        if b["protocol"] == "PRE_VERIFICATION_PROTOCOL":
            sysfix.append("already addressed: VERIFY_v2.0 gate, frozen snapshots, one-slip EXEC_v1.0")
        print("LESSON:                 " + ("process defects only; no model change is justified by this outcome" if sysfix else "no process defect found; outcome only"))
        print("SYSTEM CHANGE REQUIRED? " + ("; ".join(sysfix) if sysfix else "no"))


if __name__ == "__main__":
    main([int(x) for x in sys.argv[1:]])
