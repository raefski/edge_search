"""DK College Basketball (CBB) Classic: contest shape, scoring, team matching.

Companion to edge/nba.py. The SCORING is the NBA's -- DraftKings uses one
basketball table for both (points 1, three +0.5, rebound 1.25, assist 1.5,
steal 2, block 2, turnover -0.5, double-double +1.5, triple-double +3) -- so
actual_points is re-exported rather than copied. The ROSTER is not:

    G G G F F F UTIL UTIL     8 players, $50,000, 2+ games

There is no centre slot. DK lists college bigs as "F" or "F/C"; anything that
is not a guard fills F.

WHAT IS NOT VERIFIED (the season opens ~11/3; nothing here has met a live
payload): the lobby code "CBB", the Classic game-type id (resolved by the
lobby's GameTypes NAME in edge/dfs_run_ncaab.py, so no id is hardcoded), the
"F/C" spelling, and the 2-game minimum. `python3 -m harness run hoops-verify`
reads all of them off DraftKings and prints any disagreement.

TEAM NAMES ARE THE HARD PART. DraftKings writes "DUKE @ UNC"; a book writes
"Duke Blue Devils" and "North Carolina Tar Heels"; there are 360+ teams and no
free canonical table. So a book event is matched to a DK game the way the
NCAAF builder does it (edge/dfs_run_ncaaf.game_lines): first by which slate
players its props resolve to, and only then by name similarity inside the
same tip-off window -- see match_team below for what the name test accepts.
"""
from __future__ import annotations

import re

from edge.names import norm  # noqa: F401 (re-exported)
from edge.nba import actual_points  # noqa: F401 (same DK basketball table)

DK_SPORT = "CBB"
SLOTS = ["G", "G", "G", "F", "F", "F", "UTIL", "UTIL"]
SLOT_ELIGIBLE = {"G": {"G"}, "F": {"F"}, "UTIL": {"G", "F"}}
SALARY_CAP = 50000
MIN_TEAMS = 2
MIN_GAMES = 2
#: NOT verified for CBB (NBA's is). Kept True so slot assignment still puts
#: the latest tips in UTIL; it costs nothing if DK turns out to disallow it.
ALLOW_LATE_SWAP = True


def positions(dk_position: str) -> set[str]:
    """'G', 'F', 'G/F', 'F/C', 'C' -> {'G'} / {'F'} / {'G', 'F'}."""
    out = set()
    for tok in (dk_position or "").replace(",", "/").split("/"):
        t = tok.strip().upper()
        if t in ("G", "PG", "SG"):
            out.add("G")
        elif t in ("F", "SF", "PF", "C"):
            out.add("F")
    return out


def slots_for(dk_position: str) -> set[str]:
    pos = positions(dk_position)
    return {slot for slot, ok in SLOT_ELIGIBLE.items() if pos & ok}


# ---------------------------------------------------------------------------
# Book name <-> DraftKings abbreviation
# ---------------------------------------------------------------------------
#: Words a book appends to a school that carry no identity of their own.
_FILLER = re.compile(r"\b(university|univ|of|the|at)\b")
#: The short forms that defeat every string test. Only the ones a main slate
#: actually carries are worth listing; anything missing just falls back to the
#: league-average total (flagged on the page), it does not break a build.
ALIASES = {
    "UNC": "north carolina", "UK": "kentucky", "KU": "kansas", "UCONN": "connecticut",
    "MSU": "michigan state", "OSU": "ohio state", "LSU": "louisiana state",
    "USC": "southern california", "UCLA": "ucla", "SMU": "southern methodist",
    "TCU": "texas christian", "BYU": "brigham young", "VCU": "virginia commonwealth",
    "UNLV": "unlv", "UCF": "ucf", "ISU": "iowa state", "WVU": "west virginia",
    "PITT": "pittsburgh", "MIZZ": "missouri", "ZAGA": "gonzaga", "GONZ": "gonzaga",
    "NOVA": "villanova", "UVA": "virginia", "VT": "virginia tech", "GT": "georgia tech",
    "FSU": "florida state", "MIAFL": "miami", "ND": "notre dame", "SDSU": "san diego state",
    "ARIZ": "arizona", "ASU": "arizona state", "TTU": "texas tech", "TAMU": "texas am",
    "OKST": "oklahoma state", "KSU": "kansas state", "ILL": "illinois", "IU": "indiana",
    "PUR": "purdue", "WIS": "wisconsin", "MINN": "minnesota", "NEB": "nebraska",
    "MICH": "michigan", "MD": "maryland", "RUTG": "rutgers", "NW": "northwestern",
    "PSU": "penn state", "BAMA": "alabama", "ALA": "alabama", "AUB": "auburn",
    "ARK": "arkansas", "UGA": "georgia", "TENN": "tennessee", "VAN": "vanderbilt",
    "MISS": "mississippi", "MSST": "mississippi state", "SCAR": "south carolina",
    "CREI": "creighton", "MARQ": "marquette", "XAV": "xavier", "PROV": "providence",
    "SHU": "seton hall", "GTWN": "georgetown", "DEP": "depaul", "BUT": "butler",
    "SJU": "st johns", "HOU": "houston", "BAY": "baylor", "CIN": "cincinnati",
    "MEM": "memphis", "STMARY": "saint marys", "SMC": "saint marys",
}


def _clean(name: str) -> str:
    s = (name or "").lower().replace("&", "").replace("'", "").replace(".", "")
    s = re.sub(r"\bst\b", "st", s)
    s = _FILLER.sub(" ", s)
    return " ".join(s.split())


def match_team(dk_abbr: str, book_name: str) -> float:
    """How well a DK abbreviation names a book's team, 0 (no) to 1 (exact).

    Ordered from exact to loose. The loose tests are only ever applied INSIDE
    one tip-off window (edge/dfs_run_ncaab.game_lines), where the candidate
    set is a handful of games, so "D-U-K-E are letters of Duke Blue Devils in
    order" is discriminating enough there and would not be across a whole day.
    """
    code = (dk_abbr or "").upper().strip()
    name = _clean(book_name)
    if not code or not name:
        return 0.0
    words = name.split()
    target = ALIASES.get(code)
    if target:
        # A listed alias is authoritative either way: UK is Kentucky, so it is
        # never Kansas no matter what the letter tests below would say.
        return 1.0 if name.startswith(target) else 0.0
    flat = name.replace(" ", "")
    low = code.lower()
    if flat.startswith(low) or words[0] == low:
        return 0.9
    initials = "".join(w[0] for w in words)
    if initials.startswith(low) or (len(low) >= 3 and ("u" + initials).startswith(low)):
        return 0.8
    # letters of the code in order inside the first two words
    head = "".join(words[:2])
    it = iter(head)
    if all(ch in it for ch in low) and head[0] == low[0]:
        return 0.6
    return 0.0
