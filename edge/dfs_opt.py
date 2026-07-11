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
    if lineup:
        # remove slots via a real assignment (backtracking), not first-match:
        # first-match can strand a large forced group (e.g. a 5+3 double stack
        # where a 1B/OF player grabs 1B and blocks a 1B-only teammate) even
        # though a valid assignment exists.
        def _take(players, slots):
            if not players:
                return slots
            p = players[0]
            for s in list(dict.fromkeys(slots)):
                if s in p["pos"]:
                    rest = slots[:]; rest.remove(s)
                    sub = _take(players[1:], rest)
                    if sub is not None:
                        return sub
            return None
        taken = _take(sorted(lineup, key=lambda p: len(p["pos"])), slots_left)
        if taken is None:
            return None
        slots_left = taken
    # cheapest eligible per slot, for budget feasibility
    cheapest = {s: min((p["salary"] for p in _eligible(players, s)), default=CAP) for s in set(slots_left)}
    team_h = {}
    for p in lineup:
        if "P" not in p["pos"]:
            team_h[p["team"]] = team_h.get(p["team"], 0) + 1
    rng.shuffle(slots_left)
    for i, slot in enumerate(slots_left):
        spent = sum(p["salary"] for p in lineup)
        reserve = sum(cheapest[s] for s in slots_left[i + 1:])
        budget = CAP - spent - reserve
        cands = [p for p in _eligible(players, slot) if p["name"] not in used and p["salary"] <= budget
                 # respect DK's max-hitters-per-team cap DURING the fill, not just
                 # at final validation -- otherwise a high-proj stack team's
                 # leftovers dominate every candidate list and most fills die at
                 # _valid, wasting iterations.
                 and (slot == "P" or team_h.get(p["team"], 0) < MAX_HITTERS_PER_TEAM)]
        if not cands:
            return None
        # weight toward projection but keep randomness
        cands.sort(key=lambda p: -p[obj])
        pick = rng.choice(cands[:max(3, len(cands) // 4)])
        lineup.append(pick); used.add(pick["name"])
        if slot != "P":
            team_h[pick["team"]] = team_h.get(pick["team"], 0) + 1
    return lineup if _valid(lineup) else None


MAX_HITTERS_PER_TEAM = 5  # DK MLB rule: "a maximum of five hitters from the same team"


def _valid(lineup):
    if len(lineup) != 10:
        return False
    if sum(p["salary"] for p in lineup) > CAP:
        return False
    if len({p["game"] for p in lineup}) < 2:
        return False
    # DK hard rule (dknetwork.draftkings.com "Advanced MLB DFS: Stacking"):
    # max 5 HITTERS from one team (pitchers don't count). Without this the
    # GPP hill-climb could grow a forced stack to 6+ same-team hitters and
    # produce a lineup DK's own entry validator would reject at upload.
    team_h = {}
    for p in lineup:
        if "P" not in p["pos"]:
            team_h[p["team"]] = team_h.get(p["team"], 0) + 1
            if team_h[p["team"]] > MAX_HITTERS_PER_TEAM:
                return False
    # never roster a pitcher against a hitter he's facing -- their good outcomes
    # are directly anti-correlated: the pitcher's Ks/scoreless innings ARE that
    # hitter's bad at-bats, and vice versa. Matched by TEAM, not "game": pitcher
    # pool entries carry a DK/Odds-API game id, hitter entries carry a statsapi
    # gamePk -- different id spaces that never coincide even for the same real
    # matchup, so this must compare team abbreviations via opp_team instead.
    pitchers = [p for p in lineup if "P" in p["pos"]]
    hitters = [p for p in lineup if "P" not in p["pos"]]
    if any(h["team"] == pit.get("opp_team") or pit["team"] == h.get("opp_team")
           for pit in pitchers for h in hitters):
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


def _hitter_slots_assignable(hitters):
    """Can these hitters simultaneously occupy distinct hitter slots?"""
    slots = HITTER_SLOTS[:] + ["OF", "OF"]  # C,1B,2B,3B,SS,OF,OF,OF
    def bt(players, slots):
        if not players:
            return True
        p = players[0]
        for s in list(dict.fromkeys(slots)):
            if s in p["pos"]:
                rest = slots[:]; rest.remove(s)
                if bt(players[1:], rest):
                    return True
        return False
    return bt(sorted(hitters, key=lambda p: len(p["pos"])), slots)


def _secondary_stack(players, team, n, exclude_names, rng, obj):
    """Up to n high-obj hitters from `team` (not already forced). Prefers a
    positionally-coherent set (checked later against the full forced group)."""
    cands = [p for p in players if p["team"] == team and "P" not in p["pos"]
             and p["name"] not in exclude_names and any(s in p["pos"] for s in HITTER_SLOTS)]
    cands.sort(key=lambda p: -p.get(obj, p["proj"]))
    top = cands[:max(n + 2, 5)]
    rng.shuffle(top)
    return top[:n]


def optimize(players, mode="cash", stack_team=None, stack_n=4, iters=3000, seed=0,
             stack2_team=None, stack2_n=3):
    rng = random.Random(seed)
    players = [p for p in players if p["proj"] is not None and p.get("salary")]
    for p in players:
        p.setdefault("ceiling", p["proj"])
        p.setdefault("lev", p["ceiling"])
        p.setdefault("floor", p["proj"])
    # cash = mean nudged toward consistency (walk-rate floor signal, edge/dfs.py
    # BB_FLOOR_WEIGHT); gpp = ceiling faded by ownership
    obj = "lev" if mode == "gpp" else "floor"
    # stack forcing is caller-driven (historically GPP-only; cash callers may
    # now pass a milder stack too -- see the construction replay backtest)
    runs = _consecutive_runs(players, stack_team, stack_n) if stack_team else []
    runs.sort(key=lambda r: -sum(p.get("ceiling", p["proj"]) for p in r))
    fallback = [p for p in players if stack_team and p["team"] == stack_team
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
        # secondary stack (GPP 5-3 style construction): add 2-3 correlated
        # hitters from a second team; consecutive order matters less for the
        # secondary, so it's top-obj hitters rather than a strict run. If the
        # combined forced group can't cover distinct hitter slots, trim it
        # rather than burn the iteration.
        if forced and stack2_team and stack2_team != stack_team:
            sec = _secondary_stack(players, stack2_team, stack2_n, locked, rng, obj)
            while sec and not _hitter_slots_assignable(list(forced) + sec):
                sec = sec[:-1]
            if sec:
                forced = list(forced) + sec
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
