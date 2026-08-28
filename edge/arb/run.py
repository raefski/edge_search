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
from .oddschecker_free import fetch_fanatics_league

log = logging.getLogger("edge.arb")


class _MarketsShim:
    """FanaticsMarkets expects cfg.fanatics_markets.* and cfg.scrape.*."""
    def __init__(self, cfg: ArbConfig):
        self.scrape = cfg.scrape
        self.fanatics_markets = type("FM", (), {
            "series": cfg.fanatics_markets_series, "limit": 50,
            "min_trades": 0, "include_live": False})()


def scan(cfg: ArbConfig | None = None, progress=None) -> tuple[list, dict]:
    """Returns (opportunities, stats). `progress` is an optional callback
    taking (label, done, total) so a UI can show where it is."""
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
            try:
                payload = fd.event_markets(eid, tab="popular")
            except Exception:
                continue
            fd_quotes += fd.ingest_event(board, payload, sport, strict_match=False)["quotes"]
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

    board.prune()
    opps = engine.scan(board, cfg)
    # recorded so a UI can rescale stakes to a different bankroll
    stats["bankroll"] = int(cfg.bankroll.total)
    stats["events"] = len(board.events)
    stats["groups"] = len(board)
    stats["quotes"] = board.quote_count
    return opps, stats


def snapshot(cfg: ArbConfig | None = None, progress=None) -> dict:
    """A JSON-safe scan result, for writing to disk and reading in the app."""
    opps, stats = scan(cfg, progress=progress)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "opportunities": [o.to_dict() for o in opps],
    }
