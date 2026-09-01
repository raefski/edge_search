"""Map raw provider outcomes onto canonical (GroupKey, side) pairs.

The rules are deliberately shape-driven rather than a per-market lookup table,
so a market key the provider adds next season normalizes without a code change.
"""
from __future__ import annotations

import re

SPREAD_MARKETS = ("spreads", "alternate_spreads", "spread")
OVER_UNDER_NAMES = {"over": "over", "under": "under"}
YES_NO_NAMES = {"yes": "yes", "no": "no"}


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"\s+", " ", str(text)).strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def split_fixture(name: str) -> tuple[str, str] | None:
    """(away, home) from an event name, or None if it is not a fixture.

    Team sports are written "Away @ Home". Tennis is written "Player A vs
    Player B" -- a real two-player fixture, not a field, so it fits the normal
    model perfectly once it is parsed. Requiring "@" skipped every tennis
    event before it was looked at.

    "vs" has no home and away, so the order is taken as listed and used
    consistently. That is only a labelling convention: what matters is that
    both books produce the same two names, which match_event then pairs on
    similarity regardless of which side each lands.
    """
    text = (name or "").strip()
    if " @ " in text:
        away, home = text.split(" @ ", 1)
        return away.strip(), home.strip()
    for sep in (" vs. ", " vs ", " v "):
        if sep in text:
            first, second = text.split(sep, 1)
            if first.strip() and second.strip():
                return first.strip(), second.strip()
    return None


def is_spread_market(market: str) -> bool:
    return market.startswith(SPREAD_MARKETS) or "_spreads" in market or market.startswith("alternate_spreads")


def _same_team(name: str, team: str | None) -> bool:
    """Do these two strings name the same team?

    Books abbreviate differently -- DraftKings says "SEA Seahawks" where
    FanDuel says "Seattle Seahawks". Exact comparison silently dropped every
    spread whose event came from the other book, which looks like missing
    coverage rather than a bug.
    """
    if not team or not name:
        return False
    if name.strip().lower() == team.strip().lower():
        return True
    from .matching import team_similarity
    return team_similarity(name, team) >= 0.6


def pick_team_side(name: str, home_team: str | None, away_team: str | None,
                   threshold: float = 0.6, margin: float = 0.05) -> str | None:
    """"home", "away", or None -- whichever of the two this outcome NAMES.

    Compares against both and takes the better, rather than returning the first
    to clear the bar. Order mattered because the abbreviations books use are
    lossy in ways that make two teams in ONE fixture look alike:

        Man Utd @ Man City   normalize_team drops "city" as noise, so "Man
                             City" becomes "man"; _mascot_extension then reads
                             "Man Utd" as "man" + a mascot and scores it 0.90
                             against the HOME team. Home was tested first, so
                             both runners became `home`, the away price
                             overwrote the home price, and the group held a
                             moneyline summing to 0.59.

        Omonia @ Omonia FC Aradippou   same shape, same result.

    Taking the better match fixes both: "Man Utd" scores 1.00 against the away
    team and 0.90 against the home one. `margin` refuses the genuinely
    ambiguous rather than guessing, which is the house rule everywhere else
    here -- a wrong side is a fake arbitrage, a dropped one is only a gap.
    """
    from .matching import team_similarity
    sh = team_similarity(name, home_team) if home_team else 0.0
    sa = team_similarity(name, away_team) if away_team else 0.0
    if max(sh, sa) < threshold:
        return None
    if abs(sh - sa) < margin:
        return None
    return "home" if sh > sa else "away"


def is_totals_market(market: str) -> bool:
    return market.startswith("totals") or market.endswith("_totals")


def is_team_side(market: str) -> bool:
    """h2h / spread markets whose outcome name is a team rather than Over/Under."""
    return market.startswith(("h2h", "spreads", "alternate_spreads")) or "_spreads" in market or "_h2h" in market


def normalize_outcome(
    market: str,
    name: str,
    point: float | None,
    description: str | None,
    home_team: str | None,
    away_team: str | None,
) -> tuple[str, str | None, float | None] | None:
    """Return (side, subject, group_point), or None if unusable.

    side         canonical outcome label within the group
    subject      player or team the market is about (None for game-level markets)
    group_point  the line that both sides of the group must share
    """
    name = _clean(name)
    description = _clean(description)
    if not name:
        return None

    low = name.lower()

    # A TOTAL WITHOUT A LINE IS NOT A BET. The same hole the spread branch
    # below closes, on the other axis: a market keyed `totals` whose line does
    # not parse fell through to the team-moneyline rule, so every rung of it
    # landed on ONE keyless group and the last one won. That is how FanDuel's
    # rugby league parlays -- "Bulldogs & Over (52.5) Points" -- produced 286
    # same-book price conflicts in a single scan.
    if is_totals_market(market) and point is None:
        return None

    # 1. Over/Under: totals, team totals, and the bulk of player props.
    if low in OVER_UNDER_NAMES:
        return OVER_UNDER_NAMES[low], description, point

    # 2. Yes/No: anytime touchdown, anytime goal scorer, double-double.
    if low in YES_NO_NAMES:
        return YES_NO_NAMES[low], description, point

    # 3. Team-named sides on a spread: fold both listings onto the home line so
    #    "Home -3.5" and "Away +3.5" land in the same group.
    if is_spread_market(market):
        # A spread with no line is not a moneyline. Without this it fell
        # through to rule 4 and became one -- which is how every rung of a
        # market whose "line" does not parse as a number ("Set Betting" is
        # priced 2-0, 2-1) collapsed onto ONE keyless group, last rung wins,
        # and produced a two-sided price that summed to 0.397.
        if point is None:
            return None
        side = pick_team_side(name, home_team, away_team)
        if side == "home":
            return "home", None, round(float(point), 2)
        if side == "away":
            return "away", None, round(-float(point), 2)
        # unknown or ambiguous team on a spread: cannot pair it safely
        return None

    # 4. Team-named sides on a moneyline, plus Draw for 3-way soccer.
    side = pick_team_side(name, home_team, away_team)
    if side is not None:
        return side, None, None
    if low in ("draw", "tie"):
        return "draw", None, None

    # 5. Anything else with a distinct named outcome: futures fields, first
    #    basket scorer, method-of-victory. The name itself is the side.
    return slug(name), description, (round(float(point), 2) if point is not None else None)


def side_label(side: str, event_home: str | None, event_away: str | None, key_subject: str | None) -> str:
    if side == "home":
        return event_home or "Home"
    if side == "away":
        return event_away or "Away"
    if side in ("over", "under", "yes", "no", "draw"):
        return side.capitalize()
    return side.replace("_", " ").title()
