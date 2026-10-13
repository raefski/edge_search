"""The two basketball game theories and the ownership prior, NBA and college.

Same shape as the NHL and NASCAR builds: a lineup is scored on a PERCENTILE of
its own simulated total (edge/nba_sim.py samples whole games, so the total is
correlated across players and slightly skewed by the bonuses).

    cash  =  the 25th percentile -- clear a cash line near the field median
    gpp   =  the 90th percentile, minus an optional ownership tilt

GPP_PCT IS 90, NOT HOCKEY'S 95. Basketball's correlation is weak: pace moves a
whole game a few percent and usage pulls teammates slightly apart, but nothing
pays three players at once the way a hockey goal does. With weak correlation
the joint tail sits close to the sum of individual tails, and chasing the 95th
just buys the single highest-variance player on the board. 90 is NASCAR's
value, for a sport whose correlation is similarly diffuse.

OWNERSHIP IS A PRIOR. No NBA or college contest export exists on this machine
(2026-10-13). The shape is what every other sport's first export taught: the
field prices off what it can SEE in the lobby -- value (projection per $1K,
which the public sites all publish) and salary -- and in basketball, more than
any sport, it piles onto the cheap player whose minutes just went up because a
starter was ruled out. VALUE_WEIGHT is set higher than the NHL's for that
reason. Fit it on the first export, cash and GPP SEPARATELY (CLAUDE.md: never
pooled), with scripts/nba_calibration.py --fit-ownership.
"""
from __future__ import annotations

import math

import numpy as np

CASH_PCT = 25.0
GPP_PCT = 90.0

#: Ownership softmax weights on z-scored value and salary, per primary
#: position group. UNFITTED PRIORS (2026-10-13), see the docstring.
VALUE_WEIGHT = 1.1
SALARY_WEIGHT = 0.45
Z_CLIP = 2.5
MAX_OWN = 70.0
#: Lineups' worth of ownership per primary position group. NBA: five strict
#: slots plus G (guards), F (forwards) and UTIL (anyone) -> 8 spread over
#: five groups with the guards and forwards picking up the flex slots.
NBA_GROUP_SLOTS = {"PG": 1.65, "SG": 1.65, "SF": 1.65, "PF": 1.65, "C": 1.4}
#: College: G G G F F F UTIL UTIL.
NCAAB_GROUP_SLOTS = {"G": 4.0, "F": 4.0}


def lineup_scores(sim: np.ndarray, idx) -> np.ndarray:
    return sim[:, list(idx)].sum(axis=1)


def score(sim: np.ndarray, idx, mode: str = "cash", own: float = 0.0,
          own_weight: float = 0.0) -> float:
    totals = lineup_scores(sim, idx)
    if mode == "gpp":
        return float(np.percentile(totals, GPP_PCT)) - own_weight * own / 100.0
    return float(np.percentile(totals, CASH_PCT))


def describe(sim: np.ndarray, idx) -> dict:
    totals = lineup_scores(sim, idx)
    return {"proj": round(float(totals.mean()), 1), "sd": round(float(totals.std()), 1),
            "floor": round(float(np.percentile(totals, CASH_PCT)), 1),
            "ceil": round(float(np.percentile(totals, GPP_PCT)), 1),
            "p99": round(float(np.percentile(totals, 99)), 1)}


def _z(xs):
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5 or 1.0
    return [max(-Z_CLIP, min(Z_CLIP, (x - m) / sd)) for x in xs]


def _normalise(weights, target, cap):
    owns, free = list(weights), [True] * len(weights)
    for _ in range(12):
        room = target - sum(o for o, f in zip(owns, free) if not f)
        total = sum(o for o, f in zip(owns, free) if f)
        if total <= 0 or room <= 0:
            break
        owns = [o * room / total if f else o for o, f in zip(owns, free)]
        over = [i for i, (o, f) in enumerate(zip(owns, free)) if f and o > cap]
        if not over:
            break
        for i in over:
            owns[i], free[i] = cap, False
    return owns


def add_ownership(pool: list, group_slots: dict | None = None,
                  weights: tuple | None = None, max_own: float | None = None,
                  key: str = "own") -> list:
    """Annotate `key` (percent) and `leverage` on every row. A row's group is
    its `group` field (its first-listed DK position, set by the run module)."""
    slots = group_slots or NBA_GROUP_SLOTS
    vw, sw = weights or (VALUE_WEIGHT, SALARY_WEIGHT)
    cap = MAX_OWN if max_own is None else max_own
    groups: dict = {}
    for p in pool:
        groups.setdefault(p.get("group"), []).append(p)
    for grp, players in groups.items():
        values = [p["proj"] / (p["salary"] / 1000.0) if p.get("salary") else 0.0 for p in players]
        zv = _z(values)
        zs = _z([float(p["salary"]) for p in players])
        w = [math.exp(vw * a + sw * b) for a, b in zip(zv, zs)]
        target = 100.0 * slots.get(grp, 1.0)
        for p, own in zip(players, _normalise(w, target, cap)):
            p[key] = round(own, 1)
        if key != "own":
            continue
        n = len(players)
        pr = {id(p): i for i, p in enumerate(sorted(players, key=lambda q: q["proj"]))}
        orr = {id(p): i for i, p in enumerate(sorted(players, key=lambda q: q["own"]))}
        for p in players:
            p["leverage"] = round(100.0 * (pr[id(p)] - orr[id(p)]) / max(1, n - 1), 1)
    return pool
