"""A NASCAR race, simulated. The projection layer for DK NASCAR DFS.

WHY A SIMULATOR AND NOT A PROJECTION PLUS A CORRELATION MATRIX
Every other DFS model in this repo gives each player a mean and a standard
deviation and combines them through a measured correlation matrix. That works
because two football players' scores are two loosely coupled random variables.

A NASCAR field is not. Three of its four scoring components are CONSTRAINED
SUMS over the whole field:

    finishing position   is a PERMUTATION -- exactly one driver finishes 1st
    place differential   sums to zero across the field, by construction
    laps led             sums to exactly `actual_laps`
    fastest laps         sums to exactly `green_laps`

So six drivers cannot all dominate, and a model that lets them is not slightly
optimistic -- it is describing a race that cannot happen. The
mean-plus-correlation approach can approximate this, but only by hand-fitting a
correlation matrix whose entries are all functions of the same constraint.
Sampling a whole race instead enforces every constraint exactly and produces
the correlation structure for free.

It also gives the optimiser something better than a mean and a spread: the
full joint distribution of a LINEUP's total, which in this sport is strongly
left-skewed (a wreck) and strongly right-skewed (a dominator), and is not
usefully summarised by two numbers.

HOW ONE RACE IS SIMULATED
  1. DNF. Drawn first, from a table measured per track type and starting
     bucket. A DNF is not a bad finish -- it is a bad finish AND the loss of
     the entire place differential, which for a front-row starter is the
     single worst outcome available.
  2. FINISHING ORDER. Each driver gets a latent quality from the fitted finish
     model plus noise at the fitted residual spread; the order is the ranking
     of that latent. Ranking a noisy latent is what makes the result a
     permutation automatically, with no rejection sampling and no repair step.
     DNF'd drivers are ranked among themselves at the back, because that is
     where they finish.
  3. LAPS LED. Allocated over the whole field by a Dirichlet whose weights
     decay with simulated finishing position and rise with the driver's own
     lead-propensity. The concentration parameter is fitted so the simulated
     top-1 share and the simulated fraction of the field that leads at all
     match the measured ones.
  4. FASTEST LAPS. The same allocation, correlated with laps led at the
     measured strength -- 0.77 at every track type EXCEPT a superspeedway,
     where it is 0.01 and the fastest lap is set by whoever happens to be in
     clean air. That single number is why a "superspeedway dominator" is not a
     thing, and the simulator has to know it rather than be told it.

WHAT THIS DOES NOT MODEL, STATED SO IT IS NOT MISTAKEN FOR MODELLED
  * Stage points. DraftKings NASCAR Classic does not score them.
  * Pit strategy, fuel windows, cautions falling at a particular lap. These
    are the mechanism behind the variance that IS modelled, absorbed into the
    residual spread rather than simulated on their own.
  * Team orders and manufacturer alliances at superspeedways. Real, and a
    known gap: drafting partners' finishes are genuinely correlated beyond
    what the permutation alone implies, and nothing here captures it.
"""
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Fitted constants. scripts/nascar_fit.py, NASCAR Cup 2022-2026,
# 6,088 scored driver-races with leak-free prior-12-race form.
# ---------------------------------------------------------------------------
#: E[finish] = c + b_start*start + b_form*form_finish + b_led*form_led
#:                + b_dnf*form_dnf,  with `resid_sd` the unexplained spread.
#:
#: THE R-SQUARED COLUMN IS THE POINT. Starting position correlates 0.458 with
#: finish at a short track and 0.076 at a superspeedway; the regressions land
#: at R^2 0.287 and 0.029 respectively. A superspeedway is very nearly a
#: lottery, and the simulator expresses that as an enormous residual spread
#: rather than as a separate rule.
#:
#: THE SUPERSPEEDWAY ROW IS DELIBERATELY REDUCED, and refusing to ship the full
#: fit there is the more defensible half of this table. Fitted on all four
#: predictors it reads formLed = +11.35; refitted without 2026 the same
#: coefficient reads +3.41. A term that moves by 3x between two overlapping
#: samples is noise, and it was only ever explaining noise:
#:
#:     superspeedway model                       R^2      resid sd
#:     constant only                          0.0000        11.23
#:     form_finish only                       0.0269        11.08
#:     start + form_finish                    0.0269        11.08   <- shipped
#:     all four predictors                    0.0287        11.07
#:
#: The three extra terms buy 0.0018 of R^2 between them. So formLed and
#: formDNF are zeroed here rather than shipped at an unstable point estimate,
#: and `start` is kept at its near-zero fitted value because that IS the
#: finding: at Daytona and Talladega where you start is worth almost nothing,
#: which is precisely why place differential from the back of the grid is close
#: to free there and nowhere else.
#:
#: The other three rows are stable. Refitting them without 2026 moves `start`
#: by 0.009 at most (short 0.295 vs 0.290) and R^2 by 0.01.
FINISH_MODEL = {
    #                const   start   formFin  formLed  formDNF  resid_sd   R2
    "superspeedway": (13.18, 0.006, 0.335, 0.0, 0.0, 11.08, 0.027),
    "intermediate": (5.68, 0.214, 0.533, -8.08, -5.70, 9.64, 0.181),
    "short": (2.98, 0.290, 0.641, -16.16, -10.62, 8.88, 0.287),
    "road": (6.93, 0.335, 0.346, -4.60, -5.09, 9.87, 0.182),
}

