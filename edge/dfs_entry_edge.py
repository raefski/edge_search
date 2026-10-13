"""What ONE MORE entry is worth in a guaranteed contest that is not full.

A guaranteed contest pays its prize table whether or not it fills. Those
prizes are fixed, so a thin field pays each entrant more and cashes more of
the field. Two numbers matter to one more entry:

  * cash:   the chance you finish in a paid place, if you are one of the
            n + 1 entries and finishes are equally likely (no skill assumed).
            With 5 places and 6 entries that is 5/6 -- 83%, where a full
            11-entry field gives 5/11 = 45%.
  * return: the expected prize per $1 entered, same assumption. Above 1.00
            means the contest pays more than its entries.

Skill only moves these numbers in your favour, so a return above 1.00 is a
floor for a player who is better than the field, not a promise. Ownership and
projections do not enter here; this is the contest's own arithmetic.
"""
from __future__ import annotations


def paid_out(tiers: list, entries: int) -> float:
    """Total prizes for places 1..entries. `tiers` = [[min_place, max_place, $ each], ...]."""
    return sum(v * max(0, min(hi, entries) - lo + 1) for lo, hi, v in tiers)


def places_paid(tiers: list) -> int:
    """How many places pay anything (the last place with a prize)."""
    return max((hi for _, hi, v in tiers if v > 0), default=0)


def entry_edge(tiers: list, entries: int, fee: float) -> dict:
    """Value of one more entry when the field already holds `entries`.

    Returns entries_after, cash (chance of a paid place), return (expected $
    back per $1), roi (return - 1), places (paid places)."""
    k = entries + 1
    places = places_paid(tiers)
    ret = paid_out(tiers, k) / (k * fee) if fee else 0.0
    return {"entries_after": k, "cash": min(places, k) / k, "return": ret,
            "roi": ret - 1.0, "places": places}


#: A rate needs a long baseline. Eight seconds apart projected a 90-entry field
#: to 103; ten minutes apart (one entry, 13:32 -> 13:42) projected +14 over
#: the 137 minutes to lock, which is noise, not a trend.
MIN_SPAN_MIN = 30.0


def projected_fill(history: list, cap: int, minutes_to_lock: float,
                   min_span: float = MIN_SPAN_MIN) -> dict:
    """Where the field is likely to be at lock.

    `history` = [(datetime, entries), ...] for one contest, oldest first, from
    data/contest_fill_log.csv. The rate is a straight line from the newest
    snapshot back to the newest one at least `min_span` minutes older -- a
    floor for a field that fills late (most do). Without such a pair the field
    is taken as it stands and no rate is claimed."""
    if not history:
        return {"now": None, "rate_per_min": 0.0, "at_lock": None, "basis": "no history"}
    t_last, e_last = history[-1]
    older = [(t, e) for t, e in history[:-1] if (t_last - t).total_seconds() / 60.0 >= min_span]
    left = max(0.0, minutes_to_lock)
    if not older:
        return {"now": e_last, "rate_per_min": 0.0, "at_lock": e_last if not cap else min(cap, e_last),
                "basis": "single snapshot"}
    t0, e0 = older[-1]
    span = (t_last - t0).total_seconds() / 60.0
    rate = max(0.0, (e_last - e0) / span)
    at_lock = e_last + rate * left
    if cap:
        at_lock = min(cap, at_lock)
    return {"now": e_last, "rate_per_min": round(rate, 3), "at_lock": round(at_lock),
            "basis": f"{len(history)} snapshots over {span:.0f} min"}
