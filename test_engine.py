"""Adversarial tests for the universal engine (MATCH_v1.0, RULES_v1.0, odds N-way, time, health, closes).
Run: python3 -m unittest test_engine -v"""
import sqlite3, unittest
from datetime import date, datetime, timezone
import match, rules, odds, status, ledger, quality, movement, market_only, sgp, simulate, calibration, research_log

A = match.AUTO_THRESHOLD
ps = lambda a, b, sport="Soccer": match.participant_score(a, b, sport)


def ev(sport, d, parts, t=None):
    return dict(sport=sport, date=d, participants=parts, time_utc=t)


def cand(i, sport, start, parts):
    return dict(id=i, sport=sport, start=start, participants=parts)


class Names(unittest.TestCase):
    def test_manchester(self):
        self.assertGreaterEqual(ps("Man United", "Manchester United"), A)
        self.assertGreaterEqual(ps("Man City", "Manchester City"), A)
        self.assertLess(ps("Man City", "Manchester United"), A)
        self.assertLess(ps("Manchester United", "Manchester City"), A)

    def test_inter(self):
        self.assertLess(ps("Inter Milan", "Inter Miami"), A)
        self.assertLess(ps("Inter", "Inter Miami"), A)  # alias of a different entity
        self.assertGreaterEqual(ps("Inter", "Inter Milan"), A)

    def test_new_york_la(self):
        self.assertGreaterEqual(ps("New York J", "New York Jets"), A)
        self.assertLess(ps("New York J", "New York Giants"), A)
        self.assertGreaterEqual(ps("Los Angeles C", "Los Angeles Chargers"), A)
        self.assertLess(ps("Los Angeles C", "Los Angeles Rams"), A)
        self.assertLess(ps("LA Clippers", "Los Angeles Lakers"), A)

    def test_same_surname_and_accents(self):
        self.assertLess(ps("Karolina Pliskova", "Kristyna Pliskova"), A)
        self.assertEqual(ps("Sebastián Báez", "Sebastian Baez"), 100)
        self.assertEqual(ps("M´gladbach", "Borussia Monchengladbach"), 95)

    def test_doubles(self):
        self.assertGreaterEqual(ps("Pavic/Mektic", "Mektic / Pavic"), A)
        self.assertLess(ps("Pavic/Mektic", "Pavic/Dodig"), A)
        self.assertEqual(ps("Pavic/Mektic", "Pavic"), 0)

    def test_reserve_academy_women(self):
        self.assertEqual(ps("Barcelona B", "Barcelona"), 0)
        self.assertEqual(ps("Real Madrid Castilla", "Real Madrid"), 0)
        self.assertEqual(ps("T1 Academy", "T1"), 0)
        self.assertEqual(ps("Fnatic TQ", "Fnatic"), 0)
        self.assertEqual(ps("Arsenal W", "Arsenal"), 0)
        self.assertEqual(ps("Arsenal Women", "Arsenal W"), 100)

    def test_renamed_franchise(self):
        self.assertGreaterEqual(ps("Washington Football Team", "Washington Commanders", "Football"), A)
        self.assertGreaterEqual(ps("Oakland Raiders", "Las Vegas Raiders", "Football"), A)
        self.assertGreaterEqual(ps("Washington", "Washington Nationals", "Baseball"), A)  # no cross-sport alias leak
        self.assertGreaterEqual(ps("San Antonio", "San Antonio Spurs", "Basketball"), A)
        self.assertGreaterEqual(ps("Spurs", "Tottenham Hotspur", "Soccer"), A)


