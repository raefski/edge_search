"""Run a full free scan and return findings, or a JSON-safe snapshot.

Order matters: FanDuel goes first because its own event list is the spine the
other sources attach to. A scraped event that cannot be matched to one already
on the board is dropped rather than guessed at.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from . import books as books_parse
from . import engine
from .config import ArbConfig
from .draftkings_league import DraftKingsLeague
from .fanaticsmarkets import FanaticsMarkets
from .fanduel import FanDuelScrape
from .models import Board
from .normalize import side_label
from . import oddsmath as om
from .oddschecker_free import fetch_fanatics_league

log = logging.getLogger("edge.arb")


class _MarketsShim:
    """FanaticsMarkets expects cfg.fanatics_markets.* and cfg.scrape.*."""
    def __init__(self, cfg: ArbConfig):
        self.scrape = cfg.scrape
        self.fanatics_markets = type("FM", (), {
            "series": cfg.fanatics_markets_series, "limit": 50,
            "min_trades": 0, "include_live": False})()


def scan(cfg: ArbConfig | None = None, progress=None,
         return_board: bool = False) -> tuple:
    """Returns (opportunities, stats), or (opportunities, stats, board) when
    `return_board` is set. `progress` is an optional callback taking
    (label, done, total) so a UI can show where it is."""
    cfg = cfg or ArbConfig()
    board = Board()
    stats: dict[str, int] = {}
    steps = ["FanDuel", "DraftKings", "Fanatics", "Anchor"]

    def tick(i, label):
        if progress:
            progress(label, i, len(steps))

    # 1. FanDuel — the spine, plus main lines, props and alternate ladders
    tick(0, "FanDuel")
    fd = FanDuelScrape(state=cfg.state)
    fd_quotes = 0
    for sport in cfg.sports:
        try:
            league = fd.league_page(sport)
            events = fd.list_events(sport, league)
            fd_quotes += fd.ingest_event(board, league, sport, strict_match=False)["quotes"]
        except Exception as exc:
            log.warning("fanduel %s: %s", sport, exc)
            continue
        now = datetime.now(timezone.utc)
        window = [e for e in events
                  if cfg.detect.min_minutes_to_start * 60
                  <= (e[2] - now).total_seconds() <= cfg.detect.max_hours_to_start * 3600]
        window.sort(key=lambda e: e[2])
        for eid, _name, _when in window[: cfg.fanduel_max_events]:
            time.sleep(cfg.request_gap_seconds)
            for tab in cfg.fanduel_tabs:
                try:
                    payload = fd.event_markets(eid, tab=tab)
                except Exception:
                    continue
                fd_quotes += fd.ingest_event(board, payload, sport,
                                             strict_match=False)["quotes"]
                time.sleep(cfg.request_gap_seconds)
    stats["fanduel"] = fd_quotes

    # 2. DraftKings — league feed, plus prop subcategories
    tick(1, "DraftKings")
    dk = DraftKingsLeague(state=cfg.state)
    dk_quotes = 0
    for sport in cfg.sports:
        try:
            payload = dk.fetch(sport)
        except Exception as exc:
            log.warning("draftkings %s: %s", sport, exc)
            continue
        dk_quotes += dk.ingest(board, payload, sport,
                               strict_match=cfg.scrape.strict_event_match)["quotes"]
        if cfg.draftkings_props:
            for cid, sid, _n in dk.prop_subcategories(payload)[: cfg.draftkings_max_prop_subcategories]:
                time.sleep(cfg.request_gap_seconds)
                try:
                    sub = dk.fetch_subcategory(sport, cid, sid)
                except Exception:
                    continue
                sub = dict(sub, events=payload.get("events") or [])
                dk_quotes += dk.ingest(board, sub, sport,
                                       strict_match=cfg.scrape.strict_event_match)["quotes"]
    stats["draftkings"] = dk_quotes

    # 3. Fanatics via Oddschecker
    tick(2, "Fanatics")
    fan_quotes = 0
    for league in cfg.fanatics_leagues:
        try:
            payload = fetch_fanatics_league(league["event_id"], league["bettype_ids"])
        except Exception as exc:
            log.warning("fanatics %s: %s", league["name"], exc)
            continue
        fan_quotes += books_parse.ingest_oddschecker(
            board, payload, book="fanatics", sport_key=league["sport_key"],
            strict_match=cfg.scrape.strict_event_match)["quotes"]
    stats["fanatics"] = fan_quotes

    # 4. vig-free anchor
    tick(3, "Anchor")
    try:
        stats["anchor"] = FanaticsMarkets(_MarketsShim(cfg)).fetch_all(board, cfg.sports)["quotes"]
    except Exception as exc:
        log.warning("fanatics markets: %s", exc)
        stats["anchor"] = 0

    # Why an empty board is empty -- counted BEFORE prune(), which deletes
    # anything more than its grace period past start and would otherwise erase
    # the evidence. skip_live is right for a three-hour game and awkward for
    # golf, where a tournament is "in progress" from Thursday's first tee to
    # Sunday's last putt, so golf is usually skipped entirely. Without this the
    # app shows nothing and gives no reason.
    now_w = datetime.now(timezone.utc)
    skipped: dict[str, int] = {}
    for e in board.events.values():
        if not engine.in_window(e, cfg, now_w):
            key = ("in progress" if e.minutes_to_start(now_w) < 0 else "outside window")
            k = f"{e.sport_key} ({key})"
            skipped[k] = skipped.get(k, 0) + 1
    stats["skipped_events"] = skipped

    board.prune()
    opps = engine.scan(board, cfg)
    # recorded so a UI can rescale stakes to a different bankroll
    stats["bankroll"] = int(cfg.bankroll.total)
    stats["events"] = len(board.events)
    stats["groups"] = len(board)
    stats["quotes"] = board.quote_count
    return (opps, stats, board) if return_board else (opps, stats)


def candidates(board: Board, cfg: ArbConfig, max_sum: float = 1.35) -> list[dict]:
    """Two-way markets with both sides priced at two or more bettable books.

    Snapshotted alongside the opportunities so the app can apply a profit boost
    WITHOUT re-scanning. A boost only ever improves one leg, so every market
    that could arb under a boost is already a market that prices near fair --
    `max_sum` keeps the ones with enough headroom and drops the rest. (A 50%
    boost turns a -110/-110 pair, sum 1.048, into 0.947; by sum 1.35 no
    realistic boost rescues it, so storing those would only bloat the file.)

    Without this the boost control could only re-price opportunities that
    already exist, which is backwards: the whole point of a boost is the
    markets that are NOT arbs until you apply one.

    `prices` carries EVERY book's price per side, not just the best. The best
    is what an arbitrage needs, but a boost is tied to a specific book: a
    DraftKings token is useless if the snapshot only kept FanDuel's better
    over. `single_book` marks a market only one book prices both sides of --
    normally a data artifact and excluded by min_books, but with a boost it is
    a real position (the boost can beat one book's own vig) and it is the only
    way to devig a fair price where the other book posts one side.
    """
    books = set(cfg.books.bettable)
    now = datetime.now(timezone.utc)
    out = []
    for g in board.groups.values():
        sides = g.expected_sides()
        if len(sides) != 2:
            continue
        best = {s: g.best(s, books) for s in sides}
        if any(q is None for q in best.values()):
            continue
        if not engine.in_window(g.event, cfg, now):
            continue
        single_book = len({q.book for q in best.values()}) < cfg.detect.min_books
        s = om.arb_sum([q.decimal for q in best.values()])
        if s > max_sum:
            continue
        ev = g.event
        out.append({
            "sport_key": ev.sport_key, "sport_title": ev.sport_title,
            "event_id": ev.event_id, "matchup": ev.matchup,
            "commence_time": ev.commence_time.isoformat(),
            "market": g.key.market, "subject": g.key.subject, "point": g.key.point,
            "arb_sum": round(s, 5),
            "single_book": single_book,
            "legs": [{"side": si, "book": q.book, "decimal": round(q.decimal, 4),
                      "label": side_label(si, ev.home_team, ev.away_team, g.key.subject)}
                     for si, q in sorted(best.items())],
            "prices": {si: {b: round(q.decimal, 4)
                            for b, q in g.quotes.get(si, {}).items() if b in books}
                       for si in sorted(sides)},
        })
    out.sort(key=lambda c: c["arb_sum"])
    return out


def snapshot(cfg: ArbConfig | None = None, progress=None) -> dict:
    """A JSON-safe scan result, for writing to disk and reading in the app."""
    cfg = cfg or ArbConfig()
    opps, stats, board = scan(cfg, progress=progress, return_board=True)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "opportunities": [o.to_dict() for o in opps],
        "candidates": candidates(board, cfg),
    }