#: P(DNF) by track type and starting bucket.
#:
#: AT A SUPERSPEEDWAY THE FRONT OF THE GRID IS THE MOST DANGEROUS PLACE TO BE
#: -- 28.2% from the first ten against 23.3% from the back. Nowhere else does
#: the table run that way, and it is not a sampling artifact: the wreck happens
#: in the pack and the pack is at the front. At a short track the ordering is
#: the intuitive one, 6.6% from the front against 15.3% from the back.
DNF_RATE = {
    "superspeedway": {(1, 10): 0.282, (11, 20): 0.226, (21, 30): 0.236, (31, 99): 0.233},
    "intermediate": {(1, 10): 0.123, (11, 20): 0.152, (21, 30): 0.112, (31, 99): 0.182},
    "short": {(1, 10): 0.066, (11, 20): 0.055, (21, 30): 0.081, (31, 99): 0.153},
    "road": {(1, 10): 0.078, (11, 20): 0.068, (21, 30): 0.104, (31, 99): 0.132},
}

#: Laps-led and fastest-lap concentration, per track type.
#:
#:   led_top1   mean share of the race's laps led by whoever led most
#:   led_any    fraction of the field that leads at least one lap
#:   decay      how fast the lead weight falls with simulated finish position
#:   conc       Dirichlet concentration; lower = more winner-take-all
#:   fast_rho   correlation between a driver's lap-led share and his
#:              fastest-lap share
#:
#: fast_rho AT A SUPERSPEEDWAY IS 0.01, against 0.77 everywhere else. At
#: Daytona the fastest lap belongs to whoever is in clean air when it is set,
#: which has nothing to do with who is leading. That is the measured reason a
#: superspeedway "dominator" does not exist -- and note that led_top1 there is
#: 26.6% against 47.5% at a short track, so the lead itself is also spread.
LAP_MODEL = {
    "superspeedway": {"led_top1": 0.266, "led_any": 0.470, "decay": 0.055,
                      "conc": 0.55, "fast_rho": 0.01, "fast_any": 0.912},
    "intermediate": {"led_top1": 0.423, "led_any": 0.287, "decay": 0.135,
                     "conc": 0.28, "fast_rho": 0.77, "fast_any": 0.625},
    "short": {"led_top1": 0.475, "led_any": 0.200, "decay": 0.165,
              "conc": 0.22, "fast_rho": 0.77, "fast_any": 0.725},
    "road": {"led_top1": 0.482, "led_any": 0.203, "decay": 0.160,
             "conc": 0.22, "fast_rho": 0.76, "fast_any": 0.485},
}

DEFAULT_TRACK = "intermediate"