class Events(unittest.TestCase):
    def test_sisters_same_day_resolved_by_opponent(self):
        c = [cand("1", "Tennis", "2026-09-28T10:00:00Z", ["Karolina Pliskova", "Iga Swiatek"]),
             cand("2", "Tennis", "2026-09-28T12:00:00Z", ["Kristyna Pliskova", "Coco Gauff"])]
        best, conf, _ = match.match_event(ev("Tennis", date(2026, 9, 28), ["Pliskova", "Gauff"]), c)
        self.assertEqual(best["id"], "2")
        self.assertGreaterEqual(conf, A)

    def test_ambiguous_is_capped(self):
        c = [cand("1", "Baseball", "2026-09-28T23:05:00Z", ["New York Yankees", "Boston Red Sox"]),
             cand("2", "Baseball", "2026-09-28T23:10:00Z", ["New York Mets", "Boston Red Sox"])]
        _, conf, why = match.match_event(ev("Baseball", date(2026, 9, 28), ["New York", "Boston"]), c)
        self.assertLess(conf, A)
        self.assertIn("AMBIGUOUS", why)

    def test_postponed_date_moved_no_match(self):
        c = [cand("1", "Soccer", "2026-10-12T14:00:00Z", ["Arsenal", "Leeds United"])]
        best, conf, _ = match.match_event(ev("Soccer", date(2026, 10, 10), ["Arsenal", "Leeds United"]), c)
        self.assertIsNone(best)

    def test_sport_separation(self):
        c = [cand("1", "Soccer", "2026-10-10T14:00:00Z", ["Inter Miami", "Orlando City"])]
        best, _, _ = match.match_event(ev("Basketball", date(2026, 10, 10), ["Inter Miami", "Orlando City"]), c)
        self.assertIsNone(best)

    def test_start_time_window(self):
        c = [cand("1", "Esports", "2026-09-27T23:00:00Z", ["Procyon Gaming", "Galorys"])]
        best, _, _ = match.match_event(ev("Esports", date(2026, 9, 27), ["Procyon Gaming", "Galorys"], "2026-09-27T21:00:00+00:00"), c)
        self.assertIsNone(best)  # 2h apart: a different match (e.g. rematch / other bracket)


class Time(unittest.TestCase):
    def test_et_dates(self):
        self.assertEqual(match.et_date("2026-09-29T00:15:00Z"), date(2026, 9, 28))   # MNF crosses UTC midnight
        self.assertEqual(match.et_date("2026-09-28T03:30:00Z"), date(2026, 9, 27))
        self.assertEqual(match.et_date("2026-11-01T05:30:00Z"), date(2026, 11, 1))   # 01:30 EDT, DST ends 06:00Z
        self.assertEqual(match.et_date("2026-11-02T04:30:00Z"), date(2026, 11, 1))   # 23:30 EST


class Odds(unittest.TestCase):
    def test_nway(self):
        two = [odds.american_to_prob(x) for x in (-110, -110)]
        three = [odds.american_to_prob(x) for x in (-150, 290, 400)]
        field = [odds.decimal_to_prob(x) for x in (3, 5, 7, 9, 11, 13, 17, 21, 26, 34)]  # 10-way, overround 1.13
        for q in (two, three, field):
            for f in (odds.devig_mult, odds.devig_power, odds.devig_shin):
                self.assertAlmostEqual(sum(f(q)), 1, places=9)
        self.assertAlmostEqual(odds.devig_shin(two)[0], 0.5, places=9)
        self.assertAlmostEqual(sum(odds.devig_power([0.3, 0.3, 0.3])), 1, places=9)  # underround book
        d = odds.devig_all(field)
        self.assertGreater(d[-1]["hi"] - d[-1]["lo"], 0.001)  # longshot fair price depends on the method

    def test_fees(self):
        self.assertEqual(odds.kalshi_fee(0.5, 100, 1), 1.75)
        self.assertEqual(odds.kalshi_fee(0.5, 1, 1), 0.02)


class Rules(unittest.TestCase):
    SOCCER = "If Arsenal wins the Arsenal vs Leeds United professional EPL soccer game originally scheduled for Oct 10, 2026 after 90 minutes plus stoppage time. If the game is postponed but begins within 48 hours"
    TENNIS = "If Arthur Fils wins the match after a ball has been played. walkover, forfeiture ... within two weeks"
    NHL = "If Carolina wins the Florida vs Carolina NHL game. If the game is postponed but begins within 48 hours of its originally scheduled start time, the market will remain open and resolve based on the official final result."

    def test_verdicts(self):
        self.assertEqual(rules.equivalence("Soccer", self.SOCCER, 3, 3)[0], "NEAR_EQUIVALENT")
        self.assertEqual(rules.equivalence("Soccer", self.SOCCER, 3, 2)[0], "NOT_COMPARABLE")
        self.assertEqual(rules.equivalence("Tennis", self.TENNIS, 2, 2)[0], "MATERIAL_DIFFERENCE")
        self.assertEqual(rules.equivalence("Hockey", self.NHL, 2, 2)[0], "NEAR_EQUIVALENT")
        self.assertEqual(rules.equivalence("Table Tennis", self.TENNIS, 2, 2)[0], "NOT_COMPARABLE")

    def test_parser(self):
        self.assertFalse(rules.kalshi_profile(self.SOCCER, "Soccer")["extra_time"])
        self.assertEqual(rules.kalshi_profile("If the game ends in a tie, the market will resolve to $0.50 for each team", "Football")["tie"], "pays 0.50")


