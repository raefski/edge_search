"""Can this set of players fill these roster slots? One implementation.

Extracted because it is the ONE part of lineup building that carries no sport
in it at all: given players with a set of eligible slot names and a multiset of
slots, is there an assignment? Batting orders, FLEX eligibility and stacking
rules all live above it.

edge/dfs_opt.py's own docstring records that this recursion had already been
reimplemented three times inside that one module before someone noticed. NFL
needs it a fourth time, which is the point at which it stops being a copy and
starts being a shared function -- exactly the "duplicate first, abstract later"
line DFS_MULTISPORT_PLAN.md draws, reached honestly rather than pre-empted.
"""
from __future__ import annotations


def assign_slots(players, slots):
    """Assign each player a distinct slot they are eligible for.

    `players` are dicts with a `pos` set of slot names. `slots` is a multiset
    (a list, e.g. ["WR","WR","WR","FLEX"]). Returns [(player, slot), ...] or
    None if no full assignment of every player exists.

    Leftover unused slots are fine: callers needing an exact 1:1 match pass
    len(players) == len(slots), where "every player placed" and "every slot
    used" are the same fact by pigeonhole.

    Players are tried fewest-options-first, which is what keeps the search
    small enough to be exact rather than greedy. Greedy first-match is the
    trap here: a flexible player takes the scarce slot and strands a
    single-eligibility one, and the caller sees "infeasible" for a lineup that
    is perfectly legal.
    """
    order = sorted(players, key=lambda p: len(p["pos"]))

    def bt(ps, remaining):
        if not ps:
            return []
        p = ps[0]
        for s in list(dict.fromkeys(remaining)):
            if s in p["pos"]:
                rest = remaining[:]
                rest.remove(s)
                sub = bt(ps[1:], rest)
                if sub is not None:
                    return [(p, s)] + sub
        return None

    return bt(order, list(slots))
