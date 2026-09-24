"""A one-sided milestone LADDER -> a projected mean. The NCAAF price problem.

WHY THIS EXISTS
edge/dfs_project.py turns a prop into a projection by devigging a two-sided
Over/Under pair and reading the implied mean off a normal with the sport's
fitted sigma. That is the right arithmetic for MLB and the NFL, where
DraftKings posts `Passing Yards O/U 248.5` with a price on each side.

It cannot be used for college football at all, because DraftKings does not
post that market. Measured live on 2026-09-24 across every NCAAF prop
subcategory DK serves, EVERY player market is a one-sided MILESTONE LADDER:

    marketType "Receiving Yards Milestones"
    selections  "15+" 1.03   "25+" 1.30   "40+" 1.95   "50+" 2.55 ...

There is no Under. `_pair()` returns None for every one of them, `project()`
returns proj=None for every player, and the pool is empty. That is not a
degraded projection -- it is no projection.

WHAT A LADDER ACTUALLY IS, AND WHY IT IS BETTER THAN THE LINE IT REPLACES
A two-sided line is ONE point on a distribution, and turning it into a mean
requires assuming the whole shape around it -- that is what the sigma is, and
edge/dfs_sport.py's NFL notes are candid that the sigmas turned out nearly
inert precisely because a book moves the line rather than the price.

A ladder is the distribution. Eight to twenty rungs trace out the book's own
survival function P(X >= t) across most of its mass. So the quantity the NFL
path has to ASSUME, this path can largely MEASURE:

    E[X] = integral of P(X >= t) dt over t >= 0          (continuous stats)
    E[N] = sum over k >= 1 of P(N >= k)                  (counting stats)

Both identities are exact, and the rungs supply most of the terms directly.
The two parts a ladder cannot reach -- below its lowest rung and above its
highest -- are filled by constants MEASURED against real college outcomes
(HEAD_FILL, TAIL_EXCESS, and scripts/ncaaf_ladder_fit.py), not by integrating
an assumed density. Every term that CAN come from a posted price does.

That last point is the whole design, and it was arrived at the hard way: the
first two versions of this module fitted a lognormal and then a Weibull to the
rungs and integrated it over the unpriced ranges. Graded against real
distributions with the market removed entirely, both overstated rushing and
receiving yards by 9-20%, because those stats have a mass at and near zero that
no smooth right-skewed density reproduces. See HEAD_FILL for the table.

That inversion is worth stating plainly, because it runs opposite to how the
sports look from the outside: college football's market data is uglier than
the NFL's and yields a better-founded projection.

HOW MUCH THE ASSUMED PART CAN COST -- READ `band`, NOT `head_share`
Every estimator here returns both, and they say different things. `head_share`
is how much of the answer was modelled rather than priced; `band` is how far
wrong that modelled part could legally be. They diverge a lot, and only the
second one is a risk measure.

Monotonicity is what makes the bound real. Below the lowest rung the survival
function cannot exceed 1 and cannot fall below the rung above it, so the
unpriced head is bracketed by facts about the posted prices rather than by the
fit. Measured on the live 2026-09-24 board:

    Jeremiah Smith, receiving yards, 15 rungs from 60+ to 200+
        mean 128.1   head_share 48%   band 6.3 yards = 0.63 DK points
    a receptions ladder running 4+ .. 8+
        mean 4.66    head_share 56%   band 0.82 receptions = 0.82 DK points
    a passing-TD ladder running 1+ .. 5+
        mean 2.54    head_share  3%   band 0.00

So a ladder that starts at "4+" really does carry most of a point of genuine
model risk, and one that starts at "1+" carries none -- while `head_share`
reads 56% and 3% for reasons that are mostly about where DK chose to start
posting. The tail is excluded from the band (monotonicity bounds it only by an
arbitrary cutoff) and is worth hundredths of a point on a real ladder.

THE ESTIMATOR IS CHECKED AGAINST REALITY, NOT JUST AGAINST ITSELF. Ladder means
for the 2026-09-26 slate against the same players' season averages from
cfbfastR:
    receiving yards   n=170   ladder mean 49.2 vs actual 50.8   r = 0.807
    rushing yards     n= 98   ladder mean 62.0 vs actual 61.8   r = 0.761
Unbiased to within 3%, and correlated at 0.76-0.81 -- which is the right
answer rather than a disappointing one, since the gap from 1.0 is the market
pricing this week's opponent and this player's recent form, and that is the
whole reason to read prices instead of season averages.

THE OVERROUND IS THE ONE THING A LADDER CANNOT SELF-CORRECT
A two-sided pair devigs itself: the two prices must together describe one
event, so the excess is visible and removable. A one-sided rung has no partner
and its vig is invisible.

It is tempting to think the ladder devigs itself collectively -- the bucket
masses (1-q_1), (q_1-q_2), ..., q_n telescope to exactly 1 no matter what the
prices are. They do, and that is precisely the trap: the sum is 1 by algebra,
not by calibration. If every rung carries a multiplicative overround then every
interior bucket is inflated and the whole excess is dumped into the one bucket
nobody priced, [0, t_min) -- which is exactly the head. The distribution looks
proper and its mean is biased HIGH.

So the overround is a fitted constant, per market, with the provenance of any
other fitted constant in this repo. See LADDER_OVERROUND below and
scripts/ncaaf_overround_fit.py for how it is measured against the two-sided
lines FanDuel does post for the same players.
"""
from __future__ import annotations

