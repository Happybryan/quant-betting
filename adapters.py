"""ADAPTER_INTERFACE_v1.0 - the only place sport-specific logic may live.
The universal engine (registry, match, rules, odds, compare, verify gate, ledger, status) talks to sports ONLY through
this interface. An adapter implements what it genuinely can; everything else returns UNAVAILABLE, which routes the
sport to MARKET_ONLY. A weak model is never written just to fill a box."""
UNAVAILABLE = "UNAVAILABLE"


class SportAdapter:
    sport = None
    player_model = UNAVAILABLE      # e.g. "STAT_PASSTD_v0.2 (unvalidated)"

    def get_events(self, date): return UNAVAILABLE              # sport-native schedule (registry covers markets already)
    def get_participants(self, event): return UNAVAILABLE
    def get_injuries(self, event): return UNAVAILABLE           # [(name, position, status)]
    def get_lineups(self, event): return UNAVAILABLE            # confirmed starters / goalie / pitcher
    def get_context(self, event): return UNAVAILABLE            # weather, venue, rest, travel
    def get_historical_data(self): return UNAVAILABLE
    def build_features(self, event): return UNAVAILABLE
    def predict_event(self, event): return UNAVAILABLE          # outcome probabilities
    def predict_player_prop(self, player, stat, line): return UNAVAILABLE
    def estimate_distribution(self, player, stat): return UNAVAILABLE
    def validate_model(self): return UNAVAILABLE                # must return a backtest/calibration report

    def capabilities(self):
        names = ["get_injuries", "get_lineups", "get_context", "predict_event", "predict_player_prop", "validate_model"]
        return {n: getattr(type(self), n) is not getattr(SportAdapter, n) for n in names}


class NFLAdapter(SportAdapter):
    """Wraps the NFL logic that currently lives in verify.py (refactor target: move it here)."""
    sport = "Football"
    player_model = "STAT_PASSTD_v0.2 + ENV_PASSTD_v0.1 (pass TDs only, unvalidated)"

    def get_injuries(self, event):
        import verify
        return verify.get(f"{verify.ESPN}/summary?event={event}").get("injuries", [])

    def get_context(self, event):
        import verify
        return verify.get(f"{verify.ESPN}/summary?event={event}").get("gameInfo", {})

    def predict_player_prop(self, player, stat, line):
        import verify
        return verify.stat_model(player, stat, line) or UNAVAILABLE


class NBAAdapter(SportAdapter):
    """NBA via ESPN (stats.nba.com is unreachable from here). Model = NBA_PROP_v0.1 (nba_model.py, H9).
    Status of the model is whatever H9 concluded: see EXPERIMENTS.md; until then it is information-only."""
    sport = "Basketball"
    player_model = "NBA_PROP_v0.1 (H9 pending: information-only)"
    ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

    def _summary(self, event):
        import verify
        return verify.get(f"{self.ESPN}/summary?event={event}")

    def get_injuries(self, event):
        return [(i["athlete"]["displayName"], (i["athlete"].get("position") or {}).get("abbreviation"), i.get("status"))
                for t in self._summary(event).get("injuries", []) for i in t.get("injuries", [])]

    def get_context(self, event):
        s = self._summary(event)
        pk = next((p for p in s.get("pickcenter", []) if p.get("overUnder") is not None), {})
        return dict(total=pk.get("overUnder"), spread=pk.get("spread"), venue=(s.get("gameInfo", {}).get("venue") or {}).get("fullName"))

    def predict_player_prop(self, player, stat, line):
        """P(stat >= floor(line)+1) from the season replay up to today. Returns UNAVAILABLE for unknown players."""
        import json, math, zlib, nba_model as nm
        choice = json.loads(nm.CHOICE.read_text())["choice"] if nm.CHOICE.exists() else "C1"
        rows, games = nm.load()
        train = [r for r in rows if r["date"] < nm.TRAIN_END]
        w = nm.Walk({s: sum(r[s] for r in train) / sum(r["min"] for r in train) for s in nm.STATS})
        pid = None
        for r in rows:
            w.update(r)
            if r["player"] == player:
                pid, last = r["player_id"], r
        if not pid:
            return UNAVAILABLE
        p = w.predict(dict(last, date="9999"), games[last["game"]], "C1")  # next game; environment scaling needs the next game's line
        params, _ = nm.run(choice)
        k = int(math.floor(line)) + 1
        return nm.prob_ge(p, stat, k, params["disp"], zlib.crc32(f"{player}|{stat}|{line}".encode()))


ADAPTERS = {a.sport: a() for a in (NFLAdapter, NBAAdapter)}


def adapter(sport):
    """Every other sport gets the empty adapter -> MARKET_ONLY routing."""
    return ADAPTERS.get(sport) or type(f"{sport}Adapter", (SportAdapter,), {"sport": sport})()


if __name__ == "__main__":
    for s in ("Football", "Basketball", "Tennis", "Table Tennis", "Esports"):
        a = adapter(s)
        caps = a.capabilities()
        print(f"{s:13} player model: {a.player_model:60} routes to: {'MODEL+MARKET' if caps['predict_player_prop'] else 'MARKET_ONLY'}")