#: League-average form, for a driver with no history -- a rookie, or the first
#: race of a season. Deliberately mediocre rather than optimistic: an unknown
#: car is far more likely to be a backmarker than a contender, and a model that
#: guesses average for a part-time entry puts him in cash lineups.
DEFAULT_FORM = {"form_finish": 22.0, "form_led": 0.01, "form_fast": 0.012,
                "form_dnf": 0.14}


def _bucket(start: int, table: dict) -> float:
    for (lo, hi), p in table.items():
        if lo <= start <= hi:
            return p
    return 0.15


def dnf_probability(start: int, track: str, form_dnf: float | None = None) -> float:
    """P(this driver does not finish), blending the measured table with his own.

    The table is the base rate for the situation; a driver's own recent DNF
    rate carries real information about equipment and driving style but over
    twelve races it is a noisy estimate of a ~10% event, so it is weighted
    lightly. Clamped so no driver is ever certain either way.
    """
    base = _bucket(int(start or 20), DNF_RATE.get(track, DNF_RATE[DEFAULT_TRACK]))
    if form_dnf is None:
        return base
    return float(min(0.60, max(0.02, 0.75 * base + 0.25 * float(form_dnf))))


def expected_finish(driver: dict, track: str) -> float:
    """The fitted E[finish] for one driver, before race-day noise."""
    c, b_s, b_f, b_l, b_d, _sd, _r2 = FINISH_MODEL.get(
        track, FINISH_MODEL[DEFAULT_TRACK])
    return (c
            + b_s * float(driver.get("start") or 20)
            + b_f * float(driver.get("form_finish") or DEFAULT_FORM["form_finish"])
            + b_l * float(driver.get("form_led") or DEFAULT_FORM["form_led"])
            + b_d * float(driver.get("form_dnf") or DEFAULT_FORM["form_dnf"]))


def simulate(drivers: list[dict], race: dict, n_sims: int = 2000,
             seed: int = 0) -> np.ndarray:
    """(n_sims, n_drivers) of DK points. One column per driver, in input order.

    `drivers` are dicts with `start` and the four `form_*` keys; missing form
    falls back to DEFAULT_FORM. `race` carries `track_type`, `actual_laps` and
    `green_laps`.

    Returning the whole matrix rather than summary statistics is the point: the
    optimiser scores a LINEUP by summing six columns per simulated race, which
    is what makes the permutation constraint bind on the lineup rather than on
    the drivers individually.
    """
    rng = np.random.default_rng(seed)
    n = len(drivers)
    if n == 0:
        return np.zeros((n_sims, 0))

    track = race.get("track_type") or DEFAULT_TRACK
    laps = float(race.get("actual_laps") or 300)
    green = float(race.get("green_laps") or max(1.0, laps * 0.88))
    lap_cfg = LAP_MODEL.get(track, LAP_MODEL[DEFAULT_TRACK])
    resid_sd = FINISH_MODEL.get(track, FINISH_MODEL[DEFAULT_TRACK])[5]

    starts = np.array([float(d.get("start") or 20) for d in drivers])
    mu = np.array([expected_finish(d, track) for d in drivers])
    p_dnf = np.array([dnf_probability(int(d.get("start") or 20), track,
                                      d.get("form_dnf")) for d in drivers])
    lead_form = np.array([float(d.get("form_led") if d.get("form_led") is not None
                                else DEFAULT_FORM["form_led"]) for d in drivers])
    fast_form = np.array([float(d.get("form_fast") if d.get("form_fast") is not None
                                else DEFAULT_FORM["form_fast"]) for d in drivers])

    # 1. DNF, drawn per simulated race.
    dnf = rng.random((n_sims, n)) < p_dnf

    # 2. Finishing order = the ranking of a noisy latent quality. DNFs are
    #    pushed behind every runner by a constant larger than any latent can
    #    reach, so they occupy the back of the field and keep a sensible order
    #    among themselves (an early wreck finishes behind a late one).
    latent = mu + rng.normal(0.0, resid_sd, size=(n_sims, n))
    latent = latent + dnf * 1000.0
    order = np.argsort(latent, axis=1)
    finish = np.empty((n_sims, n), dtype=np.int32)
    ranks = np.arange(1, n + 1, dtype=np.int32)
    np.put_along_axis(finish, order, np.broadcast_to(ranks, (n_sims, n)), axis=1)

    # 3. Laps led. Weight falls with the simulated finishing position -- track
    #    position is what a lead is -- and rises with the driver's own
    #    lead-propensity. A DNF leads nothing after he is out; the simplifying
    #    assumption is that he leads nothing at all, which understates a car
    #    that dominated and then blew up. That is a real and known bias, and it
    #    is small: most DNFs are not leaders.
    decay, conc = lap_cfg["decay"], lap_cfg["conc"]
    weight = np.exp(-decay * (finish - 1)) * (1.0 + 8.0 * lead_form)
    weight = np.where(dnf, weight * 0.15, weight)
    laps_led = _dirichlet_alloc(rng, weight, conc, laps)

    # 4. Fastest laps. A blend of the lap-led weighting and a flat one, mixed
    #    at the measured correlation: at a superspeedway fast_rho is 0.01, so
    #    this is essentially uniform over the runners.
    rho = lap_cfg["fast_rho"]
    flat = np.where(dnf, 0.25, 1.0) * (1.0 + 4.0 * fast_form)
    fweight = rho * (weight / weight.sum(axis=1, keepdims=True)) + \
        (1.0 - rho) * (flat / flat.sum(axis=1, keepdims=True))
    fast_laps = _dirichlet_alloc(rng, fweight, max(conc, 0.6), green)

    # 5. DK points.
    pts = (_finish_points(finish)
           + (starts[None, :] - finish)
           + 0.25 * laps_led
           + 0.45 * fast_laps)
    return pts


