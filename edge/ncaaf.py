"""DK College Football Classic scoring + free ground truth (cfbfastR).

The NCAAF analogue of edge/nfl.py, and deliberately NOT a parameterisation of
it. College football differs from the NFL in three ways that reach all the way
down into the roster and the model, rather than in ways that are settings:

  * **No defence and no tight end.** DK's CFB Classic roster is
    QB / RB / RB / WR / WR / WR / FLEX / S-FLEX -- eight players, $50,000 cap.
    Tight ends exist on the field but DraftKings lists them as WR, and there is
    no DST slot at all. So the whole DST half of edge/nfl.py -- points-allowed
    tiers, the opposing implied team total, the hard "never roster a defence
    against your own stack" rule -- has no counterpart here, and the game lines
    it needed are not load-bearing for this sport.
  * **The S-FLEX takes a quarterback.** That is not a cosmetic extra slot. A
    lineup may start two quarterbacks, and in college football quarterbacks are
    the highest-scoring position by a wide margin (they run). Whether to double
    up at QB is the central roster-construction question of the sport, and it
    is one the NFL optimiser never has to ask.
  * **The market prices college players differently.** DraftKings posts NCAAF
    props as one-sided MILESTONE LADDERS ("50+", "60+", "70+") rather than as
    two-sided Over/Under lines. That breaks edge/dfs_project.project outright
    and is the reason edge/dfs_ladder.py exists. See that module.

SCORING IS VALIDATED, NOT ASSUMED -- and this is the one place this file can
claim something edge/nfl.py explicitly cannot. edge/nfl.py's own docstring
records that its scoring constants were "cross-referenced across multiple
independent sources" but never confirmed against DraftKings directly, because
DK's rules pages blocked every automated fetch. They still do (the rules page
is a single-page app that serves 576KB of shell and no scoring text, and
api.draftkings.com has no scoring endpoint that answers).

So the constants below were confirmed a different way, on 2026-09-24: DK's own
draftables payload publishes each player's season FANTASY POINTS PER GAME
(draftStatAttributes id 174 for CFB). Recomputing that quantity from cfbfastR
play data under competing scoring tables and keeping the one that reproduces
DK's own number is a direct test against DraftKings, just not against its prose.
Over the 393 players on the 2026-09-26 main slate who matched:

    hypothesis                       MAE     bias     verdict
    passing TD = 4                   0.900   +0.42    <- kept   (QB only)
    passing TD = 6                   2.356   +2.18       rejected
    0.04 per passing yard            1.144   +0.90    <- kept   (all)
    0.05 per passing yard            1.270   +1.07       rejected
    0.1 per rushing yard             1.144   +0.90    <- kept   (all)
    0.05 per rushing yard            1.500   +0.20       rejected
    threshold bonuses ON             1.144   +0.90    <- kept   (all)
    threshold bonuses OFF            1.231   +0.70       rejected
    full PPR                         0.678   -0.34    <- kept   (WR only)
    half PPR                         1.425   -1.20       rejected
    no PPR                           2.255   -2.07       rejected

THE PPR ROW CAME OUT BACKWARDS THE FIRST TIME, AND THE REASON IS A TRAP WORTH
NAMING. Averaged over the games a player RECORDED A STAT IN, full PPR looks
biased high for receivers (+1.24) and half PPR looks unbiased. That is an
artifact of the denominator: a receiver who played and caught nothing has no
row in a play-derived box score at all, so he is dropped from the average
instead of counted as the zero DK counts him as -- and that omission hits
receivers hardest, backs less, quarterbacks least, which is exactly the shape
of the spurious bias. Dividing by the player's TEAM's games played instead
reverses the conclusion at every position. Anyone re-running this check must
use team games, not observed rows.
"""
from __future__ import annotations

import collections
import csv
import io
import urllib.request
from pathlib import Path

from edge.names import norm  # noqa: F401  (re-exported; see edge/names.py)

ROOT = Path(__file__).resolve().parents[1]

