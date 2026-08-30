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


def boosted(decimal: float, pct: float) -> float:
    """Odds after a sportsbook profit boost.

    A profit boost multiplies the NET winnings, not the total return. A 50%
    boost on +200 (decimal 3.0) pays 300 profit per 100 staked instead of 200,
    so the effective price is 4.0 -- not 4.5, which is what multiplying the
    decimal would give and what would overstate every boosted arb.

    Mathematically this is `net_of_commission` with the sign flipped: a boost
    is a negative rake.
    """
    if pct <= 0.0:
        return float(decimal)
    return 1.0 + (float(decimal) - 1.0) * (1.0 + float(pct))


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


def at_book_price(decimal: float) -> float:
    """Snap a computed price to one a book can actually quote.

    Books deal in whole American odds, and a boosted bet is written at the
    rounded price rather than the exact one -- so the rounded price is what
    settles. A -110 leg boosted 25% is exactly 2.136364, i.e. +113.636;
    FanDuel writes it +114 and pays 21.40 on a 10 stake, not 21.36.

    Four cents, but it is the difference between a number that matches the
    slip and one that does not, and quoting +114 while paying 2.136364 is
    simply inconsistent with itself.

    Rounding is to NEAREST, so this can go either way -- it is not a hidden
    bonus. Prices at or below evens are returned untouched, since American
    odds cannot express them.
    """
    d = float(decimal)
    if d <= 1.0:
        return d
    try:
        return american_to_decimal(decimal_to_american(d))
    except ValueError:
        return d


def parlay_decimal(decimals: list[float]) -> float:
    """Price of a straight multi: the product of its legs.

    True for a STRAIGHT parlay across independent events, which is the only
    kind this code will price. A same-game parlay is not the product -- the
    book re-prices correlated legs, usually well below it -- so anything built
    on this must keep its legs in different events.
    """
    out = 1.0
    for d in decimals:
        out *= float(d)
    return out


def parlay_hedge_sum(parlay: float, opposites: list[float]) -> float:
    """The arbitrage test for a hedged parlay: below 1.0 means locked profit.

    Back a parlay of N legs, then bet the OPPOSITE of each leg as a single.
    The parlay pays only if every leg wins; if any leg loses, that leg's
    opposing single pays. Where two or more lose, several pay at once, which
    only helps -- so the binding cases are "all win" and "exactly one loses",
    N+1 outcomes that behave exactly like N+1 mutually exclusive legs:

        1/parlay + sum(1/opposite_i) < 1

    which is `arb_sum` over the parlay and the opposites. Nothing new.

    Without a boost this can never hold. For two fair legs it works out to
    1 + (1-p)(1-q), which exceeds 1 for any p, q < 1 -- the parlay's own
    compounding vig is exactly what makes it unhedgeable. A profit boost is
    what can push it under, and only on short-priced legs, because a favourite
    has a long opposite and so a cheap hedge.
    """
    return arb_sum([parlay] + list(opposites))


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
