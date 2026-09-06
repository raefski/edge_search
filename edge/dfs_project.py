"""Prop prices -> projected DK fantasy points, for any sport.

THE ARITHMETIC, WHICH IS THE SAME EVERYWHERE
A two-sided prop is a book's own distribution for one player's stat. Devig the
pair to get a fair P(over), read the implied mean off a normal with the sport's
sigma for that stat, multiply by DK's points per unit, and add up. That is
exactly what edge/dfs.py::project_pitcher has always done for MLB; this module
is that function with the MLB constants lifted out into edge/dfs_sport.py.

THAT CLAIM IS TESTED, NOT ASSERTED. tests/test_dfs_project.py runs this engine
and project_pitcher over the same payloads and requires the projections to
match. project_pitcher is NOT replaced -- it is backtested code with real
money behind it, and the right way to introduce a generalisation is to prove
it reproduces the specific case rather than to swap it out and hope.

WHAT THE NORMAL IS AND IS NOT DOING
It converts one posted line plus its price into a mean. It is not a claim that
receiving yards are Gaussian -- they are famously not, being zero-inflated and
right-skewed. It is a monotone map from "the book thinks P(over 62.5) = 0.47"
to "so the book's mean is about 60.5", and for that job the tail shape matters
much less than the sigma. The one place the distributional assumption does
real work is a threshold bonus (P(rush yards >= 100)), which is a genuine tail
probability -- so those are the numbers to distrust first, and the reason
`bonus` is reported as its own component rather than folded into the total.
"""
from __future__ import annotations

from statistics import NormalDist

from edge.dfs_sport import Sport
from edge.oddsmath import devig

_N = NormalDist()


def _fair_over(over_dec: float, under_dec: float) -> float:
    """Devigged P(over), clamped off the endpoints.

    The clamp is not cosmetic: inv_cdf(0) is -inf, and one one-sided book
    quote would otherwise produce an infinite projected mean and a lineup
    built entirely around it.
    """
    p = devig([over_dec, under_dec])[0]
    return min(max(p, 1e-3), 1 - 1e-3)


def implied_mean(over_dec: float, under_dec: float, line: float, sigma: float) -> float:
    """The book's implied mean for a stat, from one rung of its ladder."""
    return line + sigma * _N.inv_cdf(_fair_over(over_dec, under_dec))


def exceed_prob(mean: float, sigma: float, threshold: float) -> float:
    """P(stat >= threshold) under the same fitted normal. For DK's bonuses."""
    if sigma <= 0:
        return 1.0 if mean >= threshold else 0.0
    return 1.0 - _N.cdf((threshold - mean) / sigma)


def _pair(market: dict) -> tuple[float, float] | None:
    """(over, under) decimals from one market dict, whichever way it is named."""
    for a, b in (("Over", "Under"), ("Yes", "No")):
        if market.get(a) and market.get(b):
            return float(market[a]), float(market[b])
    return None


def project(player_markets: dict, sport: Sport,
            require_all: bool = False) -> dict:
    """Project one player from the markets a book posts for them.

    `player_markets` is `{market_key: {"Over": dec, "Under": dec, "point": x}}`
    -- exactly what edge/dfs.py::player_markets already returns, so this drops
    straight in behind the existing Odds-API-shaped payload.

    `require_all` is the MLB contract: every market in `sport.required` must be
    present. NFL uses the default (any one), because its positions read
    disjoint markets -- a quarterback has passing yards and no receiving yards,
    and demanding both would project nobody.

    Returns {proj, components, means, have, imputed, bonus}. `proj` is None
    when the requirement is not met, which is the signal to leave the player
    out of the pool entirely rather than to score them badly.
    """
    present = [m for m in sport.required if _pair(player_markets.get(m) or {})]
    ok = (len(present) == len(sport.required)) if require_all else bool(present)
    if not ok:
        return {"proj": None, "components": {}, "means": {}, "have": [],
                "imputed": [], "bonus": 0.0}

    means: dict[str, float] = {}
    probs: dict[str, float] = {}
    for stat in sport.stats:
        md = player_markets.get(stat.market) or {}
        pair = _pair(md)
        if not pair:
            continue
        if stat.is_probability:
            probs[stat.market] = _fair_over(*pair)
        elif md.get("point") is not None:
            means[stat.market] = implied_mean(*pair, float(md["point"]), stat.sigma)

    # Anything no book posted, filled in by the sport's own rule and recorded
    # as imputed. A caller that wants only book-priced players can check this.
    imputed: list[str] = []
    if sport.impute:
        for market, value in (sport.impute({**means, **probs}) or {}).items():
            stat = sport.stat_for(market)
            if stat is None or market in means or market in probs:
                continue
            (probs if stat.is_probability else means)[market] = value
            imputed.append(stat.label)

    components: dict[str, float] = {}
    for stat in sport.stats:
        value = probs.get(stat.market) if stat.is_probability else means.get(stat.market)
        if value is None:
            continue
        components[stat.label] = round(stat.points * value, 2)

    # Threshold bonuses, scored on the distribution rather than the mean --
    # see the module docstring for why the step function is wrong.
    bonus = 0.0
    for b in sport.bonuses:
        stat = sport.stat_for(b.market)
        mean = means.get(b.market)
        if stat is None or mean is None or stat.sigma is None:
            continue
        bonus += b.points * exceed_prob(mean, stat.sigma, b.threshold)
    if bonus:
        components["bonus"] = round(bonus, 2)

    return {"proj": round(sum(components.values()), 1),
            "components": components,
            "means": {k: round(v, 2) for k, v in means.items()},
            "have": [sport.stat_for(m).label for m in means | probs
                     if sport.stat_for(m) and sport.stat_for(m).label not in imputed],
            "imputed": imputed,
            "bonus": round(bonus, 2)}


