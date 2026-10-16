"""The simulation-percentile lineup search, once: a randomised fill plus a hill
climb whose candidate swaps are scored in one vectorised percentile pass.

WHO USES IT
    edge/dfs_opt_nba.py     DK NBA Classic   PG SG SF PF C G F UTIL
    edge/dfs_opt_ncaab.py   DK CBB Classic   G G G F F F UTIL UTIL
Each of those is a Roster and a theory (which percentile is the objective);
everything else -- legality, the fill, the climb, late-swap locks, the GPP
portfolio -- is this module. It began as the NHL search (edge/dfs_opt_nhl.py),
was generalised for basketball by the 2026-10-13 web session, and was lifted
here on 2026-10-16 so the two basketball builds stopped carrying two copies
of the same climb (the audit counted six `_hill_climb`s across the optimisers,
mean pairwise similarity 0.26: the same algorithm drifting apart per sport).

LEGALITY IS A MATCHING, NOT A COUNT
A "PG/SG" can fill PG, SG, G or UTIL, so "is this lineup legal" is a bipartite
matching of players to slots. Each row carries `slots` (the slot NAMES it may
fill); prepare() turns that into a bitmask over the roster's slot positions,
and the matching is cached on the sorted multiset of masks -- a board has only
a dozen distinct masks, so the cache is hit almost every time.

THE OBJECTIVE IS A PERCENTILE OF THE LINEUP'S SIMULATED TOTAL
`pct` is passed in (cash ~25-30th, GPP ~90th: each sport's theory module says
which and why), minus `own_weight` x the lineup's summed ownership / 100 in a
GPP. `sim` is (n_sims, len(pool)), one column per pool row.

`extra_valid(rows) -> bool` is a sport's own rule on top of the roster; None
for the basketball rosters, which have no stack rule.

WHO IS NOT ON IT, AND WHY (2026-10-16)
  NHL      tried and measured, not shipped. The NHL search (edge/dfs_opt_nhl.py)
           fills goalie-first, never pairs a goalie with skaters facing him,
           and draws candidates per position. On four real slates (10/16-17,
           scored on a fresh simulation) this engine with hockey's rules
           matched it on 7 of 12 lineups and lost on the rest (GPP -1.1 and
           -3.8, floor -1.6), even with deeper candidate lists.
  MLB, NFL, NCAAF   a different objective -- mean +/- z x sd from a correlation
           matrix, with batting-order / QB stack rules (each module argues it).
  NASCAR   the same percentile climb over six position-less slots, plus an
           exhaustive mode; not moved without the real-slate check NHL got.
  MMA      exhaustive: it scores every legal lineup, there is no climb.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable

import numpy as np


@dataclass(frozen=True)
class Roster:
    name: str
    slots: tuple
    cap: int = 50000
    min_teams: int = 2
    min_games: int = 2
    #: Per-slot candidate list length for the random fill.
    fill_depth: int = 14
    #: Hill-climb candidates: the top this-many by key and by value.
    cand_top: int = 45
    #: The single-position slots; the rest (G / F / UTIL) are flex.
    strict: tuple = field(default_factory=tuple)


def mask(roster: Roster, slots) -> int:
    return sum(1 << k for k, s in enumerate(roster.slots) if s in slots)


@lru_cache(maxsize=200_000)
def _matchable(masks: tuple, n_slots: int) -> bool:
    """Can these players (sorted tuple of slot bitmasks) fill every slot?"""
    full = (1 << n_slots) - 1

    @lru_cache(maxsize=None)
    def go(i: int, used: int) -> bool:
        if i == len(masks):
            return used == full
        free = masks[i] & ~used
        while free:
            bit = free & -free
            if go(i + 1, used | bit):
                return True
            free ^= bit
        return False

    return go(0, 0)


def _key(masks) -> tuple:
    # the most constrained first: fewer options -> faster failure
    return tuple(sorted(masks, key=lambda m: (bin(m).count("1"), m)))


def prepare(pool: list, roster: Roster) -> None:
    """Stamp each row's slot bitmask for `roster`. Idempotent."""
    for r in pool:
        r["_mask"] = mask(roster, r.get("slots") or set())


