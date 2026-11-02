"""Every outside college basketball name -> the one DraftKings player it means.

Adam, 2026-11-02, before the first CBB slate: "the names have to match DraftKings.
No using first initial as a first name. The surnames, Jr., Sr., III, etc all need to
be considered because one site might use them and another doesn't. Nicknames might
be used." College makes this harder than any sport we run, and each of these was on
that first board (155565, 308 players, 10 games):

  * Two different Trent Perrys, UCLA $7,600 and Kansas $7,500. A name-only key gives
    one man's props to both, so every join here is inside a TEAM or the two teams of
    a GAME, and a row is keyed by the salary-list key ("trentperry|KU"), never by name.
  * RotoWire DISPLAYS "D. Aberdeen" and "R. Chinyelu"; the full name is the link's
    title (edge/ncaab_rotowire.py reads that). A bare initial is refused outright.
  * Suffixes: DK "Robert Wright III" / RotoWire "Robert Wright"; DK "John Mobley Jr."
    / RotoWire "John Mobley". DK "Patrick Ngongba II" / DK sportsbook either.
  * Compound names: "Trey Kaufman-Renn", "Joaquim Boumtje Boumtje", "Ras Elijah
    Godbolt", "Honor Withers Carello", any of which a site may shorten or hyphenate.
  * Double initials: "C.J. Cox" / "CJ Cox" fold to the same text. "RJ Godfrey" against
    a site's "Ryan Godfrey" is NOT accepted; it is listed for a person to read.

A name that cannot be resolved is RETURNED IN THE AUDIT rather than dropped silently:
the page and the CLI print `audit` so a miss is visible before lock.
"""
from __future__ import annotations

import re

from edge.names import first_names_agree, norm

_TAG = re.compile(r"^(.*?)\s*\(([^)]*)\)\s*$")
_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}
#: What a site may use instead of the suffix DK has ("Trey" is the common nickname
#: of a III, "Deuce" of a II): accepted only with that suffix present on DK's name.
_SUFFIX_NICK = {"iii": {"trey", "tre", "tripp", "trip"}, "ii": {"deuce"}}


def tokens(name: str) -> tuple[list[str], str | None]:
    """(folded name parts, suffix or None). "Patrick Ngongba II" -> (["patrick",
    "ngongba"], "ii"); "Kaufman-Renn" is two parts; "Cox, C.J." reads as "C.J. Cox";
    "C. J. Cox" and "C.J. Cox" both give "cj" as the first part."""
    s = (name or "").strip()
    if s.count(",") == 1:
        last, first = (x.strip() for x in s.split(","))
        if first.lower().rstrip(".") not in _SUFFIX:
            s = f"{first} {last}"
    raw = [t for t in re.split(r"[\s\-‐-―,]+", s) if t]
    suffix = None
    if len(raw) > 2 and raw[-1].lower().rstrip(".") in _SUFFIX:
        suffix = raw.pop().lower().rstrip(".")
    parts = [norm(t) if t.lower().rstrip(".") not in _SUFFIX else t.lower().rstrip(".") for t in raw]
    parts = [p for p in parts if p]
    # "C. J. Cox": two single letters are one double-initial first name.
    if len(parts) > 2 and len(parts[0]) == 1 and len(parts[1]) == 1:
        parts = [parts[0] + parts[1]] + parts[2:]
    return parts, suffix


def is_bare_initial(name: str) -> bool:
    """"T. Stokes" / "T Stokes" / "Stokes, T.": a first initial standing in for a
    first name. Never enough to identify anyone (Adam, 2026-11-02)."""
    parts, _ = tokens(name)
    return len(parts) >= 2 and len(parts[0]) == 1


def _same_person(src: str, dk: str) -> str | None:
    """How `src` and `dk` are the same name, or None. Called only among players on
    one team, and the caller still requires exactly one match."""
    a, sa = tokens(src)
    b, sb = tokens(dk)
    if len(a) < 2 or len(b) < 2 or len(a[0]) == 1:
        return None
    flat_a, flat_b = "".join(a), "".join(b)
    if flat_a == flat_b:
        return "exact" if (sa or None) == (sb or None) else "suffix"
    if a[0] != b[0] and sb in _SUFFIX_NICK and a[0] in _SUFFIX_NICK[sb] and a[-1] == b[-1]:
        return "nickname"
    # Same parts with the first name allowed to differ: every later part must be
    # present on the other side (Kaufman <-> Kaufman-Renn; Ras Elijah Godbolt <->
    # Elijah Godbolt, where "elijah" moves from a later part to the first name).
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if short[-1] != long_[-1] and short[-1] not in long_[1:]:
        return None
    if short[0] in long_ and all(p in long_ for p in short[1:]):
        return "partial"
    if all(p in long_[1:] for p in short[1:]) and first_names_agree(short[0], long_[0], typo=True):
        return "nickname"
    return None


class Rosters:
    """The slate's players by DK team, for joins that are never name-only."""

    def __init__(self, salaries: dict):
        self.salaries = salaries
        self.by_team: dict = {}
        self.key_of: dict = {}
        for key, info in salaries.items():
            self.by_team.setdefault(str(info.get("team") or "").upper(), []).append(info)
            self.key_of[id(info)] = key

    def resolve(self, name: str, teams) -> tuple[dict | None, str]:
        """(salary entry, how) for an outside name among `teams`; (None, reason) if
        no single player. A "(DUKE)" tag narrows `teams` to the tagged team. Exact
        spellings beat looser ones, so "Malik Davis" can't lose to "Malik Davis-Moore"."""
        m = _TAG.match(name or "")
        base, tag = (m.group(1), m.group(2).upper()) if m else (name or "", None)
        wanted = [str(t).upper() for t in teams if t]
        if tag in wanted:
            wanted = [tag]
        if is_bare_initial(base):
            return None, "initial"
        hits: dict = {}
        for team in wanted:
            for info in self.by_team.get(team, ()):
                how = _same_person(base, info.get("name") or "")
                if how:
                    hits.setdefault(how, []).append(info)
        for how in ("exact", "suffix", "nickname", "partial"):
            if how in hits:
                if len(hits[how]) == 1:
                    return hits[how][0], how
                return None, "ambiguous"
        return None, "unmatched"

    def key(self, info: dict) -> str:
        """The salary-list key: unique per player, unlike the name."""
        return self.key_of[id(info)]


class Audit:
    """What one source did: joined, joined loosely (read these), refused."""

    def __init__(self, source: str):
        self.source = source
        self.joined = 0
        self.loose: list = []
        self.refused: list = []

    def add(self, name: str, info: dict | None, how: str, team: str = "") -> None:
        if info is not None:
            self.joined += 1
            if how not in ("exact", "suffix"):
                self.loose.append(f"{name} = {info['name']} ({info.get('team')}, {how})")
        else:
            self.refused.append(f"{name}{f' ({team})' if team else ''}: {how}")

    def row(self) -> dict:
        return {"source": self.source, "joined": self.joined,
                "loose": sorted(set(self.loose)), "refused": sorted(set(self.refused))}
