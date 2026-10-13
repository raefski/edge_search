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

# ---------------------------------------------------------------------------
# DraftKings contest shape (NBA Classic)
# ---------------------------------------------------------------------------
#: DK's lobby code. Unlike college ("CBB"), the NBA's is the obvious one.
DK_SPORT = "NBA"
#: DK's game-type id for NBA Classic. NOT read live yet -- the NBA was out of
#: season when this was written (2026-10-13, opening night is ~10/20) -- but
#: harness/status.py already filters the live lobby on 70 for exactly this,
#: and edge/dfs_run_nba.classic_groups resolves Classic by the lobby's own
#: GameTypes NAME first and only falls back to this id, so a wrong 70 shows up
#: as "no slates" on the page rather than as the wrong contest.
#: `python3 -m harness run hoops-verify` prints the live value.
CLASSIC_GAME_TYPE = 70
SLOTS = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"]
#: Which DK positions fill which slot. DK lists a player as "PG", "PG/SG",
#: "SF/PF", "PF/C" ...; G takes either guard, F either forward, UTIL anyone.
SLOT_ELIGIBLE = {"PG": {"PG"}, "SG": {"SG"}, "SF": {"SF"}, "PF": {"PF"}, "C": {"C"},
                 "G": {"PG", "SG"}, "F": {"SF", "PF"},
                 "UTIL": {"PG", "SG", "SF", "PF", "C"}}
#: DK NBA allows late swap, and on a seven-game Tuesday with staggered tips
#: it is where a GPP is won: a 7:00 PM star ruled out at 6:30 is replaceable,
#: but only from a slot a later-tipping player can still fill -- which is why
#: edge/dfs_opt_nba.assign_slots puts the latest tips in G / F / UTIL.
ALLOW_LATE_SWAP = True


def positions(dk_position: str) -> set[str]:
    """'PG/SG' -> {'PG', 'SG'}. Unknown tokens are dropped."""
    parts = {t.strip().upper() for t in (dk_position or "").replace(",", "/").split("/")}
    return parts & {"PG", "SG", "SF", "PF", "C"}


def slots_for(dk_position: str) -> set[str]:
    """DK position string -> every roster slot that player can fill."""
    pos = positions(dk_position)
    return {slot for slot, ok in SLOT_ELIGIBLE.items() if pos & ok}


#: DraftKings abbreviates five teams differently from stats.nba.com and the
#: books' full names (TEAM_NAME_TO_ABBR below): GS / NO / NY / SA / PHO. Every
#: comparison goes through team() so a Warriors player is never "on a team
#: with no line" because one side wrote GSW and the other GS.
DK_ALIAS = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "PHO": "PHX",
            "BRK": "BKN", "CHO": "CHA", "WSH": "WAS", "UTAH": "UTA", "NOR": "NOP",
            "GSW": "GSW", "NOP": "NOP", "NYK": "NYK", "SAS": "SAS", "PHX": "PHX"}


def team(code: str | None) -> str | None:
    if not code:
        return None
    c = str(code).strip().upper()
    return DK_ALIAS.get(c, c)


def team_from_name(name: str | None) -> str | None:
    """A book's "Golden State Warriors" (or "LA Clippers") -> GSW."""
    if not name:
        return None
    hit = TEAM_NAME_TO_ABBR.get(name.strip())
    if hit:
        return hit
    key = norm(name)
    for full, abbr in TEAM_NAME_TO_ABBR.items():
        if norm(full) == key or norm(full.rsplit(" ", 1)[-1]) == key \
                or key.endswith(norm(full.rsplit(" ", 1)[-1])):
            return abbr
    return None


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
