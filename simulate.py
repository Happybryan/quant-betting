"""Universal Monte Carlo: bankroll paths and drawdown for a list of positions, with correlation groups.
Positions sharing a group are driven by one Gaussian factor (Gaussian copula, correlation rho) so they win/lose together.
Used by GO_LIVE's MICRO-LIVE gate (simulated 95th-percentile max drawdown). Deterministic seed.
  simulate(positions, bankroll, n_paths) -> dict(p_ruin, dd_p50, dd_p95, final_p05, final_p50)"""
import math, random, statistics
from statistics import NormalDist

N = NormalDist()


def simulate(positions, bankroll=1000.0, n_paths=5000, rho=0.3, seed=42):
    """positions: [dict(p=true win prob, stake=$, payout=net $ won per $ staked, group=hashable or None)] in time order."""
    rnd = random.Random(seed)
    dds, finals, ruin = [], [], 0
    for _ in range(n_paths):
        bank = peak = bankroll
        dd = 0.0
        factors = {}
        for pos in positions:
            g = pos.get("group")
            if g is not None and g not in factors:
                factors[g] = rnd.gauss(0, 1)
            z = math.sqrt(rho) * factors[g] + math.sqrt(1 - rho) * rnd.gauss(0, 1) if g is not None else rnd.gauss(0, 1)
            win = z < N.inv_cdf(pos["p"])
            bank += pos["stake"] * pos["payout"] if win else -pos["stake"]
            peak = max(peak, bank)
            dd = max(dd, (peak - bank) / peak)
            if bank <= 0:
                ruin += 1
                break
        dds.append(dd)
        finals.append(bank)
    dds.sort(); finals.sort()
    q = lambda xs, a: xs[int(a * (len(xs) - 1))]
    return dict(p_ruin=ruin / n_paths, dd_p50=q(dds, .5), dd_p95=q(dds, .95), final_p05=q(finals, .05), final_p50=q(finals, .5))


if __name__ == "__main__":
    # sanity: fair coin at even money has median final ~ bankroll; a 55% edge at even money grows
    even = [dict(p=0.5, stake=10, payout=1.0, group=None)] * 200
    edge = [dict(p=0.55, stake=10, payout=1.0, group=None)] * 200
    corr = [dict(p=0.55, stake=10, payout=1.0, group=i // 4) for i in range(200)]
    a, b, c = simulate(even), simulate(edge), simulate(corr)
    assert abs(a["final_p50"] - 1000) < 60 and b["final_p50"] > a["final_p50"] + 100
    assert c["dd_p95"] > b["dd_p95"]  # correlation fattens the drawdown tail
    print("even", a, "\nedge", b, "\nedge+corr", c)
