"""Align a scraped event with the same event in the aggregator feed.

A scraped Fanatics price is only worth having if it can be compared against the
DraftKings and FanDuel prices already on the board. That requires deciding that
"NY Yankees" at 19:05 and "New York Yankees" at 19:05 are one event -- and
refusing to decide when it is not clear, because a wrong match manufactures an
arbitrage between two different games.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .models import Board, EventMeta

NOISE = {
    "the", "fc", "cf", "sc", "afc", "cfc", "united", "city",
    "at", "vs", "v", "@",
}
ABBREV = {
    "ny": "new york", "nj": "new jersey", "la": "los angeles", "sf": "san francisco",
    "tb": "tampa bay", "kc": "kansas city", "gb": "green bay", "ne": "new england",
    "no": "new orleans", "sd": "san diego", "st": "saint", "st.": "saint",
}


def normalize_team(name: str) -> str:
    if not name:
        return ""
    text = re.sub(r"[^a-z0-9 ]+", " ", str(name).lower())
    words = [ABBREV.get(w, w) for w in text.split()]
    words = [w for w in words if w not in NOISE]
    return " ".join(words).strip()


# Tokens that trail a team name to qualify the SQUAD, not to name it. They
# are the last word for BOTH teams in a fixture, which is exactly what mascot()
# must not key on -- see below.
QUALIFIERS = {"women", "w", "men", "m", "ii", "b", "reserves", "youth",
              "u17", "u18", "u19", "u20", "u21", "u23"}


def mascot(name: str) -> str:
    """Last word of a team name -- the most stable token across sources
    ('Yankees', 'Chiefs'). Cities get abbreviated; mascots rarely do.

    Trailing squad qualifiers are stepped over first. In a women's fixture
    every team ends in "Women", so the bare last word made the mascot shortcut
    below fire between the two SIDES of one match: "St George Illawarra
    Dragons Women" scored 0.85 against "Brisbane Broncos Women" -- beating its
    genuine 0.50 against the away name the event actually carried -- and both
    runners were filed as `home`.

    They are stripped here and NOT in normalize_team, deliberately: dropping
    "Women" from the name itself would let a women's fixture match the men's
    fixture of the same two clubs, which is a worse error than missing one.
    """
    words = [w for w in normalize_team(name).split() if w]
    while len(words) > 1 and words[-1] in QUALIFIERS:
        words.pop()
    return words[-1] if words else ""


# Words that distinguish two different schools rather than naming a mascot.
# "Michigan" vs "Michigan State" must NOT match, while "UCLA" vs "UCLA Bruins"
# must. The difference is entirely in what the extra words are.
AMBIGUOUS_SUFFIXES = {
    "state", "tech", "am", "southern", "northern", "eastern", "western",
    "central", "international", "atlantic", "pacific", "chicago", "dominion",
    "carolina", "illinois", "florida", "texas", "michigan", "washington",
}


def _mascot_extension(a_words: list[str], b_words: list[str]) -> bool:
    """True when one name is the other plus a mascot ("UCLA" / "UCLA Bruins")."""
    short, long_ = sorted((a_words, b_words), key=len)
    if not short or long_[: len(short)] != short or len(long_) == len(short):
        return False
    return not (set(long_[len(short):]) & AMBIGUOUS_SUFFIXES)


def team_similarity(a: str, b: str) -> float:
    na, nb = normalize_team(a), normalize_team(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # Books disagree on whether to include the mascot: FanDuel says "LSU",
    # Oddschecker says "LSU Tigers". Word-overlap alone scores that 0.5.
    if _mascot_extension(na.split(), nb.split()):
        return 0.9
    wa, wb = set(na.split()), set(nb.split())
    if not wa or not wb:
        return 0.0
    jaccard = len(wa & wb) / len(wa | wb)
    if mascot(a) and mascot(a) == mascot(b):
        jaccard = max(jaccard, 0.85)
    return jaccard


# Sports where the start time is an ESTIMATE, not an appointment.
#
# A tennis match is "third on Court 5": it begins when the one before it ends,
# so every book publishes a guess and the guesses diverge down the order of
# play. Measured on a live board 2026-09-07, DraftKings ran 70-100 minutes
# later than FanDuel on fixtures whose player names matched at 1.00 -- and the
# gap widened through the day, which is what a different assumed match length
# looks like. At the 30-minute default this dropped EVERY one of them: 0 of 84
# DraftKings tennis events found their FanDuel twin.
#
# MMA and boxing are the same shape -- a card's bouts start when the previous
# one finishes. Golf is not listed because it never reaches here: field_event
# collapses a tour to one synthetic event and the pairing carries the identity.
#
# WHY WIDENING IS SAFE, measured rather than argued. Both participants must
# match independently, so confusing two events means finding the same two
# players in a different match -- a rematch, which is another day. Sweeping
# the tolerance over that live board:
#
#     30 min ->  0 of 84 matched, 0 ambiguous
#    120 min -> 10 of 84 matched, 0 ambiguous
#   1440 min -> 10 of 84 matched, 0 ambiguous
#
# Flat from two hours to twenty-four with nothing ever ambiguous: the name
# test is doing all the discriminating and the clock is only a tie-breaker.
# Six hours covers a full day's order-of-play slip and stops well short of the
# next day's card, which is the only thing on the other side of it.
NO_FIXED_START = ("tennis", "mma", "boxing")
NO_FIXED_START_TOLERANCE_MINUTES = 360.0
DEFAULT_TOLERANCE_MINUTES = 30.0


def start_tolerance_minutes(sport_key: str | None) -> float:
    """How far apart two books' start times may be and still be one event."""
    if sport_key and sport_key.startswith(NO_FIXED_START):
        return NO_FIXED_START_TOLERANCE_MINUTES
    return DEFAULT_TOLERANCE_MINUTES


