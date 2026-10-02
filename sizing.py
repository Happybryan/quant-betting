"""SIZING_v1.0 - stake sizing + exposure caps. Real money is ZERO for any strategy that has not passed its go-live rule;
the paper size is still computed so the paper P/L is realistic.
  kalshi:     f* = (p - c) / (1 - c)     p = CONSERVATIVE fair, c = all-in cost per contract
  prizepicks: f* = (p*X - 1) / (X - 1)   p = conservative joint probability, X = displayed payout
  stake = bankroll * min(KELLY_FRACTION * f*, PER_POSITION_CAP), then event / total exposure caps."""
from odds import kelly_binary

KELLY_FRACTION = 0.25        # quarter Kelly: our probabilities are estimates, not truths
PER_POSITION_CAP = 0.02      # never more than 2% of bankroll on one position
PER_EVENT_CAP = 0.03         # correlated positions on the same event share this
TOTAL_OPEN_CAP = 0.10        # all open positions together
GO_LIVE = {"MARKET_ONLY_v1": False, "PP_PROP_v0.2": False}   # flipped only by a passed go-live rule, never by hand


def kelly_prizepicks(p_joint, payout):
    return max(0.0, (p_joint * payout - 1) / (payout - 1))


def stake(strategy, bankroll, f_star, event_exposure=0.0, open_exposure=0.0):
    """Returns (real_stake, paper_stake, reason)."""
    s = bankroll * min(KELLY_FRACTION * f_star, PER_POSITION_CAP)
    s = max(0.0, min(s, bankroll * PER_EVENT_CAP - event_exposure, bankroll * TOTAL_OPEN_CAP - open_exposure))
    if not GO_LIVE.get(strategy):
        return 0.0, round(s, 2), f"{strategy} has not passed its go-live rule: paper only"
    return round(s, 2), round(s, 2), "within caps"


if __name__ == "__main__":
    assert kelly_binary(0.60, 0.55) > 0 and kelly_prizepicks(1 / 3, 3) == 0
    r, p, why = stake("MARKET_ONLY_v1", 1000, kelly_binary(0.60, 0.55))
    assert r == 0 and p == 20.0, (r, p)  # quarter Kelly = 2.8% -> capped at 2%
    r, p, _ = stake("PP_PROP_v0.2", 1000, kelly_prizepicks(0.43, 3), event_exposure=25)
    assert p == 5.0, p  # event cap 3% = 30 minus 25 already exposed
    print("sizing.py ok")
