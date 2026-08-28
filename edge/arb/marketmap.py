"""Map a book's own market names onto the canonical keys the engine compares.

This is the hard part of mixing a scrape with an aggregator feed: The Odds API
says `player_pass_yds`, a book's own JSON says "Passing Yards - Patrick
Mahomes". Comparing the wrong two things invents an arb that does not exist,
so anything unrecognised is dropped rather than guessed at.
"""
from __future__ import annotations

import re

# ordered: first match wins, so put specific patterns above general ones
RULES: list[tuple[str, str]] = [
    (r"\b(money ?line|match result|match winner|win market|to win|head to head|1x2)\b", "h2h"),
    (r"\b(point spread|spread|handicap|line betting|run line|puck line)\b", "spreads"),
    (r"\b(alternate|alt)\b.*\b(spread|handicap)\b", "alternate_spreads"),
    (r"\b(alternate|alt)\b.*\b(total|over ?/? ?under)\b", "alternate_totals"),
    (r"\b(team total)\b", "team_totals"),
    (r"\b(total (points|runs|goals)|totals?|over ?/? ?under|o ?/ ?u)\b", "totals"),
    (r"\b(draw no bet)\b", "draw_no_bet"),
    (r"\bboth teams to score\b", "btts"),
]

# player prop stat -> canonical suffix, matched against the market name
PLAYER_STATS: list[tuple[str, str]] = [
    # ---- ORDER MATTERS: specific and combo markets before general ones.
    # A greedy r"\bbases\b" mapped "Stolen Bases" to batter_total_bases, and a
    # greedy r"\bhits\b" mapped "Hits Allowed" (a pitcher stat) to batter_hits.
    # A mismapped market is worse than an unmapped one: it pairs two different
    # bets and invents an arbitrage, where an unmapped one is simply dropped.

    # combos -- must precede the single-stat patterns they contain
    (r"hits\s*\+\s*runs\s*\+\s*rbis", "batter_hits_runs_rbis"),
    (r"hits\s*\+\s*walks\s*\+\s*stolen bases", None),
    (r"hits\s*\+\s*runs\s*\+\s*stolen bases", None),
    (r"extra base hits", None),
    (r"plate appearance", None),
    (r"1st inning runs|first inning runs", None),

    # pitching -- "allowed"/"recorded" markets are the pitcher's, not the batter's
    (r"hits allowed", "pitcher_hits_allowed"),
    (r"earned runs allowed|earned runs", "pitcher_earned_runs"),
    (r"walks allowed", "pitcher_walks"),
    (r"outs recorded", "pitcher_outs"),
    (r"pitcher strikeouts|strikeouts", "pitcher_strikeouts"),
    (r"record a win", "pitcher_record_a_win"),

    # batting
    (r"stolen bases", "batter_stolen_bases"),
    (r"total bases", "batter_total_bases"),
    (r"home runs?|to hit a home run", "batter_home_runs"),
    (r"\brbis?\b", "batter_rbis"),
    (r"runs scored", "batter_runs_scored"),
    (r"\bsingles\b", "batter_singles"),
    (r"\bdoubles\b", "batter_doubles"),
    (r"\btriples\b", "batter_triples"),
    (r"\bhits\b|to get a hit", "batter_hits"),

    # football
    (r"pass(ing)? yards?", "player_pass_yds"),
    (r"pass(ing)? touchdowns?|pass(ing)? tds?", "player_pass_tds"),
    (r"pass(ing)? attempts?", "player_pass_attempts"),
    (r"pass(ing)? completions?", "player_pass_completions"),
    (r"rush(ing)? yards?", "player_rush_yds"),
    (r"rush(ing)? attempts?", "player_rush_attempts"),
    (r"receiving yards?|rec(eption)? yards?", "player_reception_yds"),
    (r"receptions?", "player_receptions"),
    (r"anytime touchdown|anytime td|to score a touchdown", "player_anytime_td"),

    # basketball
    (r"points \+ rebounds \+ assists|pts \+ reb \+ ast", "player_points_rebounds_assists"),
    (r"three pointers?|3 ?pointers?|threes made", "player_threes"),
    (r"\bpoints\b", "player_points"),
    (r"\brebounds?\b", "player_rebounds"),
    (r"\bassists?\b", "player_assists"),
    (r"\bblocks?\b", "player_blocks"),
    (r"\bsteals?\b", "player_steals"),

    # hockey
    (r"shots on goal", "player_shots_on_goal"),
    (r"\bsaves\b", "player_total_saves"),
    (r"\bgoals?\b", "player_goals"),
]

def _first_match(rules, text: str) -> str | None:
    """First matching rule wins. A rule may map to None, meaning "recognised,
    but there is no canonical key for it" -- that stops a later, looser pattern
    claiming it. `Stolen Bases` must not fall through to `total bases`."""
    for pattern, key in rules:
        if re.search(pattern, text):
            return key
    return None


def canonical_market(name: str, group: str = "", player: str | None = None) -> str | None:
    """Return a canonical market key, or None if we cannot say confidently.

    Precedence depends on whether a player is in play. "Total Points" is a game
    total; "Total Points" attached to Nikola Jokic is a player prop. Guessing
    that wrong pairs a game total against a player line and invents an arb, so
    the player context decides which rule set wins rather than rule ordering.
    """
    text = f"{group} {name}".lower().strip()
    if player:
        return _first_match(PLAYER_STATS, text) or _first_match(RULES, text)
    return _first_match(RULES, text) or _first_match(PLAYER_STATS, text)


PLAYER_SPLIT = re.compile(r"\s+[-–—]\s+|\s*\(\s*|\s*\)\s*")


def split_player(name: str, known_players: set[str] | None = None) -> tuple[str, str | None]:
    """Split a market label into (market name, player).

    Books disagree on order: Oddschecker writes "Passing Yards - Patrick
    Mahomes", DraftKings writes "Fernando Tatis Jr. - Total Bases". Guessing by
    capitalisation picks "Total Bases" as the person, so the fragment that
    names a *statistic* is identified first and the remainder is the player.
    """
    parts = [p.strip() for p in PLAYER_SPLIT.split(name) if p.strip()]
    if len(parts) < 2:
        return name, None

    if known_players:
        for p in parts:
            if p in known_players:
                return " ".join(x for x in parts if x != p), p

    stat_idx = [i for i, p in enumerate(parts) if _first_match(PLAYER_STATS, p.lower())]
    if len(stat_idx) == 1:
        i = stat_idx[0]
        return parts[i], " ".join(p for j, p in enumerate(parts) if j != i) or None

    # nothing named a stat: fall back to "a short Title Case fragment is a name"
    for p in parts[1:]:
        words = p.split()
        if 2 <= len(words) <= 3 and all(w[:1].isupper() for w in words if w):
            return " ".join(x for x in parts if x != p), p
    return name, None
