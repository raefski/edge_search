"""DK College Football Classic lineup optimizer (dependency-free heuristic).

Roster: QB, RB, RB, WR, WR, WR, FLEX (RB/WR), S-FLEX (QB/RB/WR) -- $50,000
cap, >= 2 games. A player is a dict:
    {name, pos:set, salary, proj, team, opp_team, game, dk_pos}
`pos` holds DK slot names; FLEX and S-FLEX eligibility is added here, not by
the caller.

WHY THIS IS A THIRD OPTIMISER AND NOT A MODE OF edge/dfs_opt_nfl.py
The structure looks close enough to the NFL's that sharing it is tempting, and
three measured facts say not to. Each is argued in full in
edge/dfs_ncaaf_theory.py; the consequences for THIS file are:

  1. THE S-FLEX TAKES A QUARTERBACK, AND USUALLY SHOULD. College quarterbacks
     average 17.2 DK points against 10.5 for backs and 8.9 for receivers, and
     31 of the 40 highest-scoring player-seasons in the sample are
     quarterbacks. An NFL optimiser has no slot that could do this and no
     reason to reason about it. Here, "how many quarterbacks" is the first
     question the search has to answer, and it is answered by the objective
     rather than by a rule -- the only rule is (2).

  2. TWO QUARTERBACKS FROM ONE TEAM IS A HARD REFUSAL. R_QB_OWN_QB is -0.098:
     college teams rotate quarterbacks, and a backup's good game is his
     starter's bad one. This is the structural analogue of the NFL optimiser's
     "a DST must not face your own stack" rule -- a correlation strong enough
     and adverse enough that it is enforced rather than priced.

  3. A STACK COMPOUNDS HERE AND DILUTES IN THE NFL. Team-mate receivers are
     +0.048 correlated in college and -0.029 in the NFL. edge/dfs_opt_nfl.py's
     docstring reasons from its negative number that "a second pass-catcher
     from the stack team is worth LESS than the first" and sets stack_n=2.
     The sign is the other way here, so stack_n defaults to 3 and the roster
     (three WR slots plus a FLEX) can hold it.

WHAT IS GENUINELY SHARED IS SHARED. The slot matcher is imported from
edge/dfs_roster rather than copied for a fifth time, which is the line
DFS_MULTISPORT_PLAN.md draws.

WHAT IS ABSENT AND SHOULD STAY ABSENT
No DST logic of any kind. College DK has no defence slot, so there is no
points-allowed tier, no opposing implied team total, and no game line needed
to build a pool at all. edge/dfs_run_nfl.py had to go to considerable trouble
to match book events to slate games precisely because a wrong opponent broke
its DST projection and silently disabled its DST rule; here a missing game
line costs nothing, and the opponent is only used for the bring-back.
"""
from __future__ import annotations

import math
import random

from edge import dfs_ncaaf_theory as theory
from edge.dfs_roster import assign_slots
from edge.ncaaf import base_position

SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "FLEX", "S-FLEX"]
CAP = 50000
MIN_GAMES = 2
FLEX_FROM = {"RB", "WR"}
SFLEX_FROM = {"QB", "RB", "WR"}
CATCHERS = theory.CATCHERS


def eligible_slots(dk_position: str | None) -> set:
    """DK position string -> the roster slots that player can fill.

    Derived here rather than asked of the caller, because forgetting it is
    invisible: the lineup still builds, it is just never allowed to put a
    second quarterback in the S-FLEX, and quietly returns a worse lineup on a
    slate where that is the single biggest decision available.
    """
    pos = base_position(dk_position)
    out = {pos, "S-FLEX"}
    if pos in FLEX_FROM:
        out.add("FLEX")
    return out


def _valid(lineup) -> bool:
    if len(lineup) != len(SLOTS):
        return False
    if sum(p["salary"] for p in lineup) > CAP:
        return False
    if len({p["game"] for p in lineup}) < MIN_GAMES:
        return False
    # Two quarterbacks from the same team, at -0.098, is the one pairing
    # forbidden outright rather than merely priced -- see the module docstring.
    qbs = [p for p in lineup if base_position(p.get("dk_pos")) == "QB"]
    if len({q["team"] for q in qbs}) < len(qbs):
        return False
    return assign_slots(lineup, SLOTS) is not None


def _eligible(players, slot):
    return [p for p in players if slot in p["pos"]]


