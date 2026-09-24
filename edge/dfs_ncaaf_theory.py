"""The two NCAAF game theories, as objectives a lineup can be scored against.

Same shape as edge/dfs_nfl_theory.py, and deliberately so: a lineup is a sum of
eight correlated random variables, give every player a mean and a standard
deviation, and both theories fall out of the same two numbers.

    cash  =  mean  -  Z_CASH * sd        maximise the downside
    gpp   =  mean  +  Z_GPP  * sd        maximise the upside

Correlation raises a lineup's spread, so the cash objective spreads across
games and refuses a stack while the GPP objective seeks one, as consequences
rather than as rules.

WHAT IS THE SAME AS THE NFL ENDS THERE. Every constant below is measured on
college football (scripts/ncaaf_fit.py, cfbfastR 2024+2025, 66,499 player-games
reduced to 4,380 player-seasons with 6+ games and a 4.0+ DK average), and
FOUR of the findings contradict the NFL build rather than reproducing it. They
are the reason this file exists instead of a `sport=` parameter on the NFL one.

--------------------------------------------------------------------------
1. THE S-FLEX IS A SECOND QUARTERBACK, AND IT IS NOT CLOSE
--------------------------------------------------------------------------
DK's college roster is QB / RB / RB / WR / WR / WR / FLEX (RB,WR) / S-FLEX
(QB,RB,WR). The S-FLEX is the whole game, because college quarterbacks
outscore everyone:

    position    mean DK   median   p90    p99
    QB            17.22     15.6   33.4   51.9
    RB            10.45      7.8   23.5   39.9
    WR             8.89      6.8   18.9   35.1

    of the 40 highest-scoring player-seasons in the sample: 31 QB, 7 RB, 2 WR

A quarterback is worth roughly two receivers per roster slot. The NFL
optimiser never has to ask this question and has no slot that could.

--------------------------------------------------------------------------
2. TWO QUARTERBACKS, BUT NEVER FROM THE SAME TEAM
--------------------------------------------------------------------------
R_QB_OWN_QB is -0.098. Two quarterbacks on one college roster are competing
for the same snaps -- college teams rotate, and a backup's good game is his
starter's bad one. So "play two QBs" and "play two QBs from one team" are
opposite pieces of advice, and the second one is enforced as a hard rule in
edge/dfs_opt_ncaaf.py rather than left to the objective.

--------------------------------------------------------------------------
3. TEAM-MATE RECEIVERS HELP EACH OTHER IN COLLEGE AND HURT EACH OTHER IN THE NFL
--------------------------------------------------------------------------
    WR <-> own WR       NFL  -0.029        NCAAF  +0.048

This is the single most consequential difference for roster construction.
edge/dfs_opt_nfl.py's docstring reasons, correctly for the NFL, that "a second
pass-catcher from the stack team is worth LESS than the first" because
team-mates share one ball, and sets stack_n=2 on that basis. In college the
sign flips: scoring is faster, games are less script-bound, and a big passing
day lifts every receiver on the field rather than dividing a fixed pie.

So a college stack COMPOUNDS where an NFL stack does not, and STACK_N defaults
to 3 here rather than 2. That is not a taste difference; it is a measured sign
change, and it is the first thing to re-check if the GPP lineups ever look
wrong.

--------------------------------------------------------------------------
4. DO NOT PAY UP AT QUARTERBACK FOR THE FLOOR -- THE NFL LOGIC INVERTS
--------------------------------------------------------------------------
edge/dfs_nfl_theory.py observes that an NFL quarterback's spread barely moves
with his projection (CV 0.44 at a 20-point projection against 0.59 for a tight
end) and concludes that "pay up at quarterback and punt the volatile slots" is
what maximising a floor does once the spreads are real.

College inverts it. SD_FIT below gives, at a 20-point projection:

    QB  sd 11.60   CV 0.580      <- the MOST volatile, not the least
    RB  sd 10.60   CV 0.530
    WR  sd 10.14   CV 0.507      <- the least

A college quarterback's intercept is 8.48 DK points -- even a modestly
projected one carries an enormous floor-less spread, because he runs, he
throws interceptions, and he gets pulled in a blowout. A cash lineup still
wants quarterbacks for the MEAN (finding 1), but it should not pay up for the
most expensive one expecting safety, which is exactly what the NFL habit would
do here.

--------------------------------------------------------------------------
PROVENANCE AND ITS LIMITS
--------------------------------------------------------------------------
SD_FIT is regressed against a LEAVE-ONE-OUT SEASON MEAN, not against a real
model projection. scripts/nfl_variance_fit.py had 9,979 stored leak-free
projections to regress against; nothing has ever projected a college slate, so
that log does not exist and cannot until this app has run for a season. A
season mean knows nothing about the opponent, so it explains less variance than
a real projection would and these spreads are, if anything, biased HIGH.
Re-fit from data/dfs_proj_log_ncaaf.csv once there is one.

OWNERSHIP is a PRIOR and the only completely unvalidated thing in this file.
Say so wherever it is used. It is the same shape MLB's fitted gammas use, with
a parameter that has never been checked against a college contest.
"""
from __future__ import annotations

