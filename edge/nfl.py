"""DK NFL Classic scoring + free-data ground truth helpers.

Companion to edge/dfs.py's MLB functions, built fresh per
DFS_MULTISPORT_PLAN.md §2's "duplicate first, abstract later" call -- NFL's
roster shape, scoring categories, and (especially) its DST-has-no-prop-market
problem are different enough from MLB that sharing code prematurely would
fight the real differences rather than help.

Scoring values were cross-referenced across multiple independent sources
(DFS_MULTISPORT_PLAN.md §1) but DK's own rules pages blocked every automated
fetch attempt -- these are NOT yet confirmed directly against a live DK
contest page. Treat as high-confidence, not gospel, until that happens.
"""
from __future__ import annotations

from edge.names import norm  # noqa: F401 (re-exported -- see edge/names.py for why shared)

ROSTER = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "DST": 1}  # FLEX = RB/WR/TE
SALARY_CAP = 50000


def actual_offense_points(st: dict) -> float:
    """DK Classic offensive scoring from one nflverse stats_player_week row
    (or any dict with the same field names/semantics -- passing/rushing/
    receiving yards, TDs, INTs thrown, fumbles lost, receptions, 2pt
    conversions). Bonuses: +3 for 100+ rush yds, +3 for 100+ rec yds, +3 for
    300+ pass yds (each independent -- a player can earn more than one in
    the same game, e.g. a 100-rush/100-rec game)."""
    g = lambda k: float(st.get(k, 0) or 0)
    pass_yds, rush_yds, rec_yds = g("passing_yards"), g("rushing_yards"), g("receiving_yards")
    pts = (
        0.04 * pass_yds + 4 * g("passing_tds") - 1 * g("passing_interceptions")
        + 0.1 * rush_yds + 6 * g("rushing_tds")
        + 0.1 * rec_yds + 6 * g("receiving_tds") + 1 * g("receptions")
        - 1 * (g("rushing_fumbles_lost") + g("receiving_fumbles_lost") + g("sack_fumbles_lost"))
        + 2 * (g("passing_2pt_conversions") + g("rushing_2pt_conversions") + g("receiving_2pt_conversions"))
    )
    if pass_yds >= 300:
        pts += 3
    if rush_yds >= 100:
        pts += 3
    if rec_yds >= 100:
        pts += 3
    return round(pts, 2)


# DK's points-allowed tiers, as (max_points_allowed_inclusive, dk_points),
# checked in order -- the last bucket (35+) has no upper bound.
_PA_TIERS = [(0, 10), (6, 7), (13, 4), (20, 1), (27, 0), (34, -1)]


def _points_allowed_score(pa: int) -> float:
    for cap, pts in _PA_TIERS:
        if pa <= cap:
            return pts
    return -4


def actual_dst_points(team_st: dict, points_allowed: int) -> float:
    """DK Classic DST scoring from one nflverse stats_team_week row (team's
    OWN defensive/special-teams stats that game) plus the opponent's real
    final score (points_allowed -- not in the same row; join from a
    schedule/results source, see nfl_ground_truth_ingest.py).

    KNOWN GAP, stated plainly rather than silently dropped: nflverse's
    team-week schema has no "blocked kick caused" column (fg_blocked/
    pat_blocked on a team's OWN row means kicks blocked AGAINST them, the
    opposite of what DST scoring wants). Blocked-kick points (+2 each,
    genuinely rare) are NOT included here -- this is a small, real
    undercount, not a rounding artifact, and should be named as such
    wherever this function's output gets used."""
    g = lambda k: float(team_st.get(k, 0) or 0)
    pts = (
        1 * g("def_sacks") + 2 * g("def_interceptions") + 2 * g("fumble_recovery_opp")
        + 2 * g("def_safeties") + 6 * (g("def_tds") + g("special_teams_tds"))
    )
    pts += _points_allowed_score(int(points_allowed))
    return round(pts, 2)


# statsapi-style full names <-> the 2/3-letter codes nflverse and DK both use
# (unlike MLB, NFL abbreviations are already consistent between the free
# data source and DK -- no AZ/ARI-style mismatch found so far, but this is
# worth re-checking against real DK draftables once that integration exists).
TEAM_NAME_TO_ABBR = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LA", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}
