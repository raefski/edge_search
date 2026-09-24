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
fight the real difference, which is the mistake the plan warned about. NFL
therefore has its OWN optimiser, edge/dfs_opt_nfl.py, and the decision is
argued in that module's docstring against the measured correlations. The one
piece that genuinely was shared -- the slot matcher -- lives in
edge/dfs_roster.py rather than in a second copy.

CALIBRATION STATUS -- read before trusting a projection
MLB's sigmas are the ones edge/dfs.py has used all along, and its projections
are backtested. NFL's touchdown rates and sigmas were FITTED 2026-09-06
against 11,017 nflverse player-weeks (scripts/nfl_td_fit.py and
scripts/nfl_sigma_fit.py). NBA's are still PRIORS and nothing about NBA has
been checked against a live payload.

WHAT THE NFL FIT FOUND, INCLUDING THE PART THAT ARGUES AGAINST ITSELF
The touchdown rates were badly wrong and are worth having fixed: both were too
low (1 per 180 rushing yards against a measured 128, 1 per 200 receiving
against 165), and at 6 points a touchdown that was a systematic 0.4-0.8 DK
point underprojection of every ball-carrier on the board.

The SIGMAS turned out to be nearly inert, which is the more useful finding
because it says where not to spend the next hour. A sigma only does work when
the price is away from even money -- implied_mean = line + sigma*z -- and
across 13,690 real two-sided NFL prop quotes in data/odds.db the mean |z| is
0.014 to 0.128 depending on the market. Books post yardage lines at -110/-110
and move the LINE, not the price. So a 19% error in the passing-yards sigma
(65 against a measured 75) moved the implied mean by 0.68 yards at the 90th
percentile of real prices: 0.03 DK points. The sigmas are now measured rather
than guessed, but nobody should expect that to show up in a projection.

The exception, and it is the reason to run the |z| check per market rather
than once: RECEPTIONS. Its mean |z| is 0.128 against 0.014-0.025 for the
yardage markets, because a reception line is a half-integer on a low count, so
the book has to move the PRICE where it can move a yardage line instead. That
sigma is the one that earns its keep -- and it was the one furthest out, 1.8
against a measured 2.05.

