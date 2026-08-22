"""Live NFL spreads + totals for pick'em, as a multi-book consensus.

Source is The Odds API via edge/client.py -- the same paid, ToS-sanctioned
path every other sport in this repo uses. (An earlier version scraped ESPN's
public scoreboard for free; that got 403'd by Akamai in production, and
DraftKings' own endpoint hits the identical wall. See PICKEM_STATUS.md.)

WHY A CONSENSUS RATHER THAN ONE BOOK
The whole model is "CBS's frozen number vs. the market". A single book's
line is a noisy reading of "the market" -- books shade for their own
position, and a half-point of book-specific noise is large next to the
0.5-point move that separates a signal from a coin flip. Averaging across
books cancels most of that.

BOOK WEIGHTING, and an honest warning about it
`BOOK_WEIGHTS` upweights books that move first and take the sharpest action.
This is a *prior*, not a validated result: the historical backtest file
(data/pickem_odds_history.csv) is single-book, so there is no way to
backtest whether weighted consensus beats a plain mean. It is reasonable and
conventional, and it is untested here -- do not describe it as validated.

Two books people ask about are NOT available:
  * Circa -- essentially never distributed through The Odds API.
  * Pinnacle -- the classic sharp reference, but it sits in the `eu` region,
    not `us`. Adding "eu" doubles the per-call cost (markets x regions).
    `REGIONS_WITH_PINNACLE` is provided if that trade is ever worth it.
Verified against this repo's own cached NFL response, which carried:
fanduel, draftkings, betmgm, williamhill_us, betrivers, bovada, lowvig,
betonlineag, betus, mybookieag.

COST (edge/client.py's model: markets x regions, one call covers every game)
  * spreads only, us .............. 1 credit
  * spreads + totals, us .......... 2 credits   <- default here
  * spreads + totals, us + eu ..... 4 credits
Totals are pulled because the no-movement tiebreak in edge/pickem.py needs
them. At 2 captures a week that is ~72 credits for a full 18-week season,
comfortably inside the 500/month free tier shared with the MLB work.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from edge.client import OddsAPIClient
from edge.nfl import TEAM_NAME_TO_ABBR  # shared team-name->abbr map, not re-derived

SPORT = "americanfootball_nfl"
REGIONS = "us"
REGIONS_WITH_PINNACLE = "us,eu"   # doubles cost; see module docstring
MARKETS = ["spreads", "totals"]

# Relative weight in the consensus. Reuses the sharp/soft split already
# established in scripts/wnba_scout.py (which deliberately excludes
# bovada/betonlineag/mybookieag from its reference set as soft recreational
# books) rather than inventing a second, conflicting convention.
BOOK_WEIGHTS: dict[str, float] = {
    "pinnacle": 3.0,        # only present if the eu region is requested
    "circasports": 3.0,     # only if it ever appears; see docstring
    "lowvig": 2.0,          # reduced-juice, follows sharp moves quickly
    "betonlineag": 1.5,     # early opener, sharp-ish despite retail feel
    "draftkings": 1.5,
    "fanduel": 1.5,
    "betmgm": 1.0,
    "williamhill_us": 1.0,
    "betrivers": 1.0,
    "fanatics": 1.0,
    "betus": 0.5,           # recreational
    "bovada": 0.5,
    "mybookieag": 0.5,
}
DEFAULT_WEIGHT = 1.0


@dataclass
class LiveGame:
    away: str
    home: str
    away_abbr: str
    home_abbr: str
    kickoff: str                       # ISO 8601 UTC, as the API reports it
    live_line: float | None            # home spread, sharp-weighted consensus
    live_line_mean: float | None = None    # unweighted mean, for comparison
    live_line_median: float | None = None  # robust to one book being silly
    total: float | None = None         # game total, same weighting
    n_books: int = 0
    book_lines: dict[str, float] = field(default_factory=dict)   # book -> home spread
    book_totals: dict[str, float] = field(default_factory=dict)

    @property
    def book_spread(self) -> float | None:
        """Max-minus-min home spread across books -- how much the books
        disagree. A wide spread here means the consensus is shaky and the
        edge computed from it deserves less trust."""
        if len(self.book_lines) < 2:
            return None
        return max(self.book_lines.values()) - min(self.book_lines.values())


def weighted_consensus(book_to_value: dict[str, float]) -> float | None:
    """Weighted mean of per-book values using BOOK_WEIGHTS."""
    if not book_to_value:
        return None
    num = sum(BOOK_WEIGHTS.get(b, DEFAULT_WEIGHT) * v for b, v in book_to_value.items())
    den = sum(BOOK_WEIGHTS.get(b, DEFAULT_WEIGHT) for b in book_to_value)
    return num / den if den else None


def _book_points(event: dict, market_key: str, outcome_name: str | None) -> dict[str, float]:
    """{book: point} for one market. `outcome_name=None` takes the first
    outcome carrying a point, which is what totals need (Over/Under share
    the same number)."""
    out: dict[str, float] = {}
    for bm in event.get("bookmakers", []):
        for mk in bm.get("markets", []):
            if mk.get("key") != market_key:
                continue
            for o in mk.get("outcomes", []):
                if o.get("point") is None:
                    continue
                if outcome_name is not None and o.get("name") != outcome_name:
                    continue
                out[bm["key"]] = float(o["point"])
                break
    return out


def _parse_events(events: list[dict]) -> list[LiveGame]:
    """Pure parsing, split out from fetch_week so it is testable without a
    live client or key."""
    games = []
    for ev in events:
        home, away = ev.get("home_team", ""), ev.get("away_team", "")
        lines = _book_points(ev, "spreads", home)
        totals = _book_points(ev, "totals", None)
        vals = list(lines.values())
        games.append(LiveGame(
            away=away, home=home,
            away_abbr=TEAM_NAME_TO_ABBR.get(away, away[:3].upper()),
            home_abbr=TEAM_NAME_TO_ABBR.get(home, home[:3].upper()),
            kickoff=ev.get("commence_time", ""),
            live_line=weighted_consensus(lines),
            live_line_mean=(sum(vals) / len(vals)) if vals else None,
            live_line_median=statistics.median(vals) if vals else None,
            total=weighted_consensus(totals),
            n_books=len(lines),
            book_lines=lines,
            book_totals=totals,
        ))
    return games


def fetch_week(client: OddsAPIClient, sport: str = SPORT,
               regions: str = REGIONS, markets: list[str] | None = None) -> list[LiveGame]:
    """Raises NoApiKey / DryRunBlocked / CreditFloorError exactly as
    edge.client does -- callers degrade gracefully rather than crashing,
    the same way app.py already handles the MLB path."""
    return _parse_events(client.get_featured_odds(sport, markets or MARKETS, regions))
