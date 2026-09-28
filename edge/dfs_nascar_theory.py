"""The two NASCAR game theories, and the field model.

The other three sports in this repo score a lineup as `mean -/+ z*sd` through a
measured correlation matrix. NASCAR does not, and the reason is not stylistic.

WHY PERCENTILES AND NOT mean -/+ z*sd
`mean -/+ z*sd` is a summary that assumes the thing being summarised is roughly
symmetric. A NASCAR lineup's total is not, in both directions at once:

    per driver-race, measured over 6,088 starts
        mean 28.5   sd 25.5   p10 -2.6   p50 27.6   p90 59.5   p99 96.0
        minimum observed: -36.1

The left tail is a wreck -- an expensive car that starts near the front and is
collected on lap 12 loses the finishing points AND the entire place
differential, which is why the minimum is strongly negative rather than zero.
The right tail is a dominator: a car that leads 300 laps and wins collects
about 130 points where the median driver collects 27. Neither tail is
reachable from a mean and a standard deviation, and a Gaussian summary
understates both.

So `edge/nascar_sim.py` samples whole races and the objectives below read
PERCENTILES off the simulated lineup total:

    cash  =  the 25th percentile of the lineup's own simulated distribution
    gpp   =  the 90th percentile, minus an ownership tilt

That is the same idea as the other sports -- maximise the downside, maximise
the upside -- read off a real distribution instead of a fitted one.

WHAT THE SIMULATION DOES THAT A CORRELATION MATRIX CANNOT
Finishing position is a permutation, place differential sums to zero, laps led
sums to the race distance and fastest laps to the green laps. Six drivers
cannot all dominate. Sampling the whole field enforces all four constraints
exactly, so the anti-correlation between drivers is a consequence of the race
rather than a matrix someone had to fit. See edge/nascar_sim.py.

THE ONE STRUCTURAL FACT THIS PRODUCES, WITHOUT BEING TOLD
A lineup of six front-row starters has a terrible floor -- they cannot all
gain positions, and at a superspeedway the front of the grid is where the
wreck happens (28.2% DNF from the first ten, against 23.3% from the back).
A lineup of six deep starters has a poor ceiling -- nobody is leading laps.
The optimiser finds the mix on its own because the simulation prices both.
"""
from __future__ import annotations

import math

import numpy as np

#: Percentile of the lineup's own simulated total that each theory maximises.
#:
#: CASH_PCT = 25 is the same neighbourhood as the other sports' Z_CASH = 0.75
#: (about the 23rd percentile of a normal) and is chosen for the same reason:
#: a double-up pays the same for 1st and 45th, so the target is clearing a cash
#: line that sits near the field's median with high probability, not winning.
#:
#: GPP_PCT = 90 rather than 99. A NASCAR field is 36-40 cars and a lineup is
#: six of them, so the extreme upper tail of the simulated total is dominated
#: by which single driver happened to win -- maximising it just picks the six
#: highest-variance cars on the board and stops discriminating. The 90th is
#: high enough to price a dominator and low enough to still care about the
#: other five slots.
CASH_PCT = 25.0
GPP_PCT = 90.0


def lineup_scores(sim: np.ndarray, idx) -> np.ndarray:
    """The simulated total of one lineup, per simulated race."""
    return sim[:, list(idx)].sum(axis=1)


def score(sim: np.ndarray, idx, mode: str = "cash", own: float = 0.0,
          own_weight: float = 0.0) -> float:
    """The objective a lineup is ranked by, for one theory."""
    totals = lineup_scores(sim, idx)
    if mode == "gpp":
        base = float(np.percentile(totals, GPP_PCT))
        if own_weight:
            base -= own_weight * own / 100.0
        return base
    return float(np.percentile(totals, CASH_PCT))


def describe(sim: np.ndarray, idx) -> dict:
    """Everything the app shows about one lineup's simulated distribution."""
    totals = lineup_scores(sim, idx)
    return {
        "proj": round(float(totals.mean()), 1),
        "sd": round(float(totals.std()), 1),
        "floor": round(float(np.percentile(totals, CASH_PCT)), 1),
        "ceil": round(float(np.percentile(totals, GPP_PCT)), 1),
        "p99": round(float(np.percentile(totals, 99)), 1),
        "worst": round(float(totals.min()), 1),
    }


# ---------------------------------------------------------------------------
# The field model. FITTED on one race -- see SALARY_WEIGHT.
# ---------------------------------------------------------------------------
#: Six roster slots, so summed ownership across the board comes to 600%.
ROSTER_SLOTS = 6.0

OWNERSHIP_Z_CLIP = 2.5
#: A NASCAR field concentrates harder than any other sport in this repo: six
#: slots out of ~37 drivers. Kansas 2026-09-27 had Bowman at 52.5% in an 891-
#: entry GPP and 73.8% in a 229-entry cash game (which this cap clips).
MAX_OWN = 65.0