Where sigma does do real work is a threshold bonus, which is a genuine tail
probability. Even at the fitted sigma the normal UNDER-predicts P(100+ yards)
by 1.3-1.7 percentage points, because yardage is right-skewed and a normal's
right tail is too thin. That is worth about 0.04-0.05 DK points and is left
in, but it is the one place the distributional assumption is doing something
it is not really entitled to do.
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
    #: fills in stats no book posted. Takes the means resolved so far and the
    #: player's DK position (which may be None) and returns {stat_market: mean}
    #: for the gaps. Sport-specific by nature: MLB scales earned runs off
    #: projected innings and a strikeout-implied skill factor, NFL scales
    #: touchdowns off yardage at a rate that depends on the position.
    impute: Callable[[dict, str | None], dict] | None = None
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
def _mlb_impute(means: dict, position: str | None = None) -> dict:
    """MLB's existing imputation, unchanged in behaviour.

    `position` is accepted to satisfy the shared signature and ignored: this
    sport projects pitchers only.

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
#: Touchdowns per yard, by route to the end zone and by position.
#:
#: Rushing and receiving TDs have no two-sided prop market -- DraftKings
#: prices them only as "Anytime TD", which is a FIELD (a list of players with
#: no opposing side) and so is deliberately not ingested; see
#: edge/arb/draftkings_league.PROP_CATEGORIES. So they are imputed from
#: yardage, the same way MLB imputes earned runs from innings. At 6 points a
#: touchdown this is the largest imputed term in any NFL projection -- ~13% of
#: a top receiver's -- which is why it is fitted rather than assumed.
#:
#: FITTED 2026-09-06 against nflverse stats_player_week, 2023+2024 regular
#: season, 11,017 offensive player-weeks (scripts/nfl_td_fit.py). It replaces
#: two round-number priors (1 per 180 rushing yards, 1 per 200 receiving) that
#: were both too low: the pooled truth is 1 per 128 and 1 per 165.
#:
#: THE REGRESSOR IS AN EXPECTED MEAN, NOT A REALIZED GAME. Fitting on realized
#: yards is wrong here and wrong in a direction that flatters the low end: a
#: 5-yard touchdown catch IS 5 receiving yards, so the touchdown inflates its
#: own predictor and the 0-10 yard bin reads 1 TD per 66 yards. The fit uses a
#: leave-one-out season mean per player-season as the stand-in for the market's
#: implied mean, which is the quantity this rate is actually multiplied by.
#:
#: THE SHAPE IS PROPORTIONAL AND THAT WAS TESTED, NOT ASSUMED. The rate is flat
#: across expected-yardage floors -- rushing 131/132/132/131/137 and receiving
#: 170/178/177/171/167 yards per TD at floors of 0/10/20/30/40 -- so there is
#: no red-zone non-linearity to model, and a fitted intercept is not defensible
#: (the RB rushing one comes out NEGATIVE, -0.049 TDs). Constants are measured
#: over the pool-like population, expected yards >= 20, since a player below
#: that has no posted prop and is never projected.
#:
#: THE POSITION SPLITS ARE THE ONES THAT SURVIVED A BOOTSTRAP, and no more.
#: 95% CIs on yards per TD, 4,000 resamples:
#:     rush  QB     95 (80-115)  vs  RB/FB 135 (126-147)   separated, p~0.001
#:     rec   WR    161 (151-172) vs  TE    167 (149-191)   NOT separated, p~0.60
#:     rec   WR+TE 162 (153-172) vs  RB/FB 220 (180-281)   separated, p~0.007
#: So a quarterback's rushing yards convert at a different rate than a back's
#: (goal-line sneaks), and a back's receiving yards convert at a lower rate
#: than a receiver's (checkdowns and screens, not red-zone targets). Wide
#: receivers and tight ends do NOT differ and are deliberately NOT split --
#: three rates, not five. Splitting on an unmeasured difference costs the same
#: as leaving a real one out, and is harder to notice.
NFL_RUSH_TD_PER_YARD_QB = 1 / 95.0
NFL_RUSH_TD_PER_YARD_OTHER = 1 / 135.0
NFL_REC_TD_PER_YARD_WR_TE = 1 / 162.0
NFL_REC_TD_PER_YARD_RB = 1 / 220.0

#: Position-blind fallbacks, for a projection with no slate behind it. Pooled
#: over the same player-weeks, so a caller that cannot supply a position gets
#: the league rate rather than a guess at the most common one.
NFL_RUSH_TD_PER_YARD_ANY = 1 / 128.0
NFL_REC_TD_PER_YARD_ANY = 1 / 165.0

#: DK writes multi-eligibility as "RB/FLEX", so match on the parts.
_QB = {"QB"}
_BACKS = {"RB", "FB"}


def _pos_parts(position: str | None) -> set:
    return {t.strip().upper() for t in (position or "").split("/") if t.strip()}


def _nfl_impute(means: dict, position: str | None = None) -> dict:
    """Touchdowns from yardage, at the rate this player's position converts at.

    Falls back to the pooled rate when the position is unknown, with one
    exception worth stating: passing yards identify a quarterback more
    reliably than a slate lookup does, since no other position is priced for
    them. So a rusher who is also priced to pass gets the QB rate whether or
    not anyone said he was a QB.
    """
    parts = _pos_parts(position)
    out = {}
    if means.get("player_rush_tds") is None and means.get("player_rush_yds"):
        is_qb = bool(parts & _QB) or (not parts and means.get("player_pass_yds"))
        if is_qb:
            rate = NFL_RUSH_TD_PER_YARD_QB
        elif parts:
            rate = NFL_RUSH_TD_PER_YARD_OTHER
        else:
            rate = NFL_RUSH_TD_PER_YARD_ANY
        out["player_rush_tds"] = means["player_rush_yds"] * rate
    if means.get("player_reception_tds") is None and means.get("player_reception_yds"):
        if parts & _BACKS:
            rate = NFL_REC_TD_PER_YARD_RB
        elif parts:
            rate = NFL_REC_TD_PER_YARD_WR_TE
        else:
            rate = NFL_REC_TD_PER_YARD_ANY
        out["player_reception_tds"] = means["player_reception_yds"] * rate
    return out


NFL = Sport(
    key="americanfootball_nfl", dk_sport="NFL",
    roster={"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "DST": 1},
    flex={"FLEX": {"RB", "WR", "TE"}},
    salary_cap=50000,
    stats=(
        # Sigmas FITTED 2026-09-06, scripts/nfl_sigma_fit.py -- see the note
        # below on why they turned out to be nearly inert.
        # passing
        Stat("player_pass_yds", 0.04, 75.0, "pass yds"),
        Stat("player_pass_tds", 4.0, 1.1, "pass TD"),
        Stat("player_pass_interceptions", -1.0, 0.85, "INT"),
        # rushing
        Stat("player_rush_yds", 0.1, 29.0, "rush yds"),
        Stat("player_rush_tds", 6.0, 0.58, "rush TD"),
        # receiving -- DK Classic is full PPR
        Stat("player_reception_yds", 0.1, 30.0, "rec yds"),
        Stat("player_receptions", 1.0, 2.0, "rec"),
        Stat("player_reception_tds", 6.0, 0.5, "rec TD"),
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
    notes="TD rates and sigmas fitted 2026-09-06 against 11,017 nflverse "
          "player-weeks (scripts/nfl_td_fit.py, scripts/nfl_sigma_fit.py). "
          "DST is still a prior: it has no prop market at all and is projected "
          "from the game total and spread -- see dfs_project.project_dst.",
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


# --------------------------------------------------------------------------
# NCAAF -- college football. The roster and the price supply both differ from
# the NFL more than the shared scoring table suggests.
# --------------------------------------------------------------------------
#: Touchdowns per yard in COLLEGE football. FITTED 2026-09-24 against cfbfastR
#: 2024+2025 (scripts/ncaaf_fit.py --what td), 4,380 player-seasons.
#:
#: DraftKings DOES post a passing-touchdown ladder for college, so pass TDs are
#: priced and need no rate. Rushing and receiving touchdowns have no two-sided
#: market at any book -- only "Anytime TD", a field with no opposing side --
#: so they are imputed from yardage exactly as they are for the NFL.
#:
#: Fitted against a LEAVE-ONE-OUT SEASON MEAN rather than realized yards, for
#: the reason the NFL constants record: a 5-yard touchdown catch IS 5 receiving
#: yards, so realized yardage lets the touchdown inflate its own predictor.
#:
#: THE FLATNESS CHECK PASSED, so the proportional model is right and no
#: intercept is defensible. Yards per TD at expected-yardage floors of
#: 0/20/40/60/80:
#:     QB rushing      110 110 115 113 104
#:     other rushing   130 130 135 134 132
#:     receiving       193 193 196 196 197
#:
#: COLLEGE RECEIVERS NEED MORE YARDS PER SCORE THAN NFL ONES -- 193 against
#: 162 -- which is the opposite of the intuition that college football is
#: higher-scoring. It is, but the yards arrive faster than the touchdowns do:
#: more explosive plays over a longer field, and the same number of end zones.
NCAAF_RUSH_TD_PER_YARD_QB = 1 / 110.0        # 95% CI 105-116, n=4,167
NCAAF_RUSH_TD_PER_YARD_OTHER = 1 / 130.2     # 95% CI 126-134, n=12,671
#: ONE receiving rate, not two, and that is a deliberate refusal to split.
#: The NFL build splits receiving touchdowns by whether the catcher is a back,
#: because a bootstrap separated them there (161 vs 220, p~0.007). The same
#: bootstrap here does NOT separate them: WR 193 (187-199) against RB 216
#: (169-284), overlapping heavily on 583 back-receiving observations. Splitting
#: on an unmeasured difference costs the same as leaving a real one out and is
#: harder to notice, so this stays one number until a sample says otherwise.
NCAAF_REC_TD_PER_YARD = 1 / 192.9            # 95% CI 187-199, n=22,593

#: Interceptions have NO market of any kind for college -- DraftKings' NCAAF
#: passing-props category carries Pass Yards, Pass TDs, Attempts, Completions
#: and Longest Pass, and no interception tab at all (verified live 2026-09-24).
#: The NFL has one. So this term is imputed rather than priced, off pass
#: ATTEMPTS: 1 per 48.5 attempts, measured over 6,714 quarterback-games.
#:
#: Attempts rather than yards on purpose. Interceptions are a per-throw risk,
#: and a checkdown offence throwing 45 times for 250 yards is more exposed than
#: a vertical one throwing 25 times for the same 250. Per-yard (1 per 358) is
#: recorded here as the fallback for a quarterback whose attempts ladder DK did
#: not post, which happens on about half the board.
NCAAF_INT_PER_ATTEMPT = 1 / 48.5
NCAAF_INT_PER_PASS_YARD = 1 / 358.2


def _ncaaf_impute(means: dict, position: str | None = None) -> dict:
    """Rushing/receiving touchdowns from yardage, and interceptions from volume.

    Mirrors _nfl_impute, with two differences that are both measurements rather
    than choices: receiving touchdowns are NOT split by position here (the
    bootstrap did not separate them), and interceptions are imputed at all
    (college has no interception market where the NFL does).
    """
    parts = _pos_parts(position)
    out = {}

    if means.get("player_rush_tds") is None and means.get("player_rush_yds"):
        is_qb = bool(parts & _QB) or (not parts and means.get("player_pass_yds"))
        rate = (NCAAF_RUSH_TD_PER_YARD_QB if is_qb
                else NCAAF_RUSH_TD_PER_YARD_OTHER)
        out["player_rush_tds"] = means["player_rush_yds"] * rate

    if means.get("player_reception_tds") is None and means.get("player_reception_yds"):
        out["player_reception_tds"] = (means["player_reception_yds"]
                                       * NCAAF_REC_TD_PER_YARD)

    if means.get("player_pass_interceptions") is None:
        attempts = means.get("player_pass_attempts")
        if attempts:
            out["player_pass_interceptions"] = attempts * NCAAF_INT_PER_ATTEMPT
        elif means.get("player_pass_yds"):
            out["player_pass_interceptions"] = (means["player_pass_yds"]
                                                * NCAAF_INT_PER_PASS_YARD)
    return out


NCAAF = Sport(
    key="americanfootball_ncaaf", dk_sport="CFB",
    # Read off api.draftkings.com/lineups/v1/gametypes/94/rules, not
    # transcribed from an article. No TE slot (DraftKings lists college tight
    # ends as WR) and no DST slot at all -- so the whole points-allowed half of
    # the NFL build has no counterpart here.
    roster={"QB": 1, "RB": 2, "WR": 3, "FLEX": 1, "S-FLEX": 1},
    flex={"FLEX": {"RB", "WR"}, "S-FLEX": {"QB", "RB", "WR"}},
    salary_cap=50000,
    stats=(
        # Scoring VALIDATED against DraftKings' own published FPPG rather than
        # cross-referenced from articles -- see edge/ncaaf.py's docstring for
        # the full table. It is DK NFL scoring minus everything defensive.
        #
        # The sigmas are per-game standard deviations measured over cfbfastR
        # 2024+2025, and they matter far less here than in any other sport:
        # a milestone LADDER supplies the mean directly (edge/dfs_ladder.py),
        # so no sigma is inverted to get one. They are used only as the
        # fallback path for a threshold bonus when a ladder does not span the
        # threshold, which is rare.
        Stat("player_pass_yds", 0.04, 104.2, "pass yds"),
        Stat("player_pass_tds", 4.0, 0.91, "pass TD"),
        Stat("player_pass_interceptions", -1.0, 0.64, "INT"),
        Stat("player_rush_yds", 0.1, 39.6, "rush yds"),
        Stat("player_rush_tds", 6.0, 0.61, "rush TD"),
        Stat("player_reception_yds", 0.1, 32.1, "rec yds"),
        Stat("player_receptions", 1.0, 1.99, "rec"),
        Stat("player_reception_tds", 6.0, 0.53, "rec TD"),
    ),
    bonuses=(
        Bonus("player_pass_yds", 300.0, 3.0, "300+ pass"),
        Bonus("player_rush_yds", 100.0, 3.0, "100+ rush"),
        Bonus("player_reception_yds", 100.0, 3.0, "100+ rec"),
    ),
    # Any ONE is enough, as for the NFL: positions read disjoint markets.
    required=("player_pass_yds", "player_rush_yds", "player_reception_yds"),
    impute=_ncaaf_impute,
    notes="DraftKings posts NCAAF props ONLY as one-sided milestone ladders, "
          "so edge/dfs_project.project cannot read them at all and "
          "edge/dfs_ladder.py does the work instead. Scoring validated against "
          "DK's own FPPG; TD and interception rates fitted 2026-09-24 against "
          "cfbfastR 2024+2025 (scripts/ncaaf_fit.py). No DST and no TE.",
)


SPORTS: dict[str, Sport] = {s.key: s for s in (MLB_PITCHER, NFL, NBA, NCAAF)}


def get(sport_key: str) -> Sport:
    try:
        return SPORTS[sport_key]
    except KeyError:
        raise KeyError(f"no DFS sport for {sport_key!r}; have {sorted(SPORTS)}") from None