# --------------------------------------------------------------------------
# NFL team defence
# --------------------------------------------------------------------------
#: DK's points-allowed tiers, as (max allowed inclusive, DK points).
_PA_TIERS = ((0, 10.0), (6, 7.0), (13, 4.0), (20, 1.0), (27, 0.0), (34, -1.0))


def _points_allowed_score(pa: float) -> float:
    """Expected tier score for a CONTINUOUS expected points allowed.

    Interpolated across tier boundaries rather than snapped, for the same
    reason the yardage bonuses are scored on the distribution: an expectation
    of 20.4 points allowed is not "1 point, definitely", it sits between the
    20 and 27 buckets, and snapping makes the DST board jump on a tenth of a
    point of implied total.
    """
    edges = [-1.0] + [c for c, _ in _PA_TIERS] + [99.0]
    vals = [_PA_TIERS[0][1]] + [v for _, v in _PA_TIERS] + [-4.0]
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        if lo <= pa <= hi:
            if hi == lo:
                return vals[i]
            frac = (pa - lo) / (hi - lo)
            return vals[i] + frac * (vals[i + 1] - vals[i])
    return -4.0


#: Sacks + turnovers + defensive scores, as DK points, for an AVERAGE defence.
#: UNVALIDATED PRIOR. Roughly: 2.3 sacks (1 pt), 1.3 takeaways (2 pts), and a
#: small allowance for defensive/special-teams touchdowns. Fit against
#: nflverse team-week data before trusting the DST board -- and note the known
#: gap in edge/nfl.py::actual_dst_points (blocked kicks are not in the
#: nflverse schema at all), which any fit will inherit.
DST_BIG_PLAY_POINTS = 5.2


def project_dst(implied_opp_points: float,
                big_play_points: float = DST_BIG_PLAY_POINTS) -> dict:
    """Project an NFL DST from the market's own view of the opposing offence.

    A team defence has NO player prop market -- this is the one component of an
    NFL lineup that props cannot supply, and DFS_MULTISPORT_PLAN.md flagged it
    as the open problem. What the market DOES price is the opponent's implied
    team total, which is exactly the input DK's points-allowed tiers take:

        implied opponent points = total/2 - spread/2

    So the DST projection is the tier score at that number, plus a flat
    allowance for sacks and turnovers. The first term is market-derived and
    the second is a prior, which is why they are returned separately.
    """
    tier = _points_allowed_score(implied_opp_points)
    return {"proj": round(tier + big_play_points, 1),
            "components": {"points allowed": round(tier, 2),
                           "big plays": round(big_play_points, 2)},
            "implied_opp_points": round(implied_opp_points, 2),
            "imputed": ["big plays"]}


def implied_team_points(total: float, spread: float) -> float:
    """One team's implied points, from the game total and THAT team's spread.

    spread is negative for a favourite, so the favourite's implied score is
    the larger half. Standard identity, stated here because getting the sign
    backwards silently inverts every DST projection on the slate.
    """
    return total / 2.0 - spread / 2.0
