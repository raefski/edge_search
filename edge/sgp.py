"""Same-game-parlay correlation engine.

DK prices each leg's marginal sharply, then applies a correlation adjustment
when you combine them. The edge is where that adjustment is wrong. For two
binary legs A, B with marginal win-probs p, q and Pearson correlation phi:

    Cov(A,B) = phi * sqrt(p(1-p) * q(1-q))
    P(A and B) = p*q + Cov                      # the true joint
    fair_parlay_decimal = 1 / P(A and B)

Both inputs to the *check* are observable:
  * p, q  -> from the marginal prices (The Odds API, de-vigged)
  * DK's SGP combined price -> scraped from DK's parlay engine

So we can BACK OUT the correlation DK is implicitly pricing (`implied_phi`)
and compare it to the TRUE correlation we measure from historical results.
If DK's implied phi is below the true phi on a positively-correlated pair,
DK is under-correlating -> the SGP pays more than fair -> +EV.

Note on vig: a scraped DK SGP price still contains DK's hold, which inflates
their implied joint (and thus implied_phi) ABOVE their true belief. So a flag
where even DK's hold-inflated implied_phi < your measured true phi is a
*conservative* signal. Two-leg only here; n-leg needs a full joint / simulation.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

from .oddsmath import devig


def _denom(p: float, q: float) -> float:
    return math.sqrt(p * (1 - p) * q * (1 - q))


def phi_bounds(p: float, q: float) -> tuple[float, float]:
    """Feasible correlation range so the joint stays a valid probability."""
    d = _denom(p, q)
    if d == 0:
        return (0.0, 0.0)
    lo = (max(0.0, p + q - 1) - p * q) / d
    hi = (min(p, q) - p * q) / d
    return (lo, hi)


def joint_prob(p: float, q: float, phi: float) -> float:
    """P(A and B) for two binary legs given marginal probs and correlation."""
    return p * q + phi * _denom(p, q)


def fair_parlay_decimal(p: float, q: float, phi: float) -> float:
    j = joint_prob(p, q, phi)
    if j <= 0:
        raise ValueError("non-positive joint probability")
    return 1.0 / j


def implied_phi(dk_sgp_decimal: float, p: float, q: float) -> float:
    """The correlation DK's SGP price implies (vig-inclusive -> biased high)."""
    d = _denom(p, q)
    if d == 0:
        return 0.0
    dk_joint = 1.0 / dk_sgp_decimal
    return (dk_joint - p * q) / d


def sgp_edge(dk_sgp_decimal: float, p: float, q: float, phi_true: float) -> dict:
    """EV of taking DK's SGP at their price, given our true correlation estimate.

    edge = fair_prob * dk_decimal - 1   (>0 means +EV to bet it)
    """
    fair_prob = joint_prob(p, q, phi_true)
    return {
        "fair_prob": fair_prob,
        "fair_decimal": 1.0 / fair_prob if fair_prob > 0 else float("inf"),
        "dk_decimal": dk_sgp_decimal,
        "dk_implied_phi": implied_phi(dk_sgp_decimal, p, q),
        "phi_true": phi_true,
        "ev": fair_prob * dk_sgp_decimal - 1.0,
    }


# --- grader: scraped quote -> edge verdict -----------------------------------
#
# A quote is one scraped 2-leg DK SGP:
#   {"game_id":..., "dk_decimal": 3.5, "legs": [leg, leg]}
# where each leg carries DK's OWN singles prices (so we compare DK's parlay to
# DK's own marginals + the measured correlation -- isolating the correlation
# mispricing from any marginal disagreement):
#   {"type": "starter_outs_over", "taken_dec": 1.95, "opp_dec": 1.87, "line": 17.5, "desc": ...}

# leg-type pair -> the combo label used in data/phi_table.csv
_TYPE_COMBO = {
    frozenset({"starter_outs_over", "opp_total_under"}): "OutsOver + OppUnder",
    frozenset({"starter_ks_over", "opp_total_under"}): "KsOver  + OppUnder",
    frozenset({"starter_outs_over", "starter_ks_over"}): "OutsOver + KsOver",
}


def load_phi_table(path: str | Path) -> list[dict]:
    with Path(path).open() as f:
        return list(csv.DictReader(f))


def lookup_phi(table: list[dict], leg_types: set[str], opp_line: float | None = None) -> float | None:
    """True correlation for a leg-type pair, picking the row whose opponent
    total line is nearest `opp_line` (defaults to 4.5, the modal MLB total)."""
    label = _TYPE_COMBO.get(frozenset(leg_types))
    if not label:
        return None
    rows = [r for r in table if r["combo"].startswith(label)]
    if not rows:
        return None
    target = opp_line if opp_line is not None else 4.5

    def dist(r):
        try:
            return abs(float(r["opp_line"]) - target)
        except (ValueError, TypeError):
            return 0.0  # combos without an opp line (e.g. same-pitcher)

    return float(min(rows, key=dist)["phi_true"])


def grade_quote(quote: dict, table: list[dict], ev_threshold: float = 0.03) -> dict:
    """Devig DK's own singles for each leg, look up the true correlation, and
    return the SGP edge. `bet` is True only when it clears the EV bar AND DK is
    actually under-correlating (its implied phi below the measured true phi)."""
    legs = quote["legs"]
    if len(legs) != 2:
        return {"verdict": "unsupported", "reason": f"{len(legs)}-leg (engine is 2-leg)"}
    p = devig([legs[0]["taken_dec"], legs[0]["opp_dec"]])[0]
    q = devig([legs[1]["taken_dec"], legs[1]["opp_dec"]])[0]
    opp_line = next((l.get("line") for l in legs if l["type"] == "opp_total_under"), None)
    phi = lookup_phi(table, {l["type"] for l in legs}, opp_line)
    if phi is None:
        return {"verdict": "no_phi", "reason": "no correlation measured for this leg pair"}
    r = sgp_edge(quote["dk_decimal"], p, q, phi)
    r["verdict"] = "graded"
    r["bet"] = bool(r["ev"] >= ev_threshold and r["dk_implied_phi"] < phi)
    return r
