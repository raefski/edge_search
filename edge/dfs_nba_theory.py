"""The two NBA game theories, the simulation, and the field model.

A player's DK points are drawn from a gamma with his projected mean and a spread
measured on 2025-26 (scripts/nba_projection_backtest.py's residuals, 29,311
player-games, 2026-10-15):

    sd = SD_A + SD_B x projection        5.9 + 0.20 x proj
         (bands: proj 0-10 sd 6.9 | 10-20 9.9 | 20-30 11.4 | 30-45 12.7 | 45+ 14.8)

INDEPENDENT DRAWS, ON PURPOSE. Once tonight's minutes are in the projection, what
is left barely co-moves: residual correlation -0.037 between teammates (173,519
pairs) and +0.001 between opponents (185,814). Hockey's goal pays a whole line
at once; basketball's points are shared out of a fixed pool of possessions. So
there is no team-stack rule here, and the GPP percentile does not have to reach
as far out as hockey's to see a correlated tail. The gamma keeps the upside the
data shows (8.0% of games land 1.5 sd above the projection vs 6.7% for a normal).

    cash  = the CASH_PCT percentile of the lineup's simulated total
    gpp   = the GPP_PCT percentile, minus an optional ownership tilt

THE FIELD MODEL IS A PRIOR: a softmax over value and salary per position, plus a
bump for a newly promoted starter (the field chases late news too). Twelve NBA
GPP exports from 2024-25 sit in data/contests.db; fit it on them (GPP_META.md: NBA
fields rewarded chalk on all 10 slates measured).
"""
from __future__ import annotations

import math

import numpy as np

CASH_PCT = 30.0
GPP_PCT = 90.0
SD_A, SD_B = 5.91, 0.202

#: Ownership prior (UNFITTED -- see the docstring).
VALUE_WEIGHT = 1.0
SALARY_WEIGHT = 0.4
PROMOTED_BONUS = 0.6
Z_CLIP = 2.5
MAX_OWN = 75.0
#: Lineups' worth of ownership per primary position (8 slots: 5 + G, F, UTIL).
GROUP_SLOTS = {"PG": 1.7, "SG": 1.5, "SF": 1.5, "PF": 1.6, "C": 1.7}


def sd_for(proj: float) -> float:
    return SD_A + SD_B * max(0.0, proj)


def simulate(pool: list, n_sims: int = 3000, seed: int = 0) -> np.ndarray:
    """(n_sims, len(pool)) DK points, each player a gamma with his mean and sd."""
    rng = np.random.default_rng(seed)
    out = np.zeros((n_sims, len(pool)))
    for j, p in enumerate(pool):
        mu = max(0.1, float(p["proj"]))
        sd = sd_for(mu)
        shape, scale = (mu / sd) ** 2, sd * sd / mu
        out[:, j] = rng.gamma(shape, scale, n_sims)
    return out


def summarise(sim: np.ndarray, pool: list) -> list:
    for j, p in enumerate(pool):
        col = sim[:, j]
        p["sd"] = round(float(col.std()), 2)
        p["floor"] = round(float(np.percentile(col, CASH_PCT)), 2)
        p["ceil"] = round(float(np.percentile(col, GPP_PCT)), 2)
    return pool


def lineup_scores(sim: np.ndarray, idx) -> np.ndarray:
    return sim[:, list(idx)].sum(axis=1)


def describe(sim: np.ndarray, idx) -> dict:
    t = lineup_scores(sim, idx)
    return {"proj": round(float(t.mean()), 1), "sd": round(float(t.std()), 1),
            "floor": round(float(np.percentile(t, CASH_PCT)), 1),
            "ceil": round(float(np.percentile(t, GPP_PCT)), 1),
            "p99": round(float(np.percentile(t, 99)), 1)}


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


def add_ownership(pool: list) -> list:
    """Annotate `own` (percent) and `leverage` on every row, per primary position."""
    groups: dict = {}
    for p in pool:
        groups.setdefault(p["pos"], []).append(p)
    for pos, players in groups.items():
        zv = _z([p["proj"] / (p["salary"] / 1000.0) if p.get("salary") else 0.0 for p in players])
        zs = _z([float(p["salary"]) for p in players])
        w = [math.exp(VALUE_WEIGHT * a + SALARY_WEIGHT * b + (PROMOTED_BONUS if p.get("promoted") else 0))
             for p, a, b in zip(players, zv, zs)]
        for p, own in zip(players, _normalise(w, 100.0 * GROUP_SLOTS.get(pos, 1.6), MAX_OWN)):
            p["own"] = round(own, 1)
        n = len(players)
        pr = {id(p): i for i, p in enumerate(sorted(players, key=lambda q: q["proj"]))}
        orr = {id(p): i for i, p in enumerate(sorted(players, key=lambda q: q["own"]))}
        for p in players:
            p["leverage"] = round(100.0 * (pr[id(p)] - orr[id(p)]) / max(1, n - 1), 1)
    return pool
