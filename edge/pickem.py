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


def make_pick(away: str, home: str, pool_line: float, live_line: float) -> Pick:
    """The whole model in one call -- see module docstring for validation.

    |edge| >= MOVE_FLOOR: follow the side the market moved toward (that side
    is getting a better number than the market's current opinion of it).
    Below that: no validated edge, default to the live market's own favorite
    rather than manufacturing a signal that isn't there.
    """
    edge_pts = live_line - pool_line
    mag = abs(edge_pts)
    flipped = favorite_flipped(pool_line, live_line)
    moved = mag >= MOVE_FLOOR

    side = ('home' if edge_pts < 0 else 'away') if moved else ('home' if live_line < 0 else 'away')
    side_line = pool_line if side == 'home' else -pool_line

    if flipped or mag >= STRONG_FLOOR:
        tier = 'STRONG'
    elif mag >= SOLID_FLOOR:
        tier = 'SOLID'
    elif moved:
        tier = 'LEAN'
    else:
        tier = 'COIN FLIP'

    return Pick(
        matchup=f'{away} @ {home}', pool_line=pool_line, live_line=live_line,
        edge_pts=edge_pts, key_number=key_number_crossed(pool_line, live_line),
        flipped=flipped, side=side, side_line=side_line,
        prob=win_prob(edge_pts) if moved else 0.5, tier=tier,
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
