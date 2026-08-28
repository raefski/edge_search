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

    # 1. Over/Under: totals, team totals, and the bulk of player props.
    if low in OVER_UNDER_NAMES:
        return OVER_UNDER_NAMES[low], description, point

    # 2. Yes/No: anytime touchdown, anytime goal scorer, double-double.
    if low in YES_NO_NAMES:
        return YES_NO_NAMES[low], description, point

    # 3. Team-named sides on a spread: fold both listings onto the home line so
    #    "Home -3.5" and "Away +3.5" land in the same group.
    if is_spread_market(market) and point is not None:
        if _same_team(name, home_team):
            return "home", None, round(float(point), 2)
        if _same_team(name, away_team):
            return "away", None, round(-float(point), 2)
        # unknown team on a spread: cannot pair it safely
        return None

    # 4. Team-named sides on a moneyline, plus Draw for 3-way soccer.
    if _same_team(name, home_team):
        return "home", None, None
    if _same_team(name, away_team):
        return "away", None, None
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
