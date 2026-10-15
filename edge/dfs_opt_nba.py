"""DK NBA Classic optimiser: PG SG SF PF C G F UTIL, $50,000, 2+ teams, 2+ games.

The NHL search (edge/dfs_opt_nhl.py) -- a randomised fill plus a hill climb whose
candidate swaps are scored in one vectorised percentile pass over the simulation
-- with basketball's one real complication: players carry SEVERAL positions
("PG/SG", "SF/PF", "PF/C"). A lineup is legal only if its eight players can be
matched to the eight slots at once, so validity is a small matching problem
(assign_slots), not a position count. No stack shape: NBA residuals barely co-move
(edge/dfs_nba_theory.py), so the simulation has nothing for a stack rule to add.
"""
from __future__ import annotations

import random

import numpy as np

from edge import dfs_nba_theory as theory

SALARY_CAP = 50000
ROSTER_SIZE = 8
SLOTS = ("PG", "SG", "SF", "PF", "C", "G", "F", "UTIL")
SLOT_OK = {"PG": {"PG"}, "SG": {"SG"}, "SF": {"SF"}, "PF": {"PF"}, "C": {"C"},
           "G": {"PG", "SG"}, "F": {"SF", "PF"}, "UTIL": {"PG", "SG", "SF", "PF", "C"}}
CANDIDATES = 70
MIN_SALARY = 3000


def eligible(dk_pos: str | None) -> set:
    """'PG/SG' -> {'PG', 'SG'}."""
    return {x for x in (dk_pos or "").replace(",", "/").split("/") if x in SLOT_OK["UTIL"]}


def assign_slots(rows: list[dict]) -> list[tuple[str, dict]] | None:
    """A legal slot for every player, or None. Tightest slots first, players with
    fewest options first, so the backtracking stays tiny."""
    order = sorted(range(len(rows)), key=lambda i: len(rows[i]["elig"]))
    taken: dict = {}

    def place(k):
        if k == len(order):
            return True
        i = order[k]
        for slot in sorted(SLOTS, key=lambda s: len(SLOT_OK[s])):
            if slot not in taken and rows[i]["elig"] & SLOT_OK[slot]:
                taken[slot] = i
                if place(k + 1):
                    return True
                del taken[slot]
        return False

    if len(rows) != ROSTER_SIZE or not place(0):
        return None
    return [(slot, rows[taken[slot]]) for slot in SLOTS]


def _valid(pool, idx) -> bool:
    if len(set(idx)) != ROSTER_SIZE:
        return False
    rows = [pool[i] for i in idx]
    if sum(r["salary"] for r in rows) > SALARY_CAP:
        return False
    if len({r["team"] for r in rows}) < 2 or len({r["game"] for r in rows}) < 2:
        return False
    return assign_slots(rows) is not None


def _fill(rng, pool, order):
    """A random legal lineup biased toward value at the top of the list."""
    for _ in range(300):
        picked, spent = [], 0
        for slot in rng.sample(SLOTS, len(SLOTS)):
            left = ROSTER_SIZE - len(picked) - 1
            budget = SALARY_CAP - spent - left * MIN_SALARY
            cands = [j for j in order[:45] if j not in picked and pool[j]["salary"] <= budget
                     and pool[j]["elig"] & SLOT_OK[slot]]
            if not cands:
                break
            j = rng.choice(cands[:20])
            picked.append(j)
            spent += pool[j]["salary"]
        if len(picked) == ROSTER_SIZE and _valid(pool, picked):
            return picked
    return None


def _hill_climb(pool, sim, idx, cand, mode, own_weight, owns):
    q = theory.GPP_PCT if mode == "gpp" else theory.CASH_PCT
    idx = list(idx)
    totals = sim[:, idx].sum(axis=1)

    def tilt(ids):
        return own_weight * float(owns[ids].sum()) / 100.0 if own_weight and mode == "gpp" else 0.0

    best = float(np.percentile(totals, q)) - tilt(idx)
    improved = True
    while improved:
        improved = False
        for s in range(ROSTER_SIZE):
            cur = idx[s]
            trial = [j for j in cand if j not in idx and _valid(pool, idx[:s] + [j] + idx[s + 1:])]
            if not trial:
                continue
            arr = np.array(trial)
            base = totals - sim[:, cur]
            scores = np.percentile(base[:, None] + sim[:, arr], q, axis=0)
            if own_weight and mode == "gpp":
                scores = scores - own_weight * (float(owns[idx].sum()) - owns[cur] + owns[arr]) / 100.0
            k = int(np.argmax(scores))
            if scores[k] > best + 1e-9:
                idx[s] = int(arr[k])
                totals = base + sim[:, idx[s]]
                best = float(scores[k])
                improved = True
    return idx, best


def optimize(pool: list, sim: np.ndarray, mode: str = "cash", iters: int = 120, seed: int = 0,
             own_weight: float = 0.0, exclude: set | None = None) -> dict | None:
    """Best legal lineup under one theory, or None."""
    rng = random.Random(seed)
    for p in pool:
        p.setdefault("elig", eligible(p.get("dk_pos")))
    key = "ceil" if mode == "gpp" else "floor"
    usable = [i for i, p in enumerate(pool) if p.get("salary") and p["elig"]
              and not (exclude and p["name"] in exclude)]
    order = sorted(usable, key=lambda i: -(pool[i].get(key) or 0) / pool[i]["salary"] * 1000
                   - 0.12 * (pool[i].get(key) or 0))
    top = sorted(usable, key=lambda i: -(pool[i].get(key) or 0))
    cand = sorted(set(order[:CANDIDATES] + top[:CANDIDATES // 2]))
    owns = np.array([float(p.get("own") or 0.0) for p in pool])
    best, best_v = None, None
    for _ in range(iters):
        start = _fill(rng, pool, order)
        if start is None:
            continue
        idx, v = _hill_climb(pool, sim, start, cand, mode, own_weight, owns)
        if best_v is None or v > best_v:
            best, best_v = idx, v
    if best is None:
        return None
    rows = [pool[i] for i in best]
    return {"slots": assign_slots(rows), "lineup": rows, "salary": int(sum(r["salary"] for r in rows)),
            "own": round(sum(r.get("own") or 0.0 for r in rows), 1), **theory.describe(sim, best)}
