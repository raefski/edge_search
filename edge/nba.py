"""DK NBA Classic scoring + free-data ground truth helpers.

Companion to edge/dfs.py (MLB) and edge/nfl.py -- see nfl.py's module
docstring for why this isn't a shared abstraction yet. Scoring values
converged across multiple independent sources (DFS_MULTISPORT_PLAN.md §1),
NOT yet confirmed against DK's own rules page directly (every automated
fetch attempt was blocked) -- high confidence, not gospel.
"""
from __future__ import annotations

from edge.names import norm  # noqa: F401 (re-exported -- see edge/names.py for why shared)

ROSTER = {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UTIL": 1}
SALARY_CAP = 50000
MIN_TEAMS = 2
MIN_GAMES = 2


def actual_points(st: dict) -> float:
    """DK Classic scoring from one stats.nba.com leaguegamelog row (or any
    dict using the same field names: PTS/REB/AST/STL/BLK/TOV/FG3M).

    Double-double/triple-double bonus is based on double-digit totals across
    {points, rebounds, assists, blocks, steals} -- the same 5 categories DK's
    own rules use, not just the traditional pts/reb/ast trio."""
    g = lambda k: float(st.get(k, 0) or 0)
    pts, reb, ast, stl, blk, tov, fg3 = (
        g("PTS"), g("REB"), g("AST"), g("STL"), g("BLK"), g("TOV"), g("FG3M"))
    base = pts + 0.5 * fg3 + 1.25 * reb + 1.5 * ast + 2 * stl + 2 * blk - 0.5 * tov
    cats_double_digit = sum(1 for v in (pts, reb, ast, blk, stl) if v >= 10)
    if cats_double_digit >= 3:
        base += 3.0   # triple-double (does not stack with double-double)
    elif cats_double_digit >= 2:
        base += 1.5   # double-double
    return round(base, 2)


TEAM_NAME_TO_ABBR = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP", "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}
