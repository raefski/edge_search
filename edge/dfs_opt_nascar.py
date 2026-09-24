"""DK NASCAR Classic lineup optimizer: six drivers, $50,000, no positions.

Roster: D, D, D, D, D, D. Every driver is eligible for every slot, so there is
no slot-assignment problem at all and edge/dfs_roster.assign_slots -- the one
piece genuinely shared by the other three sports -- is not needed here. What
replaces it is a harder combinatorial problem with a much simpler feasibility
test: any six distinct drivers under the cap is a legal lineup.

WHY THE SEARCH IS DIFFERENT FROM THE OTHER THREE
The football and baseball optimisers hill-climb on `mean -/+ z*sd` computed
from a correlation matrix, because evaluating a lineup is arithmetic on eight
or ten numbers. Here a lineup is evaluated against a SIMULATION: its total is
the row-sum of six columns of an (n_sims x n_drivers) matrix, and its score is
a percentile of that. That is more expensive per candidate and far more
informative, because it prices the permutation constraint exactly -- six
drivers cannot all gain twenty positions, and no correlation matrix says so as
cleanly as a sampled race does.

The search is therefore a randomised fill plus a hill climb, like the others,
but with the candidate evaluation vectorised and the simulation computed ONCE
for the whole board rather than per candidate.

A BOARD IS SMALL ENOUGH THAT THE HEURISTIC IS EXACT IN PRACTICE, AND THAT WAS
CHECKED RATHER THAN ASSUMED. A Cup field is ~37 drivers, so there are
C(37,6) = 2,324,784 lineups before the salary cap removes most of them.
Measured on the live 2026-09-27 Kansas board, 1,500 simulated races:

    optimize(iters=250)            16s   cash floor 159.6   gpp ceil 283.6
    optimize_exhaustive(top_n=24)  70s   cash floor 159.6   gpp ceil 283.6

Identical lineups, four times faster. `--exhaustive` stays on the CLI for the
cases where a board is unusual enough to be worth the certainty, but it is not
the default because it does not currently buy anything.

WHAT IS DELIBERATELY NOT HERE
No stacking, no bring-back, no correlation rules. There is nothing to stack:
teammates in NASCAR share a manufacturer and sometimes a drafting partner, and
that effect is real at superspeedways but is NOT modelled (see
edge/nascar_sim.py's closing note). Inventing a stacking rule for it would be
asserting a number nobody measured.
"""
from __future__ import annotations

import itertools
import random

import numpy as np

from edge import dfs_nascar_theory as theory

SLOTS = ["D"] * 6
ROSTER_SIZE = 6
CAP = 50000


def _valid(idx, salaries) -> bool:
    return len(set(idx)) == ROSTER_SIZE and salaries[list(idx)].sum() <= CAP


def _fill(rng, order, salaries, n):
    """One randomised, salary-feasible lineup, greedy-biased toward `order`."""
    cheapest = salaries.min()
    for _ in range(60):
        picked: list[int] = []
        spent = 0
        pool = list(order)
        while len(picked) < ROSTER_SIZE and pool:
            left = ROSTER_SIZE - len(picked) - 1
            budget = CAP - spent - left * cheapest
            cands = [i for i in pool[:24] if salaries[i] <= budget]
            if not cands:
                cands = [i for i in pool if salaries[i] <= budget]
            if not cands:
                break
            pick = rng.choice(cands)
            picked.append(pick)
            spent += salaries[pick]
            pool.remove(pick)
        if len(picked) == ROSTER_SIZE and spent <= CAP:
            return tuple(sorted(picked))
    return None


def _objective(sim, idx, mode, owns, own_weight):
    own = float(owns[list(idx)].sum()) if own_weight else 0.0
    return theory.score(sim, idx, mode=mode, own=own, own_weight=own_weight)


def _hill_climb(sim, idx, salaries, owns, mode, own_weight, candidates):
    """Swap one driver at a time while the objective improves.

    EVERY CANDIDATE SWAP FOR A SLOT IS SCORED IN ONE VECTORISED PASS, and that
    is the difference between this running in seconds and running in minutes.
    Scoring candidates one at a time meant a `np.percentile` call per
    candidate -- six slots times ~28 candidates times several passes times a
    few hundred restarts, which measured 85 seconds for one slate and is far
    too slow to sit through on a phone before a green flag that does not allow
    late swap.

    The trick is that a swap only changes one term of the sum, so the totals
    for all candidates are

        (lineup_total - column[current]) [:, None] + sim[:, candidates]

    one (n_sims x n_candidates) array, whose percentiles come out of a single
    `np.percentile(..., axis=0)`.

    Candidates are restricted to the best drivers by their own simulated floor
    or ceiling, which is very nearly lossless on a 37-car board: the drivers
    this skips are ones no percentile objective was going to want.
    """
    q = theory.CASH_PCT if mode != "gpp" else theory.GPP_PCT
    idx = list(idx)
    cand_arr = np.array([c for c in candidates], dtype=int)
    cand_own = owns[cand_arr] if own_weight else None

    totals = sim[:, idx].sum(axis=1)
    best = float(np.percentile(totals, q))
    if own_weight and mode == "gpp":
        best -= own_weight * float(owns[idx].sum()) / 100.0

    improved = True
    while improved:
        improved = False
        for slot in range(ROSTER_SIZE):
            cur = idx[slot]
            rest = [i for i in idx if i != cur]
            others_salary = salaries[rest].sum()

            ok = (~np.isin(cand_arr, idx)) & (
                others_salary + salaries[cand_arr] <= CAP)
            if not ok.any():
                continue
            pick = cand_arr[ok]

            base = totals - sim[:, cur]
            trials = base[:, None] + sim[:, pick]
            scores = np.percentile(trials, q, axis=0)
            if own_weight and mode == "gpp":
                rest_own = float(owns[rest].sum())
                scores = scores - own_weight * (rest_own + cand_own[ok]) / 100.0

            j = int(np.argmax(scores))
            if scores[j] > best:
                idx[slot] = int(pick[j])
                totals = base + sim[:, idx[slot]]
                best = float(scores[j])
                improved = True
                break
    return tuple(sorted(idx)), best


