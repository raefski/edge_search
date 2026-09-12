"""DK NFL Classic lineup optimizer (dependency-free heuristic).

Roster: QB, RB, RB, WR, WR, WR, TE, FLEX (RB/WR/TE), DST -- $50,000 cap,
>=2 games. A player is a dict:
    {name, pos:set, salary, proj, team, opp_team, game}
`pos` holds DK slot names; FLEX eligibility is added here, not by the caller.

WHY THIS IS A SEPARATE MODULE AND NOT A MODE OF edge/dfs_opt.py
DFS_MULTISPORT_PLAN.md says "duplicate first, abstract later", and the honest
reading of the MLB optimizer is that almost nothing in it is a parameter of
NFL. `_consecutive_runs` walks a BATTING ORDER. `MAX_HITTERS_PER_TEAM` is a DK
MLB entry rule. `_hitter_slots_assignable` splits the roster into pitchers and
everyone else. `_secondary_stack` builds a second batting-order run. Those are
not MLB-flavoured settings of a general stacker; they are a different theory of
what correlates. Parameterising them would mean a `sport` branch inside every
one, which is the shape the plan warned against.

What genuinely IS shared is the slot matcher, and that is now imported from
edge/dfs_roster rather than copied -- see that module on why the fourth copy
is where extraction earns itself.

THE CORRELATION MODEL, MEASURED RATHER THAN ASSUMED
Every rule below is a number from nflverse 2023+2024 regular season, 544 games,
291 players averaging 5+ DK points over 6+ games (scripts/nfl_correlation.py).
Pearson r between DK point totals in the same game:

    QB  <-> own WR/TE            +0.249     <- the stack. This is the whole game.
    QB  <-> own TE               +0.266
    QB  <-> own WR               +0.246
    QB  <-> own RB               +0.065     <- a back is NOT a stack partner
    WR/TE <-> own WR/TE          -0.029     <- teammates COMPETE for targets
    RB  <-> own WR/TE            +0.002

    QB  <-> OPPOSING QB          +0.170     <- shootout, but only one QB slot
    QB  <-> OPPOSING WR/TE       +0.089     <- the bring-back
    WR/TE <-> OPPOSING WR/TE     +0.048
    RB  <-> OPPOSING RB          -0.074     <- game script: one back's day is
                                               the other's team trailing

    DST <-> OPPOSING QB          -0.351     <- the strongest number here
    DST <-> OPPOSING RB          -0.202
    DST <-> OPPOSING WR/TE       -0.118
    DST <-> own offence          -0.05      <- nothing; ignore it

Three things follow, and two of them are not the folk wisdom:

1. DST facing your own offence is a HARD constraint, not a preference. At
   -0.351 against the opposing quarterback it is a stronger relationship than
   the stack it would be cancelling, and it is the exact analogue of the MLB
   optimizer's pitcher-versus-hitter rule.

2. A second pass-catcher from the stack team is worth LESS than the first.
   Teammates are slightly negatively correlated (-0.029) because they share
   one ball; a QB+2 stack buys two exposures to the quarterback, not a
   compounding one. `stack_n` therefore defaults to 2 rather than 3.

3. Stacking a running back with his own quarterback does almost nothing
   (+0.065). RBs enter a lineup on their own projection.
"""
from __future__ import annotations

import math
import random

from edge import dfs_nfl_theory as theory
from edge.dfs_roster import assign_slots

SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
CAP = 50000
FLEX_FROM = {"RB", "WR", "TE"}
#: positions that catch passes from the quarterback being stacked
CATCHERS = frozenset({"WR", "TE"})

#: Measured Pearson r, same game, DK points -- re-exported from
#: edge/dfs_nfl_theory.py, which now owns the whole correlation matrix so the
#: objective and this module's docstring cannot drift apart.
R_QB_CATCHER = theory.R_QB_OWN_CATCHER
R_QB_OPP_CATCHER = theory.R_QB_OPP_CATCHER
R_CATCHER_TEAMMATE = theory.R_CATCHER_TEAMMATE


def eligible_slots(dk_position: str) -> set:
    """DK position string -> the roster slots that player can fill.

    DK writes multi-eligibility with a slash. FLEX is derived here rather than
    asked of the caller, because forgetting it is invisible: the lineup still
    builds, it is just never allowed to put a fourth receiver in the FLEX and
    quietly returns a worse one.
    """
    out = set()
    for tok in (dk_position or "").split("/"):
        t = tok.strip().upper()
        if t in ("DST", "D", "DEF"):
            out.add("DST")
        elif t in ("QB", "RB", "WR", "TE", "FB"):
            out.add("RB" if t == "FB" else t)
    if out & FLEX_FROM:
        out.add("FLEX")
    return out


