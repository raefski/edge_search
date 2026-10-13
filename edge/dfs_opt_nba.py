"""DK basketball Classic optimiser, NBA and college.

    NBA   PG SG SF PF C G F UTIL      $50,000   2+ teams, 2+ games
    CBB   G G G F F F UTIL UTIL       $50,000   2+ games

The NHL search (edge/dfs_opt_nhl.py) -- a randomised fill plus a hill climb
whose candidate swaps are scored in one vectorised percentile pass over the
simulation -- with the one thing basketball adds: MULTI-POSITION ELIGIBILITY.
A "PG/SG" can fill PG, SG, G or UTIL, so "is this lineup legal" is not a count
of positions, it is a bipartite matching of eight players to eight slots. That
check runs thousands of times per build, so it is cached on the multiset of
eligibility bitmasks -- an NBA board has only a dozen distinct masks, so the
cache is hit almost every time.

NO STACK RULE, EITHER MODE. Which game to stack is the simulation's call
(pace is shared across both teams, so a 90th-percentile objective walks into
the highest total on its own), and basketball has nothing like the NHL's
"two 3-man team stacks" convention to enforce. Cash gets no rule for the usual
reason: correlation it does not want, it avoids by itself.

assign_slots PUTS THE LATEST TIPS IN THE FLEX SLOTS (G / F / UTIL), so late
swap has somewhere to go when a late-game player is ruled out at 9:30.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from edge import dfs_nba_theory as theory


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
    strict: tuple = field(default_factory=tuple)


NBA = Roster("NBA", ("PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"),
             strict=("PG", "SG", "SF", "PF", "C"))
NCAAB = Roster("NCAAB", ("G", "G", "G", "F", "F", "F", "UTIL", "UTIL"), min_teams=2,
               strict=("G", "F"))


def _mask(roster: Roster, slots: set) -> int:
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

    # the most constrained first: fewer options -> faster failure
    return go(0, 0)


def _key(masks) -> tuple:
    return tuple(sorted(masks, key=lambda m: (bin(m).count("1"), m)))


def valid(pool, idx, roster: Roster) -> bool:
    if len(set(idx)) != len(roster.slots):
        return False
    rows = [pool[i] for i in idx]
    if sum(r["salary"] for r in rows) > roster.cap:
        return False
    if len({r["team"] for r in rows}) < roster.min_teams \
            or len({r["game"] for r in rows}) < roster.min_games:
        return False
    return _matchable(_key([r["_mask"] for r in rows]), len(roster.slots))


def prepare(pool: list, roster: Roster) -> None:
    """Stamp each row's slot bitmask. Idempotent."""
    for r in pool:
        r["_mask"] = _mask(roster, r.get("slots") or set())


def _fill(rng, pool, by_slot, roster: Roster, seed_idx=()):
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
        if ok and valid(pool, picked, roster):
            return picked
    return None


def _hill_climb(pool, sim, idx, cand, mode, own_weight, owns, roster, locked=frozenset()):
    q = theory.GPP_PCT if mode == "gpp" else theory.CASH_PCT
    idx = list(idx)
    totals = sim[:, idx].sum(axis=1)

    def value(tot, lineup):
        v = float(np.percentile(tot, q))
        if own_weight and mode == "gpp":
            v -= own_weight * float(owns[lineup].sum()) / 100.0
        return v

    best = value(totals, idx)
    improved = True
    while improved:
        improved = False
        for s in range(len(idx)):
            if idx[s] in locked:
                continue
            cur = idx[s]
            trial_ids = [j for j in cand if j not in idx
                         and valid(pool, idx[:s] + [j] + idx[s + 1:], roster)]
            if not trial_ids:
                continue
            arr = np.array(trial_ids)
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


def optimize(pool: list, sim: np.ndarray, roster: Roster = NBA, mode: str = "cash",
             iters: int = 150, seed: int = 0, own_weight: float = 0.0,
             exclude: set | None = None, locked: set | None = None) -> dict | None:
    """Best legal lineup under one theory, or None if none exists.

    `locked` names players who must stay (late swap: anyone whose game has
    started); they are seeded into every fill and never swapped out."""
    prepare(pool, roster)
    rng = random.Random(seed)
    key = "ceil" if mode == "gpp" else "floor"
    usable = [i for i, p in enumerate(pool) if p.get("salary") and p["_mask"]
              and not (exclude and p["name"] in exclude)]
    if not usable:
        return None

    def val(i):
        return (pool[i].get(key) or 0.0) / pool[i]["salary"] * 1000.0

    by_slot = {}
    for slot in set(roster.slots):
        bit = _mask(roster, {slot})
        elig = [i for i in usable if pool[i]["_mask"] & bit]
        by_slot[slot] = sorted(elig, key=lambda i: -(val(i) + 0.12 * (pool[i].get(key) or 0)))
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
        start = _fill(rng, pool, by_slot, roster, lock_idx)
        if start is None:
            continue
        idx, v = _hill_climb(pool, sim, start, cand, mode, own_weight, owns, roster,
                             frozenset(lock_idx))
        if best_v is None or v > best_v:
            best, best_v = idx, v
    if best is None:
        return None
    return result(pool, sim, best, roster)


def assign_slots(rows: list[dict], roster: Roster = NBA) -> list[tuple[str, dict]]:
    """(slot, row) in roster order, with the EARLIEST tips in the strict slots
    and the latest left for G / F / UTIL -- DK's late-swap convention, so a
    late scratch can be swapped for anyone still unlocked."""
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

    if not go(0):
        raise ValueError("lineup cannot fill the roster")
    return out


def result(pool, sim, idx, roster: Roster = NBA) -> dict:
    rows = [pool[i] for i in idx]
    stacks: dict = {}
    for r in rows:
        stacks[r["team"]] = stacks.get(r["team"], 0) + 1
    games: dict = {}
    for r in rows:
        games[r.get("matchup") or r["game"]] = games.get(r.get("matchup") or r["game"], 0) + 1
    return {"slots": assign_slots(rows, roster), "lineup": rows, "idx": list(idx),
            "salary": int(sum(r["salary"] for r in rows)),
            "own": round(sum(r.get("own") or 0.0 for r in rows), 1),
            "stacks": dict(sorted(stacks.items(), key=lambda kv: -kv[1])),
            "game_stacks": {g: n for g, n in sorted(games.items(), key=lambda kv: -kv[1]) if n >= 2},
            **theory.describe(sim, idx)}


def portfolio(pool: list, sim: np.ndarray, n: int, roster: Roster = NBA, max_overlap: int = 5,
              mode: str = "gpp", iters: int = 80, seed: int = 0, **kw) -> list[dict]:
    """`n` GPP lineups that differ: at most `max_overlap` of 8 shared, and each
    new lineup bans the previous one's highest-projected player."""
    out, banned = [], set()
    for i in range(n * 5):
        if len(out) >= n:
            break
        res = optimize(pool, sim, roster, mode=mode, iters=iters, seed=seed + i,
                       exclude=banned, **kw)
        if res is None:
            break
        names = {r["name"] for r in res["lineup"]}
        top = max(res["lineup"], key=lambda r: r["proj"])["name"]
        banned.add(top)
        if any(len(names & {r["name"] for r in o["lineup"]}) > max_overlap for o in out):
            continue
        out.append(res)
    return out
