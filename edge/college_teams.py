"""The college rosetta stone: every source's name for a school -> ONE school id.

Adam, 2026-11-21: "every database has its own version of the university/college
name ... build a robust rosetta stone that will decipher what college is being
referenced." The dialects we actually read, for the same programmes:

  DK DFS lobby code   "VU" (draftables) but "NOVA" in the same lobby's slate title
  DK DFS city+mascot  "Miami (FL)" + "Hurricanes"
  odds feed / books   "Miami", "Arkansas-Pine Bluff", "UConn"
  Torvik              "Miami FL", "N.C. State", "Mississippi", "Cal St. Northridge"
  ESPN                "Miami Hurricanes", "NC State", "Ole Miss", "CSUN"

and DK's codes are not even one-to-one: SDSU has been San Diego St. AND South
Dakota St., USF South Florida AND San Francisco, BSU Boise St. AND Ball St. --
which is why edge/ncaab.ALIASES grew one missed slate at a time.

The design, so it cannot half-work:

  * The canonical key is ESPN's team id (string). ESPN lists every D1 school and
    uses the SAME id for a school in basketball and football.
  * DK is anchored by DK's own `teamId`, read from the draftables `competitions`
    block (school as "city", mascot as "teamName") of every college draft group
    we have logged, and mapped to ESPN by school + mascot. The mascot is what tells
    Miami (FL) Hurricanes from Miami (OH) RedHawks. DK CODES are kept per sport as
    code -> {ids}, a set, because a code can mean two schools.
  * Every other spelling resolves by EXACT normalised alias only (normalise =
    case, accents, punctuation, Saint/St./State/St. folded). No fuzzy match at
    resolve time. A spelling two schools share (e.g. "miami" before the books'
    convention is pinned) resolves to nothing unless the caller passes `among`
    -- the schools actually in that game -- and that narrows it to one.
  * Unresolved names are returned / listed, never guessed: `audit()` and the
    build script (scripts/college_teams_build.py) print them so a new spelling
    is a one-line SEED_ALIASES entry, not a silent wrong join.

Data: data/college_teams.json, rebuilt by `python3 -m harness run college-teams`
(scripts/college_teams_build.py), committed on main like other model caches, so
Streamlit Cloud reads the same file.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data" / "college_teams.json"

#: Spellings no automatic rule can derive, as {ESPN location: [spellings]}. The
#: build resolves the left side through ESPN, so a typo here fails the build loudly
#: instead of mapping to nothing. Add to this when the build's audit lists a name.
#: Sources noted where a spelling is particular to one feed.
SEED_ALIASES = {
    "Ole Miss": ["Mississippi", "Ole Miss Rebels"],
    "UConn": ["Connecticut"],
    "Miami": ["Miami FL", "Miami (FL)", "Miami Florida", "Miami-Florida"],
    "Miami (OH)": ["Miami OH", "Miami Ohio", "Miami (Ohio)", "Miami-Ohio", "Miami RedHawks"],
    "NC State": ["N.C. State", "North Carolina State", "North Carolina St."],
    "Pittsburgh": ["Pitt"],
    "USC": ["Southern California", "Southern Cal"],
    "UCF": ["Central Florida"],
    "SMU": ["Southern Methodist"],
    "TCU": ["Texas Christian"],
    "BYU": ["Brigham Young"],
    "LSU": ["Louisiana State"],
    "VCU": ["Virginia Commonwealth"],
    "UNLV": ["Nevada-Las Vegas", "Nevada Las Vegas"],
    "UAB": ["Alabama-Birmingham", "Alabama Birmingham"],
    "UTEP": ["Texas-El Paso", "Texas El Paso"],
    "UTSA": ["Texas-San Antonio", "Texas San Antonio"],
    "Florida International": ["Florida Intl", "Florida Int'l"],
    "App State": ["Appalachian State", "Appalachian St."],
    "Louisiana": ["Louisiana-Lafayette", "Louisiana Lafayette", "UL Lafayette", "Louisiana Ragin' Cajuns"],
    "UL Monroe": ["Louisiana-Monroe", "Louisiana Monroe", "ULM"],
    "Hawai'i": ["Hawaii"],
    "San José State": ["San Jose State", "San Jose St."],
    "Massachusetts": ["UMass", "UMass Amherst"],
    "UMass Lowell": ["Massachusetts Lowell", "Mass Lowell"],
    "Texas A&M-Corpus Christi": ["Texas A&M-CC", "Texas A&M Corpus Christi", "TAMU-CC", "Texas A&M Corpus Chris"],
    "UT Rio Grande Valley": ["UTRGV", "Texas Rio Grande Valley", "UT-Rio Grande Valley"],
    "UT Martin": ["Tennessee-Martin", "Tennessee Martin", "UTM"],
    "Southeast Missouri State": ["SE Missouri State", "Southeast Missouri", "SEMO", "Southeast Missouri St."],
    "SIU Edwardsville": ["SIUE", "Southern Illinois-Edwardsville", "SIU-Edwardsville"],
    "Long Island University": ["LIU", "LIU Brooklyn", "Long Island"],
    "Loyola Chicago": ["Loyola (IL)", "Loyola-Chicago", "Loyola Illinois"],
    "Loyola Maryland": ["Loyola (MD)", "Loyola MD", "Loyola-Maryland"],
    "Loyola Marymount": ["LMU", "Loyola (CA)"],
    "California Baptist": ["Cal Baptist"],
    "Purdue Fort Wayne": ["Fort Wayne", "IPFW", "Purdue-Fort Wayne"],
    "IU Indianapolis": ["IU Indy", "IUPUI"],
    "Omaha": ["Nebraska Omaha", "Nebraska-Omaha", "UNO Mavericks"],
    "Little Rock": ["Arkansas-Little Rock", "Arkansas Little Rock", "UALR"],
    "Arkansas-Pine Bluff": ["Arkansas Pine Bluff", "UAPB"],
    "Cal State Northridge": ["CSUN", "CS Northridge", "Cal St. Northridge", "CSU Northridge"],
    "Cal State Fullerton": ["CS Fullerton", "CSUF", "CSU Fullerton", "Cal St. Fullerton"],
    "Cal State Bakersfield": ["CS Bakersfield", "CSUB", "CSU Bakersfield", "Cal St. Bakersfield"],
    "Long Beach State": ["Long Beach", "Cal State Long Beach", "LBSU"],
    "Cal Poly": ["Cal Poly SLO", "Cal Poly San Luis Obispo"],
    "UC San Diego": ["California San Diego", "UCSD"],
    "UC Santa Barbara": ["UCSB", "Santa Barbara"],
    "UC Irvine": ["UCI"],
    "UC Davis": ["California Davis"],
    "UC Riverside": ["UCR"],
    "UNC Wilmington": ["NC Wilmington", "UNCW", "North Carolina-Wilmington"],
    "UNC Greensboro": ["NC Greensboro", "UNCG", "North Carolina-Greensboro"],
    "UNC Asheville": ["NC Asheville", "UNCA", "North Carolina-Asheville"],
    "Charleston": ["College of Charleston", "Coll of Charleston"],
    "Gardner-Webb": ["Gardner Webb"],
    "Bethune-Cookman": ["Bethune Cookman"],
    "Mississippi Valley State": ["Miss Valley State", "Mississippi Valley St.", "MVSU"],
    "Grambling": ["Grambling State", "Grambling St."],
    "Prairie View A&M": ["Prairie View"],
    "Southern": ["Southern University", "Southern U"],
    "Saint Mary's": ["St. Mary's", "Saint Mary's (CA)", "St. Mary's (CA)", "Saint Marys"],
    "St. John's": ["Saint John's", "St John's (NY)"],
    "Saint Peter's": ["St. Peter's"],
    "St. Thomas": ["St. Thomas", "St. Thomas (MN)", "Saint Thomas", "St. Thomas MN"],
    "Detroit Mercy": ["Detroit"],
    "UIC": ["Illinois Chicago", "Illinois-Chicago", "UIC Flames"],
    "Milwaukee": ["Wisconsin-Milwaukee", "UW-Milwaukee"],
    "Green Bay": ["Wisconsin-Green Bay", "UW-Green Bay"],
    "Kansas City": ["UMKC", "Missouri-Kansas City"],
    "McNeese": ["McNeese State", "McNeese St."],
    "Nicholls": ["Nicholls State", "Nicholls St."],
    "Houston Christian": ["Houston Baptist"],
    "East Texas A&M": ["Texas A&M-Commerce", "Texas A&M Commerce"],
    "Sam Houston": ["Sam Houston State", "Sam Houston St."],
    "Stephen F. Austin": ["SFA", "Stephen F Austin"],
    "Southern Miss": ["Southern Mississippi"],
    "Middle Tennessee": ["Middle Tennessee State", "MTSU", "Middle Tennessee St."],
    "Queens University": ["Queens", "Queens (NC)"],
    "Florida Gulf Coast": ["FGCU"],
    "Fairleigh Dickinson": ["FDU", "Fairleigh-Dickinson"],
    "Mount St. Mary's": ["Mt. St. Mary's", "Mount Saint Mary's"],
    "Maryland Eastern Shore": ["MD Eastern Shore", "UMES", "Maryland-Eastern Shore"],
    "Central Connecticut": ["Central Connecticut State", "Central Connecticut St.", "CCSU"],
    "UAlbany": ["Albany", "Albany NY", "Albany (NY)"],
    "Pennsylvania": ["Penn"],
    "Boston University": ["Boston U"],
    "Army": ["Army West Point"],
    "Incarnate Word": ["UIW", "Incarnate Word Cardinals"],
    "Tarleton State": ["Tarleton", "Tarleton St."],
    "SE Louisiana": ["Southeastern Louisiana", "Southeastern La.", "SELA"],
    "Seattle U": ["Seattle"],
    "LSU New Orleans": ["New Orleans", "UNO Privateers"],
    "Northwestern State": ["NW State", "Northwestern St."],
    "South Carolina Upstate": ["USC Upstate", "SC Upstate"],
}

#: NOT "at": "A&T" folds to "at" (North Carolina A&T collided with North Carolina,
#: 11/21, found by the build's audit).
#: Spellings that honestly mean several schools, as {spelling: [ESPN locations]}.
#: Never pinned: they resolve only when `among` (the game's two schools) leaves one.
#: "Loyola": the books' name for Loyola Maryland (vs Georgetown, Mt. St. Mary's, 11/2-11/7).
SHARED_ALIASES = {
    "Loyola": ["Loyola Chicago", "Loyola Maryland", "Loyola Marymount"],
    "Miami": ["Miami", "Miami (OH)"],
}

_DROP = {"university", "univ", "of", "the"}
_FOLD = {"saint": "st", "state": "st", "mount": "mt", "college": "coll"}


def key(name: str) -> str:
    """Normalised spelling: "Saint Mary's (CA)" / "St. Mary's CA" -> "st marys ca";
    "Kansas St." / "Kansas State" -> "kansas st"; "Texas A&M-CC" -> "texas am cc"."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", "").replace("'", "").replace(".", "")
    s = re.sub(r"[-/(),]", " ", s)
    toks = [_FOLD.get(t, t) for t in s.split() if t not in _DROP]
    return " ".join(toks)