def _valid(lineup) -> bool:
    if len(lineup) != len(SLOTS):
        return False
    if sum(p["salary"] for p in lineup) > CAP:
        return False
    if len({p["game"] for p in lineup}) < 2:
        return False
    # A DST must not face anything else in the lineup. Measured at -0.351
    # against the opposing quarterback -- a stronger relationship than the
    # +0.249 stack it would be cancelling. Same rule, same reason, as the
    # pitcher-versus-hitter check in edge/dfs_opt.py::_valid.
    dsts = [p for p in lineup if "DST" in p["pos"]]
    others = [p for p in lineup if "DST" not in p["pos"]]
    for d in dsts:
        for o in others:
            if o["team"] == d.get("opp_team") or d["team"] == o.get("opp_team"):
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
        # QB+2WR+bring-back group contains three FLEX-eligible receivers, and
        # first-match will happily spend the FLEX slot on the first of them and
        # then fail on a lineup that was legal.
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
    """This player's DK-point standard deviation, cached on the dict.

    Looked up thousands of times inside a hill climb, and the fit behind it is
    a two-term polynomial, so the cache is about the inner loop rather than
    about the arithmetic.
    """
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
    makes a variance-aware hill climb too slow to run before lock. Only the
    terms involving the swapped player change, so only those are touched.
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
#:
#: Not a quality knob so much as a latency one, and it is the difference
#: between a lineup arriving before lock and not. The unbounded version
#: evaluated EVERY player in the pool for every slot -- including quarterbacks
#: as replacements for defences, which can never be legal -- and a 212-player
#: NFL board times nine slots times a per-swap variance update did not finish
#: in two minutes on a real slate.
CLIMB_CANDIDATES = 40

#: How many quarterbacks survive the GPP screen and get a full search. See the
#: two-stage comment in optimize(): every quarterback is scored, this is only
#: how many are scored at full resolution.
GPP_FINALISTS = 5


def _candidate_index(players):
    """{slot: [players eligible there, best first]} for the current mode.

    Built once per optimize() call and read by every hill-climb step: the sort
    is the expensive part and the ordering does not change while a search runs.
    """
    return {slot: sorted(_eligible(players, slot),
                         key=lambda p: -p.get("_pv", p["proj"]))
            for slot in set(SLOTS)}


def _climb_candidates(index, cur, limit=CLIMB_CANDIDATES):
    """Best `limit` players who could stand in for `cur`, de-duplicated.

    Restricting to players who share one of `cur`'s slots is what makes the
    step cheap, and it is very nearly lossless: _valid re-runs the whole slot
    assignment anyway, so the moves this skips are mostly ones that were going
    to be rejected as illegal.
    """
    out, seen = [], set()
    for slot in cur["pos"]:
        for p in index.get(slot, ())[:limit]:
            if p["name"] not in seen:
                seen.add(p["name"])
                out.append(p)
    return out


def _hill_climb(lineup, players, rng, locked=frozenset(), mode="cash",
                z_cash=theory.Z_CASH, z_gpp=theory.Z_GPP, index=None):
    """Improve a legal lineup one swap at a time, on the FULL objective.

    Climbing on each player's own projection -- which is what this did before
    -- cannot see correlation at all, so it produced the same lineup for both
    modes and left the stack to be bolted on as a constraint. Climbing on
    `mean -/+ z*sd` is what lets cash walk downhill away from a stack and GPP
    walk uphill into one, without either being told to.
    """
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
    """The best `n` of a quarterback's own pass-catchers that can be rostered
    together with him.

    Admitted greedily in value order, each only if the WHOLE group stays
    slot-assignable -- the lesson edge/dfs_opt.py::_secondary_stack records
    from MLB, where picking a slice first and trimming later silently produced
    a smaller stack than asked for. Here the binding slot is TE: a QB+3 stack
    of three wide receivers needs WR,WR,WR plus the FLEX, which leaves nothing
    for a running back.
    """
    cands = [p for p in players
             if p["team"] == qb["team"] and p["name"] != qb["name"]
             and p["pos"] & CATCHERS]
    cands.sort(key=lambda p: -p.get(obj, p["proj"]))
    out = []
    for c in cands:
        if len(out) >= n:
            break
        if assign_slots([qb] + out + [c], SLOTS) is not None:
            out.append(c)
    return out


def bring_back_candidates(players, qb, k, obj="proj"):
    """The best `k` pass-catchers from the team the stacked QB is FACING.

    Measured at +0.089 against the quarterback -- about a third of the primary
    stack, and the reason it is 1 by default rather than 2. What it buys is not
    the correlation itself so much as the shape of the outcome it selects for:
    the games where a stack pays are the ones both offences scored in.
    """
    opp = qb.get("opp_team")
    if not opp:
        return []
    cands = [p for p in players if p["team"] == opp and p["pos"] & CATCHERS]
    cands.sort(key=lambda p: -p.get(obj, p["proj"]))
    return cands[:k]