import math
from statistics import NormalDist

_N = NormalDist()

#: Probabilities are clamped off the endpoints before anything takes a probit
#: of them. inv_cdf(0) is -inf, and one mispriced rung would otherwise produce
#: an infinite implied mean and a lineup built entirely around that player --
#: the same guard, for the same reason, as dfs_project._fair_over.
_EPS = 1e-4

#: Multiplicative overround on ONE rung of a DraftKings milestone ladder, by
#: canonical market. p_true = q_raw / (1 + v).
#:
#: PROVENANCE: FITTED 2026-09-24 against the two-sided Over/Under lines FanDuel
#: posts for the same college players (scripts/ncaaf_overround_fit.py). A
#: FanDuel pair devigs itself, so it yields a clean P(X > L); DraftKings' raw
#: ladder interpolated at that same L gives q, and the ratio is the excess.
#: Medians, not means -- one stale rung produces a huge ratio and the mean
#: follows it.
#:
#: THESE ARE THE LEAST-VALIDATED NUMBERS IN THE NCAAF BUILD AND THEY MOVE THE
#: PROJECTION MORE THAN ANYTHING ELSE DOES. Overstating the overround
#: understates every projection roughly proportionally; understating it
#: overstates the head of every yardage ladder specifically. Re-fit when a week
#: of forward-test data exists and prefer the forward test: this is a
#: CROSS-BOOK ANCHOR, an assumption about a second book, not ground truth.
#:
#: n is small and is recorded per market for exactly that reason. FanDuel posts
#: college props only ~24-48h out, so a single run sees one night's games;
#: scripts/ncaaf_overround_fit.py accumulates pairs across runs.
#:
#: THE COUNT MARKETS ARE THE ONES TO RE-CHECK FIRST. They read 0.50 and 0.23 in
#: the first pass -- not a plausible hold, and the tell that FanDuel's "Over
#: 1.5" had been interpolated between DraftKings' "1+" and "2+" rungs instead
#: of read off "2+" directly, which for an integer stat is the same event. Once
#: that was fixed they fell into line with the yardage markets. Any future
#: refit that puts a count market far from the others should be suspected of
#: the same class of bug before being believed.
LADDER_OVERROUND = {
    "player_pass_yds": 0.072,          # n=33  (0 lopsided FanDuel pairs)
    "player_rush_yds": 0.102,          # n=49  (0)
    "player_reception_yds": 0.084,     # n=81  (0)
    "player_receptions": 0.075,        # n=22  (17) -- thin
    "player_pass_tds": 0.134,          # n=34  (30) -- thin, and the largest
}
#: Used for any market with no fitted entry. The median of the three yardage
#: markets, which are the ones with real sample behind them.
DEFAULT_OVERROUND = 0.084