def match_event(
    board: Board,
    home: str,
    away: str,
    commence: datetime | None,
    sport_key: str | None = None,
    tolerance_minutes: float | None = None,
    min_similarity: float = 0.7,
    min_team_similarity: float = 0.6,
) -> EventMeta | None:
    """Find the board event this scraped event refers to, or None.

    None means "do not merge" -- the scrape is then dropped rather than
    attached to a guess.

    `tolerance_minutes` defaults to whatever the SPORT warrants rather than to
    one number, because a scheduled fixture and a match that starts when the
    one before it ends are not the same problem -- see NO_FIXED_START. An
    explicit value still wins, so a caller that knows better can say so.
    """
    if tolerance_minutes is None:
        tolerance_minutes = start_tolerance_minutes(sport_key)
    best, best_score = None, 0.0
    for ev in board.events.values():
        if sport_key and ev.sport_key != sport_key:
            continue
        if commence and ev.commence_time:
            drift = abs((ev.commence_time - commence).total_seconds()) / 60.0
            if drift > tolerance_minutes:
                continue
        # Both teams must match independently. Averaging alone lets one exact
        # hit carry a bad one -- "Yankees vs Braves" would match "Mets vs
        # Braves" at 0.75 and pair two different games into a fake arb.
        pairs = (
            (team_similarity(home, ev.home_team or ""), team_similarity(away, ev.away_team or "")),
            # some books list the home team first regardless of convention
            (team_similarity(home, ev.away_team or ""), team_similarity(away, ev.home_team or "")),
        )
        score = 0.0
        for a, b in pairs:
            if min(a, b) >= min_team_similarity:
                score = max(score, (a + b) / 2.0)
        if score > best_score:
            best, best_score = ev, score
    return best if best_score >= min_similarity else None


def describe_match(home: str, away: str, ev: EventMeta | None) -> str:
    if ev is None:
        return f"no board event matched {away} @ {home}"
    return (f"{away} @ {home}  ->  {ev.away_team} @ {ev.home_team} "
            f"({ev.sport_title}, {ev.commence_time:%Y-%m-%d %H:%M}Z)")