#: DK CFB Classic, read off api.draftkings.com/lineups/v1/gametypes/94/rules
#: rather than transcribed from an article. FLEX takes RB/WR; S-FLEX also
#: takes a quarterback.
ROSTER = {"QB": 1, "RB": 2, "WR": 3, "FLEX": 1, "S-FLEX": 1}
SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "FLEX", "S-FLEX"]
SALARY_CAP = 50000
#: DK enforces this on Classic CFB the same way it does on NFL.
MIN_GAMES = 2
#: DK's lobby code for college football. NOT "NCAAF" -- that string returns
#: every sport in the lobby, which looks like a working call and is not.
DK_SPORT = "CFB"
#: DK's game-type id for CFB Classic. 95 is Showdown Captain Mode (one game,
#: CPT + 5 UTIL) and 377 is a Snake draft with no salary cap at all.
CLASSIC_GAME_TYPE = 94


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def actual_points(box: dict) -> float:
    """DK CFB Classic points for one player-game.

    `box` is any mapping with the field names boxscores() produces. Missing
    keys are zero, so a receiver's row need not carry passing fields.

    The three threshold bonuses are INDEPENDENT -- a back with 100 rushing and
    100 receiving yards earns both -- which is why they are three separate
    tests rather than a chain.
    """
    g = lambda k: float(box.get(k, 0) or 0)                  # noqa: E731
    pass_yds, rush_yds, rec_yds = g("pass_yds"), g("rush_yds"), g("rec_yds")
    pts = (
        0.04 * pass_yds + 4.0 * g("pass_td") - 1.0 * g("int")
        + 0.1 * rush_yds + 6.0 * g("rush_td")
        + 1.0 * g("rec") + 0.1 * rec_yds + 6.0 * g("rec_td")
        - 1.0 * g("fum_lost")
        + 6.0 * g("other_td")
        + 2.0 * g("two_pt")
    )
    if pass_yds >= 300:
        pts += 3.0
    if rush_yds >= 100:
        pts += 3.0
    if rec_yds >= 100:
        pts += 3.0
    return round(pts, 2)


def base_position(dk_position: str | None) -> str:
    """The position a DK CFB position string names.

    DK lists tight ends as WR in college, so there are only three. A bare
    "FLEX" or "S-FLEX" is a roster SLOT rather than a thing a player is, and
    must not be treated as a position -- looking a spread up under it would
    silently return the default for every flex-eligible player on the board.
    """
    for tok in (dk_position or "").replace("/", " ").split():
        t = tok.strip().upper()
        if t in ("QB", "RB", "WR"):
            return t
        if t in ("TE", "FB"):
            return "WR" if t == "TE" else "RB"
    return "WR"


def eligible_slots(dk_position: str | None) -> set:
    """DK position string -> the roster slots that player can fill.

    FLEX and S-FLEX eligibility is derived here rather than asked of the
    caller, because forgetting it is invisible: the lineup still builds, it is
    just never allowed to put a fourth receiver in the FLEX or a second
    quarterback in the S-FLEX, and quietly returns a worse lineup.
    """
    pos = base_position(dk_position)
    out = {pos, "S-FLEX"}
    if pos in ("RB", "WR"):
        out.add("FLEX")
    return out


# ---------------------------------------------------------------------------
# Ground truth: cfbfastR
# ---------------------------------------------------------------------------
#: cfbfastR publishes straight out of the repo tree rather than as GitHub
#: release assets the way nflverse does, so there is no releases API to walk --
#: the path is the contract. Seasons 2014..current.
CFBFASTR = ("https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data"
            "/main/player_stats/csv/player_stats_{season}.csv")
UA = {"User-Agent": "edge-search (research use)"}