def optimize(pool: list, sim: np.ndarray, mode: str = "cash", iters: int = 400,
             seed: int = 0, own_weight: float = 0.0,
             exclude: set | None = None) -> dict | None:
    """Best six-driver lineup under one of the two theories.

    `pool` is the driver board and `sim` is the (n_sims x len(pool)) matrix
    edge/nascar_sim.simulate produced for it -- the column order must match the
    pool order, which is why build_slate keeps them together.

    Returns {lineup, proj, sd, floor, ceil, p99, worst, salary, own} or None.
    None means no six drivers fit under the cap, which on a real board only
    happens if the pool has been filtered down too far.
    """
    usable = [i for i, d in enumerate(pool)
              if d.get("salary") and (not exclude or d["name"] not in exclude)]
    if len(usable) < ROSTER_SIZE:
        return None

    salaries = np.array([float(d.get("salary") or 0) for d in pool])
    owns = np.array([float(d.get("own") or 0.0) for d in pool])
    rng = random.Random(seed)

    # The fill's ranking must agree in SIGN with the objective, or the search
    # spends its time climbing back out of where the fill put it. Cash ranks on
    # each driver's own floor, GPP on his own ceiling.
    key = "floor" if mode != "gpp" else "ceil"
    order = sorted(usable, key=lambda i: -float(pool[i].get(key) or 0.0))
    candidates = order[:28]

    best, best_key = None, None
    for _ in range(iters):
        start = _fill(rng, order, salaries, len(pool))
        if not start or not _valid(start, salaries):
            continue
        idx, obj = _hill_climb(sim, start, salaries, owns, mode, own_weight,
                               candidates)
        if best_key is None or obj > best_key:
            best, best_key = idx, obj
    if best is None:
        return None
    return _result(pool, sim, best)


def optimize_exhaustive(pool: list, sim: np.ndarray, mode: str = "cash",
                        own_weight: float = 0.0, top_n: int = 24) -> dict | None:
    """Every combination of the `top_n` most plausible drivers. Exact, slower.

    C(24,6) is 134,596 lineups, which is a few seconds. The full C(37,6) is
    2.3M and would be minutes for a result the heuristic already reaches --
    `top_n` is the honest compromise, and it is a CAP ON THE BOARD rather than
    on the search, so what it can miss is a lineup containing a driver outside
    the top 24 by his own simulated floor or ceiling.
    """
    usable = [i for i, d in enumerate(pool) if d.get("salary")]
    if len(usable) < ROSTER_SIZE:
        return None
    salaries = np.array([float(d.get("salary") or 0) for d in pool])
    owns = np.array([float(d.get("own") or 0.0) for d in pool])
    key = "floor" if mode != "gpp" else "ceil"
    board = sorted(usable, key=lambda i: -float(pool[i].get(key) or 0.0))[:top_n]

    best, best_key = None, None
    for combo in itertools.combinations(board, ROSTER_SIZE):
        if salaries[list(combo)].sum() > CAP:
            continue
        obj = _objective(sim, combo, mode, owns, own_weight)
        if best_key is None or obj > best_key:
            best, best_key = combo, obj
    return _result(pool, sim, best) if best else None


def _result(pool: list, sim: np.ndarray, idx) -> dict:
    stats = theory.describe(sim, idx)
    drivers = [pool[i] for i in idx]
    drivers.sort(key=lambda d: -(d.get("proj") or 0))
    return {
        "lineup": drivers,
        "salary": int(sum(d.get("salary") or 0 for d in drivers)),
        "own": round(sum(d.get("own") or 0.0 for d in drivers), 1),
        "starts": [int(d.get("start") or 0) for d in drivers],
        **stats,
    }


def portfolio(pool: list, sim: np.ndarray, n: int, max_overlap: int = 3,
              mode: str = "gpp", iters: int = 300, seed: int = 0,
              **kw) -> list[dict]:
    """`n` lineups that are actually different from each other.

    `max_overlap` is 3 of 6 -- proportionally tighter than football's 6 of 9,
    because a NASCAR board is small and the same four value plays appear in
    every lineup otherwise. Diversity is enforced by banning the previous
    lineup's highest-projected driver as well as by the overlap cap: two
    lineups sharing a dominator are one bet on that dominator whatever their
    overlap count says.
    """
    out: list[dict] = []
    banned: set = set()
    for i in range(n * 5):
        if len(out) >= n:
            break
        res = optimize(pool, sim, mode=mode, iters=iters, seed=seed + i,
                       exclude=banned, **kw)
        if res is None:
            break
        names = {d["name"] for d in res["lineup"]}
        if any(len(names & prev) > max_overlap for prev in
               ({d["name"] for d in r["lineup"]} for r in out)):
            continue
        out.append(res)
        banned.add(res["lineup"][0]["name"])
    return out
