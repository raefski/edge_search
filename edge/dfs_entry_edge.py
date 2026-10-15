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

from zoneinfo import ZoneInfo


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
    """A FLOOR for the field at lock: a straight line through the fill so far.

    `history` = [(datetime, entries), ...] for one contest, oldest first, from
    data/contest_fill_log.csv. The rate is a straight line from the newest
    snapshot back to the newest one at least `min_span` minutes older -- a
    floor for a field that fills late (most do). Without such a pair the field
    is taken as it stands and no rate is claimed. For the field to EXPECT, use
    likely_at_lock() below, which knows how contests fill in their last hour."""
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


# ── the late fill, measured ────────────────────────────────────────────────────
# The straight line above is a floor, and it was being shown as the forecast. On
# 2026-10-14 "CFB $1K Wednesday Night Kickoff" (78 cap, $15) stood at 6 entries 16
# minutes out, flat over the previous snapshot, so the alert projected 6 at lock and
# "+495%". It went 7 -> 18 (9 min) -> 30 (3 min) -> 52 at lock: +28%.
#
# Measured on data/contest_fill_log.csv (10/11-10/15, 2,873 contests): of the room
# still empty N minutes before lock, the share taken by a contest's last snapshot
# (<= 4 min out) rises from ~0.15 at 10 min to ~0.6 at 1-2 h, and the two halves of
# the log agree within a few points at every horizon past 8 min. Emptier contests
# fill more of their room (0.45-0.75 under 25% full vs 0.25-0.6 above half), so the
# share is kept per fill band. It is refitted from the log on every run.
#
# Sport and day of the week matter too (Adam, 10/15). Same measure, 12-30 min out:
# NASCAR Sun 0.25 (n=142), CFB Wed 0.29 (23), NFL Sun 0.37 (722), NHL Tue 0.61 (50) vs
# Wed 0.33 (55), MLB 0.17-0.68 by day. NFL Sunday is NOT the best filler per contest:
# up to 1,439 guaranteed contests lock at 1 pm, so entries spread thin. So the share
# is also fitted per sport and per (sport, weekday of lock in ET), each cell shrunk
# toward the broader level by its contest count -- but forecasts use the pooled
# curve until STRATIFY is switched on (see it below for why it is off).

#: Fill bands (fraction of the cap entered) the late-fill share is kept for.
FILL_BANDS = ((0.0, 0.25), (0.25, 0.5), (0.5, 1.0))
#: Minutes-to-lock buckets the share is measured over.
MINUTE_BUCKETS = ((4, 8), (8, 12), (12, 18), (18, 25), (25, 35), (35, 50), (50, 75), (75, 120),
                  (120, 240))
#: A contest's last snapshot must be this close to lock to say how it finished.
LAST_SNAPSHOT_MAX_MIN = 4.0
#: The log's last snapshot is typically ~3 min out; the share covers up to there.
LAST_SNAPSHOT_TYPICAL_MIN = 3.0
#: Contests per cell before its own median counts at all.
MIN_CELL_N = 10
#: Weight (in contests) the broader level keeps against a cell's own median.
SHRINK_K = 30
#: Forecast from the sport / (sport, weekday) levels, or from all sports pooled.
#: OFF (Adam, 10/15): held out on the 4 days of log (fit < 10/13 or < 10/14, test
#: after; 87 / 50 contests under half full, 8-60 min out) the strata tied the pooled
#: curve (mean |error| 0.182 vs 0.184, 0.134 vs 0.136) and NFL Sunday had no held-out
#: contests at all. The strata are still fitted and printed; turn this on when a
#: held-out re-run (2+ weeks of log, an NFL Sunday on the test side) shows a gain.
STRATIFY = False
#: Share of the room still empty at the last pre-lock snapshot that fills by lock.
#: n=1: the 10/14 CFB contest above, 30 of 78 at 2.8 min -> 52 at lock = 22/48.
#: Replaced by the median of data/contest_finals.csv once TAIL_MIN_N finals exist
#: (per sport once that sport has TAIL_MIN_N of its own).
TAIL_DEFAULT = 0.46
TAIL_MIN_N = 5
#: Days are DK's: a contest's weekday is its lock's, in Eastern time.
ET = ZoneInfo("America/New_York")


def _median(xs: list) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _band(frac: float) -> int:
    return next((i for i, (lo, hi) in enumerate(FILL_BANDS) if lo <= frac < hi), len(FILL_BANDS) - 1)


def weekday(lock) -> str | None:
    """'Sun'..'Sat' of a lock time (aware datetime) in Eastern time."""
    return lock.astimezone(ET).strftime("%a") if lock is not None else None


