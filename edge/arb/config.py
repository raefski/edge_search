"""Arbitrage scan settings.

Plain dataclasses rather than YAML so edge/ keeps its no-third-party-dependency
rule (see requirements.txt). Everything here is free: no Odds API key, no
credits. Books are read straight from their own public endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Books:
    # where a stake can actually be placed, in Connecticut
    legal: list[str] = field(default_factory=lambda: ["draftkings", "fanduel", "fanatics"])
    # priced for fair value only, never staked
    reference: list[str] = field(default_factory=lambda: ["fanatics_markets"])
    commission: dict[str, float] = field(default_factory=dict)
    max_stake: dict[str, float] = field(default_factory=lambda: {
        "draftkings": 1000.0, "fanduel": 1000.0, "fanatics": 500.0})

    @property
    def bettable(self) -> list[str]:
        return list(self.legal)

    @property
    def feed(self) -> list[str]:
        return list(self.legal) + list(self.reference)


@dataclass
class Detect:
    min_profit_pct: float = 0.30
    max_profit_pct: float = 15.0
    # How long since WE fetched a quote -- not how long since the book moved.
    # Neither FanDuel nor DraftKings exposes a per-price update timestamp
    # (FanDuel's `marketTime` is the start time), so book-side staleness is
    # not observable for scraped sources. This only bites in a long-running
    # watch loop, where a quote can sit on the board unrefreshed; in a
    # one-shot scan every quote is seconds old and this never fires.
    max_quote_age_seconds: float = 600.0
    skip_live: bool = True
    min_minutes_to_start: float = 3.0
    # Ten days, not four. Football is a WEEKLY sport: scanned on a Sunday with
    # a 96-hour ceiling, next Saturday's 103 NCAAF games are all outside the
    # window, and NCAAF is one of the few leagues all three books price. The
    # board carried them and the scan then skipped every one.
    #
    # The usual objection to a far-out line -- it is stale and will move -- does
    # not apply to an arbitrage, because both legs are placed now and the lock
    # is taken at today's prices. What DOES apply is limits: a line ten days
    # out is posted small, so books.max_stake is the term that bounds these,
    # not the window.
    max_hours_to_start: float = 240.0
    min_books: int = 2
    max_legs: int = 3
    middles_enabled: bool = True
    middle_min_width: float = 0.5    # a whole number must also fall inside the window
    middle_max_cost_pct: float = 5.0
    # Sanity bounds. A real middle pairs two readings of the SAME market, so
    # both legs are priced like main lines and the gap between them is small.
    # Without these, one mispriced leg (a +1500 quote that landed on a spread
    # group) paired against a normal line and reported "+255%, 34 winning
    # outcomes, breakeven 100%" -- arithmetically consistent, obviously absurd.
    middle_max_width: float = 14.0
    middle_max_leg_decimal: float = 6.0     # ~+500; longer is not a main line
    middle_max_results: int = 60
    ev_enabled: bool = True
    ev_method: str = "power"
    ev_min_pct: float = 2.0
    ev_max_pct: float = 25.0
    ev_max_results: int = 80
    ev_allow_consensus: bool = True
    ev_consensus_min_books: int = 2
    kelly_fraction: float = 0.25


@dataclass
class Bankroll:
    total: float = 1000.0
    round_to: float = 1.0


@dataclass
class Scrape:
    strict_event_match: bool = True


@dataclass
class ArbConfig:
    books: Books = field(default_factory=Books)
    detect: Detect = field(default_factory=Detect)
    bankroll: Bankroll = field(default_factory=Bankroll)
    scrape: Scrape = field(default_factory=Scrape)
    state: str = "ct"
    # golf_pga is scanned but will usually show nothing: skip_live applies to
    # it like everything else, and a tournament is "in progress" from the
    # first tee time until the last putt drops on Sunday. It surfaces in the
    # window before a round starts. run.scan() counts what it skipped for
    # this reason so an empty golf board is explained rather than mysterious.
    # EMPTY means every league in catalog.LEAGUES, which is the point of the
    # catalog: the books between them offer ~25 sports and naming five here was
    # the reason only five were scanned. A non-empty list restricts the scan,
    # which is what a quick pass or a single-sport debug wants.
    sports: list[str] = field(default_factory=list)
    # A league nobody curated still gets scanned, under a key derived from its
    # own name. Two books meet there only when they spell the competition
    # identically, so it is a bonus rather than the mechanism -- but it costs
    # nothing, and FanDuel alone lists 108 soccer competitions.
    include_uncatalogued: bool = True
    fanduel_max_events: int = 40
    # See arb/config.py FanDuelScrapeConfig.tabs: "popular" alone misses
    # pitcher Outs Recorded entirely and the deeper batter thresholds.
    fanduel_tabs: list[str] = field(default_factory=lambda: [
        "popular", "pitcher-props", "batter-props"])
    # Profit-boost tokens offered to YOUR account. Not discoverable: both books
    # put promotions behind a login and issue them per account. Set from the
    # Streamlit sidebar, scripts/arb_scan.py --boost, or here.
    boosts: list = field(default_factory=list)          # list[engine.Boost]
    # 37 tennis leagues are listed; most are outright containers months out.
    # Each costs one call, so the list is capped rather than pulled whole.
    tennis_max_leagues: int = 14
    draftkings_props: bool = True
    draftkings_max_prop_subcategories: int = 40   # covers all 31 MLB tabs; see prop_subcategories
    # Soccer keeps its spreads and totals in subcategories rather than in the
    # league feed, so without these a soccer league arrives as a moneyline and
    # nothing else. 4 is enough for sides, totals and one alternate ladder.
    draftkings_main_line_subcategories: int = 4
    # Props are per event and the most expensive thing in a scan. Only the
    # leagues catalog.props_sports() marks get them, and only this many events
    # each -- soonest first, which is where the lines are firmest.
    prop_events_per_league: int = 6
    request_gap_seconds: float = 0.35

    # Oddschecker league ids for Fanatics. bettypeIds are per sport: 1 and 525
    # are universal, totals differ (526 points, 1055802107 runs).
    fanatics_leagues: list[dict] = field(default_factory=lambda: [
        {"name": "NCAAF", "sport_key": "americanfootball_ncaaf",
         "event_id": 5597, "bettype_ids": [1, 525, 526]},
        {"name": "MLB", "sport_key": "baseball_mlb",
         "event_id": 7445, "bettype_ids": [1, 525, 1055802107]},
        # captured 2026-08-30; WNBA shares NCAAF's totals bettypeId (526)
        # rather than needing a league-specific one like MLB's
        {"name": "WNBA", "sport_key": "basketball_wnba",
         "event_id": 12238, "bettype_ids": [1, 525, 526]},
    ])
    # Vig-free anchor series. WNBA verified live 2026-08-28: 11 pregame events.
    # This is a REFERENCE source, never a bet leg -- it makes WNBA +EV possible
    # without a third bettable book, which Oddschecker still cannot supply.
    # DraftKings serves golf as a league PER TOURNAMENT -- 71813 is the Tour
    # Championship, not "PGA Tour" -- and there is no listing endpoint, so the
    # id changes every week and has to be captured. Same shape as
    # fanatics_leagues for the same reason. Read it off a DevTools request on
    # the tournament page: .../sportscontent/dkusct/v1/leagues/{id}
    # There is no listing endpoint. Confirmed against sportscontent (five path
    # shapes on sportId 12), the v5 eventgroup API (Akamai blocked) and the
    # pagedata namespace (eight shapes) -- the only thing pagedata offers is
    # id -> slug, not slug -> id. So these are captured, and run.scan() reports
    # any that return no events so an expired one is visible rather than just
    # quietly contributing nothing.
    #   DevTools on the tournament page -> .../dkusct/v1/leagues/{id}
    draftkings_golf_leagues: list[dict] = field(default_factory=lambda: [
        {"name": "Tour Championship", "league_id": 71813},   # captured 2026-08-30
        {"name": "Presidents Cup", "league_id": 25461},      # captured 2026-08-30
    ])
    # cat 531 is Matchups, cat 698 Hole Props. Only the TWO-way subcategories:
    # the "(3 Way)" variants carry a Tie selection and are a different market.
    draftkings_golf_subcategories: list[tuple] = field(default_factory=lambda: [
        (531, 19877), (531, 20065), (698, 17673),
    ])
    # Discovered ids override the three hardcoded above -- see
    # oddschecker_discover.py. The file is a cache, not a requirement: with it
    # missing the scan falls back to fanatics_leagues and loses breadth, never
    # correctness.
    fanatics_cache_path: str = "data/oddschecker_leagues.json"
    fanatics_markets_series: list[str] = field(default_factory=lambda: [
        "NFL", "NCAAF", "MLB", "NBA", "WNBA", "NHL"])
