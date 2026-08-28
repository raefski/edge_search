"""Fanatics Markets — a vig-free fair-probability anchor.

This is Fanatics' *prediction market*, not the sportsbook: CFTC-style event
contracts quoted as a probability between 0 and 1. A two-way moneyline here
sums to exactly 1.0000, where a sportsbook's sums to ~1.045. That makes it a
free stand-in for Pinnacle in the +EV anchor role, and it needs no api-key,
no cookies and no captured request.

It is NOT a bet leg in Connecticut. The 2026-08-10 ruling held sports event
contracts are wagers and CT may enforce, so this belongs in books.reference
alongside Pinnacle -- priced for fair value, never staked.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import http as requests

from .models import Board, Quote, GroupKey
from .matching import match_event
from .marketmap import canonical_market

log = logging.getLogger("arb.fanaticsmarkets")

BASE = "https://api.fanaticsmarkets.com/events"
BOOK = "fanatics_markets"

# The list endpoint returns only the top few outcomes of an outright field, so
# an 'outright' group arrives incomplete. Groups are accepted only when their
# probabilities sum to 1 within this tolerance -- for a vig-free venue that is
# the definitive test of whether the field is whole.
SUM_TOLERANCE = 0.02

SERIES_TO_SPORT = {
    "NFL": "americanfootball_nfl", "NBA": "basketball_nba",
    "MLB": "baseball_mlb", "NHL": "icehockey_nhl",
    "NCAAF": "americanfootball_ncaaf", "NCAAB": "basketball_ncaab",
    "WNBA": "basketball_wnba", "MLS": "soccer_usa_mls", "EPL": "soccer_epl",
}
GROUP_TO_MARKET = {"moneyline": "h2h", "outright": "outrights"}
OUTCOME_SIDE = {"HOME": "home", "AWAY": "away", "DRAW": "draw",
                "OVER": "over", "UNDER": "under", "YES": "yes", "NO": "no"}

# Groups whose outcomes are team-level: the two sides are distinguished by
# outcomeType, NOT by participantId. Keying those by participant splits every
# spread into two one-sided markets that can never be validated.
TEAM_GROUPS = {"moneyline", "spread", "over_and_under", "outright"}

# A single-sided ladder ("player to exceed 0.5 home runs") cannot be checked
# by summing, so it is only trusted inside this probability band -- outside it
# the market is almost certainly suspended or already resolved.
SINGLE_SIDED_BAND = (0.02, 0.98)

# Only whole-game markets are ingested. "First 5 Innings Total Runs 4.5" maps
# to the same canonical key as the full-game total, and GroupKey carries no
# period, so a period market would land in the full-game group and pair a
# 5-inning line against a 9-inning one.
FULL_GAME_PERIODS = {"Full Game", None}


def fetch_series(series: str, limit: int = 50, timeout: float = 20.0,
                 session: requests.Session | None = None) -> list[dict]:
    s = session or requests
    r = s.get(BASE, params={"series": series, "limit": limit}, timeout=timeout, headers={
        "accept": "*/*", "origin": "https://fanaticsmarkets.com",
        "referer": "https://fanaticsmarkets.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151.0.0.0"})
    r.raise_for_status()
    return (r.json() or {}).get("data") or []


def _ts(ms) -> datetime:
    return datetime.fromtimestamp(float(ms) / 1000.0, timezone.utc) if ms else datetime.now(timezone.utc)


def _submarkets(group: str, outcomes: list[dict]) -> dict[tuple, list[dict]]:
    """Split one flattened group into the individual markets it contains.

    `over_and_under` arrives as 22 outcomes that are really 11 two-sided
    lines; `spread` likewise. Team markets must NOT be keyed by participantId
    -- the two sides carry different participants, and keying on it splits
    every spread into two one-sided markets that can never be validated.
    """
    out: dict[tuple, list[dict]] = {}
    team = group in TEAM_GROUPS
    for o in outcomes:
        player = None if team else o.get("title")
        # marketGroupTypeName is the specific market ("Total Bases");
        # marketGroupName is only a UI category ("Bases", "Popular").
        label = o.get("marketGroupTypeName") or o.get("marketGroupName")
        line = o.get("line")
        # A spread's line is signed per team: the complement of Away -1.5 is
        # Home +1.5, NOT Home -1.5. Grouping on the raw value pairs the two
        # favourites together and they sum to 0.675 instead of 1. Fold both
        # onto the home perspective so real complements meet.
        if line is not None and _is_spread(group, label):
            line = _to_home_line(float(line), o.get("outcomeType"))
        key = (player, label, o.get("periodType"), line)
        out.setdefault(key, []).append(o)
    return out


def _explicit_lines(markets: dict) -> set[tuple]:
    """(player, canonical market, line) for every outcome that states a line."""
    out: set[tuple] = set()
    for group, outcomes in markets.items():
        if group in TEAM_GROUPS:
            continue
        for o in outcomes or []:
            if o.get("line") is None:
                continue
            label = o.get("marketGroupTypeName") or o.get("marketGroupName")
            player = o.get("title")
            mkey = canonical_market(label or "", player=player)
            if mkey:
                out.add((player, mkey, round(float(o["line"]), 2)))
    return out


def _is_spread(group: str, label: str | None) -> bool:
    return group == "spread" or "spread" in (label or "").lower()


def _to_home_line(line: float, outcome_type: str | None) -> float:
    return round(line if outcome_type == "HOME" else -line, 2)


def _home_point(market: str, line: float, side: str) -> float:
    """Spreads are stored from the home team's perspective, as elsewhere."""
    if market.startswith("spread"):
        return round(float(line) if side == "home" else -float(line), 2)
    return round(float(line), 2)