@lru_cache(maxsize=1)
def _load(path: str = str(PATH)) -> dict:
    p = Path(path)
    if not p.exists():
        return {"schools": {}, "alias": {}, "dk_code": {}, "dk_team": {}, "major": []}
    return json.loads(p.read_text())


def reload() -> None:
    _load.cache_clear()


def school(sid: str | None) -> dict | None:
    return _load()["schools"].get(str(sid)) if sid else None


def candidates(name: str) -> set[str]:
    """Every school id a spelling could mean (empty = unknown). Tries the whole
    name, then the name with a trailing mascot removed when that mascot IS the
    candidate school's ("Wichita State Shockers")."""
    reg = _load()
    k = key(name)
    if not k:
        return set()
    hit = set(reg["alias"].get(k, ()))
    if hit:
        return hit
    toks = k.split()
    for cut in (1, 2, 3):
        if len(toks) <= cut:
            break
        head, tail = " ".join(toks[:-cut]), " ".join(toks[-cut:])
        got = {s for s in reg["alias"].get(head, ())
               if key(reg["schools"][s].get("mascot", "")) == tail}
        if got:
            return got
    return set()


def resolve(name: str, among=None) -> str | None:
    """The one school id `name` means, or None (unknown or ambiguous).

    `among`: the ids that can be right here (the two schools of a game). A
    spelling several schools share is decided only by that, then by preferring
    D1/DK/book-seen schools over the rest of ESPN's football list -- and only
    when exactly one is left."""
    c = candidates(name)
    if among is not None:
        c &= {str(x) for x in among}
    if len(c) > 1:
        major = set(_load().get("major", ()))
        c = (c & major) or c
    return next(iter(c)) if len(c) == 1 else None


