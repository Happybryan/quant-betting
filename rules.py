"""RULES_v1.0 - settlement profiles per platform/sport and a MARKET_EQUIVALENCE verdict.
Kalshi profiles are PARSED from each contract's own rule text (authoritative per market).
Pinnacle profiles are quoted from https://www.pinnacle.com/en/future/betting-rules (fetched 2026-09-28);
anything not on that page is 'unverified' and can never produce EXACT."""
import re

PINNACLE_SOURCE = "pinnacle.com/en/future/betting-rules, fetched 2026-09-28"
PINNACLE = {  # sport -> profile (moneyline / match winner, full game)
    "Soccer": dict(periods="90min+stoppage", extra_time=False, penalties=False, tie="outcome (3-way)", postpone_h=12),
    "Hockey": dict(periods="incl OT+shootout", extra_time=True, tie="n/a", postpone_h=12),
    "Football": dict(periods="incl OT", extra_time=True, tie="unverified (2-way ML draw rule not on page)", postpone_h=12),
    "Basketball": dict(periods="incl OT", extra_time=True, tie="n/a", postpone_h=12),
    "Baseball": dict(periods="incl extra innings", extra_time=True, tie="n/a", postpone_h=12),
    "Tennis": dict(retirement="action if >=1 set completed, else void", walkover="void", postpone_h=12),
    "Esports": dict(forfeit="map voided -> match bets void", periods="full match", postpone_h=12),
}
SPORT_ALIASES = {"E Sports": "Esports", "Ice Hockey": "Hockey", "American Football": "Football"}


def kalshi_profile(text, sport):
    """Parse the rule text of one Kalshi contract into the same fields."""
    t = " ".join(text.split()).lower()
    p = {}
    if "90 minutes plus stoppage" in t:
        p.update(periods="90min+stoppage", extra_time=False, penalties=False)
    elif "official final result" in t or "wins the" in t and sport in ("Hockey", "Football", "Basketball", "Baseball"):
        p.update(periods="official final (incl OT)", extra_time=True)
    if "full match" in t:
        p["periods"] = "full match"
    m = re.search(r"tie, the market will resolve to \$?([\d.]+)", t)
    p["tie"] = f"pays {m.group(1)}" if m else ("outcome (3-way)" if "if tie is the result" in t else "n/a")
    if "after a ball has been played" in t:
        p["retirement"] = "settles on result once a ball is played"
        p["walkover"] = "fair price" if "walkover" in t else "unstated"
    if "forfeited before any play" in t:
        p["forfeit"] = "before play -> fair price; after start: see contract"
    m = re.search(r"within (\d+) hours", t)
    p["postpone_h"] = int(m.group(1)) if m else (336 if "within two weeks" in t else None)
    return p


def equivalence(sport, kalshi_rules_text, n_kalshi_outcomes, n_pin_outcomes):
    """Returns (verdict, [reasons]). Verdicts: EXACT, NEAR_EQUIVALENT, MATERIAL_DIFFERENCE, NOT_COMPARABLE."""
    sport = SPORT_ALIASES.get(sport, sport)
    pin = PINNACLE.get(sport)
    if not pin:
        return "NOT_COMPARABLE", [f"no verified Pinnacle rule profile for {sport}"]
    if n_kalshi_outcomes != n_pin_outcomes:
        return "NOT_COMPARABLE", [f"outcome structure differs: Kalshi {n_kalshi_outcomes}-way vs Pinnacle {n_pin_outcomes}-way"]
    k = kalshi_profile(kalshi_rules_text, sport)
    material, near = [], []
    if "extra_time" in pin and "extra_time" in k and pin["extra_time"] != k["extra_time"]:
        material.append(f"periods: Kalshi {k.get('periods')} vs Pinnacle {pin.get('periods')}")
    if sport == "Tennis":
        material.append(f"retirement: Kalshi '{k.get('retirement', 'unstated')}' vs Pinnacle '{pin['retirement']}' "
                        "(differ when a player retires in set 1; frequency unmeasured)")
    if sport == "Esports":
        near.append(f"forfeit/void handling: Kalshi '{k.get('forfeit', 'unstated')}' vs Pinnacle '{pin['forfeit']}' (rare)")
    if "unverified" in str(pin.get("tie", "")):
        near.append(f"ties: Kalshi {k.get('tie')} vs Pinnacle {pin['tie']} (NFL tie rate ~0.3%)")
    if k.get("postpone_h") != pin.get("postpone_h"):
        near.append(f"postponement window: Kalshi {str(k.get('postpone_h')) + 'h' if k.get('postpone_h') else 'unstated'} vs Pinnacle {pin['postpone_h']}h (rare; different void/fair-price handling)")
    if material:
        return "MATERIAL_DIFFERENCE", material + near
    return ("NEAR_EQUIVALENT", near) if near else ("EXACT", ["periods and outcome structure match"])
