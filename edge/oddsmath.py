"""Odds conversion and de-vigging.

The core job here is turning a book's two-sided price into a no-vig (fair)
probability. We implement three methods behind one interface so they can be
A/B tested:

  - multiplicative : split the overround proportionally (the standard default)
  - power          : raise raw implied probs to a common exponent k s.t. they sum to 1
  - shin           : Shin (1992) model that attributes some overround to insider
                     trading, which shrinks the favourite less than the dog

All methods take *raw implied probabilities* (1/decimal_odds) for the N
outcomes of one market from ONE book, and return fair probabilities summing
to 1. Props are two-way (Over/Under) but the implementations are N-way safe.
"""
from __future__ import annotations

import math
from typing import Sequence


# --- price conversions -------------------------------------------------------

def american_to_decimal(american: float) -> float:
    if american >= 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def decimal_to_american(dec: float) -> float:
    if dec >= 2.0:
        return round((dec - 1.0) * 100.0)
    return round(-100.0 / (dec - 1.0))


def implied_prob(decimal_odds: float) -> float:
    """Raw implied probability (still contains vig)."""
    return 1.0 / decimal_odds


# --- de-vig methods ----------------------------------------------------------

def _multiplicative(q: Sequence[float]) -> list[float]:
    s = sum(q)
    return [qi / s for qi in q]


def _power(q: Sequence[float]) -> list[float]:
    """Find exponent k>=1 such that sum(qi**k) == 1, then fair_i = qi**k.

    Raw implied probs sum to >1 (the overround) and each qi<1, so raising to
    a power k>1 shrinks the total; a unique k exists. Solved by bisection.
    """
    if abs(sum(q) - 1.0) < 1e-12:
        return list(q)

    def total(k: float) -> float:
        return sum(qi ** k for qi in q)

    lo, hi = 1.0, 1.0
    while total(hi) > 1.0:
        hi *= 2.0
        if hi > 1e6:
            break
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if total(mid) > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    fair = [qi ** k for qi in q]
    s = sum(fair)
    return [f / s for f in fair]  # tiny renormalisation for float safety


def _shin(q: Sequence[float]) -> list[float]:
    """Shin (1992) de-vig. Solves for the insider proportion z in [0, ~0.4)
    such that the recovered probabilities sum to 1.

        p_i = ( sqrt(z^2 + 4(1-z) * q_i^2 / Z) - z ) / (2(1-z)),  Z = sum(q)

    z=0 reduces to a sqrt-normalisation that over-sums; z grows until sum==1.
    """
    Z = sum(q)
    if abs(Z - 1.0) < 1e-12:
        return list(q)

    def probs(z: float) -> list[float]:
        out = []
        for qi in q:
            num = math.sqrt(z * z + 4.0 * (1.0 - z) * qi * qi / Z) - z
            out.append(num / (2.0 * (1.0 - z)))
        return out

    lo, hi = 0.0, 0.5
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if sum(probs(mid)) > 1.0:
            lo = mid
        else:
            hi = mid
    z = (lo + hi) / 2.0
    p = probs(z)
    s = sum(p)
    return [pi / s for pi in p]


_METHODS = {
    "multiplicative": _multiplicative,
    "power": _power,
    "shin": _shin,
}


def devig(decimal_odds: Sequence[float], method: str = "multiplicative") -> list[float]:
    """Return no-vig fair probabilities for the outcomes priced at the given
    decimal odds, using the named method."""
    if method not in _METHODS:
        raise ValueError(f"unknown devig method {method!r}; choose {list(_METHODS)}")
    if len(decimal_odds) < 2:
        raise ValueError("need at least two outcomes to de-vig")
    q = [implied_prob(d) for d in decimal_odds]
    return _METHODS[method](q)


def two_way_fair(over_dec: float, under_dec: float, method: str = "multiplicative") -> tuple[float, float]:
    """Convenience for the common Over/Under prop case."""
    p = devig([over_dec, under_dec], method=method)
    return p[0], p[1]
