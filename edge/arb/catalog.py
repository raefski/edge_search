"""One league catalog, shared by all three books.

The scanner used to name leagues three separate times -- a FanDuel page slug, a
hardcoded DraftKings league id, a hand-captured Oddschecker eventId -- and a
league only existed where all three happened to be filled in. That is why five
sports were scanned out of the ~25 each book offers.

This module holds the correspondence instead, and each book resolves its own
side of it at scan time:

    FanDuel      one call per SPORT, split into leagues by competition name
    DraftKings   league ids read off the public catalog page (478 of them)
    Fanatics     Oddschecker eventIds, discovered by probe and cached

`key` is the canonical sport_key, and it is load-bearing: `matching.match_event`
will only merge two events that carry the SAME sport_key, so it is the join
that lets three books price one game. Two consequences follow, and both are
deliberate:

* A league whose name no pattern matches is not dropped -- it lands under a
  generic key derived from its own name, so it still gets a board and can still
  arbitrage against another book that lands on the same generic key. What it
  cannot do is collide with a curated league.
* Getting a pattern WRONG is worse than leaving it out, because it would file
  two different competitions under one key. match_event still has to agree on
  both team names and the start time, so a mismatch shows up as no matches
  rather than a fake arb -- but the patterns are anchored and specific for the
  same reason the market map is.

Every id and name here was verified live on 2026-08-30.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .normalize import slug


@dataclass(frozen=True)
class League:
    """One competition, and what each book calls it."""
    key: str                      # canonical sport_key -- the cross-book join
    title: str
    fd_sport: str | None = None   # key into fanduel.SPORT_EVENT_TYPES
    fd_names: tuple[str, ...] = ()   # regexes, matched against the competition name
    dk_sport: str | None = None   # key into draftkings_league.DISPLAY_GROUPS
    dk_names: tuple[str, ...] = ()   # regexes, matched against the eventGroupName
    fx_paths: tuple[str, ...] = ()   # Oddschecker urlPath league segments
    props: bool = False           # worth spending per-event calls on prop tabs
    tournament: bool = False      # league-per-event: ids rotate, discover them


# ---------------------------------------------------------------------------
# The catalog.
#
# Ordered by book-count then volume, because that is the order they matter in:
# three books is where an arbitrage lives without a boost, two books is where a
# boost creates one. A league with one book still earns its place -- it feeds
# the vig-free anchor's +EV path -- but it cannot arbitrage.
# ---------------------------------------------------------------------------
LEAGUES: tuple[League, ...] = (
    # ---- US majors: all three books ----
    League("baseball_mlb", "MLB", "baseball", (r"^MLB$",),
           "baseball", (r"^MLB$",), ("/us/baseball/mlb",), props=True),
    League("americanfootball_nfl", "NFL", "americanfootball", (r"^NFL$",),
           "americanfootball", (r"^NFL$",), ("/us/football/nfl",), props=True),
    League("americanfootball_ncaaf", "NCAAF", "americanfootball",
           (r"^NCAA Football", r"^College Football"),
           "americanfootball", (r"^College Football$",),
           ("/us/football/college-football",), props=True),
    League("basketball_nba", "NBA", "basketball", (r"^NBA$",),
           "basketball", (r"^NBA$",), ("/us/basketball/nba",), props=True),
    League("basketball_wnba", "WNBA", "basketball", (r"^WNBA$",),
           "basketball", (r"^WNBA$",), ("/us/basketball/wnba",), props=True),
    League("icehockey_nhl", "NHL", "icehockey", (r"^NHL$",),
           "icehockey", (r"^NHL$",), ("/us/hockey/nhl",), props=True),
    League("basketball_ncaab", "NCAAB", "basketball",
           (r"^NCAA Basketball", r"^College Basketball"),
           "basketball", (r"^College Basketball$",),
           ("/us/basketball/ncaam",), props=True),

    # ---- soccer: Fanatics carries the big European leagues ----
    #
    # Every dk_names pattern here is anchored to the FULL league name, not to a
    # country. `r"Belgium"` matched "Belgium - Challenger Pro League" before it
    # reached "Belgium - Jupiler Pro League", and the second tier's id 404s on
    # the feed -- a wasted request AND the real league lost. Country names are
    # never specific enough: DraftKings lists two Turkish, two Danish, three
    # Swedish and four Mexican competitions.
    League("soccer_epl", "English Premier League", "soccer",
           (r"^English Premier League$",), "soccer", (r"^English Premier League$",),
           ("/us/soccer/premier-league",)),
    League("soccer_uefa_champs_league", "UEFA Champions League", "soccer",
           (r"^UEFA Champions League$",), "soccer", (r"^(UEFA )?Champions League$",),
           ("/us/soccer/uefa-champions-league",)),
    League("soccer_spain_la_liga", "La Liga", "soccer", (r"^Spanish La Liga$",),
           "soccer", (r"^Spain - La Liga$",), ("/us/soccer/la-liga-primera",)),
    League("soccer_italy_serie_a", "Serie A", "soccer", (r"^Italian Serie A$",),
           "soccer", (r"^Italy - Serie A$",), ("/us/soccer/serie-a",)),
    League("soccer_germany_bundesliga", "Bundesliga", "soccer",
           (r"^German Bundesliga$",), "soccer", (r"^Germany - Bundesliga$",),
           ("/us/soccer/bundesliga",)),
    League("soccer_france_ligue_one", "Ligue 1", "soccer", (r"^French Ligue 1$",),
           "soccer", (r"^France - Ligue 1$",), ("/us/soccer/ligue-1",)),
    League("soccer_usa_mls", "MLS", "soccer", (r"^US MLS$", r"^MLS$"),
           "soccer", (r"^MLS$",), ("/us/soccer/mls",)),
    League("soccer_netherlands_eredivisie", "Eredivisie", "soccer",
           (r"^Dutch Eredivisie$",), "soccer", (r"^Netherlands - Eredivisie$",),
           ("/us/soccer/eredivisie",)),
    League("soccer_portugal_primeira_liga", "Primeira Liga", "soccer",
           (r"^Portuguese Primeira Liga$",),
           "soccer", (r"^Portugal - Primeira Liga$",),
           ("/us/soccer/primeira-liga",)),
    League("soccer_mexico_ligamx", "Liga MX", "soccer",
           (r"^Mexican Liga MX$", r"^Mexico - Liga MX$"),
           "soccer", (r"^Mexico - Liga MX$",),
           ("/us/soccer/mexican-primera-division",)),
    League("soccer_england_championship", "EFL Championship", "soccer",
           (r"^English Championship$",), "soccer", (r"^England - Championship$",),
           ("/us/soccer/english-championship",)),
    League("soccer_scotland_premiership", "Scottish Premiership", "soccer",
           (r"^Scottish Premiership$",), "soccer", (r"^Scotland - Premiership$",),
           ("/us/soccer/scottish-premiership",)),
    League("soccer_belgium_first_div", "Jupiler Pro League", "soccer",
           (r"^Belgian (First Division A|Jupiler Pro League|Pro League)$",),
           "soccer", (r"^Belgium - Jupiler Pro League$",),
           ("/us/soccer/belgian-pro-league",)),
    League("soccer_turkey_super_lig", "Super Lig", "soccer",
           (r"^Turkish Super Lig$",), "soccer", (r"^Turkey - Super Lig$",),
           ("/us/soccer/super-lig",)),
    League("soccer_greece_super_league", "Greek Super League", "soccer",
           (r"^Greek Super League$",), "soccer", (r"^Greece - Super League$",),
           ("/us/soccer/superleague",)),
    League("soccer_denmark_superliga", "Danish Superliga", "soccer",
           (r"^Danish Superliga(en)?$",), "soccer", (r"^Denmark - Superligaen$",),
           ("/us/soccer/denmark",)),
    League("soccer_austria_bundesliga", "Austrian Bundesliga", "soccer",
           (r"^Austrian Bundesliga$",), "soccer", (r"^Austria - Bundesliga$",),
           ("/us/soccer/austrian-bundesliga",)),
    League("soccer_japan_j_league", "J-League", "soccer",
           (r"^Japanese J.?League( 1)?$",), "soccer", (r"^Japan - J-League 1\s*$",),
           ("/us/soccer/japan",)),
    League("soccer_norway_eliteserien", "Eliteserien", "soccer",
           (r"^Norwegian Eliteserien$",), "soccer", (r"^Norway - Eliteserien$",),
           ("/us/soccer/norway",)),
    League("soccer_sweden_allsvenskan", "Allsvenskan", "soccer",
           (r"^Swedish Allsvenskan$",), "soccer", (r"^Sweden - Allsvenskan$",),
           ("/us/soccer/sweden",)),
    League("soccer_england_league_one", "EFL League One", "soccer",
           (r"^English League One$",), "soccer", (r"^England - League One$",),
           ("/us/soccer/english-league-1",)),
    League("soccer_england_league_two", "EFL League Two", "soccer",
           (r"^English League Two$",), "soccer", (r"^England - League Two$",),
           ("/us/soccer/english-league-2",)),
    League("soccer_uefa_europa", "UEFA Europa League", "soccer",
           (r"^UEFA Europa League$",), "soccer", (r"^Europa League$",),
           ("/us/soccer/uefa-europa-league",)),

    # ---- two books (DraftKings + FanDuel) ----
    League("americanfootball_cfl", "CFL", "americanfootball",
           (r"CFL",), "americanfootball", (r"^CFL$",), ("/us/football/cfl",)),
    # Oddschecker files both fight sports under one `boxing-mma` segment, so
    # the league is the segment after it -- `/us/boxing-mma/ufc-mma` is not
    # under `/us/mma/`, which is what the obvious guess would have been.
    League("mma_ufc", "UFC", "mma", (r"^UFC",), "mma", (r"^UFC$",),
           ("/us/boxing-mma/ufc-mma",)),
    League("boxing_boxing", "Boxing", "boxing", (r"^Boxing",),
           "boxing", (r"^Boxing$",), ("/us/boxing-mma/boxing",)),
    League("basketball_euroleague", "EuroLeague", "basketball",
           (r"^Euroleague",), "basketball", (r"^EuroLeague$",),
           ("/us/basketball/euroleague",)),
    League("aussierules_afl", "AFL", "aussierules", (r"AFL",),
           "aussierules", (r"^AFL$",), ("/us/australian-rules/afl",)),
    League("rugbyleague_nrl", "NRL", "rugbyleague", (r"NRL",),
           "rugbyleague", (r"^NRL$",), ("/us/rugby-league/nrl",)),

    # ---- league-per-tournament: the id rotates, so it is discovered ----
    League("tennis_atp", "Tennis", "tennis", (r".",), "tennis", (r".",),
           ("/us/tennis",), tournament=True),
    League("golf_pga", "Golf", "golf", (r".",), "golf", (r".",),
           ("/us/golf",), tournament=True),
)

BY_KEY: dict[str, League] = {lg.key: lg for lg in LEAGUES}


def _compiled(patterns: tuple[str, ...]) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


_FD_RULES = [(lg, _compiled(lg.fd_names)) for lg in LEAGUES if lg.fd_names]
_DK_RULES = [(lg, _compiled(lg.dk_names)) for lg in LEAGUES if lg.dk_names]


def _match(rules, sport: str, name: str, attr: str) -> League | None:
    """First league whose sport AND name pattern both match.

    The sport check is not redundant with the name check: "Championship" names
    a soccer competition, a golf tournament and a darts event, and only the
    display group separates them.
    """
    text = (name or "").strip()
    if not text:
        return None
    for lg, pats in rules:
        if getattr(lg, attr) != sport:
            continue
        if any(p.search(text) for p in pats):
            return lg
    return None


def fanduel_league(sport: str, competition_name: str) -> League | None:
    """The catalog league a FanDuel competition belongs to, or None."""
    return _match(_FD_RULES, sport, competition_name, "fd_sport")


def draftkings_league(sport: str, league_name: str) -> League | None:
    """The catalog league a DraftKings eventGroupName belongs to, or None."""
    return _match(_DK_RULES, sport, league_name, "dk_sport")


def fanatics_league(url_path: str) -> League | None:
    """The catalog league an Oddschecker urlPath belongs to, or None.

    Paths read `/us/<sport>/<league>/<fixture>`, so the league is a prefix
    match. Longest first, because `/us/golf` would otherwise claim
    `/us/golf/pga-championship` before a more specific entry could.
    """
    path = (url_path or "").strip().lower()
    if not path:
        return None
    best: League | None = None
    best_len = 0
    for lg in LEAGUES:
        for prefix in lg.fx_paths:
            if (path == prefix or path.startswith(prefix + "/")) and len(prefix) > best_len:
                best, best_len = lg, len(prefix)
    return best


def generic_key(sport: str, name: str) -> str:
    """A sport_key for a league the catalog does not name.

    Two books can still meet on one of these -- both derive it from the
    competition's own name -- but they only meet when they spell it the same
    way, which is why the curated entries exist. Prefixed with the sport so
    "Championship" in golf and in soccer cannot collide.
    """
    return f"{slug(sport)}_{slug(name)}" if name else slug(sport)


def props_sports() -> set[str]:
    """Sport keys worth spending per-event prop calls on."""
    return {lg.key for lg in LEAGUES if lg.props}
