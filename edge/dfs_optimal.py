"""Optimal %, leverage, boom / bust -- per player, from a build's own simulation.

WHAT OPTIMAL % IS
Each row of a simulation is one possible night. On that night there is exactly
one best legal lineup (the most DK points under the roster, the cap and the
games rule). A player's Optimal % is the share of simulated nights he is in it.
It is the GPP question asked directly -- "how often is this player part of the
winning build?" -- where projection and ceiling only ask it indirectly, and it
prices salary in: a $3,600 player who is in the optimal lineup 30% of the time is
worth more to a GPP than a $10,000 star at 25%.

LEVERAGE = Optimal % - projected ownership. Positive means the field is under the
player relative to how often he wins; that is the GPP edge (Stokastic's column,
2026-10-27, Adam). It is only as good as the ownership projection beside it.

HOW
Exact, not sampled: one 0/1 integer program per simulated night (scipy's HiGHS
MILP), variables x[player, slot] over the slots the player is eligible for:
every slot filled once, a player at most once, salary <= cap, players from at
least `min_games` games. ~150 players x 8 slots solves in a few ms; 500 nights
is the default because Optimal % to +-2 points needs ~500 draws for a 30%
player (binomial se 2.0) -- 2,000 halves that at 4x the time.

BOOM / BUST: P(points >= BOOM_X x salary/1000) and P(points < BUST_X x
salary/1000) from the same simulation; NBA value conventions (6x a "boom" game,
under 4x a bust). Per-sport multipliers can be passed in.
"""
from __future__ import annotations

import numpy as np

BOOM_X = 6.0
BUST_X = 4.0


def _program(salary: np.ndarray, cand: list[tuple[int, int]], n_slots: int, cap: float,
             games: np.ndarray, min_games: int):
    """The constraint matrix -- identical every simulated night, so built once."""
    from scipy.optimize import LinearConstraint
    from scipy.sparse import lil_matrix

    nv, n_p = len(cand), len(salary)
    g_ids = np.unique(games) if min_games > 1 else np.array([])
    ng = len(g_ids)
    A = lil_matrix((n_slots + n_p + 1 + (ng + 1 if ng else 0), nv + ng))
    lo = [1] * n_slots + [0] * n_p + [0]
    hi = [1] * n_slots + [1] * n_p + [cap]
    for j, (p, s) in enumerate(cand):
        A[s, j] = 1                      # every slot filled once
        A[n_slots + p, j] = 1            # a player at most once
        A[n_slots + n_p, j] = salary[p]  # the cap
    if ng:                               # y_g <= players from game g; sum y_g >= min_games
        base = n_slots + n_p + 1
        gidx = {g: k for k, g in enumerate(g_ids)}
        for j, (p, _) in enumerate(cand):
            A[base + gidx[games[p]], j] = -1
        for k in range(ng):
            A[base + k, nv + k] = 1
            A[base + ng, nv + k] = 1
        lo += [-np.inf] * ng + [min_games]
        hi += [0] * ng + [np.inf]
    return LinearConstraint(A.tocsr(), lo, hi), nv + ng


def _count(rows, salary, cand, n_slots, cap, games, min_games, n_pool):
    """(hits per player, nights solved) over these simulated nights."""
    from scipy.optimize import Bounds, milp
    cons, nvar = _program(salary, cand, n_slots, cap, games, min_games)
    who = np.array([p for p, _ in cand])
    integ, bounds = np.ones(nvar), Bounds(0, 1)
    hits, done = np.zeros(n_pool), 0
    for row in rows:
        c = np.zeros(nvar)
        c[:len(cand)] = -row[who]
        res = milp(c, constraints=cons, integrality=integ, bounds=bounds)
        if res.x is None:
            continue
        hits[np.unique(who[res.x[:len(cand)] > 0.5])] += 1
        done += 1
    return hits, done


def optimal_pct(pool: list, sim: np.ndarray, slots: tuple, slot_ok: dict, cap: float,
                n: int = 500, min_games: int = 2, elig_key: str = "elig",
                game_key: str = "game", workers: int | None = None) -> np.ndarray:
    """Share of the first `n` simulated nights (percent) each player is in that
    night's optimal lineup. `slot_ok[slot]` = the positions that slot accepts;
    `pool[i][elig_key]` = the player's positions."""
    # A player whose 99th-percentile night is below the 30th-best player's median
    # (deep bench, ~0-minute rows) is never optimal in practice; leaving him out
    # of the program cuts its size by about half. His Optimal % is 0.
    p99 = np.percentile(sim[:n], 99, axis=0)
    med = np.median(sim[:n], axis=0)
    bar = np.sort(med)[-30] if len(med) > 30 else -np.inf
    live = {i for i in range(len(pool)) if p99[i] >= bar}
    cand = [(i, s) for i, p in enumerate(pool) if i in live for s, slot in enumerate(slots)
            if set(p.get(elig_key) or ()) & set(slot_ok[slot])]
    salary = np.array([float(p.get("salary") or 0) for p in pool])
    games = np.array([str(p.get(game_key) or p.get("team") or i) for i, p in enumerate(pool)])
    import os
    rows = sim[:n]
    workers = workers or min(8, os.cpu_count() or 1)
    args = (salary, cand, len(slots), cap, games, min_games, len(pool))
    if workers <= 1 or len(rows) < 50:
        hits, done = _count(rows, *args)
    else:
        # Each night is its own program, so the nights split across processes
        # cleanly: ~130 ms a night (193 players, HiGHS) -> 500 nights in ~10 s on
        # 8 of the desktop's 20 cores instead of ~75 s serial.
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(workers) as ex:
            parts = list(ex.map(_count, np.array_split(rows, workers), *[[a] * workers for a in args]))
        hits = sum(h for h, _ in parts)
        done = sum(d for _, d in parts)
    return 100.0 * hits / max(1, done)


def annotate(pool: list, sim: np.ndarray, slots: tuple, slot_ok: dict, cap: float,
             n: int = 500, min_games: int = 2, boom_x: float = BOOM_X,
             bust_x: float = BUST_X, workers: int | None = None) -> None:
    """`optimal`, `opt_leverage` (optimal - own), `boom`, `bust` (percent) on every row."""
    opt = optimal_pct(pool, sim, slots, slot_ok, cap, n=n, min_games=min_games, workers=workers)
    for j, p in enumerate(pool):
        k = float(p.get("salary") or 0) / 1000.0
        col = sim[:, j]
        p["optimal"] = round(float(opt[j]), 1)
        p["opt_leverage"] = round(float(opt[j]) - float(p.get("own") or 0), 1)
        p["boom"] = round(100.0 * float((col >= boom_x * k).mean()), 1)
        p["bust"] = round(100.0 * float((col < bust_x * k).mean()), 1)