#: Stats whose outcome is a COUNT, so the exact sum-of-survival identity
#: applies and only the two end terms are modelled.
COUNT_MARKETS = frozenset({"player_receptions", "player_pass_tds",
                           "player_rush_tds", "player_reception_tds",
                           "player_pass_attempts", "player_pass_completions",
                           "player_rush_attempts"})

#: WHERE THE UNPRICED HEAD SITS INSIDE ITS OWN BRACKET.
#:
#:     head = t_min * [ S(t_min) + c * (1 - S(t_min)) ]
#:
#: c = 0 is the rectangle t_min*S(t_min), the strict LOWER bound; c = 1 is the
#: full box, the strict UPPER bound. Monotonicity puts the truth between them
#: whatever the shape, so c is a position inside a bracket the posted prices
#: establish -- not an extrapolation.
#:
#: FITTED 2026-09-24, scripts/ncaaf_ladder_fit.py, against cfbfastR 2024+2025.
#: For one player's own sample the head integral is EXACTLY mean(min(x, t_min)),
#: so every player-season with 8+ games gives one exact observation of c and the
#: constant is the median over thousands of them.
#:
#: THIS REPLACED A PARAMETRIC FIT, AND THE REASON IS WORTH KEEPING. The first
#: version integrated a fitted lognormal over [0, t_min]; the second tried a
#: Weibull. Graded end-to-end against real distributions with the market removed
#: entirely, both overstated rushing and receiving badly:
#:        lognormal   Weibull   this
#:   pass    + 5.5%    + 0.8%   see scripts/ncaaf_ladder_fit.py --grade-only
#:   rush    +15.8%    + 8.9%
#:   rec     +20.0%    +12.4%
#: Both families miss the same thing: rushing and receiving yards have a large
#: mass at and near zero -- a receiver targeted once for four yards, or not at
#: all -- and no smooth right-skewed density reproduces it. Swapping families
#: moved the bias without removing it. Passing yards, which aggregate ~30
#: attempts and have no such spike, were nearly unbiased under both, and that
#: is exactly why c is larger for passing (0.53) than for receiving (0.38).
HEAD_FILL = {
    "player_pass_yds": 0.528,          # n=430
    "player_rush_yds": 0.400,          # n=1496
    "player_reception_yds": 0.378,     # n=2640
    "player_receptions": 0.430,        # n=1978
    "player_pass_tds": 0.500,          # n=103 -- thin
}
DEFAULT_HEAD_FILL = 0.42

#: MEAN EXCESS ABOVE THE TOP RUNG, as a multiple of the ladder's mean rung gap.
#:
#:     tail = S(t_max) * (m * gap)
#:
#: FITTED the same way and from the same identity -- for one player's sample the
#: tail integral is EXACTLY mean(max(0, x - t_max)).
#:
#: THE PARAMETRISATION IS THE FINDING. Measured against t_max the ratio runs
#: 0.017 / 0.056 / 0.067 across passing / rushing / receiving with wide spreads;
#: measured against the RUNG GAP it is 0.50 / 0.45 / 0.40 with tight ones. The
#: spacing a book chooses reflects the local scale of the distribution where it
#: stopped posting, so the gap is the natural unit and t_max is not.
#:
#: What this replaced was worse than a bad constant: integrating the fitted
#: lognormal past the top rung overstated the true tail by 680-740%.
TAIL_EXCESS = {
    "player_pass_yds": 0.500,          # n=38 -- thin
    "player_rush_yds": 0.450,          # n=250
    "player_reception_yds": 0.400,     # n=406
}
DEFAULT_TAIL_EXCESS = 0.45

#: E[N - k_max | N >= k_max] for a COUNT market, in whole counts. Measured the
#: same way, over the same seasons. These are as small as they look: the median
#: and the 90th percentile are both exactly 0 for both markets, because a book
#: stops posting rungs where almost nobody clears them and those who do clear
#: them by nothing. Worth under 0.07 DK points, and kept only so the estimator
#: is not biased low by construction. See mean_count on the unbounded geometric
#: tail this replaced.
COUNT_TAIL_EXCESS = {
    "player_receptions": 0.062,        # n=529
    "player_pass_tds": 0.000,          # n=83
}
DEFAULT_COUNT_TAIL = 0.05