def fit_late_fill(contests: dict, finals: dict | None = None) -> dict:
    """The late-fill model from the fill log.

    `contests` = {contest_id: {"cap": int, "points": {minutes_to_lock: entries},
    "sport": str, "lock": datetime}} (sport/lock optional); `finals` = {contest_id:
    entries at lock} (data/contest_finals.csv). Returns {"curves": {(sport, day, band):
    [(minutes, share), ...]}, "tail", "tails": {sport: share}, "tail_n", "n"}; sport and
    day are None at the broader levels."""
    cells: dict = {}
    used = 0
    for c in contests.values():
        cap, pts = c["cap"], c["points"]
        if cap < 10 or not pts:
            continue
        m_last = min(pts)
        if m_last > LAST_SNAPSHOT_MAX_MIN:
            continue
        e_last = min(pts[m_last], cap)
        sport, day = c.get("sport"), weekday(c.get("lock"))
        used += 1
        for b, (lo, hi) in enumerate(MINUTE_BUCKETS):
            ms = [m for m in pts if lo <= m < hi]
            if not ms:
                continue
            e = pts[max(ms)]
            if e >= cap:
                continue
            share = max(0.0, (e_last - e) / (cap - e))
            band = _band(e / cap)
            for key in ((None, None, None), (None, None, band), (sport, None, band),
                        (sport, day, band)):
                if key[0] is None and key[1] is not None:
                    continue
                cells.setdefault((key, b), []).append(share)
    # Broad to narrow: each level's bucket uses its own median when it has enough
    # contests, else the level above's value for that bucket.
    raw: dict = {}
    def level(key, parent):
        out = {}
        for b in range(len(MINUTE_BUCKETS)):
            xs = cells.get((key, b), [])
            if parent is not None and b in parent:
                # Shrink toward the broader level: a cell of n contests counts n
                # against SHRINK_K for the parent, so 10 NHL Wednesdays barely move
                # it and 700 NFL Sundays mostly speak for themselves.
                n = len(xs) if len(xs) >= MIN_CELL_N else 0
                out[b] = ((n * _median(xs) if n else 0.0) + SHRINK_K * parent[b]) / (n + SHRINK_K)
            elif len(xs) >= MIN_CELL_N:
                out[b] = _median(xs)
        raw[key] = out
        return out
    pooled = level((None, None, None), None)
    keys = {k for k, _ in cells}
    for band in range(len(FILL_BANDS)):
        top = level((None, None, band), pooled)
        for sport in sorted({k[0] for k in keys if k[0] is not None and k[2] == band}):
            mid = level((sport, None, band), top)
            for day in sorted({k[1] for k in keys if k[0] == sport and k[1] and k[2] == band}):
                level((sport, day, band), mid)
    curves = {}
    for key, vals in raw.items():
        if key[2] is None:
            continue
        pts_out, best = [], 0.0
        for b, (lo, hi) in enumerate(MINUTE_BUCKETS):
            if b in vals:
                # More time left can only mean as much of the room fills, never less.
                best = max(best, vals[b])
                pts_out.append(((lo + hi) / 2.0, best))
        curves[key] = pts_out
    tails_by: dict = {}
    for cid, final in (finals or {}).items():
        c = contests.get(cid)
        if not c or not c["points"]:
            continue
        m_last = min(c["points"])
        e_last = c["points"][m_last]
        if m_last <= LAST_SNAPSHOT_MAX_MIN + 1 and e_last < c["cap"]:
            share = max(0.0, (min(final, c["cap"]) - e_last) / (c["cap"] - e_last))
            tails_by.setdefault(c.get("sport"), []).append(share)
    every = [x for xs in tails_by.values() for x in xs]
    tail = _median(every) if len(every) >= TAIL_MIN_N else TAIL_DEFAULT
    tails = {sp: _median(xs) for sp, xs in tails_by.items() if sp and len(xs) >= TAIL_MIN_N}
    return {"curves": curves, "tail": tail, "tails": tails, "tail_n": len(every), "n": used,
            "cells": {k: len(v) for k, v in cells.items()}}


def _curve(model: dict, frac: float, sport: str | None, day: str | None) -> tuple[list, str]:
    """The most specific curve the model has for this contest, and what it is."""
    band = _band(frac)
    if not STRATIFY:
        pts = model["curves"].get((None, None, band))
        return (pts, "all sports") if pts else ([], "none")
    for key, label in (((sport, day, band), f"{sport} {day}"), ((sport, None, band), sport),
                       ((None, None, band), "all sports")):
        if key[0] is None and key != (None, None, band):
            continue
        pts = model["curves"].get(key)
        if pts:
            return pts, label
    return [], "none"


def late_fill_share(model: dict, frac: float, minutes: float, sport: str | None = None,
                    day: str | None = None) -> float:
    """Share of the empty room expected to fill between now and the last
    pre-lock snapshot, interpolated in minutes for this contest's sport, weekday
    and fill band (or the broadest level that has data)."""
    pts, _ = _curve(model, frac, sport, day)
    if not pts or minutes <= LAST_SNAPSHOT_TYPICAL_MIN:
        return 0.0
    prev_m, prev_s = LAST_SNAPSHOT_TYPICAL_MIN, 0.0
    for m, s in pts:
        if minutes <= m:
            return prev_s + (s - prev_s) * (minutes - prev_m) / (m - prev_m)
        prev_m, prev_s = m, s
    return prev_s


def fill_basis(model: dict | None, entries: int, cap: int, sport: str | None = None,
               day: str | None = None) -> str:
    """Which level of the model a contest is forecast from ("NFL Sun", "NHL", ...)."""
    if not model or not cap:
        return "none"
    return _curve(model, entries / cap, sport, day)[1]


def likely_at_lock(model: dict | None, entries: int, cap: int, minutes: float,
                   sport: str | None = None, day: str | None = None) -> int:
    """The field to expect at lock: the usual late fill for this sport and weekday,
    then the last minutes. Without a model (or a cap) the field is taken as it stands."""
    if not model or not cap or entries >= cap:
        return min(entries, cap) if cap else entries
    e = entries + late_fill_share(model, entries / cap, minutes, sport, day) * (cap - entries)
    # The tail covers the minutes after the last snapshot; inside them, a part of it.
    share = model.get("tails", {}).get(sport, model["tail"]) if STRATIFY else model["tail"]
    tail = share * min(1.0, max(0.0, minutes) / LAST_SNAPSHOT_TYPICAL_MIN)
    e += tail * (cap - e)
    return min(cap, round(e))
