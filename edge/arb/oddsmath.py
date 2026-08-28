"""Odds conversion, vig removal, stake allocation and Kelly sizing.

Everything internally is decimal odds. American odds are an I/O format only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


# --------------------------------------------------------------------------
# conversions
# --------------------------------------------------------------------------
def american_to_decimal(american: float) -> float:
    if american is None:
        raise ValueError("american odds required")
    a = float(american)
    if a == 0:
        raise ValueError("american odds cannot be 0")
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def decimal_to_american(decimal: float) -> float:
    d = float(decimal)
    if d <= 1.0:
        raise ValueError("decimal odds must exceed 1.0")
    return round((d - 1.0) * 100.0) if d >= 2.0 else round(-100.0 / (d - 1.0))


def implied_prob(decimal: float) -> float:
    return 1.0 / float(decimal)


def prob_to_decimal(p: float) -> float:
    if not 0.0 < p < 1.0:
        raise ValueError("probability must be in (0, 1)")
    return 1.0 / p


def format_american(decimal: float) -> str:
    a = decimal_to_american(decimal)
    return f"+{a:.0f}" if a > 0 else f"{a:.0f}"


def net_of_commission(decimal: float, commission: float) -> float:
    """Effective odds on an exchange that rakes `commission` off net winnings."""
    if commission <= 0.0:
        return decimal
    return 1.0 + (decimal - 1.0) * (1.0 - commission)


# --------------------------------------------------------------------------
# vig removal -- turning a book's two-sided price into a fair probability
# --------------------------------------------------------------------------
def devig_multiplicative(probs: list[float]) -> list[float]:
    total = sum(probs)
    return [p / total for p in probs]


def devig_additive(probs: list[float]) -> list[float]:
    n = len(probs)
    excess = (sum(probs) - 1.0) / n
    out = [p - excess for p in probs]
    # additive can push a longshot negative; fall back rather than emit garbage
    if any(p <= 0.0 for p in out):
        return devig_multiplicative(probs)
    return out


def devig_power(probs: list[float], tol: float = 1e-10, max_iter: int = 200) -> list[float]:
    """Find k where sum(p_i ** k) == 1. Favours the favourite less than
    multiplicative does, which matches observed closing-line behaviour."""
    lo, hi = 0.05, 10.0
    for _ in range(max_iter):
        k = (lo + hi) / 2.0
        s = sum(p ** k for p in probs)
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = k
        else:
            hi = k
    return [p ** k for p in probs]


def devig_shin(probs: list[float], tol: float = 1e-10, max_iter: int = 200) -> list[float]:
    """Shin (1993): models the book's overround as protection against insider
    money. Best-regarded devig for two-way markets."""
    total = sum(probs)
    if len(probs) != 2 or total <= 1.0:
        return devig_multiplicative(probs)
    lo, hi = 0.0, 0.5

    def fair(z: float) -> list[float]:
        out = []
        for p in probs:
            disc = z * z + 4.0 * (1.0 - z) * (p * p) / total
            out.append((math.sqrt(max(disc, 0.0)) - z) / (2.0 * (1.0 - z)))
        return out

    for _ in range(max_iter):
        z = (lo + hi) / 2.0
        s = sum(fair(z))
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = z
        else:
            hi = z
    f = fair(z)
    return devig_multiplicative(f)


DEVIG_METHODS = {
    "multiplicative": devig_multiplicative,
    "additive": devig_additive,
    "power": devig_power,
    "shin": devig_shin,
}


def devig(probs: list[float], method: str = "power") -> list[float]:
    if method == "worst_case":
        # most conservative fair probability per leg across every method
        grids = [fn(list(probs)) for fn in DEVIG_METHODS.values()]
        return [min(g[i] for g in grids) for i in range(len(probs))]
    try:
        fn = DEVIG_METHODS[method]
    except KeyError:
        raise ValueError(f"unknown devig method {method!r}") from None
    return fn(list(probs))


def fair_probs_from_decimals(decimals: list[float], method: str = "power") -> list[float]:
    return devig([implied_prob(d) for d in decimals], method=method)


# --------------------------------------------------------------------------
# expected value / staking
# --------------------------------------------------------------------------
def ev_pct(decimal: float, fair_prob: float) -> float:
    """Expected return per unit staked, in percent."""
    return (fair_prob * float(decimal) - 1.0) * 100.0


def kelly_fraction(decimal: float, fair_prob: float) -> float:
    b = float(decimal) - 1.0
    if b <= 0:
        return 0.0
    f = (fair_prob * b - (1.0 - fair_prob)) / b
    return max(f, 0.0)


# --------------------------------------------------------------------------
# arbitrage allocation
# --------------------------------------------------------------------------
@dataclass
class Allocation:
    stakes: list[float]
    total: float
    payouts: list[float]          # gross return if that leg wins
    profits: list[float]          # payout - total staked
    worst_profit: float
    worst_profit_pct: float
    ideal_profit_pct: float       # before rounding / caps
    capped: bool                  # a per-book max stake bound the allocation


def arb_sum(decimals: list[float]) -> float:
    """sum(1/d). Below 1.0 means a guaranteed profit exists."""
    return sum(1.0 / float(d) for d in decimals)


def allocate(
    decimals: list[float],
    bankroll: float,
    round_to: float = 1.0,
    max_stakes: list[float] | None = None,
    anchor: int | None = None,
    anchor_stake: float | None = None,
) -> Allocation:
    """Split `bankroll` across mutually exclusive legs to equalise return.

    round_to    stake increment; rounding is what usually kills a thin arb, so
                the worst-case profit is always recomputed on rounded stakes.
    max_stakes  per-leg ceiling (book limits); the total shrinks to respect it.
    anchor      index of a leg already placed at `anchor_stake`; the rest are
                sized off it instead of off `bankroll`.
    """
    ds = [float(d) for d in decimals]
    s = arb_sum(ds)
    ideal_pct = (1.0 / s - 1.0) * 100.0

    if anchor is not None and anchor_stake is not None:
        total = anchor_stake * ds[anchor] * s
    else:
        total = float(bankroll)

    capped = False
    if max_stakes:
        for i, cap in enumerate(max_stakes):
            if cap is None or cap <= 0:
                continue
            want = total * (1.0 / ds[i]) / s
            if want > cap:
                total = min(total, cap * ds[i] * s)
                capped = True

    raw = [total * (1.0 / d) / s for d in ds]
    if round_to and round_to > 0:
        stakes = [round(x / round_to) * round_to for x in raw]
        if anchor is not None and anchor_stake is not None:
            stakes[anchor] = anchor_stake
        stakes = [max(x, round_to) for x in stakes]
    else:
        stakes = raw

    staked = sum(stakes)
    payouts = [stakes[i] * ds[i] for i in range(len(ds))]
    profits = [p - staked for p in payouts]
    worst = min(profits)
    return Allocation(
        stakes=[round(x, 2) for x in stakes],
        total=round(staked, 2),
        payouts=[round(p, 2) for p in payouts],
        profits=[round(p, 2) for p in profits],
        worst_profit=round(worst, 2),
        worst_profit_pct=round(worst / staked * 100.0, 4) if staked else 0.0,
        ideal_profit_pct=round(ideal_pct, 4),
        capped=capped,
    )