import math

from edge.ncaaf import base_position

#: sd(actual DK points) ~ a + b * projection, per position. MEASURED --
#: scripts/ncaaf_fit.py --what sd, cfbfastR 2024+2025.
#: n: QB 6,714 player-games, RB 10,560, WR 27,264.
#: See finding 4 in the module docstring: the ordering is the opposite of the
#: NFL's and it changes what a cash lineup should pay for.
SD_FIT = {
    "QB": (8.476, 0.1560),
    "RB": (5.546, 0.2526),
    "WR": (4.143, 0.2999),
}

#: How many standard deviations down/up each theory looks. Carried over from
#: edge/dfs_nfl_theory.py unchanged, and carried over ON PURPOSE rather than
#: re-derived: Z_CASH is a statement about what beats a double-up's cash line
#: and Z_GPP about how far up the tail to look before the objective stops
#: discriminating between lineups and just picks the eight most volatile
#: players on the board. Neither depends on the sport. The SPREADS they
#: multiply are the sport-specific part, and those are measured above.
Z_CASH = 0.75
Z_GPP = 1.25

#: MEASURED Pearson r between two players' DK-point DEVIATIONS in the same
#: game. scripts/ncaaf_fit.py --what corr, cfbfastR 2024+2025.
#:
#: Deviations, not raw totals: each player's own season mean is removed first,
#: or the numbers would mostly be measuring that good players outscore bad ones
#: in every pairing equally.
#:
#: n is given because two of these are sign changes against the NFL and a sign
#: change deserves its sample stated next to it.
R_QB_OWN_WR = 0.217          # n=28,946   the stack        (NFL +0.249)
R_QB_OWN_RB = 0.093          # n=11,194                    (NFL +0.065)
R_QB_OWN_QB = -0.098         # n= 1,116   NEVER pair these (no NFL equivalent)
R_WR_OWN_WR = 0.048          # n=50,508   SIGN FLIP        (NFL -0.029)
R_RB_OWN_RB = 0.067          # n= 5,796
R_RB_OWN_WR = 0.026          # n=42,512
R_QB_OPP_QB = 0.131          # n= 3,325   the shootout     (NFL +0.170)
R_QB_OPP_WR = 0.058          # n=28,791   the bring-back   (NFL +0.089)
R_WR_OPP_WR = 0.048          # n=58,588
R_RB_OPP_RB = -0.015         # n= 8,309                    (NFL -0.074)
R_RB_OPP_WR = 0.023          # n=44,405
R_QB_OPP_RB = 0.028          # n=10,876

#: Pass-catchers, for stacking. Exactly one position in college, because
#: DraftKings lists tight ends as WR -- there is no second catcher position to
#: include and no TE-specific spread to fit.
CATCHERS = frozenset({"WR"})

#: How many of a quarterback's own pass-catchers to stack by default.
#:
#: THREE, not the NFL's two, and finding 3 in the module docstring is the whole
#: argument: team-mate receivers are POSITIVELY correlated in college (+0.048)
#: and negatively correlated in the NFL (-0.029), so a third receiver compounds
#: the bet here and dilutes it there. The roster also allows it -- three WR
#: slots plus a FLEX -- where an NFL Classic roster's TE slot gets in the way.
STACK_N = 3
BRING_BACK = 1


def player_sd(player: dict) -> float:
    """One player's standard deviation in DK points, from the measured fit."""
    a, b = SD_FIT.get(base_position(player.get("dk_pos")), SD_FIT["WR"])
    return max(0.5, a + b * max(0.0, float(player.get("proj") or 0.0)))


