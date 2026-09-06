"""One description of a DFS sport, so adding a sport is data rather than code.

WHY THIS EXISTS NOW AND NOT BEFORE
DFS_MULTISPORT_PLAN.md called "duplicate first, abstract later", and that was
right: edge/nfl.py and edge/nba.py were written fresh precisely so the real
differences between the sports could be seen before anything was shared. They
have now been seen, and they are narrower than expected. What actually differs
between MLB, NFL and NBA DFS is:

  * which prop markets carry the stats,
  * how many DK points a unit of each stat is worth,
  * how noisy a stat is around its line (the sigma that turns an over/under
    into an implied mean),
  * what to do when a book does not post a market,
  * roster shape.

All five are DATA. The arithmetic that turns a two-sided price into a
projection is identical in every sport, and it is already written once in
edge/dfs.py::project_pitcher. So this module holds the data and
edge/dfs_project.py holds that one piece of arithmetic.

WHAT IS DELIBERATELY NOT ABSTRACTED
The optimiser. edge/dfs_opt.py is built around MLB batting-order stacking
(`_consecutive_runs`, `_hitter_slots_assignable`), and NFL's correlation
structure (QB with his own receivers, against a game total) is a different
shape rather than a parameter of the same shape. Forcing them together would
fight the real difference, which is the mistake the plan warned about.

CALIBRATION STATUS -- read before trusting a projection
MLB's sigmas are the ones edge/dfs.py has used all along, and its projections
are backtested. The NFL and NBA sigmas below are PRIORS, chosen from the
typical spread of each stat, and they have not been fitted to outcomes. A
sigma that is too small overstates how far the implied mean sits from the
line; too large understates it. `scripts/dfs_calibration.py` is the existing
harness for checking that against results, and none of these has been through
it yet. Treat NFL/NBA projections as ranked-order guidance, not as calibrated
point estimates, until they have.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Stat:
    """One scoring component, and how to read it off a prop market."""
    #: canonical market key, as edge/arb/marketmap.py emits it
    market: str
    #: DK fantasy points per unit of this stat
    points: float
    #: spread of the outcome around the posted line. None marks a
    #: PROBABILITY market (Yes/No, e.g. "to record a win"), where the devigged
    #: probability is the quantity rather than an input to a normal.
    sigma: float | None = None
    #: human name, used in the components breakdown
    name: str = ""

    @property
    def is_probability(self) -> bool:
        return self.sigma is None

    @property
    def label(self) -> str:
        return self.name or self.market


@dataclass(frozen=True)
class Bonus:
    """A DK threshold bonus, scored on the DISTRIBUTION rather than the mean.

    NFL pays +3 for 100 rushing yards. Scoring that off the projected mean
    makes it a step function -- a back projected at 99.4 gets nothing and one
    at 100.1 gets the full 3 -- which is wrong in both directions and jumpy
    exactly where the pool is densest. The fitted normal already gives
    P(yards >= 100) for free, so the bonus is scored as its expected value.
    """
    market: str
    threshold: float
    points: float
    name: str = ""


@dataclass(frozen=True)
class Sport:
    key: str                                  # odds sport key, e.g. americanfootball_nfl
    dk_sport: str                             # DraftKings lobby code, e.g. NFL
    roster: dict                              # slot -> count
    salary_cap: int
    stats: tuple[Stat, ...]
    bonuses: tuple[Bonus, ...] = ()
    #: markets without which no projection is attempted at all. A player
    #: missing one of these is ABSENT from the pool rather than badly
    #: projected -- the same contract project_pitcher has always had.
    required: tuple[str, ...] = ()
    #: fills in stats no book posted. Takes the means resolved so far and
    #: returns {stat_market: mean} for the gaps. Sport-specific by nature:
    #: MLB scales earned runs off projected innings and a strikeout-implied
    #: skill factor, NFL scales touchdowns off yardage.
    impute: Callable[[dict], dict] | None = None
    #: DK position strings that map onto roster slots
    positions: Callable[[str], set] | None = None
    flex: dict = field(default_factory=dict)  # slot -> eligible positions
    notes: str = ""

    def market_keys(self) -> list[str]:
        """Every market this sport wants pulled, for a collection profile."""
        return sorted({s.market for s in self.stats}
                      | {b.market for b in self.bonuses})

    def stat_for(self, market: str) -> Stat | None:
        return next((s for s in self.stats if s.market == market), None)


# --------------------------------------------------------------------------
# MLB -- pitchers. The sigmas and scoring are lifted verbatim from
# edge/dfs.py (P_SIGMA / P_SCORE) rather than restated, so the two cannot
# drift; edge/dfs_project.py is checked against project_pitcher's own output
# in tests/test_dfs_project.py, which is what makes that claim testable rather
# than merely asserted.
# --------------------------------------------------------------------------
def _mlb_impute(means: dict) -> dict:
    """MLB's existing imputation, unchanged in behaviour.

    Earned runs, hits and walks are scaled off projected innings; aces
    suppress them below league average, so the strikeout-implied K/9 sets a
    skill factor. Mirrors project_pitcher exactly -- see edge/dfs.py.
    """
    from edge.dfs import LEAGUE_PER_IP, WIN_DEFAULT
    outs = means.get("pitcher_outs")
    if not outs:
        return {}
    ip = outs / 3.0
    km = means.get("pitcher_strikeouts") or 0.0
    k9 = 27 * km / outs if outs else 8.5
    sf = min(1.35, max(0.55, 1 - 0.5 * (k9 - 8.5) / 8.5))
    out = {}
    for market, key, skilled in (("pitcher_earned_runs", "ER", True),
                                 ("pitcher_hits_allowed", "hit", True),
                                 ("pitcher_walks", "bb", False)):
        if means.get(market) is None:
            out[market] = ip * LEAGUE_PER_IP[key] * (sf if skilled else 1.0)
    if means.get("pitcher_record_a_win") is None:
        out["pitcher_record_a_win"] = WIN_DEFAULT
    return out


MLB_PITCHER = Sport(
    key="baseball_mlb", dk_sport="MLB",
    roster={"P": 2, "C": 1, "1B": 1, "2B": 1, "3B": 1, "SS": 1, "OF": 3},
    salary_cap=50000,
    stats=(
        Stat("pitcher_outs", 0.75, 4.0, "outs"),
        Stat("pitcher_strikeouts", 2.0, 2.0, "K"),
        Stat("pitcher_earned_runs", -2.0, 2.0, "ER"),
        Stat("pitcher_hits_allowed", -0.6, 2.5, "hits"),
        Stat("pitcher_walks", -0.6, 1.2, "walks"),
        Stat("pitcher_record_a_win", 4.0, None, "win"),
    ),
    required=("pitcher_outs", "pitcher_strikeouts"),
    impute=_mlb_impute,
    notes="Backtested. The reference implementation is edge/dfs.py::project_pitcher.",
)


# --------------------------------------------------------------------------
# NFL
# --------------------------------------------------------------------------
#: Touchdowns per yard, by route to the end zone. UNVALIDATED PRIORS.
#: Rushing and receiving TDs have no two-sided prop market -- DraftKings
#: prices them only as "Anytime TD", which is a FIELD (a list of players with
#: no opposing side) and so is deliberately not ingested; see
#: edge/arb/draftkings_league.PROP_CATEGORIES. So they are imputed from
#: yardage, the same way MLB imputes earned runs from innings.
#: These two numbers are the least defensible thing in this file. They are
#: round-number league-shaped priors, NOT fitted: roughly one rushing TD per
#: 180 rushing yards and one receiving TD per 200 receiving yards. Fit them
#: against nflverse player-week data before treating any NFL projection as
#: calibrated, and note that TDs are 6 points each, so this term moves a
#: projection more than any other imputed one.
NFL_RUSH_TD_PER_YARD = 1 / 180.0
NFL_REC_TD_PER_YARD = 1 / 200.0


def _nfl_impute(means: dict) -> dict:
    out = {}
    if means.get("player_rush_tds") is None and means.get("player_rush_yds"):
        out["player_rush_tds"] = means["player_rush_yds"] * NFL_RUSH_TD_PER_YARD
    if means.get("player_reception_tds") is None and means.get("player_reception_yds"):
        out["player_reception_tds"] = means["player_reception_yds"] * NFL_REC_TD_PER_YARD
    return out


NFL = Sport(
    key="americanfootball_nfl", dk_sport="NFL",
    roster={"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "DST": 1},
    flex={"FLEX": {"RB", "WR", "TE"}},
    salary_cap=50000,
    stats=(
        # passing
        Stat("player_pass_yds", 0.04, 65.0, "pass yds"),
        Stat("player_pass_tds", 4.0, 1.0, "pass TD"),
        Stat("player_pass_interceptions", -1.0, 0.8, "INT"),
        # rushing
        Stat("player_rush_yds", 0.1, 30.0, "rush yds"),
        Stat("player_rush_tds", 6.0, 0.7, "rush TD"),
        # receiving -- DK Classic is full PPR
        Stat("player_reception_yds", 0.1, 28.0, "rec yds"),
        Stat("player_receptions", 1.0, 1.8, "rec"),
        Stat("player_reception_tds", 6.0, 0.7, "rec TD"),
    ),
    bonuses=(
        # Each is independent: a 100-rush/100-rec game earns both.
        Bonus("player_pass_yds", 300.0, 3.0, "300+ pass"),
        Bonus("player_rush_yds", 100.0, 3.0, "100+ rush"),
        Bonus("player_reception_yds", 100.0, 3.0, "100+ rec"),
    ),
    # Any ONE of these is enough: a QB has passing yards, a back has rushing
    # yards, a receiver has receiving yards, and no player has all three.
    # `required` is checked as "at least one" for a sport whose positions use
    # disjoint markets -- see dfs_project.project.
    required=("player_pass_yds", "player_rush_yds", "player_reception_yds"),
    impute=_nfl_impute,
    notes="Sigmas and TD rates are UNVALIDATED priors. DST has no prop market "
          "at all and is projected separately from the game total and spread "
          "-- see dfs_project.project_dst.",
)


# --------------------------------------------------------------------------
# NBA -- the price supply exists; the sport is out of season and unverified.
# --------------------------------------------------------------------------
NBA = Sport(
    key="basketball_nba", dk_sport="NBA",
    roster={"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UTIL": 1},
    flex={"G": {"PG", "SG"}, "F": {"SF", "PF"}, "UTIL": {"PG", "SG", "SF", "PF", "C"}},
    salary_cap=50000,
    stats=(
        Stat("player_points", 1.0, 6.0, "pts"),
        Stat("player_rebounds", 1.25, 2.6, "reb"),
        Stat("player_assists", 1.5, 2.0, "ast"),
        Stat("player_threes", 0.5, 1.3, "3PM"),
        Stat("player_steals", 2.0, 1.0, "stl"),
        Stat("player_blocks", 2.0, 1.0, "blk"),
    ),
    required=("player_points",),
    notes="NOT verified: NBA is out of season as of 2026-09-06, so neither the "
          "DraftKings prop category ids nor the FanDuel tab names have been "
          "checked against a live payload. Do that FIRST -- the NFL ids were "
          "wrong in exactly this way and it cost two thirds of the markets. "
          "Double-double and triple-double bonuses are not modelled yet.",
)


SPORTS: dict[str, Sport] = {s.key: s for s in (MLB_PITCHER, NFL, NBA)}


def get(sport_key: str) -> Sport:
    try:
        return SPORTS[sport_key]
    except KeyError:
        raise KeyError(f"no DFS sport for {sport_key!r}; have {sorted(SPORTS)}") from None
