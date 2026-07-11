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


def _secondary_stack(forced_primary, players, team, n, exclude_names, obj):
    """Up to n high-obj hitters from `team`, greedily built in value order,
    each one admitted only if it keeps the WHOLE group (primary + secondary
    so far) simultaneously slot-assignable.

    Found live 2026-07-11 running the real app: the previous version picked
    a random n-of-top-5 by leverage with no position awareness, then trimmed
    from the tail if infeasible. Checked against every real logged slate:
    the secondary stack NEVER reached its target n=3 in production -- when
    the secondary team's best hitters were mostly OF (common for a good
    offense) and the primary 5-stack had already claimed all 3 OF slots (also
    common, since OF is the most frequent position), the random slice was
    OF-heavy and got trimmed to 0-1, silently degrading the "correlated
    3-stack" this was built and backtested to be (§18) into 1-2 incidental
    value picks scattered across unrelated teams -- exactly the "B" arm the
    2025 backtest showed was WORSE (P99 131.2 vs 137.0 for a real 3-stack).
    Greedily trying candidates in value order (instead of a random slice)
    finds the best FEASIBLE combination directly, with no trim-and-hope."""
    cands = [p for p in players if p["team"] == team and "P" not in p["pos"]
             and p["name"] not in exclude_names and any(s in p["pos"] for s in HITTER_SLOTS)]
    cands.sort(key=lambda p: -p.get(obj, p["proj"]))
    sec = []
    for c in cands:
        if len(sec) >= n:
            break
        if _hitter_slots_assignable(list(forced_primary) + sec + [c]):
            sec.append(c)
    return sec


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

    def run_iterations(secondary_k):
        """Full `iters`-iteration search forcing EXACTLY `secondary_k`
        secondary-team hitters alongside the primary stack (0 = primary
        only). An iteration that can't reach secondary_k (position or salary
        infeasible for that primary-run draw) is skipped, not downgraded --
        downgrading belongs to the caller, one level at a time, see below."""
        best, best_score = None, -1
        for it in range(iters):
            forced, locked = None, frozenset()
            if runs:                                   # prefer a CONSECUTIVE batting-order run
                forced = rng.choice(runs[:max(1, len(runs) // 2)])    # explore the higher-ceiling runs
                locked = frozenset(p["name"] for p in forced)
            elif len(fallback) >= stack_n:             # no full run available -> any 4 from team
                rng.shuffle(fallback); forced = fallback[:stack_n]
                locked = frozenset(p["name"] for p in forced)
            if forced and secondary_k > 0:
                sec = _secondary_stack(forced, players, stack2_team, secondary_k, locked, obj)
                if len(sec) < secondary_k:
                    continue    # this primary draw can't reach secondary_k at all -- try another
                forced = list(forced) + sec
                locked = frozenset(p["name"] for p in forced)
            lu = _fill(players, rng, forced, obj)
            if not lu:
                continue
            lu, score = _hill_climb(lu, players, rng, locked=locked, obj=obj)
            if score > best_score:
                best, best_score = lu, score
        return best

    if stack2_team and stack2_team != stack_team:
        # Try the FULL secondary stack for the whole iteration budget first;
        # only shrink the target if that size is proven unreachable across
        # every iteration, then retry the whole search at one size smaller.
        # An earlier version of this degraded PER-ITERATION (fall back to
        # k-1 the moment one iteration's _fill failed) -- that let a lucky
        # unconstrained lineup from one iteration beat out a fully-achievable
        # k=3 lineup from a different iteration, since raw "lev" doesn't
        # reward stack completeness on its own (the same reason primary-stack
        # players are locked against hill-climb swaps to begin with). Found
        # live 2026-07-11 testing the real app: a genuinely infeasible
        # secondary team (salary, not position -- see _secondary_stack's
        # docstring) correctly returned nothing before this fix, but the
        # per-iteration version then silently downgraded lineups that COULD
        # have hit the full 5-3 stack, once tested against every real slate.
        best = None
        for k in range(stack2_n, -1, -1):
            best = run_iterations(k)
            if best:
                break
    else:
        best = run_iterations(0)

    if not best:
        return None
    return {"lineup": _assign(best), "proj": round(sum(p["proj"] for p in best), 1),
            "ceil": round(sum(p.get("ceiling", p["proj"]) for p in best), 1),
            "salary": sum(p["salary"] for p in best)}