def rho(a: dict, b: dict) -> float:
    """Measured correlation between two players' DK totals.

    Zero unless they are in the same game -- which is exactly why a cash lineup
    that maximises `mean - z*sd` spreads itself across games without being told
    to, and a GPP lineup that maximises `mean + z*sd` concentrates.
    """
    if a.get("game") != b.get("game") or a.get("game") is None:
        return 0.0
    pa = base_position(a.get("dk_pos"))
    pb = base_position(b.get("dk_pos"))
    same_team = a.get("team") == b.get("team")
    pair = frozenset((pa, pb))

    if same_team:
        if pa == "QB" and pb == "QB":
            return R_QB_OWN_QB
        if pair == frozenset(("QB", "WR")):
            return R_QB_OWN_WR
        if pair == frozenset(("QB", "RB")):
            return R_QB_OWN_RB
        if pa == "WR" and pb == "WR":
            return R_WR_OWN_WR
        if pa == "RB" and pb == "RB":
            return R_RB_OWN_RB
        return R_RB_OWN_WR

    if pa == "QB" and pb == "QB":
        return R_QB_OPP_QB
    if pair == frozenset(("QB", "WR")):
        return R_QB_OPP_WR
    if pair == frozenset(("QB", "RB")):
        return R_QB_OPP_RB
    if pa == "WR" and pb == "WR":
        return R_WR_OPP_WR
    if pa == "RB" and pb == "RB":
        return R_RB_OPP_RB
    return R_RB_OPP_WR


def lineup_mean(lineup: list) -> float:
    return sum(float(p.get("proj") or 0.0) for p in lineup)


def lineup_sd(lineup: list) -> float:
    """sqrt of the full quadratic form, correlations included.

    This is the term that makes the two theories opposites. A quarterback
    stacked with three of his own receivers adds 2*0.217*sigma*sigma three
    times over, PLUS three receiver-to-receiver terms at +0.048 that the NFL
    version subtracts; the cash objective walks away from all of it and the
    GPP objective walks into it.
    """
    sds = [player_sd(p) for p in lineup]
    var = sum(s * s for s in sds)
    for i in range(len(lineup)):
        for j in range(i + 1, len(lineup)):
            var += 2.0 * rho(lineup[i], lineup[j]) * sds[i] * sds[j]
    return math.sqrt(max(0.0, var))


def score(lineup: list, mode: str = "cash", z_cash: float = Z_CASH,
          z_gpp: float = Z_GPP, own_weight: float = 0.0) -> float:
    """The objective a lineup is ranked by, for one theory."""
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
#: Roster slots per position on a DK CFB Classic lineup, with FLEX and S-FLEX
#: distributed over the positions eligible for them. Summed ownership within a
#: position must come to this many lineups' worth, and these must sum to 8.
#:
#: The split of the S-FLEX is the assumption doing the work. Finding 1 says a
#: quarterback is worth about two receivers per slot, so a sharp field would put
#: a QB there almost always; a real field contains many entries that never
#: consider it. 0.6 is a guess at that mix and it is the single number most
#: worth replacing with a fitted one from a real contest export.
SLOTS_BY_POSITION = {"QB": 1.6, "RB": 2.3, "WR": 4.1}

#: Softmax sharpness on value, and the guards around it. Shape inherited from
#: edge/dfs_nfl_theory.py, which inherited it from MLB where it WAS fitted.
#:
#: MAX_OWN is higher than the NFL's 45 because a college main slate is 12 games
#: and DraftKings prices only ~110 of its ~860 players with a prop at all, so
#: the field concentrates harder on a narrower board. Still a guess.
OWNERSHIP_GAMMA = 1.1
OWNERSHIP_Z_CLIP = 2.0
MAX_OWN = 55.0


def _normalise(weights, target, cap):
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
    lineup slots it fills, then capped. UNVALIDATED for college football.

    `leverage` is the projection percentile minus the ownership percentile
    within position. Positive means the field is underweighting him relative to
    how good the model thinks he is -- the quantity a GPP wants and a cash
    lineup should completely ignore.
    """
    by_pos: dict[str, list] = {}
    for p in pool:
        by_pos.setdefault(base_position(p.get("dk_pos")), []).append(p)

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
        for p, own in zip(players, _normalise(weights, target, cap)):
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
