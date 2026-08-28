"""Free NFL spreads + totals for pick'em — no Odds API credits.

Reads DraftKings and FanDuel from their own public endpoints (the same ones
edge/arb uses) and re-shapes the result into the Odds API's event dicts, so
edge.pickem_live._parse_events, the book weighting and everything downstream
stay untouched.

HOW THIS DIFFERS FROM THE PAID PATH, honestly
edge/pickem_live.py builds its consensus from ~10 books for 2 credits a call.
This gets 2 (DraftKings, FanDuel), because those are the endpoints that
answer without a key. The model's own reasoning is that averaging cancels
book-specific noise, so a 2-book mean is a weaker reading of "the market"
than a 10-book one -- most of the gain from averaging arrives by about the
third or fourth book.

Both are provided rather than one replacing the other: use `compare()` to see
how far apart they actually are on a real slate before deciding. If the gap
is small relative to the 0.5-point move the model cares about, the free path
is enough.

Fanatics can be added as a third book once its Oddschecker league id is
known; NFL is not listed there until the season opens.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from edge.arb.config import ArbConfig
from edge.arb.draftkings_league import DraftKingsLeague
from edge.arb.fanduel import FanDuelScrape
from edge.arb.models import Board
from edge.pickem_live import LiveGame, _parse_events

log = logging.getLogger("edge.pickem_free")

SPORT_KEY = "americanfootball_nfl"


def _main_line(points: dict[float, dict[str, float]]) -> float | None:
    """Pick a book's *main* line out of an alternate ladder.

    The main line is the one priced closest to even on both sides; alternates
    are deliberately lopsided. Without this, a -8.5 alt would be as likely to
    be chosen as the -3 the market is actually on.
    """
    best, best_gap = None, None
    for point, sides in points.items():
        if len(sides) < 2:
            continue
        gap = abs(max(sides.values()) - min(sides.values()))
        if best_gap is None or gap < best_gap:
            best, best_gap = point, gap
    if best is None and points:
        best = next(iter(points))
    return best


def board_to_events(board: Board) -> list[dict]:
    """Board -> Odds API shaped event dicts, main lines only."""
    per_event: dict[str, dict] = {}
    for group in board.groups.values():
        if group.key.market not in ("spreads", "totals", "h2h") or group.key.subject:
            continue
        ev = per_event.setdefault(group.event.event_id, {
            "home_team": group.event.home_team, "away_team": group.event.away_team,
            "commence_time": group.event.commence_time.isoformat().replace("+00:00", "Z"),
            "_books": {},
        })
        for side, per_book in group.quotes.items():
            for book, q in per_book.items():
                slot = ev["_books"].setdefault(book, {"spreads": {}, "totals": {}})
                if group.key.market == "spreads" and group.key.point is not None:
                    slot["spreads"].setdefault(group.key.point, {})[side] = q.decimal
                elif group.key.market == "totals" and group.key.point is not None:
                    slot["totals"].setdefault(group.key.point, {})[side] = q.decimal

    out = []
    for ev in per_event.values():
        bookmakers = []
        for book, markets in ev.pop("_books").items():
            entry = {"key": book, "markets": []}
            spread_pt = _main_line(markets["spreads"])
            if spread_pt is not None:
                entry["markets"].append({"key": "spreads", "outcomes": [
                    {"name": ev["home_team"], "point": float(spread_pt)},
                    {"name": ev["away_team"], "point": -float(spread_pt)}]})
            total_pt = _main_line(markets["totals"])
            if total_pt is not None:
                entry["markets"].append({"key": "totals", "outcomes": [
                    {"name": "Over", "point": float(total_pt)},
                    {"name": "Under", "point": float(total_pt)}]})
            if entry["markets"]:
                bookmakers.append(entry)
        if bookmakers:
            ev["bookmakers"] = bookmakers
            out.append(ev)
    return out


def fetch_week_free(sport_key: str = SPORT_KEY, max_events: int = 0) -> list[LiveGame]:
    """Free replacement for edge.pickem_live.fetch_week. Costs 0 credits."""
    cfg = ArbConfig()
    board = Board()

    fd = FanDuelScrape(state=cfg.state)
    try:
        league = fd.league_page(sport_key)
        fd.ingest_event(board, league, sport_key, strict_match=False)
    except Exception as exc:
        log.warning("fanduel nfl: %s", exc)

    # FanDuel above created the events; DraftKings must MATCH onto them rather
    # than create its own, or every game ends up with one book and the
    # consensus is a single reading dressed up as an average.
    dk = DraftKingsLeague(state=cfg.state)
    try:
        dk.ingest(board, dk.fetch(sport_key), sport_key, strict_match=True)
    except Exception as exc:
        log.warning("draftkings nfl: %s", exc)

    games = _parse_events(board_to_events(board))
    games.sort(key=lambda g: g.kickoff or "")
    return games[:max_events] if max_events else games


def compare(paid: list[LiveGame], free: list[LiveGame]) -> list[dict]:
    """Line-by-line difference between the paid and free consensus, so the
    trade can be judged on a real slate rather than argued about."""
    by_key = {(g.home_abbr, g.away_abbr): g for g in free}
    rows = []
    for p in paid:
        f = by_key.get((p.home_abbr, p.away_abbr))
        if not f or p.live_line is None or f.live_line is None:
            continue
        rows.append({
            "game": f"{p.away_abbr} @ {p.home_abbr}",
            "paid_line": round(p.live_line, 2), "free_line": round(f.live_line, 2),
            "line_diff": round(f.live_line - p.live_line, 2),
            "paid_books": p.n_books, "free_books": f.n_books,
            "paid_total": None if p.total is None else round(p.total, 2),
            "free_total": None if f.total is None else round(f.total, 2),
        })
    return rows