def valid(pool, idx, roster: Roster, extra_valid: Callable | None = None) -> bool:
    if len(set(idx)) != len(roster.slots):
        return False
    rows = [pool[i] for i in idx]
    if sum(r["salary"] for r in rows) > roster.cap:
        return False
    if len({r["team"] for r in rows}) < roster.min_teams \
            or len({r["game"] for r in rows}) < roster.min_games:
        return False
    if not _matchable(_key([r["_mask"] for r in rows]), len(roster.slots)):
        return False
    return extra_valid is None or extra_valid(rows)


def _fill(rng, pool, by_slot, roster: Roster, seed_idx=(), extra_valid=None):
    """A random legal lineup, biased toward the top of each slot's list.
    `seed_idx` (locked players) are always in it; the fill takes the rest of
    the slots in random order."""
    cheapest = min(pool[i]["salary"] for lst in by_slot.values() for i in lst)
    n = len(roster.slots)
    for attempt in range(300):
        # The top of each list first; if that cannot fit under the cap (a
        # top-heavy board, or locked players who ate the budget), the whole
        # list -- a legal start the climb can improve beats no start at all.
        depth = roster.fill_depth if attempt < 120 else None
        picked = list(seed_idx)
        spent = sum(pool[i]["salary"] for i in picked)
        order = list(range(n))
        rng.shuffle(order)
        order = order[len(picked):]
        ok = True
        for step, k in enumerate(order):
            left = len(order) - step - 1
            budget = roster.cap - spent - left * cheapest
            cands = [j for j in by_slot[roster.slots[k]][:depth]
                     if j not in picked and pool[j]["salary"] <= budget]
            if not cands:
                ok = False
                break
            j = rng.choice(cands)
            picked.append(j)
            spent += pool[j]["salary"]
        if ok and valid(pool, picked, roster, extra_valid):
            return picked
    return None


def _hill_climb(pool, sim, idx, cand, pct, own_weight, owns, roster, locked=frozenset(),
                extra_valid=None):
    idx = list(idx)
    totals = sim[:, idx].sum(axis=1)
    best = float(np.percentile(totals, pct)) - own_weight * float(owns[idx].sum()) / 100.0
    improved = True
    while improved:
        improved = False
        for s in range(len(idx)):
            if idx[s] in locked:
                continue
            cur = idx[s]
            trial_ids = [j for j in cand if j not in idx
                         and valid(pool, idx[:s] + [j] + idx[s + 1:], roster, extra_valid)]
            if not trial_ids:
                continue
            arr = np.array(trial_ids)
            base = totals - sim[:, cur]
            scores = np.percentile(base[:, None] + sim[:, arr], pct, axis=0)
            if own_weight:
                scores = scores - own_weight * (float(owns[idx].sum()) - owns[cur] + owns[arr]) / 100.0
            k = int(np.argmax(scores))
            if scores[k] > best + 1e-9:
                idx[s] = int(arr[k])
                totals = base + sim[:, idx[s]]
                best = float(scores[k])
                improved = True
    return idx, best


