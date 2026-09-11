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

import random

from edge.dfs_roster import assign_slots

SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
CAP = 50000
FLEX_FROM = {"RB", "WR", "TE"}
#: positions that catch passes from the quarterback being stacked
CATCHERS = frozenset({"WR", "TE"})

#: Measured Pearson r, same game, DK points. Used for the GPP ceiling bonus and
#: quoted in this module's docstring with its provenance.
R_QB_CATCHER = 0.249
R_QB_OPP_CATCHER = 0.089
R_CATCHER_TEAMMATE = -0.029


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


def _hill_climb(lineup, players, rng, locked=frozenset(), obj="proj"):
    improved = True
    while improved:
        improved = False
        for i in range(len(lineup)):
            cur = lineup[i]
            if cur["name"] in locked:
                continue
            others = sum(p["salary"] for p in lineup) - cur["salary"]
            names = {p["name"] for p in lineup}
            for cand in players:
                if cand["name"] in names or cand[obj] <= cur[obj]:
                    continue
                if others + cand["salary"] > CAP:
                    continue
                trial = lineup[:]
                trial[i] = cand
                if _valid(trial):
                    lineup = trial
                    improved = True
                    break
    return lineup, sum(p[obj] for p in lineup)


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


def _ceiling(lineup) -> float:
    """A correlation-aware ceiling, for ranking lineups that project the same.

    Not a simulation and not presented as one. Sum of projections plus a bonus
    for each correlated PAIR actually in the lineup, weighted by the measured r
    and the pair's own size. It exists to break ties in the direction the
    measurements point, which is what separates a stacked lineup from nine
    unrelated players at the same projected total.
    """
    total = sum(p["proj"] for p in lineup)
    bonus = 0.0
    for a in lineup:
        for b in lineup:
            if a["name"] >= b["name"]:
                continue
            scale = (a["proj"] * b["proj"]) ** 0.5
            qb_c = ("QB" in a["pos"] and b["pos"] & CATCHERS) or \
                   ("QB" in b["pos"] and a["pos"] & CATCHERS)
            same = a["team"] == b["team"]
            facing = a["team"] == b.get("opp_team")
            if qb_c and same:
                bonus += R_QB_CATCHER * scale
            elif qb_c and facing:
                bonus += R_QB_OPP_CATCHER * scale
            elif same and (a["pos"] & CATCHERS) and (b["pos"] & CATCHERS):
                # Measured at -0.029: two pass-catchers on one team share one
                # football. Counted because leaving it out is what makes a
                # ceiling function pile a whole receiving corps onto the
                # cheapest passing game -- each of them scores the full
                # QB bonus and nothing charges for the overlap between them.
                bonus += R_CATCHER_TEAMMATE * scale
    return total + bonus


def optimize(players, mode="cash", stack_qb=None, stack_n=2, bring_back=1,
             iters=2000, seed=0):
    """Best NFL lineup found.

    mode="cash"  maximize projection; no stack is forced.
    mode="gpp"   force a QB + `stack_n` of his own pass-catchers, plus
                 `bring_back` catchers from the opposing team, and rank the
                 survivors by the correlation-aware ceiling.

    `stack_qb` names the quarterback to stack; None in gpp mode tries every
    quarterback in the pool and keeps the best result.

    Returns {lineup: [(player, slot)], proj, ceil, salary, stack} or None --
    None means no legal lineup exists under the cap, which is a real answer on
    a short slate and must not be confused with a bad one.
    """
    rng = random.Random(seed)
    players = [p for p in players
               if p.get("proj") is not None and p.get("salary")]
    for p in players:
        p.setdefault("opp_team", None)
        p.setdefault("game", p.get("opp_team") or p["team"])
    if not players:
        return None
    obj = "proj"

    def search(forced, locked):
        best, best_key = None, None
        for _ in range(iters):
            lu = _fill(players, rng, forced, obj)
            if not lu:
                continue
            lu, _ = _hill_climb(lu, players, rng, locked=locked, obj=obj)
            key = _ceiling(lu) if mode == "gpp" else sum(p["proj"] for p in lu)
            if best_key is None or key > best_key:
                best, best_key = lu, key
        return best, best_key

    if mode != "gpp":
        best, _ = search(None, frozenset())
        return _result(best, None) if best else None

    qbs = [p for p in players if "QB" in p["pos"]]
    if stack_qb:
        qbs = [p for p in qbs if p["name"] == stack_qb]
    # try the strongest quarterbacks first, but do not silently cap the field
    qbs.sort(key=lambda p: -p["proj"])
    best, best_key, best_qb = None, None, None
    for qb in qbs:
        group = [qb] + stack_candidates(players, qb, stack_n, obj)
        if len(group) < 1 + stack_n:
            continue
        group += bring_back_candidates(players, qb, bring_back, obj)
        if assign_slots(group, SLOTS) is None:
            continue
        locked = frozenset(p["name"] for p in group)
        lu, key = search(group, locked)
        if lu and (best_key is None or key > best_key):
            best, best_key, best_qb = lu, key, qb
    if best is None:                     # no stack was feasible; say so by
        return None                      # returning nothing rather than a
                                         # silently unstacked lineup
    return _result(best, best_qb)


def _result(lineup, qb):
    stack = None
    if qb is not None:
        mates = [p["name"] for p in lineup
                 if p["team"] == qb["team"] and p["name"] != qb["name"]
                 and p["pos"] & CATCHERS]
        back = [p["name"] for p in lineup
                if p["team"] == qb.get("opp_team") and p["pos"] & CATCHERS]
        stack = {"qb": qb["name"], "team": qb["team"],
                 "with": mates, "bring_back": back}
    return {"lineup": assign_slots(lineup, SLOTS),
            "proj": round(sum(p["proj"] for p in lineup), 1),
            "ceil": round(_ceiling(lineup), 1),
            "salary": sum(p["salary"] for p in lineup),
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