def dk_ids(code: str, sport: str) -> set[str]:
    """School ids a DK DFS code has meant in `sport` ("CBB"/"CFB"); can be 2."""
    return set(_load()["dk_code"].get(sport, {}).get((code or "").upper().strip(), ()))


def dk_team(team_id, sport: str) -> str | None:
    """School id of DK's own teamId (exact; prefer it to a code when you have it)."""
    return _load()["dk_team"].get(f"{sport}:{team_id}")


def same_school(dk_code: str, name: str, sport: str) -> bool | None:
    """Does DK code `dk_code` name the school called `name`?  True / False when the
    registry knows both, None when it knows either side not at all (the caller
    falls back to its own test, and should report the name)."""
    ids = dk_ids(dk_code, sport)
    if not ids:
        return None
    c = candidates(name)
    if not c:
        return None
    if ids & c:
        return True
    return False


def audit(names, sport: str | None = None) -> dict:
    """{"unknown": [...], "ambiguous": {name: [ids]}} for a list of spellings --
    what a page or CLI prints so a miss is visible before lock."""
    out = {"unknown": [], "ambiguous": {}}
    for n in sorted({str(x) for x in names if x}):
        if sport and dk_ids(n, sport):
            continue
        c = candidates(n)
        if not c:
            out["unknown"].append(n)
        elif resolve(n) is None:
            out["ambiguous"][n] = sorted(c)
    return out
