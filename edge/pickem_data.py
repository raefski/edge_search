"""The single loader for pick'em historical data, and the holdout guard rail.

WHY THIS EXISTS
---------------
Five scripts each had their own parser for data/pickem_odds_history.csv
(pickem_backtest, pickem_feature_lab, pickem_idea_lab, pickem_season_sim,
pickem_vs_random). They had already drifted: season_sim applied CBS's
half-point convention and the other four did not, so the same file yielded
different lines depending on which script read it. That drift is how the push
bug survived as long as it did. One loader, one set of conventions.

It also owns the HOLDOUT policy, which used to be five copy-pasted
`HOLDOUT = {2023, 2024}` declarations plus five hand-rolled filters. The
protection this project depends on most was the thing least protected. Now
reading the holdout requires passing an argument named to be hard to type by
accident, and everything else is dev-only by construction.

    load(Split.DEV)                      # train + validate, the default
    load(Split.TRAIN) / load(Split.VALIDATE)
    load(Split.HOLDOUT, spend_the_holdout=True)   # deliberate, and loud
"""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ODDS = ROOT / "data" / "pickem_odds_history.csv"
SITU = ROOT / "data" / "pickem_situational.csv"

TRAIN_SEASONS = frozenset(range(2014, 2020))
VAL_SEASONS = frozenset(range(2020, 2023))
HOLDOUT_SEASONS = frozenset({2023, 2024})


class Split(str, Enum):
    TRAIN = "train"
    VALIDATE = "validate"
    DEV = "dev"                 # train + validate
    HOLDOUT = "holdout"
    ALL = "all"


def seasons_for(split: Split) -> frozenset[int]:
    return {
        Split.TRAIN: TRAIN_SEASONS,
        Split.VALIDATE: VAL_SEASONS,
        Split.DEV: TRAIN_SEASONS | VAL_SEASONS,
        Split.HOLDOUT: HOLDOUT_SEASONS,
        Split.ALL: TRAIN_SEASONS | VAL_SEASONS | HOLDOUT_SEASONS,
    }[Split(split)]


def _f(v):
    """Float or None. Was defined identically in four separate scripts."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Game:
    season: int
    week: int
    away: str
    home: str
    pool_line: float          # the frozen-line proxy (a sportsbook opener)
    live_line: float          # the late/closing market line
    margin: int               # home_score - away_score
    total_open: float | None
    total_close: float | None
    home_ml_open: float | None = None
    home_ml_close: float | None = None
    away_ml_open: float | None = None
    away_ml_close: float | None = None
    situational: dict | None = None

    @property
    def move(self) -> float:
        return self.live_line - self.pool_line

    @property
    def abs_line(self) -> float:
        return abs(self.pool_line)

    @property
    def home_covers(self) -> bool | None:
        """None on a push. CBS's pool never posts an integer, so under
        half_point=True this is never None -- see load()."""
        v = self.margin + self.pool_line
        return None if v == 0 else v > 0


def _load_situational() -> dict:
    if not SITU.exists():
        return {}
    out = {}
    with SITU.open() as f:
        for r in csv.DictReader(f):
            out[(int(r["season"]), int(r["week"]), r["home_team"], r["away_team"])] = r
    return out


def load(split: Split | str = Split.DEV, *, half_point: bool = False,
         situational: bool = False, spend_the_holdout: bool = False,
         half_point_seed: int = 11) -> list[Game]:
    """Load the odds history for one split.

    half_point: rewrite integer pool lines to a half point, which is what CBS
        actually posts (16/16 of the real Week 1 lines ended in .5, while the
        historical proxy is an integer 52.5% of the time). Makes pushes
        impossible, matching the pool. Seeded, so it is reproducible.

    situational: attach the nflverse context row (rest, weather, roof, QB,
        referee, spread juice) as `Game.situational`.

    spend_the_holdout: required to read 2023-24. The holdout is spent when it
        is used; it exists to be touched once, by a finished model.
    """
    split = Split(split)
    wanted = seasons_for(split)
    if (wanted & HOLDOUT_SEASONS) and not spend_the_holdout:
        raise PermissionError(
            f"load(Split.{split.name}) would read holdout seasons "
            f"{sorted(HOLDOUT_SEASONS)}. The holdout is spent the moment it is "
            "used, and only a finished model has earned it. If that is genuinely "
            "what you are doing, pass spend_the_holdout=True and say so in the "
            "script's docstring. See PICKEM_MODEL.md section 3.")

    situ = _load_situational() if situational else {}
    rnd = random.Random(half_point_seed)
    games: list[Game] = []
    with ODDS.open() as f:
        for r in csv.DictReader(f):
            s = int(r["season"])
            if s not in wanted:
                continue
            pool = float(r["home_line_open"])
            if half_point and pool == int(pool):
                pool += rnd.choice((-0.5, 0.5))
            week = int(r["week"])
            games.append(Game(
                season=s, week=week, away=r["away_team"], home=r["home_team"],
                pool_line=pool, live_line=float(r["home_line_close"]),
                margin=int(r["home_score"]) - int(r["away_score"]),
                total_open=_f(r["total_open"]), total_close=_f(r["total_close"]),
                home_ml_open=_f(r.get("home_ml_open")),
                home_ml_close=_f(r.get("home_ml_close")),
                away_ml_open=_f(r.get("away_ml_open")),
                away_ml_close=_f(r.get("away_ml_close")),
                situational=situ.get((s, week, r["home_team"], r["away_team"])),
            ))
    games.sort(key=lambda g: (g.season, g.week))
    return games


def by_season(games: list[Game]) -> dict[int, list[Game]]:
    out: dict[int, list[Game]] = {}
    for g in games:
        out.setdefault(g.season, []).append(g)
    return out
