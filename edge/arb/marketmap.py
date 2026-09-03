"""Map a book's own market names onto the canonical keys the engine compares.

This is the hard part of mixing a scrape with an aggregator feed: The Odds API
says `player_pass_yds`, a book's own JSON says "Passing Yards - Patrick
Mahomes". Comparing the wrong two things invents an arb that does not exist,
so anything unrecognised is dropped rather than guessed at.
"""
from __future__ import annotations

import re

# Markets that are NOT the full game, claimed first so no later rule takes them.
#
# GroupKey carries no period and no team, so "1st Quarter Point Spread -1.5"
# and the full-game "Point Spread -1.5" produce the SAME key -- two different
# bets in one group, which is how a fake arbitrage gets invented. FanDuel's own
# parser has guarded this since it was written (fanduel.PERIOD_MARKERS); this
# is the same guard for every book that comes through the market map, and it
# became load-bearing the moment the Fanatics feed stopped being filtered down
# to three bet types and started returning all seventeen.
#
# Team totals are dropped for the same reason rather than kept as `team_totals`:
# "Total Home Goals" and "Total Away Goals" both normalise to a subject of None,
# so at a shared line they would collide with each other.
NOT_FULL_GAME = re.compile("|".join((
    r"\b(1st|2nd|3rd|4th|5th|6th|7th|8th|9th|first|second|third|fourth)\b"
    r".{0,24}\b(half|quarter|period|innings?|set|map|round)\b",
    r"\b(half|quarter|period|innings?)\b\s*[-–—]?\s*\b(1st|2nd|3rd|4th|first|second)\b",
    # "Set 1 Game Handicap" -- the period numbered with a bare digit rather
    # than an ordinal. FanDuel names all three of a tennis match's set
    # handicaps with ONE marketType and distinguishes them only in marketName.
    r"\b(set|period|quarter|half|map|innings?)\s*\d+\b",
    r"\bhalf ?time\b|\bfull ?time\b|\bht/ft\b|\brest of (match|game)\b|\bhalves\b",
    # "(Regular Time)" is a settlement basis, not a decoration. DraftKings
    # prices soccer "Spread" and "Alt Spread (Regular Time)" as two ladders on
    # one match, and they disagree: Tottenham -2.5 was 7.0 on one and 8.5 on
    # the other. Merged onto `spreads` they were two prices for one side of one
    # group -- 23 of them across EPL, the Champions League and La Liga.
    r"\bregular time\b|\breg\.? time\b|\b90 min",
    # Side markets that share a total's vocabulary. "Total Corners" would map
    # to `totals` on the bare `totals?` rule and meet a game total at a line
    # they happen to share.
    r"\bcorners?\b|\bcards?\b|\bbookings?\b|\boffsides?\b|\bthrow ?ins?\b",
    r"\b(1st|2nd|3rd|4th|first|second|third|fourth)\b"
    r".{0,16}\b(goal|run|score|touchdown|td|basket|point)\b",
    r"\brace to\b|\bto win (either|both) half\b|\bwinning margin\b",
    r"\bteam totals?\b|\btotal (home|away)\b|\b(home|away) team\b",
    # "Set Betting" is a correct score, not a handicap: its lines are "2-0" and
    # "2-1" and its bets are player names. Claimed as a set handicap it lost
    # its line to float("2-0"), fell through to the team-moneyline rule and
    # put Joint 2-1 at 6.8 against Samsonova 2-1 at 4.0 in one group -- an
    # arb_sum of 0.397.
    r"\bodd/even\b|\bcorrect score\b|\bset betting\b|\bdouble chance\b|\bboth halves\b",
    r"\binterval\b|\bbands?\b|\bexact\b",
    # Same-game parlays. FanDuel's NRL page carries "Head to Head / Total
    # Points Parlay" and friends, whose marketTypes contain TOTAL_POINTS and
    # so reached the totals rule; the runners are "Bulldogs & Over (52.5)
    # Points", which no line parses out of, so every one of them collapsed
    # onto a single keyless `totals` group. A parlay is two bets and cannot be
    # arbitraged as one anyway -- see HANDOFF.md section 5.
    # `_doubles?` with the leading underscore, NOT a bare \bdoubles?\b: the
    # bare form also matched "Doubles O/U", which is a batter's two-base hits.
    # The parlay marketTypes are underscore-joined (TOTAL_POINTS_DOUBLE), and
    # their display names all say "Parlay".
    r"\bparlay\b|_doubles?\b",
    # The three-way variant of a two-way market is a DIFFERENT bet: the
    # two-way pushes where the three-way pays a third outcome. DraftKings'
    # golf pairings already had this documented; FanDuel's rugby league page
    # offers "Moneyline" and "Moneyline (3-Way)" on one match, and both were
    # writing to the same h2h key. Soccer is unaffected -- its three-way IS
    # the market and is named WIN-DRAW-WIN, not "3-Way".
    r"\b3[-  ]?way\b",
    # Soccer's derivatives of the moneyline. "To Win To Nil" is a different bet
    # from "to win" -- it also requires a clean sheet -- and it was mapping to
    # h2h off the bare `to win` in the rule below. Fanatics posted Eintracht
    # Frankfurt 4.1 / Augsburg 7.0 for it; filed as a three-way alongside
    # FanDuel's genuine Draw at 4.1 that summed to 0.63 and was reported as a
    # 58% arbitrage. Nothing about the numbers looked wrong until the market
    # name was read.
    # `to score` stops short of "to score a touchdown", which IS a full-game
    # market and pairs fine once it is keyed by player.
    r"\bto nil\b|\bclean sheet\b|\bgoalscorer\b|\bhat-?trick\b|\bto score (?!a touchdown)",
)), re.I)


