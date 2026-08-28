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


def mascot(name: str) -> str:
    """Last word of a team name -- the most stable token across sources
    ('Yankees', 'Chiefs'). Cities get abbreviated; mascots rarely do."""
    norm = normalize_team(name)
    return norm.split()[-1] if norm else ""


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


def match_event(
    board: Board,
    home: str,
    away: str,
    commence: datetime | None,
    sport_key: str | None = None,
    tolerance_minutes: float = 30.0,
    min_similarity: float = 0.7,
    min_team_similarity: float = 0.6,
) -> EventMeta | None:
    """Find the board event this scraped event refers to, or None.

    None means "do not merge" -- the scrape is then dropped rather than
    attached to a guess.
    """
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
