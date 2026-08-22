"""Pick'em spread-edge model -- CBS Sportsline pick'em pools.

The mechanism: pool operators (CBS included) freeze a spread once posted and
never update it, while the real market keeps moving all week on injury news,
sharp money, and weather. The gap between the frozen number and the live
market is the edge -- this module scores it.

Validated 2026-08 on 2,878 NFL games (2014-2024), leak-free chronological
train/test split (train 2014-2022, test 2023-2024, evaluated once): following
the market's move off a stale line covered 55.5% ATS out-of-sample
(296-237-10), vs. 54.2% for blind favorites, and a dead coin flip (48-49%)
when the SAME strategies are run against a closing line instead of a stale
one -- confirming the edge is the staleness itself, not a market-beating
signal. Games that didn't move covered only 49.4% (the "COIN FLIP" tier
below), so the model deliberately claims no edge there rather than guessing.
See scripts/pickem_backtest.py and data/pickem_backtest_results.json for the
full methodology and numbers, and PICKEM_STATUS.md for the plain-English
summary + open questions.

Probability model: P(cover) = Phi(|edge| / 13.45), the classic normal
approximation of NFL margin-vs-spread error (Stern 1991, "On the Probability
of Winning a Football Game"; independently reconfirmed against the same
2014-2024 data this module is validated on -- see the calibration table in
data/pickem_backtest_results.json). Rule of thumb: every point of stale value
is worth about +3% win probability.

One honest gap, stated plainly rather than silently assumed away: CBS sets
its pick'em spreads "at its own discretion" (its posted contest rules) rather
than mirroring a specific sportsbook's opener, so CBS's number can start the
week already offset from the market by CBS's own house methodology -- not all
of an observed edge is necessarily time-decay staleness. Feed a genuine
posting-time market reading as `pool_line` where possible (see PICKEM_STATUS)
to net that baseline offset out; absent one, this module scores CBS's raw
number against the market and accepts that some of the edge could be
methodology noise rather than drift.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

SIGMA = 13.45  # points; normal-approx std dev of NFL margin vs. spread (Stern 1991)
KEY_NUMBERS = (3, 7)  # most common NFL winning margins -- books resist crossing these
MOVE_FLOOR = 0.5   # below this, no validated edge (test-era: 49.4%, ~coin flip)
SOLID_FLOOR = 1.5
STRONG_FLOOR = 3.0

# Points added to the effective edge when the move crosses a key number.
#
# DISABLED (0.0) ON PURPOSE -- read PICKEM_MODEL.md section 5d before changing.
# The key-number EFFECT is real and replicated: crossing 3 or 7 beat
# not-crossing in 9 of 9 dev seasons, and again on the 2023-24 holdout
# (62.9% vs 55.7%). But the MAGNITUDE fitted on dev (3.6 pts, from a
# +15.7pp dev gap) badly overshoots the holdout's +7.2pp gap: with the
# bonus live the model predicted 69.7% on those games against an actual
# 62.9% (-6.8pp), strictly WORSE calibration than leaving it off (+3.0pp).
#
# A shrunk value near 1.8 would calibrate well -- but that number can only
# be read off the holdout, which would make it a fitted-on-test parameter
# and no longer an honest out-of-sample result. So it ships at zero until
# 2025+ seasons provide enough fresh data to fit and validate it cleanly.
# `Pick.key_number` still reports the flag so the signal stays visible.
KEY_BONUS = 0.0

# A falling total means a lower-scoring game, which compresses margins and
# helps the underdog cover; a rising total does the reverse. Used ONLY to
# break ties on no-movement games, where taking the market favourite was
# measurably the wrong default (48.3% across the dev split). Beat that
# default in 8 of 9 dev seasons.
TOTAL_DRIFT_FLOOR = 0.5


def phi(x: float) -> float:
    """Standard normal CDF, stdlib-only (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def win_prob(edge_pts: float) -> float:
    """P(the side the market moved toward covers the frozen pool line)."""
    return phi(abs(edge_pts) / SIGMA)


def key_number_crossed(pool_line: float, live_line: float) -> bool:
    """True if 3 or 7 (either sign) sits strictly between the two lines."""
    lo, hi = min(pool_line, live_line), max(pool_line, live_line)
    return any(lo < k < hi for k in (*KEY_NUMBERS, *(-k for k in KEY_NUMBERS)))


def favorite_flipped(pool_line: float, live_line: float) -> bool:
    """True if the market now favors the side CBS did NOT freeze as favorite.

    The single strongest pattern in the backtest -- treated as an automatic
    STRONG play in `make_pick` regardless of the raw point gap.
    """
    return pool_line != 0 and live_line != 0 and (pool_line < 0) != (live_line < 0)


def effective_edge(pool_line: float, live_line: float) -> float:
    """|edge|, plus KEY_BONUS when the move crossed 3 or 7.

    A stale line sitting on the good side of the modal NFL margin is worth
    far more than its raw point size suggests: a market that moved from
    -2.5 through to -3.5 hands us the 3 for free. Empirically these games
    cover ~70% regardless of how big the move was, so the bonus is flat
    rather than proportional.
    """
    raw = abs(live_line - pool_line)
    if raw >= MOVE_FLOOR and key_number_crossed(pool_line, live_line):
        return raw + KEY_BONUS
    return raw


def _coinflip_side(live_line: float, total_open: float | None,
                   total_close: float | None) -> str:
    """Which side to take when the spread never moved.

    Falls back to the market favourite when no totals are available, which
    is what shipped before -- but when we DO have the totals, a drift of at
    least TOTAL_DRIFT_FLOOR points overrides it.
    """
    fav = 'home' if live_line < 0 else 'away'
    if total_open is None or total_close is None:
        return fav
    drift = total_close - total_open
    if abs(drift) < TOTAL_DRIFT_FLOOR:
        return fav
    if drift > 0:
        return fav                                    # more scoring -> favourite
    return 'away' if fav == 'home' else 'home'        # less scoring -> underdog


@dataclass
class Pick:
    matchup: str
    pool_line: float   # CBS's frozen home-team spread; negative = home favored
    live_line: float   # current (or at-lock) home-team spread, same convention
    edge_pts: float     # live_line - pool_line; negative = market moved toward home
    key_number: bool
    flipped: bool
    side: str           # 'home' or 'away'
    side_line: float    # the spread the picked side gets, from pool_line
    prob: float
    tier: str           # STRONG / SOLID / LEAN / COIN FLIP
    eff_edge: float = 0.0   # |edge| after any key-number bonus


def make_pick(away: str, home: str, pool_line: float, live_line: float,
              total_open: float | None = None,
              total_close: float | None = None) -> Pick:
    """The whole model in one call -- see module docstring for validation.

    |edge| >= MOVE_FLOOR: follow the side the market moved toward (that side
    is getting a better number than the market's current opinion of it),
    with a key-number bonus folded into the confidence when the move crossed
    3 or 7.

    Below MOVE_FLOOR there is no movement signal. Pass `total_open` and
    `total_close` (the game total when the pool line froze, and now) to break
    that tie on totals drift; omit them and it falls back to the market
    favourite, which is what shipped before. Both totals are optional so
    every existing caller keeps working unchanged.
    """
    edge_pts = live_line - pool_line
    mag = abs(edge_pts)
    eff = effective_edge(pool_line, live_line)
    flipped = favorite_flipped(pool_line, live_line)
    moved = mag >= MOVE_FLOOR

    if moved:
        side = 'home' if edge_pts < 0 else 'away'
    else:
        side = _coinflip_side(live_line, total_open, total_close)
    side_line = pool_line if side == 'home' else -pool_line

    # Tiering runs off the EFFECTIVE edge, so a 1-point move that crosses 3
    # is rated above a 2-point move that crosses nothing -- which is what the
    # data says (crossing games covered ~70% in every dev season).
    if flipped or eff >= STRONG_FLOOR:
        tier = 'STRONG'
    elif eff >= SOLID_FLOOR:
        tier = 'SOLID'
    elif moved:
        tier = 'LEAN'
    else:
        tier = 'COIN FLIP'

    return Pick(
        matchup=f'{away} @ {home}', pool_line=pool_line, live_line=live_line,
        edge_pts=edge_pts, key_number=key_number_crossed(pool_line, live_line),
        flipped=flipped, side=side, side_line=side_line,
        prob=win_prob(eff) if moved else 0.5, tier=tier, eff_edge=eff,
    )


def ats_result(home_margin: float, pool_line: float, side: str) -> str:
    """'W' / 'L' / 'P' for `side` ('home' or 'away') against `pool_line`.

    home_margin = home_score - away_score. Shared by the backtest and by
    weekly grading (scripts/pickem_backtest.py, scripts/pickem_grade.py) so
    the definition of "covered" can't drift between the two.
    """
    v = home_margin + pool_line
    if v == 0:
        return 'P'
    home_covers = v > 0
    return 'W' if (home_covers == (side == 'home')) else 'L'