# ---------------------------------------------------------------------------
# Reading a ladder
# ---------------------------------------------------------------------------
def rungs_from_outcomes(outcomes: list[dict], player: str) -> list[tuple[float, float]]:
    """[(threshold, decimal)] for one player, from Odds-API-shaped outcomes.

    A ladder arrives as many `Over` outcomes on one market, each with its own
    `point`, which is exactly what edge/odds/source.py emits when it is built
    with main_line_only=False. With main_line_only left ON it emits a single
    rung and this returns a one-element ladder -- enough to notice, not enough
    to project from, which is why build_pool asks for the full ladder.

    DraftKings writes a milestone "50+" and edge/arb/draftkings_nash.py stores
    it as Over 49.5 so that it can meet another book's line. The half-point is
    undone here: the ladder's own arithmetic wants the inclusive threshold,
    since P(X >= 50) and P(X > 49.5) are the same number for an integer stat
    and it is the integer that the sum-of-survival identity counts.
    """
    out = []
    for o in outcomes:
        if o.get("description") != player:
            continue
        if o.get("name") not in ("Over", "Yes"):
            continue
        point, price = o.get("point"), o.get("price")
        if point is None or not price or float(price) <= 1.0:
            continue
        t = float(point)
        if abs(t - round(t)) > 0.4:            # stored as x.5 -> threshold x+1
            t = math.floor(t) + 1.0
        out.append((t, float(price)))
    return _dedupe(out)