class HealthAndCloses(unittest.TestCase):
    def db(self):
        con = sqlite3.connect(":memory:")
        con.executescript(open("schema.sql").read())
        return con

    def test_stale_flag(self):
        con = self.db()
        con.execute("INSERT INTO collector_runs VALUES ('2026-09-28T10:00:00+00:00','NFL','pinnacle_prop',1,500,5,NULL,1)")
        h = status.health(con, datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(h[0]["state"], "STALE")

    def test_close_missing_without_coverage(self):
        con = self.db()
        for side, price in (("Over", 150), ("Under", -180)):
            con.execute("INSERT INTO snapshots VALUES ('2026-09-28T15:00:00+00:00','pinnacle_prop','NFL','1',"
                        "'2026-09-28T17:00:00Z',?, NULL,NULL,?,NULL,NULL,'{\"points\":1.5}')", (f"X Total Touchdown Passes|{side}", price))
        p, st, _ = ledger.prop_close(con, "X Total Touchdown Passes", "LESS", "2026-09-28T17:00:00Z")
        self.assertEqual(st, "MISSING")
        self.assertIsNone(p)
        con.execute("INSERT INTO collector_runs VALUES ('2026-09-28T16:55:00+00:00','NFL','pinnacle_prop',1,500,0,NULL,1)")
        p, st, _ = ledger.prop_close(con, "X Total Touchdown Passes", "LESS", "2026-09-28T17:00:00Z")
        self.assertEqual(st, "ok")
        self.assertGreater(p, 0.6)


class QualityAndMovement(unittest.TestCase):
    def test_grades(self):
        self.assertEqual(quality.grade_dim("pinnacle_limit", 2000), "A")
        self.assertEqual(quality.grade_dim("pinnacle_limit", 100), "C")
        self.assertEqual(quality.grade_dim("kalshi_spread", 0.10), "D")
        self.assertEqual(quality.grade_dim("kalshi_ask_size", None), "C")  # unknown never better than C
        self.assertIsNone(quality.REQUIRED_EDGE["D"])

    def test_follow_lead_none(self):
        pin = [("2026-09-28T10:00:00+00:00", 0.50), ("2026-09-28T10:02:00+00:00", 0.54)]
        follow = [("2026-09-28T09:58:00+00:00", 0.50), ("2026-09-28T10:08:00+00:00", 0.53)]
        lead = [("2026-09-28T09:58:00+00:00", 0.50), ("2026-09-28T10:01:00+00:00", 0.53)]
        none = [("2026-09-28T09:58:00+00:00", 0.50), ("2026-09-28T10:10:00+00:00", 0.50)]
        self.assertEqual(movement.lead_lag(pin, follow)[0]["result"], "KALSHI_FOLLOWED")
        self.assertEqual(movement.lead_lag(pin, follow)[0]["lag_min"], 6)
        self.assertEqual(movement.lead_lag(pin, lead)[0]["result"], "KALSHI_FIRST_OR_SAME_WINDOW")
        self.assertEqual(movement.lead_lag(pin, none)[0]["result"], "NO_FOLLOW")
        self.assertEqual(movement.lead_lag(pin, none[:1])[0]["result"], "UNOBSERVED (no Kalshi data after move)")

    def test_health_states(self):
        con = sqlite3.connect(":memory:")
        con.executescript(open("schema.sql").read())
        now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
        con.executemany("INSERT INTO collector_runs VALUES (?,?,?,?,?,?,?,?)", [
            ("2026-09-28T11:50:00+00:00", "A", "kalshi", 1, 5, 5, None, 1),     # 10m old, cadence 30 -> HEALTHY
            ("2026-09-28T10:00:00+00:00", "B", "kalshi", 1, 5, 5, None, 1),     # 120m > 3x30 -> STALE
            ("2026-09-28T11:59:00+00:00", "C", "kalshi", 0, None, None, "403", 1),  # last attempt failed
            ("2026-09-26T11:00:00+00:00", "D", "pinnacle", 1, 5, 5, None, 1)])  # nothing in 24h -> RETIRED
        st = {h["league"]: h["state"] for h in status.health(con, now)}
        self.assertEqual(st, {"A": "HEALTHY", "B": "STALE", "C": "FAILED", "D": "RETIRED"})


class AuditFixes(unittest.TestCase):
    def test_sgp_joint(self):
        self.assertAlmostEqual(sgp.joint(0.6, 0.6, "MORE", "MORE", 0.0), 0.36, places=3)
        self.assertGreater(sgp.joint(0.6, 0.6, "MORE", "MORE", 0.4), 0.36)
        self.assertLess(sgp.joint(0.6, 0.6, "MORE", "LESS", 0.4), 0.36)

    def test_simulator(self):
        base = simulate.simulate([dict(p=0.55, stake=10, payout=1.0, group=None)] * 100, n_paths=1000)
        corr = simulate.simulate([dict(p=0.55, stake=10, payout=1.0, group=i // 5) for i in range(100)], n_paths=1000)
        self.assertGreater(corr["dd_p95"], base["dd_p95"])

    def test_wilson(self):
        lo, hi = calibration.wilson(6, 10)
        self.assertLess(lo, 0.6)
        self.assertGreater(hi, 0.6)

    def test_burned_guard(self):
        with self.assertRaises(SystemExit):
            research_log.require_fresh("H9_NBA_PROPS", "nflverse_games", "2021", "2025")
        with self.assertRaises(SystemExit):
            research_log.require_fresh("NOT_REGISTERED", "espn_nba", "2026-03-01", "2026-06-14")
        research_log.require_fresh("H9_NBA_PROPS", "espn_nba", "2025-10-21", "2026-06-14")


class MarketOnly(unittest.TestCase):
    def a_o(self, edge=0.04, grade="B", need=0.03, size=500, stale=None, conf=100, eq="NEAR_EQUIVALENT", hours=5, kal_age_min=2):
        now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
        a = dict(conf=conf, eq=eq, hours=hours, kalshi_ts=(now.replace(minute=0) - (now - now.replace(minute=kal_age_min))).isoformat() if False
                 else datetime(2026, 9, 28, 11, 60 - kal_age_min, tzinfo=timezone.utc).isoformat())
        o = dict(fair=dict(lo=0.55, mult=0.56, hi=0.57), cost=0.50, ask=0.49, edge=edge, need=need, grade=grade, ask_size=size,
                 stale=stale or [], dim_grades=dict(pinnacle_limit="B", kalshi_spread="A", pin_range_6h="A"))
        return a, o, now

    def test_gate(self):
        a, o, now = self.a_o()
        self.assertTrue(market_only.gate(a, o, now)[1])
        for kw, check in ((dict(edge=0.02), "EDGE SURVIVES CONSERVATIVE"), (dict(grade="D", need=None), "MARKET QUALITY ACCEPTABLE"),
                          (dict(size=50), "LIQUIDITY"), (dict(conf=85), "EVENT MATCH VERIFIED"), (dict(eq="MATERIAL_DIFFERENCE"), "RULES VERIFIED"),
                          (dict(kal_age_min=9), "CURRENT PRICES"), (dict(hours=200), "TIMING"), (dict(stale=["x STALE"]), "COLLECTOR HEALTHY")):
            a, o, now = self.a_o(**kw)
            g, ok = market_only.gate(a, o, now)
            self.assertFalse(ok, kw)
            self.assertFalse(g[check], kw)

    def test_close_full_set_only_and_immutable(self):
        con = sqlite3.connect(":memory:")
        con.executescript(open("schema.sql").read())
        con.executescript(market_only.SCHEMA)
        ins = lambda ts, sel, p: con.execute("INSERT INTO snapshots VALUES (?, 'pinnacle','X','9','2026-09-28T12:00:00Z',?,NULL,NULL,?,NULL,NULL,'{}')", (ts, sel, p))
        ins("2026-09-28T11:00:00+00:00", "Home", -150); ins("2026-09-28T11:00:00+00:00", "Away", 400); ins("2026-09-28T11:00:00+00:00", "Draw", 290)
        ins("2026-09-28T11:50:00+00:00", "Home", -200)  # one leg of a later pull: evaluated with the other two carried forward
        ins("2026-09-28T12:05:00+00:00", "Home", -900)  # after start: must be ignored
        p, ts = market_only.close_fair(con, "9", "Home", "2026-09-28T12:00:00Z")
        self.assertEqual(ts, "2026-09-28T11:50:00+00:00")
        self.assertAlmostEqual(p, 0.6667 / (0.6667 + 0.2 + 0.2564), places=3)  # 11:50 pull; 11:00 would give 0.568
        con.execute("INSERT INTO mo_entries (ts,kind,strategy,versions,market_ticker,payload,sha256,edge) VALUES ('t','ENTRY','s','v','M','{}','h',0.05)")
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute("UPDATE mo_entries SET edge=0.9")
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("INSERT INTO mo_entries (ts,kind,strategy,versions,market_ticker,payload,sha256) VALUES ('t','ENTRY','s','v','M','{}','h')")


if __name__ == "__main__":
    unittest.main()