def ingest_events(board: Board, events: list[dict], sport_key: str | None = None,
                  min_trades: int = 0, strict_match: bool = True,
                  include_live: bool = False) -> dict:
    stats = {"events": 0, "matched": 0, "unmatched": 0, "quotes": 0,
             "truncated_skipped": 0, "illiquid_skipped": 0, "live_skipped": 0,
             "degenerate_skipped": 0, "unmapped_skipped": 0,
             "period_skipped": 0, "yes_superseded": 0}
    now = datetime.now(timezone.utc)

    for ev in events:
        stats["events"] += 1
        if (ev.get("isLive") and not include_live) or ev.get("isOver"):
            stats["live_skipped"] += 1
            continue

        title = ev.get("title") or ""
        away, home = None, None
        if " @ " in title:
            away, home = [p.strip() for p in title.split(" @ ", 1)]
        commence = _ts(ev.get("startTime"))
        sport = sport_key or SERIES_TO_SPORT.get(ev.get("series") or "")

        target = None
        if home and away:
            target = match_event(board, home, away, commence, sport)
        if target is None:
            stats["unmatched"] += 1
            if strict_match:
                continue
        else:
            stats["matched"] += 1

        # A YES contract carries no line and cannot be cross-checked, so where
        # the same bet also exists as an explicit milestone ("Home Runs Over
        # 0.5") the milestone wins. One player's "To Hit A Home Run" came back
        # at 0.49 while his Home Runs Over 0.5 said 0 -- the same bet
        # contradicting itself, and only the lined version is verifiable.
        explicit = _explicit_lines(ev.get("markets") or {})

        for group, outcomes in (ev.get("markets") or {}).items():
            if not outcomes:
                continue
            for key, bucket in _submarkets(group, outcomes).items():
                player, label, period, line = key
                # Outrights carry their own period ("Playoffs") and cannot
                # collide with a full-game market, so they are exempt.
                if group != "outright" and period not in FULL_GAME_PERIODS:
                    stats["period_skipped"] += 1
                    continue
                mkey = (GROUP_TO_MARKET.get(group)
                        or canonical_market(label or "", player=player))
                if mkey is None:
                    stats["unmapped_skipped"] += 1
                    continue

                probs = [o.get("probability") for o in bucket]
                if any(p is None for p in probs):
                    continue

                if len(bucket) >= 2:
                    # Vig-free: a complete field sums to 1. Anything else is
                    # truncated or suspended -- the list endpoint returns only
                    # the top outright outcomes, and a 5-of-32 field summing to
                    # 0.495 would otherwise read as a 100% arbitrage.
                    if abs(sum(probs) - 1.0) > SUM_TOLERANCE:
                        stats["truncated_skipped"] += 1
                        continue
                else:
                    lo, hi = SINGLE_SIDED_BAND
                    if not (lo < probs[0] < hi):
                        stats["degenerate_skipped"] += 1
                        continue

                for o in bucket:
                    p = float(o["probability"])
                    if p <= 0.0 or p >= 1.0:
                        continue
                    if min_trades and (o.get("tradeCount") or 0) < min_trades:
                        stats["illiquid_skipped"] += 1
                        continue
                    side = OUTCOME_SIDE.get(o.get("outcomeType") or "")
                    if side is None:
                        continue
                    # `line` is already home-normalised for spreads by _submarkets
                    point = line
                    # "To Hit A Home Run: Yes" is the same bet as "Home Runs
                    # Over 0.5". Left as a `yes` side it would sit in the same
                    # group as a sportsbook's `over` and read as a third
                    # outcome of a two-way market.
                    if side in ("yes", "no") and point is None:
                        side = "over" if side == "yes" else "under"
                        point = 0.5
                        if (player, mkey, 0.5) in explicit:
                            stats["yes_superseded"] += 1
                            continue
                    board.group(GroupKey(target.event_id, mkey, player, point),
                                target).add(Quote(
                        book=BOOK, side=side, decimal=1.0 / p,
                        point=line, last_update=now,
                    ))
                    stats["quotes"] += 1
    return stats


class FanaticsMarkets:
    """Reference-only anchor. Costs nothing and needs no credentials."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.session = requests.Session()

    def fetch_all(self, board: Board, sports: list[str] | None = None) -> dict:
        fm = self.cfg.fanatics_markets
        totals = {"series": 0, "matched": 0, "unmatched": 0, "quotes": 0,
                  "truncated_skipped": 0, "illiquid_skipped": 0, "live_skipped": 0,
                  "degenerate_skipped": 0, "unmapped_skipped": 0, "period_skipped": 0,
                  "yes_superseded": 0}
        for series in fm.series:
            sport = SERIES_TO_SPORT.get(series)
            if sports and sport and sport not in sports:
                continue
            try:
                events = fetch_series(series, limit=fm.limit, session=self.session)
            except requests.RequestException as exc:
                log.warning("fanatics markets %s: %s", series, exc)
                continue
            stats = ingest_events(board, events, sport_key=sport,
                                  min_trades=fm.min_trades,
                                  strict_match=self.cfg.scrape.strict_event_match,
                                  include_live=fm.include_live)
            totals["series"] += 1
            for k in totals:
                if k != "series":
                    totals[k] += stats.get(k, 0)
            log.info("fanatics markets %s: %d matched, %d quotes%s", series,
                     stats["matched"], stats["quotes"],
                     f", {stats['truncated_skipped']} incomplete field(s) skipped"
                     if stats["truncated_skipped"] else "")
        return totals
