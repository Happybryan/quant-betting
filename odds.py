"""Price math. Every number the system reports routes through here. Run `python3 odds.py` to self-check."""
import math


def american_to_prob(a):
    """Implied probability of an American price, vig included."""
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def prob_to_american(p):
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def devig_mult(probs):
    """Multiplicative (proportional) no-vig: scale so probabilities sum to 1."""
    s = sum(probs)
    return [p / s for p in probs]


def devig_power(probs):
    """Power no-vig: find k with sum(p_i^k) = 1. Shifts more vig onto longshots than multiplicative."""
    lo, hi = 0.2, 3.0  # k < 1 handles an underround book (sum < 1)
    for _ in range(80):
        k = (lo + hi) / 2
        lo, hi = (k, hi) if sum(p ** k for p in probs) > 1 else (lo, k)
    return [p ** k for p in probs]


def decimal_to_prob(d):
    return 1 / d


def fractional_to_prob(num, den):
    return den / (num + den)


def devig_shin(probs, iters=100):
    """Shin (1993) no-vig for N outcomes: solve for insider share z with sum(p_i) = 1.
    p_i = (sqrt(z^2 + 4(1-z) q_i^2 / S) - z) / (2(1-z)), q = implied probs, S = sum(q)."""
    S = sum(probs)
    f = lambda z: sum((math.sqrt(z * z + 4 * (1 - z) * q * q / S) - z) / (2 * (1 - z)) for q in probs) - 1
    lo, hi = 0.0, 0.4
    for _ in range(iters):
        z = (lo + hi) / 2
        lo, hi = (z, hi) if f(z) > 0 else (lo, z)
    return [(math.sqrt(z * z + 4 * (1 - z) * q * q / S) - z) / (2 * (1 - z)) for q in probs]


def devig_all(probs):
    """All three methods for N outcomes, plus the per-outcome spread across methods (how much the answer
    depends on the vig assumption). If the spread is bigger than an edge, the edge is an assumption, not a finding."""
    m, pw, sh = devig_mult(probs), devig_power(probs), devig_shin(probs)
    return [dict(mult=a, power=b, shin=c, lo=min(a, b, c), hi=max(a, b, c)) for a, b, c in zip(m, pw, sh)]


def devig_range(probs):
    """Point estimate (multiplicative) plus the spread across devig methods, per side.
    The method spread is a floor on the uncertainty of the fair price, not the whole thing."""
    m, pw = devig_mult(probs), devig_power(probs)
    return [(a, min(a, b), max(a, b)) for a, b in zip(m, pw)]


def kalshi_fee(price, contracts, multiplier, maker=False):
    """Kalshi quadratic fee in dollars, rounded UP to the cent per order.
    taker = ceil(0.07 * M * C * P * (1-P)); maker coefficient 0.0175.
    M comes from the series' fee_multiplier (API), never hardcoded (NFL 1.0, MLB 0.5 as of 2026-09-27)."""
    coef = 0.0175 if maker else 0.07
    raw = coef * multiplier * contracts * price * (1 - price)
    return math.ceil(round(raw * 100, 9)) / 100


def kalshi_ev(p, price, contracts, multiplier, maker=False):
    """EV in dollars of buying `contracts` YES at `price` when true win prob is p. Returns (ev, cost)."""
    cost = price * contracts + kalshi_fee(price, contracts, multiplier, maker)
    return p * contracts - cost, cost


def kelly_binary(p, cost_per_unit):
    """Full-Kelly fraction for a contract that costs c (fees included) and pays 1: f = (p - c) / (1 - c)."""
    return max(0.0, (p - cost_per_unit) / (1 - cost_per_unit))


def american_ev(p, a, stake=1.0):
    win = stake * (a / 100 if a > 0 else 100 / -a)
    return p * win - (1 - p) * stake


if __name__ == "__main__":
    assert abs(american_to_prob(-110) - 0.5238) < 1e-4 and abs(american_to_prob(150) - 0.4) < 1e-9
    fair = devig_mult([american_to_prob(-110), american_to_prob(-110)])
    assert abs(fair[0] - 0.5) < 1e-12
    pw = devig_power([american_to_prob(-300), american_to_prob(250)])
    assert abs(sum(pw) - 1) < 1e-9
    assert pw[0] > devig_mult([american_to_prob(-300), american_to_prob(250)])[0]  # power favours the favourite
    assert kalshi_fee(0.50, 100, 1) == 1.75 and kalshi_fee(0.50, 1, 1) == 0.02  # rounding punishes 1-lots
    assert kalshi_fee(0.50, 100, 0.5) == 0.88
    ev, cost = kalshi_ev(0.60, 0.55, 100, 1)
    assert abs(cost - 56.74) < 1e-9 and abs(ev - 3.26) < 1e-9
    assert kelly_binary(0.5, 0.5) == 0 and abs(kelly_binary(0.6, 0.5) - 0.2) < 1e-12
    # OddsJam video example: Blue Jays -155 with fair win prob 63.45% -> EV about +4.4% of stake
    assert abs(american_ev(0.6345, -155) - 0.0439) < 1e-3
    three = [american_to_prob(x) for x in (-150, 290, 400)]  # soccer home/draw/away
    for method in (devig_mult, devig_power, devig_shin):
        assert abs(sum(method(three)) - 1) < 1e-9
    d = devig_all(three)
    assert d[0]["lo"] <= d[0]["mult"] <= d[0]["hi"] and d[2]["power"] < d[2]["mult"]  # longshot shaded by power
    assert abs(decimal_to_prob(2.5) - 0.4) < 1e-12 and abs(fractional_to_prob(5, 2) - 2 / 7) < 1e-12
    print("odds.py ok")