#: Where ncaaf_ground_truth_collect.py parks the raw seasons. Gitignored: the
#: 2025 file alone is 60MB and it is free to re-download, the same call MLB's
#: data/bt_boxscores and NFL's data/nfl_ground_truth already make.
GROUND_TRUTH_DIR = ROOT / "data" / "ncaaf_ground_truth"


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_season(season: int, cache_dir: Path | None = None,
                 refresh: bool = False) -> list[dict]:
    """Every play-stat row cfbfastR has for one season. Cached on disk.

    BE AWARE OF THE SHAPE: this is ONE ROW PER PLAY, not per player-game --
    57,099 rows for 2026 weeks 1-3, 213,202 for all of 2025. Aggregating is
    boxscores()' job, and doing it wrong is the most likely way to get a fit
    that looks fine and is not.
    """
    cache_dir = cache_dir or GROUND_TRUTH_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"player_stats_{season}.csv"
    if refresh or not path.exists():
        req = urllib.request.Request(CFBFASTR.format(season=season), headers=UA)
        path.write_bytes(urllib.request.urlopen(req, timeout=300).read())
    text = path.read_text(encoding="utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def boxscores(play_rows: list[dict]) -> dict:
    """{(game_id, player): per-game counting stats} from cfbfastR play rows.

    TOUCHDOWNS ARE INFERRED, AND THAT IS NOT A SHORTCUT -- IT IS THE ONLY WAY.
    cfbfastR's `touchdown_player` column is populated for RUSHING scores only:
    of the 1,485 touchdown rows in the 2026 file, every single one also carries
    a `rush_player`, and not one carries a `reception_player`. Scoring off that
    column alone would give every receiver in college football zero receiving
    touchdowns, worth 6 points each, and would do it silently.

    So a score is read off the geometry instead: a play that gains at least
    `yards_to_goal` reached the end zone. That rule is checkable, because on
    rushes the column IS ground truth -- against it the rule scores 0.9965
    precision and 0.9910 recall. Applied to receptions it attributes 49.6% of
    all touchdowns to the passing game, which is the right neighbourhood for
    college football.

    A passing touchdown is credited to the completion's own quarterback on the
    same row, so the two sides of one scoring play cannot disagree.

    FUMBLES LOST AND TWO-POINT CONVERSIONS ARE NOT IN THIS FEED. `fumble_player`
    records a fumble but not whether it was recovered by the defence, and there
    is no two-point column at all. Both are left at zero and are worth about
    -0.1 and +0.05 points per player-game respectively; they are named here so
    that a future discrepancy is recognised rather than re-derived.
    """
    NA = "NA"
    box: dict = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in play_rows:
        gid = r["game_id"]
        rec, rush, cmpl = r["reception_player"], r["rush_player"], r["completion_player"]

        if rec != NA:
            k = (gid, rec)
            box[k]["rec"] += 1
            box[k]["rec_yds"] += _num(r["reception_yds"]) or 0.0
            box[k]["team"] = r["team"]
            box[k]["opponent"] = r["opponent"]
        if rush != NA:
            k = (gid, rush)
            box[k]["rush_att"] += 1
            box[k]["rush_yds"] += _num(r["rush_yds"]) or 0.0
            box[k]["team"] = r["team"]
            box[k]["opponent"] = r["opponent"]
        if cmpl != NA:
            k = (gid, cmpl)
            box[k]["cmp"] += 1
            box[k]["pass_yds"] += _num(r["completion_yds"]) or 0.0
            box[k]["team"] = r["team"]
            box[k]["opponent"] = r["opponent"]
        if r["incompletion_player"] != NA:
            k = (gid, r["incompletion_player"])
            box[k]["pass_att_inc"] += 1
            box[k]["team"] = r["team"]
        if r["interception_thrown_player"] != NA:
            k = (gid, r["interception_thrown_player"])
            box[k]["int"] += 1
            box[k]["team"] = r["team"]
        if r["target_player"] != NA:
            box[(gid, r["target_player"])]["targets"] += 1

        goal = _num(r["yards_to_goal"])
        if goal and goal > 0:
            if rec != NA and (_num(r["reception_yds"]) or -1) >= goal:
                box[(gid, rec)]["rec_td"] += 1
                if cmpl != NA:
                    box[(gid, cmpl)]["pass_td"] += 1
            if rush != NA and (_num(r["rush_yds"]) or -1) >= goal:
                box[(gid, rush)]["rush_td"] += 1

    for k, v in box.items():
        v["game_id"], v["player"] = k[0], k[1]
        v["pass_att"] = v.get("cmp", 0.0) + v.get("pass_att_inc", 0.0)
        v["dk"] = actual_points(v)
    return dict(box)


def team_games(play_rows: list[dict]) -> dict:
    """{team: {game_id, ...}} -- the denominator a per-game average needs.

    Exists because getting this wrong reverses a real conclusion: see the
    module docstring on the PPR test. A player-game average taken over rows
    observed silently drops every game a player appeared in and did nothing,
    which is not a rounding error for a wide receiver.
    """
    out: dict = collections.defaultdict(set)
    for r in play_rows:
        out[r["team"]].add(r["game_id"])
    return dict(out)


def player_teams(box: dict) -> dict:
    """{normalised player name: team} by majority vote over their games."""
    votes: dict = collections.defaultdict(collections.Counter)
    for v in box.values():
        team = v.get("team")
        if team:
            votes[norm(v["player"])][team] += 1
    return {n: c.most_common(1)[0][0] for n, c in votes.items()}
