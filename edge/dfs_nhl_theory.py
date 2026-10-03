"""The two NHL game theories, and the field model.

Same shape as the NASCAR build (edge/dfs_nascar_theory.py): a lineup is scored
on a PERCENTILE of its own simulated total, because edge/nhl_sim.py samples
whole games and the lineup total is neither symmetric nor independent across
players.

    cash  =  the 25th percentile -- clear a cash line near the field median
    gpp   =  the 95th percentile, minus an optional ownership tilt

GPP_PCT IS 95, NOT NASCAR'S 90, and the reason is hockey's correlation. A goal
pays a scorer and up to two linemates at once, so a line stack's upside is a
JOINT tail: all three score in the same games or none do. At the 90th
percentile a stack and three unrelated skaters of the same means look alike;
the difference appears further out, which is where a tournament is won. The
95th is far enough out to reward correlation and not so far that it just
chases the single highest-variance player.

THE FIELD MODEL IS A PRIOR. No NHL contest export exists yet. The shape follows
what the NASCAR export taught (the field prices off what it can see in the
lobby -- salary -- as much as off value), plus the one thing every NHL DFS
guide agrees the field chases: first power-play unit skaters. Fit it with
scripts/nhl_calibration.py --fit-ownership on the first export.
"""
from __future__ import annotations

import math

import numpy as np

CASH_PCT = 25.0
GPP_PCT = 95.0

#: Ownership: softmax over value (proj per $1K), salary, PP1, and buzz, per position
#: group, normalised to the group's share of the nine slots. VALUE/SALARY/PP1 are UNFITTED,
#: BUZZ is unfitted and experimental (collected 9/29, fit pending 4-6 contests).
VALUE_WEIGHT = 0.8
SALARY_WEIGHT = 0.5
PP1_BONUS = 0.4
BUZZ_GAMMA = 0.0
BUZZ_BETA = 0.0
Z_CLIP = 2.5
MAX_OWN = 60.0
#: Lineups' worth of ownership per position group (UTIL mostly goes to wings
#: and centres). Sums to 9.
GROUP_SLOTS = {"C": 2.4, "W": 3.45, "D": 2.15, "G": 1.0}


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


def add_ownership(pool: list, buzz_gamma: float | None = None, buzz_beta: float | None = None) -> list:
    """Annotate `own` (percent) and `leverage` on every row.

    buzz_gamma/buzz_beta override BUZZ_GAMMA/BUZZ_BETA if provided (for testing).
    """
    bg = buzz_gamma if buzz_gamma is not None else BUZZ_GAMMA
    bb = buzz_beta if buzz_beta is not None else BUZZ_BETA
    groups: dict = {}
    for p in pool:
        groups.setdefault(p["pos"], []).append(p)
    for pos, players in groups.items():
        values = [p["proj"] / (p["salary"] / 1000.0) if p.get("salary") else 0.0 for p in players]
        zv = _z(values)
        zs = _z([float(p["salary"]) for p in players])
        buzz_vals = [math.log1p(p.get("buzz", 0)) for p in players]
        zb = _z(buzz_vals)
        weights = [math.exp(VALUE_WEIGHT * zv_i + SALARY_WEIGHT * zs_i
                            + (PP1_BONUS if p.get("pp") == "PP1" else 0.0)
                            + (bg * zb_i if bg else 0.0))
                   for p, zv_i, zs_i, zb_i in zip(players, zv, zs, zb)]
        for p, own in zip(players, _normalise(weights, 100.0 * GROUP_SLOTS.get(pos, 1.0), MAX_OWN)):
            p["own"] = round(own, 1)
        n = len(players)
        pr = {id(p): i for i, p in enumerate(sorted(players, key=lambda q: q["proj"]))}
        orr = {id(p): i for i, p in enumerate(sorted(players, key=lambda q: q["own"]))}
        for p in players:
            p["leverage"] = round(100.0 * (pr[id(p)] - orr[id(p)]) / max(1, n - 1), 1)
    return pool
