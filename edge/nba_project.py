"""NBA DK points per player: tonight's minutes x his DK points per minute.

    mean = minutes (edge/nba_minutes.py: role + tonight's starters / inactives)
           x rate  (DK points per minute, recent games weighted RATE_DECAY per game)
           x opp   (the opponent's DK points allowed per minute vs the league,
                    over its last OPP_WINDOW games, to the power OPP_POW)
           x env   (tonight's implied team total vs the league average, when the
                    books have posted one; 1.0 otherwise -- no history to fit it on)

Minutes carry the news (who starts, who is out); the rate carries the player.
The decay and the opponent power are fitted on 2024-25 and scored on 2025-26 by
scripts/nba_projection_backtest.py -- see the constants.
"""
from __future__ import annotations

#: Fitted by scripts/nba_projection_backtest.py on 2024-25 (2,470 team-games; decay
#: 0.85-1.0 x opponent power 0/0.5/1), scored on 2025-26 (29,311 player-games),
#: 2026-10-15. Mean |error| in DK points / correlation, held out:
#:   this model (confirmed starters)      7.654 / 0.765
#:   his last-10 average                  7.876 / 0.743
#:   his season-to-date average           8.185 / 0.725
#:   this rate x the minutes he PLAYED    4.535 / 0.909   <- minutes are most of the error
RATE_DECAY = 0.98
RATE_WINDOW = 40
#: A rate needs this many minutes behind it; below, it is shrunk to a position prior.
RATE_MIN_MINUTES = 60.0
POSITION_RATE = {"G": 0.95, "F": 0.95, "C": 1.05}
OPP_WINDOW = 15
OPP_POW = 0.5
#: Tonight's implied team points / league average, to this power (unfitted:
#: no historical NBA lines; 1.0 is the plain proportional assumption).
ENV_POW = 1.0
LEAGUE_TEAM_POINTS = 115.0


def rate(history: list, position: str | None = None, decay: float = RATE_DECAY) -> float:
    """DK points per minute from [(minutes, started, dk_points), ...] newest last,
    shrunk toward a position prior when he has played few minutes."""
    num = den = 0.0
    w = 1.0
    for m, _, pts in reversed(history[-RATE_WINDOW:]):
        if m > 0:
            num += w * pts
            den += w * m
        w *= decay
    prior = POSITION_RATE.get((position or "F").split("-")[0], 0.95)
    # RATE_MIN_MINUTES pseudo-minutes at the position's rate: a rookie with 20
    # minutes behind him is mostly prior, a starter with 400 is all his own.
    return (num + prior * RATE_MIN_MINUTES) / (den + RATE_MIN_MINUTES)


def opponent_factor(allowed_per_min: float | None, league_per_min: float | None,
                    power: float = OPP_POW) -> float:
    if not power or not allowed_per_min or not league_per_min:
        return 1.0
    return (allowed_per_min / league_per_min) ** power


def env_factor(implied_points: float | None, power: float = ENV_POW) -> float:
    if not implied_points:
        return 1.0
    return (implied_points / LEAGUE_TEAM_POINTS) ** power


def project(minutes: float, history: list, position: str | None = None,
            opp: float = 1.0, env: float = 1.0) -> float:
    return minutes * rate(history, position) * opp * env
