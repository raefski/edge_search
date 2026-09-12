"""The two NFL game theories, as objectives a lineup can be scored against.

WHAT WAS WRONG BEFORE
edge/dfs_opt_nfl.py had a cash mode that maximised the sum of projections and
a GPP mode that forced a stack and ranked by a correlation-aware ceiling. Those
are not two theories. The first one is the second one with the stack switched
off: both maximise a MEAN, and nothing in either priced the spread around it.
MLB has never worked that way -- its cash optimiser maximises a walk-rate
adjusted floor precisely because a double-up pays the same for 1st and 45th.

WHAT REPLACES IT, AND WHY IT IS ONE MODEL RATHER THAN TWO
A lineup is a sum of nine correlated random variables. Give every player a mean
and a standard deviation, combine them through the measured correlation matrix,
and BOTH theories fall out of the same two numbers:

    cash  =  mean  -  Z_CASH * sd        maximise the downside
    gpp   =  mean  +  Z_GPP  * sd        maximise the upside

That is the whole idea, and the useful part is what it implies without being
told. Correlation raises a lineup's sd. So the cash objective automatically
spreads across games and REFUSES a stack, while the GPP objective automatically
seeks one -- the same arithmetic, opposite signs, no separate rule saying "do
not stack in cash". The stack stops being a mode flag and becomes a consequence.

IT ALSO EXPLAINS A POSITION PREFERENCE NOBODY HAD TO ASSERT
SD_FIT below is measured, and the shape of it is the interesting part: a
quarterback's spread barely moves with his projection (sd ~ 7.98 + 0.038*proj,
i.e. ~8.5 across the board) while a tight end's nearly doubles it
(2.21 + 0.480*proj). At a 20-point projection that is a coefficient of
variation of 0.44 for a QB against 0.59 for a TE, and 0.93 for a defence. So
"pay up at quarterback and punt the volatile slots" is not folk wisdom bolted
on here -- it is what maximising a floor does once the spreads are real.

PROVENANCE, PER BLOCK
  * SD_FIT / FLOOR_FIT -- scripts/nfl_variance_fit.py, 9,979 leak-free
    player-week projections over 2020+2021. DST's row is the unconditional
    spread of 1,056 team-weeks, because a defence has no player row and its
    projection's own range across a slate is only a few points wide.
  * CORR -- scripts/nfl_correlation.py, nflverse 2023+2024, 544 games. The
    same numbers edge/dfs_opt_nfl.py's docstring quotes and the same ones its
    hard DST rule is built on; imported from here now so there is one copy.
  * OWNERSHIP -- a PRIOR, and the only unmeasured thing in this file. Say so
    out loud wherever it is used: there are no NFL contest exports on this
    machine, so unlike MLB's gammas (tuned against real DK entry data) this is
    a shape with a plausible parameter and no validation behind it. It tilts
    GPP selection; it is deliberately not allowed to drive it.
"""
from __future__ import annotations

import math

#: sd(actual DK points) ~ a + b * projection, per position. Measured.
SD_FIT = {
    "QB": (7.980, 0.0384),
    "RB": (4.474, 0.2767),
    "WR": (3.756, 0.3570),
    "TE": (2.205, 0.4798),
    "DST": (5.564, 0.0000),
}

#: p25(actual DK points) ~ a + b * projection, per position. Measured, and NOT
#: what the optimiser sums -- the 25th percentile of a sum is not the sum of
#: 25th percentiles. Kept as the assumption-free read that the sd path is
#: sanity-checked against (see scripts/nfl_lineup_backtest.py).
FLOOR_FIT = {
    "QB": (-2.732, 0.7393),
    "RB": (-1.343, 0.5767),
    "WR": (-1.398, 0.5529),
    "TE": (-0.628, 0.4435),
}

#: How many standard deviations down/up each theory looks.
#:
#: Z_CASH = 0.75 is roughly the 23rd percentile of the lineup total, which is
#: the right neighbourhood for a double-up: the target is not the median score,
#: it is clearing a cash line that sits near the field's 45th percentile with
#: high probability. Z_GPP = 1.25 is the upside equivalent, deliberately
#: smaller in magnitude than the "win a Milly Maker" tail because a
#: mean-plus-k-sd objective with a large k stops discriminating between lineups
#: and simply picks the highest-variance nine players on the board.
Z_CASH = 0.75
Z_GPP = 1.25

CATCHERS = frozenset({"WR", "TE"})