# Markets about MORE THAN ONE subject. GroupKey carries a single `subject`, so
# these arrive with subject=None and every one of them in an event collapses
# onto the same key.
#
# "Either Pitcher Strikeouts Thrown" (one of the two starters reaches 11.5) and
# "Combined Pitcher Strikeouts Thrown" (their totals added) are different bets
# that both keyed to pitcher_strikeouts / 11.5 / over -- 19.00 against 1.613,
# an 11x price gap in one group. ingest_sportscontent already refuses a market
# whose selections name two Players, but these do not populate `participants`,
# so the name is the only signal. Neither can pair against a single pitcher's
# line in any case.
MULTI_SUBJECT = re.compile(
    r"\beither\b|\bcombined\b|\bboth (pitchers|players|teams|fighters)\b", re.I)


def is_full_game(name: str) -> bool:
    """False for a market this map must not key.

    Two conditions, both of which end in two different bets sharing one
    GroupKey: the market is not the whole game (a period, a team, a
    moneyline lookalike), or it is not about a single subject.
    """
    text = name or ""
    return not (NOT_FULL_GAME.search(text) or MULTI_SUBJECT.search(text))


# ordered: first match wins, so put specific patterns above general ones
RULES: list[tuple[str, str]] = [
    # Golf, first: these are two-player head-to-heads and must not fall through
    # to a generic winner rule. Only the TWO-way versions are usable -- the
    # "(3 Way)" variants carry a Tie selection and so are a different market --
    # but that is decided at ingest, where the runner count is visible, rather
    # than by pattern-matching a label. The hole rule precedes the 2-ball rule
    # because "2 Ball Holes Winner" matches both.
    (r"\b2 ball\b.*\bhole|\bholes?\b.*\bwinner\b", "golf_hole_group"),
    (r"\b2 ball\b", "golf_2ball"),
    (r"tournament.*matchup|\bh2h matchup\b", "golf_matchup"),
    # `to win` was bare here, which is how "To Win To Nil" became a moneyline.
    # It has to name what is being won: books write "Moneyline", "Win Market"
    # or "Match Betting" for the real thing, never a bare "To Win", so nothing
    # is lost by requiring the noun.
    (r"\b(money ?line|match result|match winner|match betting|win market"
     r"|to win (the )?(match|game|fight|bout)|head to head|1x2)\b", "h2h"),
    # Tennis counts three different things and calls them all handicaps and
    # totals. A set handicap of -1.5 and a games handicap of -1.5 are not the
    # same bet, and on one `spreads` key at one point they would be one group.
    # These sit above the generic rules so the generic rules never see them.
    (r"\bsets? handicap\b", "spreads_sets"),
    (r"\bgames? handicap\b", "spreads_games"),
    (r"\btotal sets\b", "totals_sets"),
    (r"\btotal games\b", "totals_games"),
    # `handicaps?` -- the plural was the whole of Fanatics' soccer spread
    # coverage. Oddschecker names the market "Handicaps", `\bhandicap\b` did
    # not match it, and all 218 soccer handicap markets across 22 leagues were
    # silently dropped: 1,843 bets, and it looked exactly like Fanatics not
    # offering soccer spreads at all.
    (r"\b(point spreads?|spreads?|handicaps?|line betting|run line|puck line)\b",
     "spreads"),
    # An alternate ladder is the SAME bet as the main line at the same number,
    # so both fold onto the main key and let the point in the GroupKey do the
    # distinguishing. That was already true of spreads by accident -- the
    # `spreads` rule above fires first on "Alternate Run Line", which made this
    # rule unreachable -- and NOT true of totals, which took a key of their
    # own. FanDuel maps every ALTERNATE_TOTAL_* to `totals`, so a DraftKings
    # alt total at 9.5 and a FanDuel total at 9.5 sat in two different groups
    # and could never pair. Alt ladders are where three-book middles come from,
    # so that was the wrong half of the asymmetry to keep.
    (r"\b(alternate|alt)\b.*\b(spread|handicap)\b", "spreads"),
    (r"\b(alternate|alt)\b.*\b(total|over ?/? ?under)\b", "totals"),
    (r"\b(team total)\b", "team_totals"),
    (r"\b(total (points|runs|goals)|totals?|over ?/? ?under|o ?/ ?u)\b", "totals"),
    (r"\b(draw no bet)\b", "draw_no_bet"),
    (r"\bboth teams to score\b", "btts"),
]