def optimize(players, mode="cash", stack_qb=None, stack_n=2, bring_back=1,
             iters=2000, seed=0, z_cash=theory.Z_CASH, z_gpp=theory.Z_GPP,
             own_weight=0.0):
    """Best NFL lineup found, under one of the two game theories.

    mode="cash"  maximise `mean - z_cash*sd` of the lineup total. No stack is
                 forced and none is wanted: correlation raises the spread, so
                 the objective walks away from a stack on its own and spreads
                 across games. See edge/dfs_nfl_theory.py.
    mode="gpp"   maximise `mean + z_gpp*sd`, and force a QB + `stack_n` of his
                 own pass-catchers plus `bring_back` catchers from the
                 opposing team. The stack is forced rather than merely
                 preferred because a randomised search will not reliably find
                 the one configuration the objective most rewards.

    `stack_qb` names the quarterback to stack; None in gpp mode tries every
    quarterback in the pool and keeps the best result. In gpp mode the
    quarterbacks are tried in LEVERAGE order when the pool has been through
    theory.add_ownership, so a short search reaches the contrarian stacks
    first rather than the chalk one.

    Returns {lineup, proj, sd, floor, ceil, salary, stack, own} or None -- None
    means no legal lineup exists under the cap, which is a real answer on a
    short slate and must not be confused with a bad one.
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
    # out of where the fill put it. Correlation is a property of a PAIR and so
    # cannot appear here; it enters through the hill climb and the ranking.
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

    qbs = [p for p in players if "QB" in p["pos"]]
    if stack_qb:
        qbs = [p for p in qbs if p["name"] == stack_qb]
    # Leverage first where it is known, projection otherwise. Both are only an
    # ORDER -- every quarterback is still tried, so a short search reaches the
    # contrarian ones early without the chalk one being silently excluded.
    qbs.sort(key=lambda p: (-p.get("leverage", 0.0), -p["proj"]))

    # TWO STAGES, so that trying every quarterback stays affordable.
    #
    # A slate has ~24 of them and a full search each is ~24x the work of the
    # cash build -- 33s measured on the live 2026-09-13 board, which is too
    # long to sit through twice on a phone before lock. So every quarterback
    # gets a cheap look first and only the best few get the full one. This is
    # a resolution ladder, NOT a cap: no quarterback is excluded before being
    # scored, which is the property the previous comment here was defending.
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
    if best is None:                     # no stack was feasible; say so by
        return None                      # returning nothing rather than a
                                         # silently unstacked lineup
    return _result(best, best_qb, z_cash, z_gpp)


def _result(lineup, qb, z_cash=theory.Z_CASH, z_gpp=theory.Z_GPP):
    stack = None
    if qb is not None:
        mates = [p["name"] for p in lineup
                 if p["team"] == qb["team"] and p["name"] != qb["name"]
                 and p["pos"] & CATCHERS]
        back = [p["name"] for p in lineup
                if p["team"] == qb.get("opp_team") and p["pos"] & CATCHERS]
        stack = {"qb": qb["name"], "team": qb["team"],
                 "with": mates, "bring_back": back}
    mean, var = _stats(lineup)
    sd = math.sqrt(max(0.0, var))
    return {"lineup": assign_slots(lineup, SLOTS),
            "proj": round(mean, 1),
            "sd": round(sd, 1),
            # Reported from the SAME quadratic form the objective maximises,
            # so what the app shows and what the search chose cannot disagree.
            "floor": round(mean - z_cash * sd, 1),
            "ceil": round(mean + z_gpp * sd, 1),
            "salary": sum(p["salary"] for p in lineup),
            "own": round(sum(p.get("own", 0.0) for p in lineup), 1),
            "stack": stack}


def portfolio(players, n, max_overlap=6, mode="gpp", stack_n=2, bring_back=1,
              iters=1500, seed=0, **kw):
    """`n` lineups that are actually DIFFERENT from each other.

    Asking a deterministic search for several lineups and varying only the seed
    returns the same lineup several times -- it converges to the same optimum,
    which is correct behaviour for one lineup and useless for a portfolio. The
    point of entering more than one GPP lineup is covering more outcomes, so
    each lineup here must share at most `max_overlap` players with EVERY lineup
    already accepted.

    Diversity is bought by banning the previous lineup's quarterback stack
    rather than by re-rolling the seed and hoping: a portfolio built on one
    quarterback is one bet with extra entry fees, whatever its overlap count
    says. Returns fewer than `n` if the pool cannot support that many distinct
    lineups -- a short answer, never a padded one.
    """
    out, used_qbs = [], set()
    for i in range(n * 4):
        if len(out) >= n:
            break
        pool = [p for p in players
                if not ("QB" in p["pos"] and p["name"] in used_qbs)]
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
