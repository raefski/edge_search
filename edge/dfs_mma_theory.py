"""The two DK MMA game theories, scored against a SIMULATED FIELD.

WHAT CHANGES FROM NASCAR, AND WHY
NASCAR ranks lineups on percentiles of their OWN simulated total -- the 25th
for cash, the 90th for GPP. That is a sound proxy when the thing you have to
beat does not move with your own outcome. In MMA it moves a lot. A card is
twelve coin-flips-with-bonuses and the whole field is built from the same
twenty-four fighters: when the chalk favourite gets knocked out in round one,
YOUR score drops and so does the cash line, because half the field had him
too. A percentile of your own total cannot see that; it scores the night he
loses as a bad night for you when it is a bad night for everyone.

So the objectives are what a contest actually pays on, computed on the SAME
simulated fights the field is scored on:

    cash = P(lineup beats the double-up line)
           the line in each simulated card = the field's 56th percentile
           (a DK double-up pays the top ~44%)
    gpp  = P(lineup finishes in the top 1% of the field)

This is also where GPP leverage comes from, with no hand-set penalty: a
fighter the field is heavy on cannot lift you past the field on the nights he
wins, and a fighter the field ignored does. NASCAR subtracts
`own_weight * ownership`; here the simulation prices it.

THE FIELD IS A NOISY OPTIMISER, NOT A SET OF INDEPENDENT PICKS
The first version of this module did what the other sports do -- a softmax
over each fighter's value, normalised to 600% -- and it produced boards no
field could build: 70% on a $9,800 fighter, 70% on an $8,700 one and 59% on a
$9,300 one (an average lineup over the cap), and then 70% on BOTH corners of
the main event (140% of a fight nobody pairs). Patching each constraint on
top is whack-a-mole.

The structure that cannot produce an impossible board is the one the NFL
ownership research found in Dan's Projections ("Opt Rate" x popularity):
ownership is how often a fighter appears in the lineups people actually
BUILD, and people build lineups that score well on the projection they see.
So every legal, pair-free lineup gets weight

    w(L) = exp( (public projection of L - best) / tau )

ownership is each fighter's inclusion frequency under w, and the simulated
field is drawn from w directly. The cap, the no-pairing habit and the budget
are satisfied by construction, and tau is one number for a contest export to
fit. The PUBLIC projection mixes this model's with the FPPF DraftKings prints
in its own lobby, because that is the number the field is looking at.

EVERY CONSTANT IN THE FIELD MODEL IS A PRIOR. Nothing here has been fitted to
an MMA contest; scripts/mma_calibration.py --fit-ownership is where that
happens once exports exist.
"""
from __future__ import annotations

import numpy as np

#: A DK double-up pays the top ~44% (a 50/50 pays 50%).
CASH_PAID_FRACTION = 0.44
#: The GPP objective: finish in this top fraction of the field.
GPP_TOP_FRACTION = 0.01
#: Simulated field size. The GPP line is the 99th percentile of it, so 1,000
#: puts ten lineups above it per simulated card.
FIELD_SIZE = 1000

#: Temperature of the field's lineup softmax, in DK points of PUBLIC lineup
#: projection. At 8 the top fighter on the 2026-09-26 card came out ~60%
#: owned and ~650 lineups carried the weight; at 12, ~49% and ~2,700. PRIOR.
FIELD_TAU_GPP = 8.0
#: A cash field concentrates harder: measured in MLB (never MMA), 23-entry
#: cash games put 80-95% on the top pitcher where GPPs put 40-65%. PRIOR.
FIELD_TAU_CASH = 5.0
#: Weight of DK's own lobby FPPF in the projection the field is assumed to
#: be optimising. PRIOR -- the field reads it, how much is unmeasured. 0.3
#: was tried first and put 32% on a $6,900, 21%-to-win fighter because his
#: FPPF is 87.1 over three career wins; FPPF from a handful of fights is too
#: noisy to carry that much of anyone's projection.
PUBLIC_FPPF_WEIGHT = 0.15


def public_projection(pool: list) -> np.ndarray:
    out = []
    for d in pool:
        p = float(d.get("proj") or 0.0)
        f = d.get("dk_fppf")
        out.append(p if f is None else (1 - PUBLIC_FPPF_WEIGHT) * p + PUBLIC_FPPF_WEIGHT * float(f))
    return np.array(out)


def pair_free(lineups: np.ndarray, opponent: np.ndarray) -> np.ndarray:
    """Boolean mask: the lineup holds no two corners of one fight."""
    ok = np.ones(len(lineups), dtype=bool)
    for a in range(lineups.shape[1]):
        for b in range(a + 1, lineups.shape[1]):
            ok &= opponent[lineups[:, a]] != lineups[:, b]
    return ok


#: Captain Mode: the CPT slot (column 0 of a captain lineup) scores 1.5x.
CPT_MULT = 1.5


def lineup_weights(pub: np.ndarray, lineups: np.ndarray, tau: float,
                   captain: bool = False) -> np.ndarray:
    tot = pub[lineups].sum(axis=1)
    if captain:
        tot = tot + (CPT_MULT - 1.0) * pub[lineups[:, 0]]
    w = np.exp((tot - tot.max()) / tau)
    return w / w.sum()


def ownership(n_fighters: int, lineups: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Percent of the field holding each fighter, under lineup weights."""
    own = np.zeros(n_fighters)
    np.add.at(own, lineups.ravel(), np.repeat(weights, lineups.shape[1]))
    return 100.0 * own


def field_model(pool: list, lineups: np.ndarray, opponent: np.ndarray,
                tau: float, captain: bool = False
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ownership %, field lineups, their weights) for one field. Ownership is
    inclusion in ANY slot; captain_ownership() splits out the CPT share."""
    fl = lineups[pair_free(lineups, opponent)]
    w = lineup_weights(public_projection(pool), fl, tau, captain)
    return ownership(len(pool), fl, w), fl, w


def captain_ownership(n_fighters: int, lineups: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Percent of the field with each fighter in the CPT slot (column 0)."""
    own = np.zeros(n_fighters)
    np.add.at(own, lineups[:, 0], weights)
    return 100.0 * own


def sample_field(lineups: np.ndarray, weights: np.ndarray, n: int = FIELD_SIZE,
                 seed: int = 0) -> np.ndarray:
    """(n, 6) field lineups drawn from the field's own lineup distribution."""
    rng = np.random.default_rng(seed)
    return lineups[rng.choice(len(lineups), size=n, p=weights)]


def field_scores(points: np.ndarray, field: np.ndarray,
                 captain: bool = False) -> np.ndarray:
    """(n_sims, n_field) total DK points of every field lineup, per sim, as a
    matrix product (the gather version allocated a sims x field x 6 array)."""
    ind = np.zeros((points.shape[1], len(field)), dtype=np.float32)
    for c in range(field.shape[1]):
        np.add.at(ind, (field[:, c], np.arange(len(field))),
                  CPT_MULT if (captain and c == 0) else 1.0)
    return points.astype(np.float32) @ ind


def _row_quantile(a: np.ndarray, q: float) -> np.ndarray:
    k = int(round(q * (a.shape[1] - 1)))
    return np.partition(a, k, axis=1)[:, k]


def cash_line(points: np.ndarray, field: np.ndarray, captain: bool = False) -> np.ndarray:
    return _row_quantile(field_scores(points, field, captain), 1.0 - CASH_PAID_FRACTION)


def gpp_line(points: np.ndarray, field: np.ndarray, captain: bool = False) -> np.ndarray:
    return _row_quantile(field_scores(points, field, captain), 1.0 - GPP_TOP_FRACTION)