#: Measured Pearson r between two players' DK totals IN THE SAME GAME.
#: scripts/nfl_correlation.py, nflverse 2023+2024 regular season, 544 games,
#: 291 players averaging 5+ DK points over 6+ games.
R_QB_OWN_CATCHER = 0.249
R_QB_OWN_RB = 0.065
R_CATCHER_TEAMMATE = -0.029
R_RB_OWN_CATCHER = 0.002
R_QB_OPP_QB = 0.170
R_QB_OPP_CATCHER = 0.089
R_CATCHER_OPP_CATCHER = 0.048
R_RB_OPP_RB = -0.074
R_DST_OPP_QB = -0.351
R_DST_OPP_RB = -0.202
R_DST_OPP_CATCHER = -0.118
R_DST_OWN_OFFENCE = -0.05


def base_position(player: dict) -> str:
    """The position SD_FIT is keyed on, from a DK position string.

    DK writes multi-eligibility as "RB/FLEX", and FLEX is a roster slot rather
    than a thing a player IS -- looking up a spread under it would silently
    return the default for every flex-eligible player on the board.
    """
    raw = (player.get("dk_pos") or "").upper()
    for token in raw.replace("/", " ").split():
        if token in ("DST", "D", "DEF"):
            return "DST"
        if token in SD_FIT:
            return token
        if token == "FB":
            return "RB"
    pos = player.get("pos") or set()
    for cand in ("DST", "QB", "TE", "RB", "WR"):
        if cand in pos:
            return cand
    return "WR"


def player_sd(player: dict) -> float:
    """One player's standard deviation in DK points, from the measured fit."""
    a, b = SD_FIT.get(base_position(player), SD_FIT["WR"])
    return max(0.5, a + b * max(0.0, float(player.get("proj") or 0.0)))


def player_floor(player: dict) -> float:
    """The measured 25th percentile for one player. Reporting, not summing."""
    pos = base_position(player)
    if pos == "DST":
        return max(0.0, float(player.get("proj") or 0.0) - 0.75 * player_sd(player))
    a, b = FLOOR_FIT.get(pos, FLOOR_FIT["WR"])
    return max(0.0, a + b * max(0.0, float(player.get("proj") or 0.0)))


def rho(a: dict, b: dict) -> float:
    """Measured correlation between two players' DK totals.

    Zero unless they are in the same game -- which is exactly why a cash lineup
    that maximises `mean - z*sd` spreads itself across games without being told
    to, and a GPP lineup that maximises `mean + z*sd` concentrates.
    """
    if a.get("game") != b.get("game") or a.get("game") is None:
        return 0.0
    pa, pb = base_position(a), base_position(b)
    same_team = a.get("team") == b.get("team")

    if "DST" in (pa, pb):
        if pa == pb:
            return 0.0
        dst, other = (a, b) if pa == "DST" else (b, a)
        po = base_position(other)
        if other.get("team") == dst.get("team"):
            return R_DST_OWN_OFFENCE
        return {"QB": R_DST_OPP_QB, "RB": R_DST_OPP_RB}.get(po, R_DST_OPP_CATCHER)

    if same_team:
        if "QB" in (pa, pb):
            other = pb if pa == "QB" else pa
            if other == "QB":
                return 0.0
            return R_QB_OWN_RB if other == "RB" else R_QB_OWN_CATCHER
        if pa in CATCHERS and pb in CATCHERS:
            return R_CATCHER_TEAMMATE
        if "RB" in (pa, pb):
            return R_RB_OWN_CATCHER
        return 0.0

    # Same game, opposite teams.
    if pa == "QB" and pb == "QB":
        return R_QB_OPP_QB
    if "QB" in (pa, pb):
        other = pb if pa == "QB" else pa
        return R_QB_OPP_CATCHER if other in CATCHERS else 0.0
    if pa == "RB" and pb == "RB":
        return R_RB_OPP_RB
    if pa in CATCHERS and pb in CATCHERS:
        return R_CATCHER_OPP_CATCHER
    return 0.0


def lineup_mean(lineup: list) -> float:
    return sum(float(p.get("proj") or 0.0) for p in lineup)


def lineup_sd(lineup: list) -> float:
    """sqrt of the full quadratic form, correlations included.

    This is the term that makes the two theories opposites. A QB stacked with
    two of his own receivers adds 2*0.249*sigma*sigma twice over to the
    variance; the cash objective subtracts that and the GPP objective adds it.
    """
    sds = [player_sd(p) for p in lineup]
    var = sum(s * s for s in sds)
    for i in range(len(lineup)):
        for j in range(i + 1, len(lineup)):
            var += 2.0 * rho(lineup[i], lineup[j]) * sds[i] * sds[j]
    return math.sqrt(max(0.0, var))


