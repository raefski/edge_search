"""The NHL cash field, simulated: what a double-up field rosters, and the score it takes to cash.

The floor objective (dfs_nhl_theory.CASH_PCT) maximises a lineup's OWN 25th
percentile and never looks at the field. On a small slate the field is most of
the game: 75-88% of a 3-4 game double-up rosters the same top player
(NHL_STATUS.md 6b), so the cash line is roughly "how the chalk did", and a
lineup with a fine floor misses whenever the chalk hits.

So: sample the field from a CASH ownership model, score every field lineup in
every simulated game set, and take the line it must clear -- the (1 - PAY_FRAC)
quantile of the field in that simulation. A lineup is scored on the share of
simulations in which it clears that line, smoothed so the hill climb has a
gradient (dfs_opt_nhl.optimize(..., line=...)).
"""
from __future__ import annotations

import random

import numpy as np

from edge import dfs_nhl_theory as theory, dfs_opt_nhl, nhl

#: DK double-ups pay 27 of 62, 10 of 23, 17 of 39 (data/contest_meta.json).
PAY_FRAC = 0.44
#: Cash-field ownership (value, salary, PP1) and cap. Fitted (squared error) on
#: the eight graded NHL double-ups by scripts/nhl_cash_field.py; NHL_STATUS.md 6c.
CASH_WEIGHTS = (1.0, 0.5, 0.8)
CASH_MAX_OWN = 90.0
FIELD_SIZE = 300
#: Logistic width, in DK points, of the "clears the line" indicator.
SMOOTH = 2.0
#: Field lineups leave little salary unused.
MIN_SPEND = 48_000


def cash_ownership(pool: list, weights: tuple | None = None,
                   max_own: float | None = None) -> np.ndarray:
    """Cash-field ownership as a probability per pool row (also set as `cash_own`)."""
    theory.add_ownership(pool, weights=weights or CASH_WEIGHTS,
                         max_own=CASH_MAX_OWN if max_own is None else max_own, key="cash_own")
    return np.array([p["cash_own"] / 100.0 for p in pool])


def _draw(rng, pool, w) -> list | None:
    goalies = [i for i, p in enumerate(pool) if p["pos"] == "G" and w[i] > 0]
    if not goalies:
        return None
    g = rng.choices(goalies, weights=[w[i] for i in goalies])[0]
    opp = pool[g].get("opp")
    picked, spent = [g], pool[g]["salary"]
    slots = ["C", "C", "W", "W", "W", "D", "D", "U"]
    rng.shuffle(slots)
    for k, slot in enumerate(slots):
        budget = nhl.SALARY_CAP - spent - (len(slots) - k - 1) * 2500
        # The last pick must bring the lineup up to MIN_SPEND; drawn freely,
        # most field lineups left too much on the table and were thrown away.
        floor = MIN_SPEND - spent if k == len(slots) - 1 else 0
        cands = [i for i, p in enumerate(pool)
                 if p["pos"] != "G" and (slot == "U" or p["pos"] == slot) and i not in picked
                 and floor <= p["salary"] <= budget and p["team"] != opp and w[i] > 0]
        if not cands:
            return None
        j = rng.choices(cands, weights=[w[i] for i in cands])[0]
        picked.append(j)
        spent += pool[j]["salary"]
    if spent < MIN_SPEND or not dfs_opt_nhl._valid(pool, picked):
        return None
    return picked


def sample_field(pool: list, own: np.ndarray, n: int = FIELD_SIZE, seed: int = 0,
                 rounds: int = 2) -> np.ndarray:
    """(n, 9) legal lineups whose per-player rates track `own`.

    Drawing slot by slot in proportion to ownership does not land on the
    target rates (salary and position rules push them around), so the draw
    weights get ONE damped correction toward target / observed. More rounds
    compound: acceptance fell 48% -> 19% -> 4% -> 1% with no better fit.
    """
    rng = random.Random(seed)
    w = np.maximum(own, 1e-4)
    field = []
    for _ in range(rounds):
        field = []
        tries = 0
        while len(field) < n and tries < n * 60:
            tries += 1
            lu = _draw(rng, pool, w)
            if lu is not None:
                field.append(lu)
        if not field:
            raise ValueError("could not sample a legal field lineup")
        seen = np.bincount(np.array(field).ravel(), minlength=len(pool)) / len(field)
        w = w * np.clip(np.sqrt((own + 0.005) / (seen + 0.005)), 0.5, 2.0)
    return np.array(field)


def cash_line(sim: np.ndarray, field: np.ndarray, pay_frac: float = PAY_FRAC) -> np.ndarray:
    """Per simulation, the score a field lineup needs to cash."""
    totals = sim[:, field].sum(axis=2)
    return np.quantile(totals, 1.0 - pay_frac, axis=1)


def optimize_cash(pool: list, sim: np.ndarray, iters: int = 150, seed: int = 0,
                  weights: tuple | None = None, max_own: float | None = None,
                  field_size: int = FIELD_SIZE) -> dict | None:
    """The cash lineup most likely to clear the simulated field's cash line."""
    own = cash_ownership(pool, weights, max_own)
    field = sample_field(pool, own, n=field_size, seed=seed)
    line = cash_line(sim, field)
    res = dfs_opt_nhl.optimize(pool, sim, mode="cash", iters=iters, seed=seed, line=line)
    if res is not None:
        idx = [pool.index(r) for r in res["lineup"]]
        res["p_cash"] = round(float((sim[:, idx].sum(axis=1) >= line).mean()), 3)
        res["field_line"] = round(float(np.median(line)), 1)
    return res