# How books join the parts of a COMBINED stat. "+" was the only one handled,
# and Fanatics uses commas.
_SEP = r"(?:\s*[+,&/]\s*|\s+and\s+|\s+)"

# player prop stat -> canonical suffix, matched against the market name
PLAYER_STATS: list[tuple[str, str]] = [
    # ---- ORDER MATTERS: specific and combo markets before general ones.
    # A greedy r"\bbases\b" mapped "Stolen Bases" to batter_total_bases, and a
    # greedy r"\bhits\b" mapped "Hits Allowed" (a pitcher stat) to batter_hits.
    # A mismapped market is worse than an unmapped one: it pairs two different
    # bets and invents an arbitrage, where an unmapped one is simply dropped.

    # combos -- must precede the single-stat patterns they contain.
    #
    # The separator is NOT just "+". Fanatics writes the same market with
    # commas -- "Player Hits, Runs, RBIs" -- which fell past a plus-only
    # pattern and landed on `batter_rbis` via the bare \brbis?\b rule below.
    # Shohei Ohtani's H+R+RBI Under 2.5 was then filed as his RBI Under 2.5 and
    # paired against a genuine FanDuel RBI Over 1.5: a reported free middle
    # between two completely different stats. His actual RBI line is 0.5.
    (rf"hits{_SEP}runs{_SEP}rbis", "batter_hits_runs_rbis"),
    (rf"hits{_SEP}walks{_SEP}stolen bases", None),
    (rf"hits{_SEP}runs{_SEP}stolen bases", None),
    (r"extra base hits", None),
    (r"plate appearance", None),
    (r"1st inning runs|first inning runs", None),
    # Game totals that contain a stat word. A player can be in context (an
    # SGP leg, a same-game tab) without making "Team Total Runs" a prop, so
    # these are claimed here to stop the batter rule below taking them.
    # None falls through to RULES, which keys them as the game markets.
    (r"team total runs|alternate total runs|\btotal runs\b", None),

    # "(batter)" names the batter's side of a stat the pitcher also has, so
    # these must precede the pitching rules. DraftKings labels the hitter's
    # strikeout prop "Strikeouts (Batter) Milestones", which the bare
    # `strikeouts` rule below otherwise claims as a PITCHER market -- pairing a
    # hitter's line against a starter's.
    (r"strikeouts \(batter\)", "batter_strikeouts"),
    (r"walks \(batter\)", "batter_walks"),

    # pitching -- "allowed"/"recorded" markets are the pitcher's, not the batter's
    (r"hits allowed", "pitcher_hits_allowed"),
    (r"earned runs allowed|earned runs", "pitcher_earned_runs"),
    (r"walks allowed", "pitcher_walks"),
    # The league feed says "Outs O/U" where the per-event feed says "Outs
    # Recorded". Without the bare form the trailing O/U fell through to the
    # game-totals rule and every starter's outs line became a game total.
    (r"outs recorded|\bouts\b", "pitcher_outs"),
    (r"pitcher strikeouts|strikeouts", "pitcher_strikeouts"),
    (r"record a win", "pitcher_record_a_win"),

    # batting
    (r"stolen bases", "batter_stolen_bases"),
    (r"total bases", "batter_total_bases"),
    (r"home runs?|to hit a home run", "batter_home_runs"),
    (r"\brbis?\b", "batter_rbis"),
    # DraftKings says "Runs O/U" and "Runs (Batter) Milestones", never "Runs
    # Scored". Safe as a bare word only because every game variant is claimed
    # above -- home runs, earned runs, the H+R+RBI combo, first-inning runs,
    # and the team/alternate/game total-runs rule.
    (r"runs scored|\bruns\b", "batter_runs_scored"),
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
    (rf"points{_SEP}rebounds{_SEP}assists|pts{_SEP}reb{_SEP}ast",
     "player_points_rebounds_assists"),
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
    # Checked here rather than as a None-mapping rule in RULES, because
    # `_first_match` cannot tell "matched a rule that maps to None" from "no
    # rule matched" -- so a None claimed in RULES falls straight through to
    # PLAYER_STATS and the guard would not hold.
    if not is_full_game(text):
        return None
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