def _dirichlet_alloc(rng, weight: np.ndarray, conc: float,
                     total: float) -> np.ndarray:
    """Split `total` across the field, per simulated race, at a given concentration.

    A Dirichlet with alpha = conc * normalised_weight. Low `conc` is
    winner-take-all, which is what a short track looks like (one car led 47.5%
    of the laps on average); high `conc` spreads it, which is a superspeedway.

    Sampling Dirichlet as normalised Gammas rather than calling
    rng.dirichlet in a loop, because this runs once per simulated race per
    allocation and the loop version dominates the whole simulation's runtime.
    """
    w = weight / weight.sum(axis=1, keepdims=True)
    alpha = np.maximum(conc * w * weight.shape[1], 1e-3)
    g = rng.gamma(alpha)
    s = g.sum(axis=1, keepdims=True)
    s[s <= 0] = 1.0
    return (g / s) * total


#: The DK finishing-points table, vectorised. Identical to
#: edge/nascar.finish_points -- see that function for the validation, and note
#: that this is the table with the extra -1 at each decade boundary, not the
#: flat one every strategy article prints.
def _finish_points(finish: np.ndarray) -> np.ndarray:
    f = finish.astype(np.float64)
    pts = np.where(f <= 10, 44.0 - f,
                   np.where(f <= 20, 43.0 - f,
                            np.where(f <= 30, 42.0 - f,
                                     np.where(f <= 40, 41.0 - f,
                                              np.maximum(0.0, 40.0 - f)))))
    return np.where(f == 1, 45.0, pts)


def summarise(scores: np.ndarray, drivers: list[dict]) -> list[dict]:
    """Per-driver mean/sd/floor/ceiling from a simulation, for the board.

    `floor` and `ceil` are the 25th and 90th percentiles of the driver's own
    simulated distribution rather than mean -/+ k*sd, because a NASCAR driver's
    distribution is neither normal nor symmetric: it has a wreck in the left
    tail and a dominator in the right, and a symmetric summary hides both.
    """
    out = []
    for i, d in enumerate(drivers):
        col = scores[:, i]
        out.append({
            **d,
            "proj": round(float(col.mean()), 2),
            "sd": round(float(col.std()), 2),
            "floor": round(float(np.percentile(col, 25)), 2),
            "ceil": round(float(np.percentile(col, 90)), 2),
            "p_win": round(float((col >= np.percentile(col, 99.9)).mean()), 4),
        })
    return out