def search(pool: list, sim: np.ndarray, roster: Roster, pct: float, key: str = "floor",
           iters: int = 150, seed: int = 0, own_weight: float = 0.0,
           exclude: set | None = None, locked: set | None = None,
           extra_valid: Callable | None = None) -> list | None:
    """Pool indices of the best legal lineup at the `pct` percentile, or None.

    `key` is the per-player column that ranks candidates ("floor" for cash,
    "ceil" for GPP). `locked` names players who must stay (late swap: anyone
    whose game has started); they seed every fill and are never swapped out."""
    prepare(pool, roster)
    rng = random.Random(seed)
    usable = [i for i, p in enumerate(pool) if p.get("salary") and p["_mask"]
              and not (exclude and p["name"] in exclude)]
    if not usable:
        return None

    def val(i):
        return (pool[i].get(key) or 0.0) / pool[i]["salary"] * 1000.0

    by_slot = {}
    for slot in set(roster.slots):
        bit = mask(roster, {slot})
        elig = [i for i in usable if pool[i]["_mask"] & bit]
        by_slot[slot] = sorted(elig, key=lambda i: -(val(i) + 0.12 * (pool[i].get(key) or 0)))
    if any(not lst for lst in by_slot.values()):
        return None
    by_key = sorted(usable, key=lambda i: -(pool[i].get(key) or 0))
    by_val = sorted(usable, key=lambda i: -val(i))
    cand = sorted(set(by_key[:roster.cand_top]) | set(by_val[:roster.cand_top])
                  | {i for lst in by_slot.values() for i in lst[:8]})
    lock_idx = [i for i in usable if locked and pool[i]["name"] in locked]
    if locked:
        cand = [i for i in cand if i not in lock_idx]
    owns = np.array([float(p.get("own") or 0.0) for p in pool])
    best, best_v = None, None
    for _ in range(iters):
        start = _fill(rng, pool, by_slot, roster, lock_idx, extra_valid)
        if start is None:
            continue
        idx, v = _hill_climb(pool, sim, start, cand, pct, own_weight, owns, roster,
                             frozenset(lock_idx), extra_valid)
        if best_v is None or v > best_v:
            best, best_v = idx, v
    return best


def assign_slots(rows: list[dict], roster: Roster) -> list[tuple[str, dict]] | None:
    """(slot, row) in roster order, or None if the rows cannot fill it. The
    EARLIEST tips go in the strict slots and the latest are left for the flex
    slots -- DK's late-swap convention, so a late scratch can be swapped for
    anyone still unlocked. Rows with no `start` keep their projection order."""
    if len(rows) != len(roster.slots):
        return None
    order = sorted(rows, key=lambda r: (str(r.get("start") or ""), -r.get("proj", 0)))
    slots = list(roster.slots)
    out: list = [None] * len(slots)
    used: set = set()

    def go(k: int) -> bool:
        if k == len(slots):
            return True
        for r in order:
            if id(r) in used or slots[k] not in (r.get("slots") or ()):
                continue
            used.add(id(r))
            out[k] = (slots[k], r)
            if go(k + 1):
                return True
            used.discard(id(r))
        return False

    return out if go(0) else None


def result(pool, sim, idx, roster: Roster, describe: Callable) -> dict:
    """The lineup dict every page and CLI reads. `describe(sim, idx)` is the
    sport theory's summary (proj / sd / floor / ceil / p99)."""
    rows = [pool[i] for i in idx]
    stacks: dict = {}
    games: dict = {}
    for r in rows:
        stacks[r["team"]] = stacks.get(r["team"], 0) + 1
        g = r.get("matchup") or r["game"]
        games[g] = games.get(g, 0) + 1
    return {"slots": assign_slots(rows, roster), "lineup": rows, "idx": list(idx),
            "salary": int(sum(r["salary"] for r in rows)),
            "own": round(sum(r.get("own") or 0.0 for r in rows), 1),
            "stacks": dict(sorted(stacks.items(), key=lambda kv: -kv[1])),
            "game_stacks": {g: n for g, n in sorted(games.items(), key=lambda kv: -kv[1]) if n >= 2},
            **describe(sim, idx)}


def portfolio(optimize: Callable, n: int, max_overlap: int = 5, seed: int = 0) -> list[dict]:
    """`n` lineups that differ: at most `max_overlap` players shared with any
    earlier one, and each new lineup bans the previous one's top projection.
    `optimize(seed=, exclude=)` is the sport's own optimize, partially applied."""
    out, banned = [], set()
    for i in range(n * 5):
        if len(out) >= n:
            break
        res = optimize(seed=seed + i, exclude=set(banned))
        if res is None:
            break
        names = {r["name"] for r in res["lineup"]}
        banned.add(max(res["lineup"], key=lambda r: r["proj"])["name"])
        if any(len(names & {r["name"] for r in o["lineup"]}) > max_overlap for o in out):
            continue
        out.append(res)
    return out