def score(lineup: list, mode: str = "cash", z_cash: float = Z_CASH,
          z_gpp: float = Z_GPP, own_weight: float = 0.0) -> float:
    """The objective a lineup is ranked by, for one theory.

    `own_weight` subtracts the lineup's total projected ownership, in DK
    points per 100 percentage points of summed ownership. Zero by default
    everywhere except GPP, and even there it is a tilt -- see OWNERSHIP's note
    in the module docstring on why an unvalidated field model is not allowed to
    drive selection.
    """
    mean, sd = lineup_mean(lineup), lineup_sd(lineup)
    if mode == "gpp":
        base = mean + z_gpp * sd
        if own_weight:
            base -= own_weight * sum(p.get("own", 0.0) for p in lineup) / 100.0
        return base
    return mean - z_cash * sd


# ---------------------------------------------------------------------------
# The field model. A PRIOR -- read the module docstring before trusting it.
# ---------------------------------------------------------------------------
#: Roster slots per position on a DK NFL Classic lineup, FLEX distributed over
#: the three positions eligible for it. Summed ownership within a position must
#: come to this many lineups' worth.
SLOTS_BY_POSITION = {"QB": 1.0, "RB": 2.4, "WR": 3.4, "TE": 1.2, "DST": 1.0}

#: Softmax sharpness on value, and the guards around it.
#:
#: UNFITTED: MLB's equivalent gammas were tuned against real DK contest
#: exports and there are no NFL exports on this machine. What IS enforced is
#: that the output be possible, which the first version of this was not -- an
#: unclipped z-score softmax handed a $2,900 tight end projecting 10.1 points
#: (a 3.5x value outlier on the live 2026-09-13 board) 99.1% ownership, and a
#: lineup summing to 377%. No NFL main-slate player has ever been 99% owned.
#:
#: So the value z-score is clipped before it is exponentiated, and the result
#: is capped and the excess redistributed. The cap is the binding statement:
#: the chalkiest play on a big main slate lands in the 40s.
OWNERSHIP_GAMMA = 1.1
OWNERSHIP_Z_CLIP = 2.0
MAX_OWN = 45.0


def _normalise(players, weights, target, cap):
    """Scale `weights` to sum to `target`, then cap and redistribute.

    Capping without redistributing would quietly lose ownership -- the field
    has to play somebody, so points taken off a capped player belong on the
    others rather than nowhere. Iterated because redistribution can push a
    second player over the cap.
    """
    owns = list(weights)
    free = [True] * len(owns)
    for _ in range(12):
        pool_total = sum(o for o, f in zip(owns, free) if f)
        capped_total = sum(o for o, f in zip(owns, free) if not f)
        room = target - capped_total
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


def add_ownership(pool: list, gamma: float = OWNERSHIP_GAMMA,
                  cap: float = MAX_OWN) -> list:
    """Annotate every player with `own` (percent) and `leverage`.

    Ownership is a power-softmax over VALUE (projected points per $1,000),
    normalised within a position so the position's total equals the number of
    lineup slots it fills, then capped. That shape is MLB's, which was fitted;
    the parameter is not. Read OWNERSHIP_GAMMA's note before using the number
    for anything load-bearing.

    `leverage` is the projection percentile minus the ownership percentile
    within position. Positive means the field is underweighting him relative to
    how good the model thinks he is -- the quantity a GPP wants and a cash
    lineup should completely ignore.
    """
    by_pos: dict[str, list] = {}
    for p in pool:
        by_pos.setdefault(base_position(p), []).append(p)

    for pos, players in by_pos.items():
        target = 100.0 * SLOTS_BY_POSITION.get(pos, 1.0)
        values = []
        for p in players:
            salary = float(p.get("salary") or 0) / 1000.0
            values.append((float(p.get("proj") or 0.0) / salary) if salary else 0.0)
        mean_v = sum(values) / len(values) if values else 0.0
        spread = (sum((v - mean_v) ** 2 for v in values) / len(values)) ** 0.5 or 1.0
        weights = []
        for v in values:
            z = max(-OWNERSHIP_Z_CLIP, min(OWNERSHIP_Z_CLIP, (v - mean_v) / spread))
            weights.append(math.exp(gamma * z))
        for p, own in zip(players, _normalise(players, weights, target, cap)):
            p["own"] = round(own, 1)

        n = len(players)
        proj_rank = {id(p): i for i, p in enumerate(
            sorted(players, key=lambda q: float(q.get("proj") or 0.0)))}
        own_rank = {id(p): i for i, p in enumerate(
            sorted(players, key=lambda q: q.get("own", 0.0)))}
        for p in players:
            denom = max(1, n - 1)
            p["leverage"] = round(
                100.0 * (proj_rank[id(p)] - own_rank[id(p)]) / denom, 1)
    return pool