def _fill(players, rng, forced=None, obj="proj"):
    """One randomized, salary-feasible fill. `forced` is pre-placed players."""
    lineup = list(forced or [])
    used = {p["name"] for p in lineup}
    slots_left = SLOTS[:]
    if lineup:
        # Remove slots via a real assignment rather than first-match: a forced
        # QB + 3 WR + bring-back group contains four WR-eligible players and
        # first-match will happily spend the FLEX and S-FLEX on the wrong two
        # and then fail on a lineup that was legal.
        pairs = assign_slots(lineup, slots_left)
        if pairs is None:
            return None
        for _, s in pairs:
            slots_left.remove(s)
    cheapest = {s: min((p["salary"] for p in _eligible(players, s)), default=CAP)
                for s in set(slots_left)}
    rng.shuffle(slots_left)
    for i, slot in enumerate(slots_left):
        spent = sum(p["salary"] for p in lineup)
        reserve = sum(cheapest[s] for s in slots_left[i + 1:])
        budget = CAP - spent - reserve
        cands = [p for p in _eligible(players, slot)
                 if p["name"] not in used and p["salary"] <= budget]
        if not cands:
            return None
        cands.sort(key=lambda p: -p[obj])
        pick = rng.choice(cands[:max(3, len(cands) // 4)])
        lineup.append(pick)
        used.add(pick["name"])
    return lineup if _valid(lineup) else None


def _sd(p) -> float:
    """This player's DK-point standard deviation, cached on the dict."""
    v = p.get("_sd")
    if v is None:
        v = p["_sd"] = theory.player_sd(p)
    return v


def _stats(lineup) -> tuple[float, float]:
    """(mean, variance) of the lineup total, correlations included."""
    mean = sum(p["proj"] for p in lineup)
    var = 0.0
    for i, a in enumerate(lineup):
        sa = _sd(a)
        var += sa * sa
        for b in lineup[i + 1:]:
            r = theory.rho(a, b)
            if r:
                var += 2.0 * r * sa * _sd(b)
    return mean, var


def _swap_stats(lineup, i, cand, mean, var) -> tuple[float, float]:
    """(mean, variance) after replacing lineup[i] with `cand`, in O(n).

    Recomputing the whole quadratic form for every candidate swap is what
    makes a variance-aware hill climb too slow to run before lock.
    """
    cur = lineup[i]
    s_cur, s_new = _sd(cur), _sd(cand)
    mean = mean - cur["proj"] + cand["proj"]
    var = var - s_cur * s_cur + s_new * s_new
    for j, other in enumerate(lineup):
        if j == i:
            continue
        s_o = _sd(other)
        r_cur, r_new = theory.rho(cur, other), theory.rho(cand, other)
        if r_cur:
            var -= 2.0 * r_cur * s_cur * s_o
        if r_new:
            var += 2.0 * r_new * s_new * s_o
    return mean, var


def _objective(mean: float, var: float, mode: str,
               z_cash: float, z_gpp: float) -> float:
    """mean -/+ z*sd. The whole difference between the two theories."""
    sd = math.sqrt(max(0.0, var))
    return mean + z_gpp * sd if mode == "gpp" else mean - z_cash * sd


#: How many candidates a hill-climb step considers for one roster spot.
#: A latency knob, not a quality one -- see edge/dfs_opt_nfl.py, where an
#: unbounded version did not finish in two minutes on a 212-player board. A
#: college board is smaller (~110 players carry a prop) so this is comfortable.
CLIMB_CANDIDATES = 40

#: How many quarterbacks survive the GPP screen and get a full search.
GPP_FINALISTS = 5


def _candidate_index(players):
    """{slot: [players eligible there, best first]} for the current mode."""
    return {slot: sorted(_eligible(players, slot),
                         key=lambda p: -p.get("_pv", p["proj"]))
            for slot in set(SLOTS)}


def _climb_candidates(index, cur, limit=CLIMB_CANDIDATES):
    """Best `limit` players who could stand in for `cur`, de-duplicated."""
    out, seen = [], set()
    for slot in cur["pos"]:
        for p in index.get(slot, ())[:limit]:
            if p["name"] not in seen:
                seen.add(p["name"])
                out.append(p)
    return out


def _hill_climb(lineup, players, rng, locked=frozenset(), mode="cash",
                z_cash=theory.Z_CASH, z_gpp=theory.Z_GPP, index=None):
    """Improve a legal lineup one swap at a time, on the FULL objective."""
    index = index if index is not None else _candidate_index(players)
    mean, var = _stats(lineup)
    best = _objective(mean, var, mode, z_cash, z_gpp)
    improved = True
    while improved:
        improved = False
        for i in range(len(lineup)):
            cur = lineup[i]
            if cur["name"] in locked:
                continue
            others = sum(p["salary"] for p in lineup) - cur["salary"]
            names = {p["name"] for p in lineup}
            for cand in _climb_candidates(index, cur):
                if cand["name"] in names:
                    continue
                if others + cand["salary"] > CAP:
                    continue
                m, v = _swap_stats(lineup, i, cand, mean, var)
                obj = _objective(m, v, mode, z_cash, z_gpp)
                if obj <= best:
                    continue
                trial = lineup[:]
                trial[i] = cand
                if _valid(trial):
                    lineup, mean, var, best = trial, m, v, obj
                    improved = True
                    break
    return lineup, best


def stack_candidates(players, qb, n, obj="proj"):
    """The best `n` of a quarterback's own pass-catchers that fit with him.

    Admitted greedily in value order, each only if the WHOLE group stays
    slot-assignable. Here the binding constraint is that a QB + 3 WR group
    uses all three WR slots, so a fourth catcher would have to take the FLEX
    and leave nothing for a running back -- which is legal but is a different
    lineup shape and should be reached by the objective, not by the stacker.
    """
    cands = [p for p in players
             if p["team"] == qb["team"] and p["name"] != qb["name"]
             and base_position(p.get("dk_pos")) in CATCHERS]
    cands.sort(key=lambda p: -p.get(obj, p["proj"]))
    out = []
    for c in cands:
        if len(out) >= n:
            break
        if assign_slots([qb] + out + [c], SLOTS) is not None:
            out.append(c)
    return out


def bring_back_candidates(players, qb, k, obj="proj"):
    """The best `k` players from the team the stacked QB is FACING.

    College differs from the NFL here in a way worth exploiting: the OPPOSING
    QUARTERBACK is the strongest opposing correlation (+0.131) and a receiver
    is weaker (+0.058), where the NFL's bring-back convention reaches for a
    receiver because its lineup has only one quarterback slot. The S-FLEX
    means a college lineup can bring back the opposing quarterback himself,
    so both are offered and ranked by the objective.
    """
    opp = qb.get("opp_team")
    if not opp:
        return []
    cands = [p for p in players
             if p["team"] == opp
             and base_position(p.get("dk_pos")) in (CATCHERS | {"QB"})]
    cands.sort(key=lambda p: -p.get(obj, p["proj"]))
    return cands[:k]


def optimize(players, mode="cash", stack_qb=None, stack_n=theory.STACK_N,
             bring_back=theory.BRING_BACK, iters=2000, seed=0,
             z_cash=theory.Z_CASH, z_gpp=theory.Z_GPP, own_weight=0.0):
    """Best college lineup found, under one of the two game theories.

    mode="cash"  maximise `mean - z_cash*sd`. No stack is forced and none is
                 wanted: correlation raises the spread, so the objective walks
                 away from a stack on its own and spreads across games.
    mode="gpp"   maximise `mean + z_gpp*sd`, and force a QB + `stack_n` of his
                 own pass-catchers plus `bring_back` players from the opposing
                 team. The stack is forced rather than preferred because a
                 randomised search will not reliably find the one configuration
                 the objective most rewards.

    Returns {lineup, proj, sd, floor, ceil, salary, stack, own, qbs} or None.
    None means no legal lineup exists under the cap, which is a real answer on
    a short slate and must not be confused with a bad one.
    """
    rng = random.Random(seed)
    players = [p for p in players
               if p.get("proj") is not None and p.get("salary")]
    for p in players:
        p.setdefault("opp_team", None)
        p.setdefault("game", p.get("opp_team") or p["team"])
        p.pop("_sd", None)
    if not players:
        return None

    # The greedy fill needs a per-player ranking, and it must agree in sign
    # with the lineup objective or the search spends its time climbing back
    # out of where the fill put it.
    for p in players:
        p["_pv"] = (p["proj"] + z_gpp * _sd(p)) if mode == "gpp" \
            else (p["proj"] - z_cash * _sd(p))

    index = _candidate_index(players)

    def search(forced, locked, n_iters=None):
        best, best_key = None, None
        for _ in range(n_iters if n_iters is not None else iters):
            lu = _fill(players, rng, forced, "_pv")
            if not lu:
                continue
            lu, key = _hill_climb(lu, players, rng, locked=locked, mode=mode,
                                  z_cash=z_cash, z_gpp=z_gpp, index=index)
            if own_weight and mode == "gpp":
                key -= own_weight * sum(q.get("own", 0.0) for q in lu) / 100.0
            if best_key is None or key > best_key:
                best, best_key = lu, key
        return best, best_key

    if mode != "gpp":
        best, _ = search(None, frozenset())
        return _result(best, None, z_cash, z_gpp) if best else None

    qbs = [p for p in players if base_position(p.get("dk_pos")) == "QB"]
    if stack_qb:
        qbs = [p for p in qbs if p["name"] == stack_qb]
    qbs.sort(key=lambda p: (-p.get("leverage", 0.0), -p["proj"]))

    # Two stages, so trying every quarterback stays affordable: every one gets
    # a cheap look and only the best few get the full search. A resolution
    # ladder, not a cap -- no quarterback is excluded before being scored.
    groups = {}
    for qb in qbs:
        group = [qb] + stack_candidates(players, qb, stack_n, "_pv")
        if len(group) < 1 + stack_n:
            continue
        group += bring_back_candidates(players, qb, bring_back, "_pv")
        if assign_slots(group, SLOTS) is None:
            continue
        groups[qb["name"]] = (qb, group)
    if not groups:
        # A 3-catcher stack plus a bring-back is 5 of 8 slots, and on a thin
        # board no quarterback may support one. Fall back to a smaller stack
        # rather than returning nothing, and say so through `stack`.
        if stack_n > 1:
            return optimize(players, mode=mode, stack_qb=stack_qb,
                            stack_n=stack_n - 1, bring_back=bring_back,
                            iters=iters, seed=seed, z_cash=z_cash,
                            z_gpp=z_gpp, own_weight=own_weight)
        return None

    screen_iters = max(20, iters // 8)
    screened = []
    for name, (qb, group) in groups.items():
        locked = frozenset(q["name"] for q in group)
        lu, key = search(group, locked, screen_iters)
        if lu:
            screened.append((key, name))
    screened.sort(reverse=True)

    best, best_key, best_qb = None, None, None
    for _, name in screened[:GPP_FINALISTS]:
        qb, group = groups[name]
        locked = frozenset(q["name"] for q in group)
        lu, key = search(group, locked)
        if lu and (best_key is None or key > best_key):
            best, best_key, best_qb = lu, key, qb
    if best is None:
        return None
    return _result(best, best_qb, z_cash, z_gpp)


def _result(lineup, qb, z_cash=theory.Z_CASH, z_gpp=theory.Z_GPP):
    stack = None
    if qb is not None:
        mates = [p["name"] for p in lineup
                 if p["team"] == qb["team"] and p["name"] != qb["name"]
                 and base_position(p.get("dk_pos")) in CATCHERS]
        back = [p["name"] for p in lineup
                if p["team"] == qb.get("opp_team")]
        stack = {"qb": qb["name"], "team": qb["team"],
                 "with": mates, "bring_back": back}
    mean, var = _stats(lineup)
    sd = math.sqrt(max(0.0, var))
    qbs = [p["name"] for p in lineup if base_position(p.get("dk_pos")) == "QB"]
    return {"lineup": assign_slots(lineup, SLOTS),
            "proj": round(mean, 1),
            "sd": round(sd, 1),
            # Reported from the SAME quadratic form the objective maximises,
            # so what the app shows and what the search chose cannot disagree.
            "floor": round(mean - z_cash * sd, 1),
            "ceil": round(mean + z_gpp * sd, 1),
            "salary": sum(p["salary"] for p in lineup),
            "own": round(sum(p.get("own", 0.0) for p in lineup), 1),
            "qbs": qbs,
            "stack": stack}


def portfolio(players, n, max_overlap=5, mode="gpp", stack_n=theory.STACK_N,
              bring_back=theory.BRING_BACK, iters=1500, seed=0, **kw):
    """`n` lineups that are actually DIFFERENT from each other.

    Diversity is bought by banning the previous lineup's stacked quarterback
    rather than by re-rolling the seed: a portfolio built on one quarterback is
    one bet with extra entry fees, whatever its overlap count says.

    `max_overlap` is 5 of 8 here against the NFL's 6 of 9 -- the same
    proportion, rounded toward more diversity because a college pool is
    narrower (DraftKings prices only ~110 of a 12-game slate's ~860 players
    with a prop) and lineups collide more easily.
    """
    out, used_qbs = [], set()
    for i in range(n * 4):
        if len(out) >= n:
            break
        pool = [p for p in players
                if not (base_position(p.get("dk_pos")) == "QB"
                        and p["name"] in used_qbs)]
        res = optimize(pool, mode=mode, stack_n=stack_n, bring_back=bring_back,
                       iters=iters, seed=seed + i, **kw)
        if res is None:
            break
        names = {p["name"] for p, _ in res["lineup"]}
        if any(len(names & prev) > max_overlap for prev in
               ({p["name"] for p, _ in r["lineup"]} for r in out)):
            continue
        out.append(res)
        if res.get("stack"):
            used_qbs.add(res["stack"]["qb"])
    return out
