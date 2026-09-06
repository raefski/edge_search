"""Market data layer: one normalised source of sportsbook prices.

Replaces The Odds API as the price source for every model in this repo. The
scrapers underneath it are the arbitrage ones (edge/arb), which already speak
the Odds API's own market vocabulary -- `pitcher_strikeouts`,
`player_pass_yds`, `player_points` -- because edge/arb/marketmap.py was written
to compare a scrape against that feed. So this is a change of shape, not of
meaning, which is what makes it safe to do at all.

    from edge.odds import OddsStore, ScrapedOddsClient, collect

    with OddsStore() as store:
        collect("dfs_mlb", store)                       # scrape + persist
        client = ScrapedOddsClient(store, "dfs_mlb", max_age_seconds=600)
        pool = build_slate(client, date)                # unchanged consumer

Layers, and what each is allowed to do:

    edge/arb/*        collectors. Talk to books. Own the market mapping.
    edge/odds/store   persistence. Stores facts, derives nothing.
    edge/odds/source  presentation. Reshapes facts for consumers.
    edge/{dfs,pickem} models. Derive everything, own no I/O.
"""
from .collect import CollectionError, collect
from .ingest import board_to_rows, group_key_str
from .profiles import PROFILES, Profile, for_sport, get as get_profile
from .publish import SnapshotOddsClient, export as export_snapshot
from .source import ScrapedOddsClient, StaleOdds, client_for
from .store import EventRow, OddsStore, QuoteRow

__all__ = [
    "OddsStore", "QuoteRow", "EventRow",
    "ScrapedOddsClient", "StaleOdds", "client_for",
    "SnapshotOddsClient", "export_snapshot",
    "collect", "CollectionError",
    "Profile", "PROFILES", "get_profile", "for_sport",
    "board_to_rows", "group_key_str",
]
