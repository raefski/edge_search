"""DK College Basketball Classic optimiser: G G G F F F UTIL UTIL, $50,000, 2+ games.

The search is edge/dfs_opt_climb.py (shared with the NBA); the objective is
college's theory (edge/dfs_ncaab_theory.py: cash = 25th percentile, GPP =
90th). Rows carry `slots` from edge/ncaab.slots_for. No stack rule: which game
to stack is the simulation's call (edge/ncaab_sim.py shares pace across both
teams, so a 90th-percentile objective walks into the highest total on its own).
"""
from __future__ import annotations

from functools import partial

import numpy as np

from edge import dfs_ncaab_theory as theory, dfs_opt_climb as climb

SALARY_CAP = 50000
NCAAB = climb.Roster("NCAAB", ("G", "G", "G", "F", "F", "F", "UTIL", "UTIL"), cap=SALARY_CAP,
                     min_teams=2, strict=("G", "F"))
ROSTER_SIZE = len(NCAAB.slots)


def prepare(pool: list) -> None:
    climb.prepare(pool, NCAAB)


def valid(pool, idx) -> bool:
    return climb.valid(pool, idx, NCAAB)


def assign_slots(rows: list[dict]) -> list[tuple[str, dict]] | None:
    return climb.assign_slots(rows, NCAAB)


def optimize(pool: list, sim: np.ndarray, mode: str = "cash", iters: int = 150, seed: int = 0,
             own_weight: float = 0.0, exclude: set | None = None,
             locked: set | None = None) -> dict | None:
    """Best legal lineup under one theory, or None if none exists."""
    pct = theory.GPP_PCT if mode == "gpp" else theory.CASH_PCT
    best = climb.search(pool, sim, NCAAB, pct, key="ceil" if mode == "gpp" else "floor",
                        iters=iters, seed=seed,
                        own_weight=own_weight if mode == "gpp" else 0.0,
                        exclude=exclude, locked=locked)
    return None if best is None else climb.result(pool, sim, best, NCAAB, theory.describe)


def portfolio(pool: list, sim: np.ndarray, n: int, max_overlap: int = 5, iters: int = 60,
              seed: int = 0, **kw) -> list[dict]:
    return climb.portfolio(partial(optimize, pool, sim, "gpp", iters, **kw), n,
                           max_overlap=max_overlap, seed=seed)
