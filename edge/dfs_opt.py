"""DK MLB Classic lineup optimizer (dependency-free heuristic).

Roster: 2 P, 1 C, 1 1B, 1 2B, 1 3B, 1 SS, 3 OF — $50,000 cap, >=2 games.
A player is a dict: {name, pos:set, salary, proj, team, game}.

cash mode: maximize projection (high floor). gpp mode: force a hitter stack from
one team (correlated ceiling) then maximize projection. Solved by randomized
greedy fill + hill-climb over many restarts — near-optimal for a 10-slot roster.
"""
from __future__ import annotations

import random

SLOTS = ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"]
CAP = 50000
HITTER_SLOTS = ["C", "1B", "2B", "3B", "SS", "OF"]


def _eligible(players, slot):
    return [p for p in players if slot in p["pos"]]


def _fill(players, rng, forced=None, obj="proj"):
    """One randomized, salary-feasible fill. `forced` = list of pre-placed players."""
    used = set(p["name"] for p in (forced or []))
    lineup = list(forced or [])
    slots_left = SLOTS[:]
    for p in lineup:                       # remove one matching slot per forced player
        for s in slots_left:
            if s in p["pos"]:
                slots_left.remove(s); break
    # cheapest eligible per slot, for budget feasibility
    cheapest = {s: min((p["salary"] for p in _eligible(players, s)), default=CAP) for s in set(slots_left)}
    rng.shuffle(slots_left)
    for i, slot in enumerate(slots_left):
        spent = sum(p["salary"] for p in lineup)
        reserve = sum(cheapest[s] for s in slots_left[i + 1:])
        budget = CAP - spent - reserve
        cands = [p for p in _eligible(players, slot) if p["name"] not in used and p["salary"] <= budget]
        if not cands:
            return None
        # weight toward projection but keep randomness
        cands.sort(key=lambda p: -p[obj])
        pick = rng.choice(cands[:max(3, len(cands) // 4)])
        lineup.append(pick); used.add(pick["name"])
    return lineup if _valid(lineup) else None


def _valid(lineup):
    if len(lineup) != 10:
        return False
    if sum(p["salary"] for p in lineup) > CAP:
        return False
    if len({p["game"] for p in lineup}) < 2:
        return False
    # never roster a pitcher against a hitter he's facing (same game, opposing
    # teams) -- their good outcomes are directly anti-correlated: the pitcher's
    # Ks/scoreless innings ARE that hitter's bad at-bats, and vice versa.
    pitchers = [p for p in lineup if "P" in p["pos"]]
    hitters = [p for p in lineup if "P" not in p["pos"]]
    if any(h["game"] == pit["game"] and h["team"] != pit["team"] for pit in pitchers for h in hitters):
        return False
    # assignable to slots (greedy by scarcity)
    return _assign(lineup) is not None


def _assign(lineup):
    """Check the 10 players fill the 10 distinct slots; return assignment or None."""
    slots = SLOTS[:]
    # backtracking assignment (small)
    def bt(players, slots):
        if not players:
            return [] if not slots else None
        p = players[0]
        for s in list(dict.fromkeys(slots)):
            if s in p["pos"]:
                rest = slots[:]; rest.remove(s)
                sub = bt(players[1:], rest)
                if sub is not None:
                    return [(p, s)] + sub
        return None
    order = sorted(lineup, key=lambda p: len(p["pos"]))   # fewest options first
    return bt(order, slots)


def _consecutive_runs(players, team, n):
    """All cyclic length-n runs of the team's batting order (e.g. n=4 -> 2-3-4-5,
    ... 8-9-1-2). Returns lists of n hitters; empty if the order isn't covered.
    A consecutive stack captures inning-level correlation a scattered one misses."""
    slot_player = {}
    for p in players:
        if p["team"] != team or "P" in p["pos"] or not p.get("slot"):
            continue
        s = p["slot"]
        if s not in slot_player or p.get("ceiling", p["proj"]) > slot_player[s].get("ceiling", slot_player[s]["proj"]):
            slot_player[s] = p
    runs = []
    for start in range(1, 10):
        slots = [((start - 1 + i) % 9) + 1 for i in range(n)]
        if all(s in slot_player for s in slots):
            runs.append([slot_player[s] for s in slots])
    return runs


def _hill_climb(lineup, players, rng, locked=frozenset(), obj="proj"):
    score = sum(p[obj] for p in lineup)
    improved = True
    while improved:
        improved = False
        for i in range(len(lineup)):
            cur = lineup[i]
            if cur["name"] in locked:        # never swap out the forced stack
                continue
            others = sum(p["salary"] for p in lineup) - cur["salary"]
            names = set(p["name"] for p in lineup)
            for cand in players:
                if cand["name"] in names or cand[obj] <= cur[obj]:
                    continue
                if others + cand["salary"] > CAP:
                    continue
                trial = lineup[:]; trial[i] = cand
                if _valid(trial):
                    lineup = trial; score = sum(p[obj] for p in lineup)
                    improved = True; break
    return lineup, score


def optimize(players, mode="cash", stack_team=None, stack_n=4, iters=3000, seed=0):
    rng = random.Random(seed)
    players = [p for p in players if p["proj"] is not None and p.get("salary")]
    for p in players:
        p.setdefault("ceiling", p["proj"])
        p.setdefault("lev", p["ceiling"])
    obj = "lev" if mode == "gpp" else "proj"   # cash=mean; gpp=ceiling faded by ownership
    runs = _consecutive_runs(players, stack_team, stack_n) if (mode == "gpp" and stack_team) else []
    runs.sort(key=lambda r: -sum(p.get("ceiling", p["proj"]) for p in r))
    fallback = [p for p in players if mode == "gpp" and stack_team and p["team"] == stack_team
                and any(s in p["pos"] for s in HITTER_SLOTS)]
    best, best_score = None, -1
    for it in range(iters):
        forced, locked = None, frozenset()
        if runs:                                   # prefer a CONSECUTIVE batting-order run
            forced = rng.choice(runs[:max(1, len(runs) // 2)])    # explore the higher-ceiling runs
            locked = frozenset(p["name"] for p in forced)
        elif len(fallback) >= stack_n:             # no full run available -> any 4 from team
            rng.shuffle(fallback); forced = fallback[:stack_n]
            locked = frozenset(p["name"] for p in forced)
        lu = _fill(players, rng, forced, obj)
        if not lu:
            continue
        lu, score = _hill_climb(lu, players, rng, locked=locked, obj=obj)
        if score > best_score:
            best, best_score = lu, score
    if not best:
        return None
    return {"lineup": _assign(best), "proj": round(sum(p["proj"] for p in best), 1),
            "ceil": round(sum(p.get("ceiling", p["proj"]) for p in best), 1),
            "salary": sum(p["salary"] for p in best)}