def _dedupe(rungs: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """One price per threshold, sorted by threshold ascending."""
    best: dict[float, float] = {}
    for t, d in rungs:
        if t not in best or d < best[t]:
            best[t] = d
        # the shorter price is the more confident quote when a feed repeats a
        # rung; taking min keeps this deterministic rather than order-dependent
    return sorted(best.items())


def survival(rungs: list[tuple[float, float]],
             overround: float = DEFAULT_OVERROUND) -> list[tuple[float, float]]:
    """[(threshold, devigged P(X >= threshold))], monotone non-increasing.

    Two corrections, in order, and the order matters:

    1. DEVIG. Every rung is divided by (1 + overround). See the module
       docstring on why a ladder cannot do this for itself.
    2. MONOTONICITY. P(X >= t) must not increase with t, and a real board
       violates that -- adjacent rungs are priced by different market makers at
       slightly different times, and a stale rung inverts the pair. Left alone
       an inversion produces a negative bucket mass, which is not a small error
       but an impossible one: it can drive the fitted curve anywhere. The fix
       is pool-adjacent-violators, the standard isotonic regression, which
       replaces a violating run with its mean and is the least-squares-closest
       monotone sequence to what was posted.
    """
    scale = 1.0 / (1.0 + max(0.0, overround))
    pts = [(t, min(1.0 - _EPS, max(_EPS, (1.0 / d) * scale))) for t, d in rungs]
    return list(zip((t for t, _ in pts), _pava([p for _, p in pts])))


def _pava(values: list[float]) -> list[float]:
    """Least-squares-closest NON-INCREASING sequence. Pool adjacent violators.

    Blocks carry (sum, count) so a pooled block can itself be pooled with the
    next one without re-walking -- the usual linear-time form.
    """
    blocks: list[list[float]] = []            # [sum, count]
    for v in values:
        blocks.append([v, 1.0])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            s, c = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
    out: list[float] = []
    for s, c in blocks:
        out.extend([s / c] * int(c))
    return out


# ---------------------------------------------------------------------------
# The fitted curve -- used only where the ladder does not reach
# ---------------------------------------------------------------------------
def fit_lognormal(points: list[tuple[float, float]]) -> tuple[float, float, float] | None:
    """(A, mu, sigma) for S(t) = A * P(LogNormal(mu, sigma) >= t), t > 0.

    `A` is the mass above zero: a receiver can finish a game with no catches,
    and a ladder that starts at 15+ cannot see the difference between "rarely
    plays" and "plays and is held under 15". Both press S down by the same
    factor, and both are correctly absorbed into A -- which is why A is fitted
    rather than fixed at 1, and why it must never be read as "probability he
    records a catch". It is only the level the curve is scaled to.

    Lognormal because the quantity is positive and right-skewed, and because
    it linearises: with z = probit(S(t)/A),

        z = (mu - ln t) / sigma

    is a straight line in ln t. So for a FIXED A the fit is an ordinary
    least-squares line and needs no optimiser, and A itself is a one-dimensional
    grid search over the only range it can legally occupy -- A must exceed every
    S(t) for the probit to be finite, so the grid starts just above the lowest
    rung's probability. That is usually a narrow window, which is the useful
    part: when the bottom of the ladder is priced near certainty there is very
    little room for the unpriced head to hide in.

    Returns None when fewer than three usable rungs survive, because two points
    determine a line exactly and leave no residual to choose A by -- a fit with
    no degrees of freedom would silently return whichever A the grid tried
    first.
    """
    usable = [(t, p) for t, p in points if t > 0 and _EPS < p < 1.0 - _EPS]
    if len(usable) < 3:
        return None
    p_max = max(p for _, p in usable)
    lo = min(0.999, p_max + 1e-3)
    best = None
    for i in range(41):
        A = lo + (1.0 - lo) * i / 40.0
        if A <= p_max:
            continue
        xs, zs = [], []
        for t, p in usable:
            r = p / A
            if not (_EPS < r < 1.0 - _EPS):
                continue
            xs.append(math.log(t))
            zs.append(_N.inv_cdf(r))
        if len(xs) < 3:
            continue
        fit = _ols(xs, zs)
        if fit is None:
            continue
        slope, intercept, sse = fit
        if slope >= -1e-6:              # z must DECREASE in ln t; sigma > 0
            continue
        sigma = -1.0 / slope
        mu = intercept * sigma
        if not (0.05 < sigma < 5.0):
            continue
        if best is None or sse < best[0]:
            best = (sse, A, mu, sigma)
    return (best[1], best[2], best[3]) if best else None


def _ols(xs: list[float], ys: list[float]) -> tuple[float, float, float] | None:
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    sse = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    return slope, intercept, sse


def _curve(fit, t: float) -> float:
    """S(t) from a fitted (A, mu, sigma), for t > 0."""
    A, mu, sigma = fit
    if t <= 0:
        return A
    return A * _N.cdf((mu - math.log(t)) / sigma)


# ---------------------------------------------------------------------------
# The estimators
# ---------------------------------------------------------------------------
def mean_count(rungs: list[tuple[float, float]],
               overround: float = DEFAULT_OVERROUND,
               head_fill: float = DEFAULT_HEAD_FILL,
               market_hint: str | None = None,
               max_k: int = 40) -> dict:
    """E[N] for a COUNTING stat, from the exact sum-of-survival identity.

        E[N] = sum over k >= 1 of P(N >= k)

    Every k the ladder prices contributes its posted probability unchanged.
    Only the terms the ladder does not reach are modelled: the ks below its
    lowest rung, and the tail above its highest. Both are read off the fitted
    curve, and both are CLAMPED to stay monotone against their observed
    neighbour, so a bad fit can never make the sum non-monotone.

    Returns {mean, priced, modelled, head_share, rungs, fit}.
    `priced` and `modelled` split the sum so a caller can see how much of the
    answer was a price rather than a model, and `band` is how wide the
    modelled part could legally be -- read that one, not the share.
    """
    pts = survival(rungs, overround)
    if not pts:
        return {"mean": None, "priced": 0.0, "modelled": 0.0, "band": 0.0,
                "head_share": 1.0, "rungs": 0, "fit": None}
    known = {int(round(t)): p for t, p in pts if t >= 1}
    if not known:
        return {"mean": None, "priced": 0.0, "modelled": 0.0, "band": 0.0,
                "head_share": 1.0, "rungs": 0, "fit": None}
    k_lo, k_hi = min(known), max(known)
    priced = sum(known.values())

    # Below the lowest priced rung. Monotonicity brackets every one of these
    # terms in [S(k_lo), 1], and head_fill says where in that bracket the
    # measured truth sits -- the same mechanism, and the same fitted constant,
    # as the continuous head. Each term is placed closer to 1 than the one
    # above it, since the survival function is still rising as k falls.
    modelled = 0.0
    floor = known[k_lo]
    for k in range(k_lo - 1, 0, -1):
        floor = min(1.0, floor + head_fill * (1.0 - floor))
        modelled += floor

    # The tail above the top priced rung, as a MEASURED mean excess.
    #
    # This replaced a geometric decay whose ratio was read off the last two
    # rungs, and the replacement was not a refinement -- the old version was
    # unbounded in a way that fired on real ladders. When the top two rungs
    # carry the SAME probability (which a book does post, and which the
    # isotonic step above also produces whenever it pools an inversion) the
    # ratio comes out 1.0, gets clamped to its 0.9 ceiling, and then compounds:
    # 0.083 * 0.9 / (1 - 0.9) = 0.75 of a touchdown invented out of a flat
    # pair. Graded against real distributions that pushed passing touchdowns
    # +33.7% and receptions +12.0%.
    #
    # The honest number is far smaller. E[N - k_max | N >= k_max] measured
    # over cfbfastR 2024+2025 (scripts/ncaaf_ladder_fit.py) is 0.062 for
    # receptions and 0.000 for passing touchdowns -- median zero for both, and
    # zero at the 90th percentile, because a book stops posting where almost
    # nobody clears the rung and those who do clear it by nothing. So the term
    # is a small constant times the top rung's own probability, and it cannot
    # run away.
    modelled += known[k_hi] * COUNT_TAIL_EXCESS.get(market_hint, DEFAULT_COUNT_TAIL)

    # THE BAND IS THE NUMBER WORTH READING, not the share.
    #
    # `head_share` says how much of the answer was modelled, and on a ladder
    # that starts high that reads alarmingly large while the actual exposure is
    # small -- because monotonicity brackets every unpriced term between its
    # priced neighbour and the limit of a probability. For the ks below the
    # lowest rung each term lies in [P(N >= k_lo), 1], so the whole head lies
    # in a window of width (k_lo - 1) * (1 - P(N >= k_lo)). That bracket is a
    # fact about the posted prices, not an output of the fit.
    #
    # The TAIL is not bracketed by monotonicity in any useful way (only by
    # max_k, which is arbitrary), so it is excluded from the band and reported
    # separately. It is worth a few hundredths of a point on a real ladder.
    band = max(0.0, (k_lo - 1) * (1.0 - known[k_lo]))
    total = priced + modelled
    return {"mean": total, "priced": priced, "modelled": modelled,
            "band": band, "head_share": (modelled / total) if total else 1.0,
            "rungs": len(pts), "fit": None}


def mean_continuous(rungs: list[tuple[float, float]],
                    overround: float = DEFAULT_OVERROUND,
                    head_fill: float = DEFAULT_HEAD_FILL,
                    tail_excess: float = DEFAULT_TAIL_EXCESS) -> dict:
    """E[X] for a CONTINUOUS stat (yardage), from the same identity in integral
    form:

        E[X] = integral from 0 to infinity of P(X >= t) dt

    Between two adjacent priced rungs the integral is done by TRAPEZOID on the
    posted probabilities rather than from the fitted curve. That is deliberate:
    the rungs are the data, and a curve that fits them well enough for the tail
    can still be visibly off between two of them. Trapezoid on a convex
    survival function is a slight UNDER-estimate, which is the safe direction
    for a projection that feeds a salary-cap optimiser.

    The head [0, t_min] and the tail [t_max, infinity) come from the fitted
    curve, and the head is where the model risk lives -- see the module
    docstring. `head_share` reports its size so the caller can see it.

    Falls back to a rectangular head (t_min * S(t_min), the strict lower bound)
    and an exponential tail when the lognormal fit fails, so a thin ladder
    still projects rather than dropping the player.
    """
    pts = survival(rungs, overround)
    pts = [(t, p) for t, p in pts if t > 0]
    if not pts:
        return {"mean": None, "priced": 0.0, "head": 0.0, "tail": 0.0,
                "band": 0.0, "head_share": 1.0, "rungs": 0, "fit": None}

    priced = 0.0
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        priced += 0.5 * (p0 + p1) * (t1 - t0)

    # The head, as a measured position inside the bracket monotonicity gives.
    t_min, p_min = pts[0]
    head = t_min * (p_min + head_fill * (1.0 - p_min))

    # The tail, as a measured mean excess in units of the ladder's own spacing.
    t_max, p_max = pts[-1]
    if len(pts) >= 2:
        gap = (t_max - t_min) / (len(pts) - 1)
        tail = p_max * tail_excess * gap
    else:
        tail = 0.0

    total = priced + head + tail
    # See mean_count on why this, and not head_share, is the number to read.
    # S(t) for t < t_min lies in [S(t_min), 1] by monotonicity alone, so the
    # head lies in [t_min * S(t_min), t_min] whatever the fit says -- a window
    # of t_min * (1 - S(t_min)). On the live 2026-09-24 board that is about
    # 6 yards (0.6 DK points) for the deepest ladder on the slate, even though
    # head_share reads 48%.
    band = max(0.0, t_min * (1.0 - p_min))
    return {"mean": total, "priced": priced, "head": head, "tail": tail,
            "band": band, "head_share": ((head + tail) / total) if total else 1.0,
            "rungs": len(pts), "fit": None}


def _integrate(f, a: float, b: float, n: int) -> float:
    """Composite Simpson over [a, b]. n is forced even."""
    if b <= a:
        return 0.0
    n = max(2, n + (n % 2))
    h = (b - a) / n
    total = f(a) + f(b)
    for i in range(1, n):
        total += (4.0 if i % 2 else 2.0) * f(a + i * h)
    return total * h / 3.0


def exceed(rungs: list[tuple[float, float]], threshold: float,
           overround: float = DEFAULT_OVERROUND) -> float | None:
    """P(X >= threshold), for a DK threshold bonus.

    THIS IS THE PART A TWO-SIDED LINE CANNOT DO HONESTLY, and edge/dfs_sport.py
    says so about the NFL build in its own words: scoring the 100-yard bonus
    off a fitted normal is "the one place the distributional assumption is
    doing something it is not really entitled to do", and it under-predicts the
    real tail by 1.3-1.7 percentage points because yardage is right-skewed and
    a normal's right tail is too thin.

    A ladder frequently prices the bonus threshold OUTRIGHT -- DK posts a
    "100+" rushing rung -- in which case this returns a devigged market price
    and no distribution is assumed at all. Only when the threshold falls
    between rungs, or beyond the top of the ladder, does the fitted curve get
    used, and then it is interpolating over a short span rather than
    extrapolating a shape.
    """
    pts = survival(rungs, overround)
    if not pts:
        return None
    for t, p in pts:
        if abs(t - threshold) < 0.5:
            return p
    below = [(t, p) for t, p in pts if t < threshold]
    above = [(t, p) for t, p in pts if t > threshold]
    if below and above:
        (t0, p0), (t1, p1) = below[-1], above[0]
        w = (threshold - t0) / (t1 - t0)
        return p0 + w * (p1 - p0)
    fit = fit_lognormal(pts)
    if fit:
        val = _curve(fit, threshold)
        if above:                       # never above the rung just beyond it
            val = min(val, above[0][1])
        if below:
            val = min(val, below[-1][1])
        return max(0.0, min(1.0, val))
    return above[0][1] if above else 0.0


def project_market(rungs: list[tuple[float, float]], market: str,
                   overround: float | None = None) -> dict:
    """One market's ladder -> {mean, ...}, choosing the right estimator.

    A counting stat gets the exact sum-of-survival treatment and a continuous
    one gets the integral. Getting this backwards is not a rounding error:
    integrating a receptions ladder treats "4+" as four yards of area and
    returns a mean of about 20.
    """
    if overround is None:
        overround = LADDER_OVERROUND.get(market, DEFAULT_OVERROUND)
    head_fill = HEAD_FILL.get(market, DEFAULT_HEAD_FILL)
    if market in COUNT_MARKETS:
        out = mean_count(rungs, overround, head_fill, market_hint=market)
    else:
        out = mean_continuous(rungs, overround, head_fill,
                              TAIL_EXCESS.get(market, DEFAULT_TAIL_EXCESS))
    out["market"] = market
    out["overround"] = overround
    out["head_fill"] = head_fill
    return out
