"""DK NBA Classic optimiser: PG SG SF PF C G F UTIL, $50,000, 2+ teams, 2+ games.

The search itself is edge/dfs_opt_climb.py (shared with college basketball):
a randomised fill plus a hill climb whose candidate swaps are scored in one
vectorised percentile pass over the simulation. What is NBA here is the roster
and the objective. Basketball's one real complication -- players carry SEVERAL
positions ("PG/SG", "SF/PF", "PF/C") -- is handled there as a slot matching.
No stack shape: NBA residuals barely co-move (edge/dfs_nba_theory.py), so the
simulation has nothing for a stack rule to add.

assign_slots puts the LATEST tips in G / F / UTIL, so late swap has somewhere
to go when a late-game player is ruled out at 9:30; `locked` keeps players
whose games have started.
"""
from __future__ import annotations

from functools import partial

import numpy as np

from edge import dfs_nba_theory as theory, dfs_opt_climb as climb

SALARY_CAP = 50000
ROSTER_SIZE = 8
SLOTS = ("PG", "SG", "SF", "PF", "C", "G", "F", "UTIL")
SLOT_OK = {"PG": {"PG"}, "SG": {"SG"}, "SF": {"SF"}, "PF": {"PF"}, "C": {"C"},
           "G": {"PG", "SG"}, "F": {"SF", "PF"}, "UTIL": {"PG", "SG", "SF", "PF", "C"}}
NBA = climb.Roster("NBA", SLOTS, cap=SALARY_CAP, strict=("PG", "SG", "SF", "PF", "C"))


def eligible(dk_pos: str | None) -> set:
    """'PG/SG' -> {'PG', 'SG'}."""
    return {x for x in (dk_pos or "").replace(",", "/").split("/") if x in SLOT_OK["UTIL"]}


def slots_for(elig: set) -> set:
    """{'PG', 'SG'} -> every slot those positions fill: {'PG', 'SG', 'G', 'UTIL'}."""
    return {s for s, ok in SLOT_OK.items() if elig & ok}


def prepare(pool: list) -> None:
    """`elig` (DK positions) and `slots` (fillable slots) on every row."""
    for p in pool:
        p.setdefault("elig", eligible(p.get("dk_pos")))
        p["slots"] = slots_for(p["elig"])
    climb.prepare(pool, NBA)


def assign_slots(rows: list[dict]) -> list[tuple[str, dict]] | None:
    """A legal slot for every player (latest tips in the flex slots), or None."""
    for r in rows:
        r.setdefault("elig", eligible(r.get("dk_pos")))
        r.setdefault("slots", slots_for(r["elig"]))
    return climb.assign_slots(rows, NBA)


def valid(pool, idx) -> bool:
    return climb.valid(pool, idx, NBA)


def optimize(pool: list, sim: np.ndarray, mode: str = "cash", iters: int = 120, seed: int = 0,
             own_weight: float = 0.0, exclude: set | None = None,
             locked: set | None = None) -> dict | None:
    """Best legal lineup under one theory, or None."""
    prepare(pool)
    pct = theory.GPP_PCT if mode == "gpp" else theory.CASH_PCT
    best = climb.search(pool, sim, NBA, pct, key="ceil" if mode == "gpp" else "floor",
                        iters=iters, seed=seed,
                        own_weight=own_weight if mode == "gpp" else 0.0,
                        exclude=exclude, locked=locked)
    return None if best is None else climb.result(pool, sim, best, NBA, theory.describe)


def portfolio(pool: list, sim: np.ndarray, n: int, max_overlap: int = 5, iters: int = 60,
              seed: int = 0, **kw) -> list[dict]:
    """`n` different GPP lineups (edge/dfs_opt_climb.portfolio)."""
    return climb.portfolio(partial(optimize, pool, sim, "gpp", iters, **kw), n,
                           max_overlap=max_overlap, seed=seed)
