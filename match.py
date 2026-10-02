"""MATCH_v1.0 - cross-platform entity resolution with a confidence score.
Identity = sport + ET calendar date (+ start time when both sides state it) + ALL participants matched one-to-one.
Fuzzy similarity alone can never produce a deployable match: it is capped below the threshold.

Participant score:  100 exact after normalization | 95 alias table | 92 abbreviated form ("Chicago C" ~ "Chicago Cubs")
                    90 unique token-subset ("Leeds" ~ "Leeds United", "Fils" ~ "Arthur Fils") | <=75 fuzzy | 0 flag mismatch
Event confidence = min(participant scores); 0 if dates differ; capped at 60 if another candidate is within 5 points
(ambiguous) or a participant maps to two candidates. AUTO_THRESHOLD is provisional until test + live audit data exist."""
import difflib, re, unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
AUTO_THRESHOLD = 90
STOP = {"fc", "cf", "sc", "afc", "ac", "club", "the", "de", "esports", "gaming", "team", "jr", "sr", "cd", "sk", "fk", "bk"}
# identity flags: these must MATCH, never be stripped
FLAG_PATTERNS = {
    "women": r"\b(w|women|womens|ladies|fem|feminino|femenino)\b|\(w\)",
    "reserve": r"\b(b|ii|reserves?|u17|u18|u19|u20|u21|u23|castilla|atletic)\b",
    "academy": r"\b(academy|acad|youth|tq|challengers)\b",
    "doubles": r"/",
}
ALIASES = {  # sport -> canonical -> alternate forms. Sport-scoped: "Spurs" is Tottenham in soccer, not in the NBA.
    "Soccer": {
        "manchester united": {"man united", "man utd", "manchester utd"},
        "manchester city": {"man city"},
        "paris saint germain": {"psg", "paris sg", "paris st germain"},
        "inter milan": {"internazionale", "inter"},
        "tottenham hotspur": {"tottenham", "spurs"},
        "wolverhampton wanderers": {"wolves", "wolverhampton"},
        "nottingham forest": {"nottm forest"},
        "borussia monchengladbach": {"m gladbach", "mgladbach", "monchengladbach", "gladbach"},
        "koln": {"1 koln", "cologne"},
        "bayern munich": {"bayern munchen", "bayern"},
    },
    "Football": {  # renamed franchises
        "washington commanders": {"washington football team", "washington redskins"},
        "las vegas raiders": {"oakland raiders"},
    },
}


def _alias(sport):
    return {core(a): core(c) for c, al in ALIASES.get(sport, {}).items() for a in al} | {core(c): core(c) for c in ALIASES.get(sport, {})}


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("st.", "saint ").replace("-", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9/ ()]", " ", s)).strip()


def flags(s):
    n = norm(s)
    return {k for k, p in FLAG_PATTERNS.items() if re.search(p, n)}


def core(s):
    toks = [t for t in re.sub(r"[()]", " ", norm(s)).split() if t not in STOP]
    for name, pat in FLAG_PATTERNS.items():
        if name != "doubles":  # keep the "/" separator between partners
            toks = [t for t in toks if not re.fullmatch(pat.replace(r"\(w\)", "(w)"), t)]
    return " ".join(toks)


def participant_score(a, b, sport=None):
    """Score how likely names a (platform X) and b (platform Y) denote the same participant."""
    if flags(a) != flags(b):
        return 0
    if "doubles" in flags(a):  # every player of a pair must match
        pa, pb = [sorted(p.strip() for p in core(x).split("/")) for x in (a, b)]
        return min(participant_score(x, y, sport) for x, y in zip(pa, pb)) if len(pa) == len(pb) else 0
    ca, cb = core(a), core(b)
    if not ca or not cb:
        return 0
    if ca == cb:
        return 100
    al = _alias(sport)
    if al.get(ca, ca) == al.get(cb, cb):
        return 95
    if (ca in al or cb in al) and al.get(ca, ca) != al.get(cb, cb):
        return 40  # a known alias of a DIFFERENT entity ("inter" = Inter Milan, never Inter Miami)
    ta, tb = ca.split(), cb.split()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    # abbreviated trailing token: "chicago c" ~ "chicago cubs", "new york j" ~ "new york jets"
    if len(short) >= 2 and short[:-1] == long_[: len(short) - 1] and len(short[-1]) <= 3 and long_[len(short) - 1].startswith(short[-1]):
        return 92
    if set(short) <= set(long_) and len(" ".join(short)) >= 4:
        return 90
    return int(min(75, difflib.SequenceMatcher(None, ca, cb).ratio() * 75))


def et_date(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET).date()


def match_event(k, candidates):
    """k: dict(sport, date (ET date), participants [names], time_utc optional)
    candidates: list of dict(id, sport, start (UTC iso), participants [names]).
    Returns (best_candidate or None, confidence, reason)."""
    scored = []
    for c in candidates:
        if c["sport"] != k["sport"]:
            continue
        if et_date(c["start"]) != k["date"]:
            continue
        if k.get("time_utc") and abs((datetime.fromisoformat(c["start"].replace("Z", "+00:00")) -
                                      datetime.fromisoformat(k["time_utc"].replace("Z", "+00:00"))).total_seconds()) > 45 * 60:
            continue
        kp, cp = k["participants"], c["participants"]
        if len(kp) != len(cp):
            continue
        best = 0
        for order in (cp, cp[::-1]) if len(cp) == 2 else (cp,):
            best = max(best, min(participant_score(a, b, k["sport"]) for a, b in zip(kp, order)))
        if best:
            scored.append((best, c))
    if not scored:
        return None, 0, "no candidate on the same ET date with all participants matching"
    scored.sort(key=lambda x: -x[0])
    conf, c = scored[0]
    if conf < 50:  # only vague similarity to some other event: not a candidate at all
        return None, conf, "no plausible candidate (best is a different event by name similarity)"
    if len(scored) > 1 and scored[1][0] >= conf - 5 and scored[1][0] >= 60:
        return c, min(conf, 60), f"AMBIGUOUS: {len(scored)} candidates within 5 points"
    return c, conf, "ok" if conf >= AUTO_THRESHOLD else "below auto threshold: manual verification required"