#: The field picks on SALARY and STARTING POSITION -- what it can see in the
#: lobby -- not on this build's projection.
#:
#: The first version was a softmax on a blend of value (simulated points per
#: $1,000) and simulated ceiling. On the first real export (Kansas 2026-09-27,
#: 891-entry GPP, board rebuilt on the real qualifying grid) it was WORSE than
#: guessing every driver equally owned: mean abs error 12.8 points against
#: 10.5 for uniform, rank correlation -0.04. It made the cheapest deep starters
#: chalk (Custer 57% predicted, 2.6% real; Ty Dillon 38%, 13.9%) because our
#: simulator gives a backmarker real place-differential points. The field does
#: not buy that. It played good cars starting deep (Bowman P33 52.5%, Blaney
#: P25 39.7%, Briscoe P23 38.3%) and the expensive favourites (Hamlin 51.0%,
#: Larson 38.0%), and left the $4-5K cars alone however far back they started.
#:
#: Salary is the field's read on car quality and start is its read on place
#: differential. On the same GPP:
#:
#:   | model                         | MAE  | rank corr |
#:   |-------------------------------|------|-----------|
#:   | value/ceiling blend (shipped) | 12.8 | -0.04     |
#:   | same, refitted                |  8.1 | +0.51     |
#:   | uniform                       | 10.5 |           |
#:   | salary + start (this)         |  5.9 | +0.78     |
#:
#: Adding the simulated ceiling or projection as a third term moved MAE by
#: under 0.1, so neither is in. The fit is flat around its optimum (0.6-0.8 /
#: 0.3-0.4 all score within 0.1), not a spike.
#:
#: ONE RACE. An intermediate track; a superspeedway, where the whole field
#: stacks deep starters, may want a steeper START_WEIGHT. The same race's
#: 229-entry cash game fits much steeper (1.9 / 1.5, MAE 7.5) -- small-field
#: cash concentrates harder, as in every other sport here -- and these are
#: the GPP numbers because only the GPP objective reads ownership.
SALARY_WEIGHT = 0.7
START_WEIGHT = 0.3


def add_ownership(pool: list, cap: float = MAX_OWN,
                  salary_weight: float = SALARY_WEIGHT,
                  start_weight: float = START_WEIGHT) -> list:
    """Annotate every driver with `own` (percent) and `leverage`.

    A softmax over z-scored salary and starting position, normalised so the
    board sums to six lineups' worth, then capped with the excess
    redistributed -- the field has to play somebody, so points taken off a
    capped driver belong on the others rather than nowhere. Before qualifying
    `start` is the provisional grid, the best guess there is at where the
    field will see each car line up.
    """
    if not pool:
        return pool
    salaries = [float(d.get("salary") or 0.0) for d in pool]
    starts = [float(d.get("start") or 0.0) for d in pool]
    s_mean, s_sd = _mean_sd(salaries)
    g_mean, g_sd = _mean_sd(starts)
    weights = [math.exp(salary_weight * _clip((s - s_mean) / s_sd)
                        + start_weight * _clip((g - g_mean) / g_sd))
               for s, g in zip(salaries, starts)]

    target = 100.0 * ROSTER_SLOTS
    for d, own in zip(pool, _normalise(weights, target, cap)):
        d["own"] = round(own, 1)

    n = len(pool)
    proj_rank = {id(d): i for i, d in enumerate(
        sorted(pool, key=lambda q: float(q.get("proj") or 0.0)))}
    own_rank = {id(d): i for i, d in enumerate(
        sorted(pool, key=lambda q: q.get("own", 0.0)))}
    for d in pool:
        d["leverage"] = round(
            100.0 * (proj_rank[id(d)] - own_rank[id(d)]) / max(1, n - 1), 1)
    return pool


def _mean_sd(xs):
    m = sum(xs) / len(xs) if xs else 0.0
    sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5 if xs else 1.0
    return m, (sd or 1.0)


def _clip(z):
    return max(-OWNERSHIP_Z_CLIP, min(OWNERSHIP_Z_CLIP, z))


def _normalise(weights, target, cap):
    """Scale to `target`, then cap and redistribute. Iterated, because
    redistribution can push a second driver over the cap."""
    owns = list(weights)
    free = [True] * len(owns)
    for _ in range(12):
        pool_total = sum(o for o, f in zip(owns, free) if f)
        capped = sum(o for o, f in zip(owns, free) if not f)
        room = target - capped
        if pool_total <= 0 or room <= 0:
            break
        scale = room / pool_total
        owns = [o * scale if f else o for o, f in zip(owns, free)]
        over = [i for i, (o, f) in enumerate(zip(owns, free)) if f and o > cap]
        if not over:
            break
        for i in over:
            owns[i], free[i] = cap, False
    return owns
