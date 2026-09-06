"""Collection profiles: which leagues, how deep, and how fresh is fresh enough.

WHY PROFILES EXIST
One scan cannot serve three consumers well. The arbitrage scan is wide and
shallow-ish -- ~30 leagues, ~200s, capped at `prop_events_per_league=6`
because props are the most expensive thing in it and arbitrage only needs
*some* events to pair. DFS is the opposite: one league, but EVERY game on the
slate, because a pitcher whose props were not fetched is a pitcher with no
projection and an empty roster slot. Pick'em is narrower still and needs no
props at all.

Running the wide profile and hoping it covered the slate is how DFS would
silently lose half its pool. So the depth is named per consumer, and the cost
of each profile is stated rather than discovered.

MEASURED COSTS are from the arb scan's own instrumentation (HANDOFF.md
section 2: full scan ~200s / ~20,000 quotes across ~30 leagues). The per-profile
numbers below are estimates from that baseline scaled by league count and prop
depth; `scripts/odds_collect.py` prints the real figure after every run, and
these should be corrected from that rather than trusted.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from edge.arb.config import ArbConfig


@dataclass(frozen=True)
class Profile:
    """A named collection job."""
    name: str
    sports: tuple[str, ...]
    #: canonical markets this profile promises to carry. Documentation for the
    #: consumer, and what `verify` checks the result against -- a profile that
    #: silently stops returning `pitcher_outs` should fail loudly, because
    #: `project_pitcher` requires it and returns proj=None without it.
    required_markets: tuple[str, ...] = ()
    props: bool = True
    #: events per league to pull props for. The arb default of 6 is a cost
    #: control; a DFS slate needs all of them.
    prop_events: int = 6
    #: alternate ladders are where middles come from -- pure arbitrage value,
    #: no modelling value. Off for the model profiles, which halves their cost.
    alt_lines: bool = True
    #: how old a scan may be before a consumer should refuse it.
    max_age_seconds: float = 3600.0
    #: rough wall-clock, for scheduling. Corrected from real runs.
    est_seconds: int = 60
    #: markets written into the published snapshot the cloud app reads. Trims
    #: the committed file to what the consumer actually indexes -- pick'em
    #: needs spreads and totals and nothing else, which is the difference
    #: between a 100KB commit and a 5MB one, on every collection. Empty
    #: publishes everything in the scan.
    publish_markets: tuple[str, ...] = ()
    notes: str = ""

    def config(self, base: ArbConfig | None = None) -> ArbConfig:
        """Materialise this profile as an ArbConfig the existing scanner takes.

        Deliberately a projection onto the CONFIG the arb scanner already
        understands rather than a second scanner: there is one scraping code
        path in this system and adding a parallel one is how the two copies of
        the shared core drifted the first time (HANDOFF.md section 1).
        """
        cfg = base or ArbConfig()
        cfg.sports = list(self.sports)
        # An uncatalogued league cannot be one of ours by construction (the
        # profiles name curated keys), and scanning them is the bulk of the
        # wide scan's cost.
        cfg.include_uncatalogued = not self.sports
        cfg.draftkings_props = self.props
        cfg.prop_events_per_league = self.prop_events
        if not self.alt_lines:
            cfg.fanduel_alt_line_events = 0
        return cfg


#: A DFS main slate is 7-12 MLB games, 13-16 NFL, 5-12 NBA. 20 covers every
#: one of those with headroom, and costs nothing on a day the slate is small
#: because the cap is a ceiling, not a target.
SLATE_EVENTS = 20

PROFILES: dict[str, Profile] = {
    # --- arbitrage: unchanged behaviour, named so it is schedulable ---------
    "arb": Profile(
        name="arb",
        sports=(),                      # empty == every catalogued league
        props=True, prop_events=6, alt_lines=True,
        max_age_seconds=600,
        est_seconds=200,
        notes="The existing wide scan. Alternate ladders on, because middles "
              "come from them and only arbitrage uses them.",
    ),

    # --- DFS: narrow and deep ----------------------------------------------
    "dfs_mlb": Profile(
        name="dfs_mlb",
        sports=("baseball_mlb",),
        required_markets=("pitcher_outs", "pitcher_strikeouts"),
        props=True, prop_events=SLATE_EVENTS, alt_lines=False,
        # Pre-lock, salaries are frozen but props move. Ten minutes is the
        # same window edge/client.py used for live odds (live_ttl=600).
        max_age_seconds=600,
        est_seconds=45,
        publish_markets=(
            "pitcher_outs", "pitcher_strikeouts", "pitcher_earned_runs",
            "pitcher_hits_allowed", "pitcher_walks", "pitcher_record_a_win",
            "batter_hits", "batter_total_bases", "batter_rbis",
            "batter_runs_scored", "batter_walks", "batter_stolen_bases",
            "batter_home_runs", "spreads", "totals", "h2h"),
        notes="pitcher_outs + pitcher_strikeouts are REQUIRED: project_pitcher "
              "returns proj=None without both, so a pitcher missing them is "
              "absent from the pool rather than badly projected.",
    ),
    "dfs_nfl": Profile(
        name="dfs_nfl",
        sports=("americanfootball_nfl",),
        required_markets=("player_pass_yds", "player_rush_yds",
                          "player_reception_yds", "player_receptions"),
        props=True, prop_events=SLATE_EVENTS, alt_lines=False,
        max_age_seconds=1800,
        est_seconds=40,
        publish_markets=(
            "player_pass_yds", "player_pass_tds", "player_pass_interceptions",
            "player_pass_attempts", "player_pass_completions",
            "player_rush_yds", "player_rush_attempts",
            "player_reception_yds", "player_receptions",
            "spreads", "totals", "h2h"),
        notes="DST has no prop market at all -- see DFS_MULTISPORT_PLAN.md. "
              "That component has to come from team totals and spreads, which "
              "this profile carries as game markets.",
    ),
    "dfs_nba": Profile(
        name="dfs_nba",
        sports=("basketball_nba",),
        required_markets=("player_points", "player_rebounds", "player_assists"),
        props=True, prop_events=SLATE_EVENTS, alt_lines=False,
        max_age_seconds=900,
        est_seconds=40,
        notes="NBA is two books (DK + FanDuel) until Oddschecker lists it in "
              "season -- see COVERAGE.md. Two-book consensus is thinner; the "
              "parity harness should be re-run once Fanatics resolves.",
    ),

    # --- pick'em: narrowest ------------------------------------------------
    "pickem_nfl": Profile(
        name="pickem_nfl",
        sports=("americanfootball_nfl",),
        required_markets=("spreads", "totals"),
        # No props at all. The model is a spread model; pulling player props
        # for it would be most of the cost for none of the signal.
        props=False, prop_events=0, alt_lines=False,
        # The pool line is frozen all week and the model reads the MOVE, so a
        # capture from this morning is fine. Twice a week is the cadence
        # PICKEM_WEEKLY.md already uses.
        max_age_seconds=86400,
        est_seconds=15,
        publish_markets=("spreads", "totals"),
        notes="Three books (DK, FD, Fanatics NFL id 11730) against The Odds "
              "API's ~10. See parity.py before trusting the swap.",
    ),
}


def get(name: str) -> Profile:
    try:
        return PROFILES[name]
    except KeyError:
        raise KeyError(f"unknown profile {name!r}; have {sorted(PROFILES)}") from None


def for_sport(sport_key: str, consumer: str) -> Profile:
    """Look a profile up the way a consumer thinks about it: 'I am DFS and I
    need MLB'. Keeps sport-key strings out of the call sites."""
    name = f"{consumer}_{sport_key.split('_')[-1]}"
    return get(name)
