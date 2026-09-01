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
from . import catalog
from . import engine
from . import oddschecker_discover
from .config import ArbConfig
from .draftkings_league import (DraftKingsLeague, DISPLAY_GROUPS,
                                discover_leagues as dk_discover, fetch_catalog,
                                main_line_subcategories)
from . import draftkings_nash as books_nash
from .fanaticsmarkets import FanaticsMarkets
from .fanduel import FanDuelScrape, SPORT_EVENT_TYPES
from .models import Board, field_event
from .normalize import side_label
from . import oddsmath as om
from .oddschecker_free import fetch_fanatics_league

log = logging.getLogger("edge.arb")


def _wanted(cfg: ArbConfig) -> set[str] | None:
    """The sport keys this scan is restricted to, or None for all of them."""
    return set(cfg.sports) if getattr(cfg, "sports", None) else None


def _fanduel_pass(board: Board, cfg: ArbConfig, stats: dict) -> None:
    """FanDuel first, because its event list is the spine the rest attach to.

    One request per SPORT rather than per league. That is the change that made
    the other twenty leagues affordable: `page=SPORT` returns every event in
    soccer, or in basketball, together with the competitions that name them and
    the main-line markets, where `customPageId` returns one league and only
    exists for the seven with a slug.

    Every event is then routed to a sport_key by its competition, because
    sport_key is what match_event joins on -- filing all of soccer under one
    key would let a Bundesliga fixture match a Serie A one.
    """
    fd = FanDuelScrape(state=cfg.state)
    wanted = _wanted(cfg)
    quotes = 0
    prop_targets: list[tuple[str, str]] = []       # (sport_key, fanduel event id)
    alt_targets: list[tuple[str, str]] = []       # same, for the alt-line pass
    for sport, event_type in SPORT_EVENT_TYPES.items():
        try:
            payload = fd.sport_page(event_type)
        except Exception as exc:                   # noqa: BLE001
            log.warning("fanduel %s: %s", sport, exc)
            continue
        competitions = (payload.get("attachments") or {}).get("competitions") or {}

        def sport_key_of(ev, _sport=sport, _comps=competitions):
            comp = _comps.get(str(ev.get("competitionId"))) or {}
            name = comp.get("name") or ""
            league = catalog.fanduel_league(_sport, name)
            key = league.key if league else (
                catalog.generic_key(_sport, name)
                if cfg.include_uncatalogued and name else None)
            if key is None or (wanted is not None and key not in wanted):
                return None
            return key

        st = fd.ingest_event(board, payload, sport, strict_match=False,
                             sport_key_of=sport_key_of)
        quotes += st["quotes"]

        # Queue the per-event calls, soonest first. Two queues, because they
        # buy different things and cost differently:
        #
        #   props     one request per event PER TAB, and only where the
        #             catalog says two books post the same stat
        #   alt lines one request per event, for every catalogued league
        #
        # The SPORT page carries exactly ONE line per market -- the main one.
        # FanDuel's alternate ladders exist only on the per-event `popular`
        # tab, where a single MLB game returned 19 alternate total rungs and
        # 15 alternate run-line rungs. Without that call FanDuel contributed
        # 1.4 rungs per event against DraftKings' 8.6, so its main line only
        # ever paired when another book happened to hang the same number.
        # There is no league-wide shortcut: `game-lines`, `alternate-lines`,
        # `totals` and `spreads` as tab values all fall through to a default
        # payload with no alternates in it.
        now = datetime.now(timezone.utc)
        events = (payload.get("attachments") or {}).get("events") or {}
        per_league: dict[str, list[tuple[datetime, str]]] = {}
        for eid, ev in events.items():
            key = sport_key_of(ev)
            if key is None:
                continue
            when = _ts_iso(ev.get("openDate"))
            minutes = (when - now).total_seconds() / 60.0
            if minutes < cfg.detect.min_minutes_to_start:
                continue
            if (cfg.detect.max_hours_to_start
                    and minutes / 60.0 > cfg.detect.max_hours_to_start):
                continue
            per_league.setdefault(key, []).append((when, str(eid)))
        for key, rows in per_league.items():
            rows.sort()
            if key in catalog.props_sports():
                prop_targets += [(key, eid) for _w, eid in
                                 rows[: cfg.prop_events_per_league]]
            # Alt lines only for leagues the catalog names: those are the ones
            # another book is actually likely to be on, and an uncatalogued
            # competition pairs only when two books spell it identically.
            if key in catalog.BY_KEY:
                alt_targets += [(key, eid) for _w, eid in
                                rows[: cfg.fanduel_alt_line_events]]

    prop_queue = prop_targets[: cfg.fanduel_max_events]
    # An event in the prop queue already gets `popular`, which is where the
    # alternates are -- so asking for it again would buy nothing.
    covered = {eid for _k, eid in prop_queue}
    alt_queue = [(k, eid) for k, eid in alt_targets
                 if eid not in covered][: cfg.fanduel_max_alt_events]
    stats["fanduel_prop_events"] = len(prop_queue)
    stats["fanduel_alt_events"] = len(alt_queue)

    for key, eid in prop_queue:
        time.sleep(cfg.request_gap_seconds)
        for tab in cfg.fanduel_tabs:
            try:
                payload = fd.event_markets(eid, tab=tab)
            except Exception:                      # noqa: BLE001
                continue
            quotes += fd.ingest_event(board, payload, key,
                                      strict_match=False)["quotes"]
            time.sleep(cfg.request_gap_seconds)

    for key, eid in alt_queue:
        time.sleep(cfg.request_gap_seconds)
        try:
            payload = fd.event_markets(eid, tab="popular")
        except Exception:                          # noqa: BLE001
            continue
        quotes += fd.ingest_event(board, payload, key,
                                  strict_match=False)["quotes"]
    stats["fanduel"] = quotes


def _draftkings_pass(board: Board, cfg: ArbConfig, stats: dict) -> int:
    """DraftKings by discovered league id, plus its main lines and props.

    The league ids are read off the public catalog page rather than hardcoded.
    That is one request for all 478 of them, and it reproduces every id the
    seven-entry LEAGUE_IDS table held -- which is what makes it safe to prefer.
    """
    dk = DraftKingsLeague(state=cfg.state)
    wanted = _wanted(cfg)
    quotes = 0
    page = fetch_catalog(session=dk.session)
    stats["draftkings_leagues_listed"] = sum(len(v) for v in page.values())

    targets: list[tuple[str, int, str]] = []       # (sport_key, league id, name)
    for league in catalog.LEAGUES:
        if league.tournament or not league.dk_sport:
            continue
        if wanted is not None and league.key not in wanted:
            continue
        group = DISPLAY_GROUPS.get(league.dk_sport)
        for lid, name in (page.get(group) or {}).items():
            if catalog.draftkings_league(league.dk_sport, name) is league:
                targets.append((league.key, lid, name))
                break
    stats["draftkings_leagues_scanned"] = len(targets)

    for sport_key, league_id, name in targets:
        time.sleep(cfg.request_gap_seconds)
        try:
            payload = dk.fetch_league(league_id)
        except Exception as exc:                   # noqa: BLE001
            log.warning("draftkings %s (%s): %s", name, league_id, exc)
            continue
        if not (payload.get("events") or []):
            continue                               # out of season; nothing to price
        quotes += dk.ingest(board, payload, sport_key,
                            strict_match=cfg.scrape.strict_event_match)["quotes"]

        for cid, sid, _n in main_line_subcategories(
                payload, cfg.draftkings_main_line_subcategories):
            time.sleep(cfg.request_gap_seconds)
            try:
                sub = dk.fetch_league_subcategory(league_id, cid, sid)
            except Exception:                      # noqa: BLE001
                continue
            sub = dict(sub, events=payload.get("events") or [])
            quotes += dk.ingest(board, sub, sport_key,
                                strict_match=cfg.scrape.strict_event_match)["quotes"]

        if cfg.draftkings_props and sport_key in catalog.props_sports():
            subs = dk.prop_subcategories(payload)[: cfg.draftkings_max_prop_subcategories]
            for cid, sid, _n in subs:
                time.sleep(cfg.request_gap_seconds)
                try:
                    sub = dk.fetch_league_subcategory(league_id, cid, sid)
                except Exception:                  # noqa: BLE001
                    continue
                sub = dict(sub, events=payload.get("events") or [])
                quotes += dk.ingest(board, sub, sport_key,
                                    strict_match=cfg.scrape.strict_event_match)["quotes"]
    return quotes


def _fanatics_pass(board: Board, cfg: ArbConfig, stats: dict) -> None:
    """Fanatics for every league whose Oddschecker id is known.

    Ids come from the discovery cache when there is one and from the three
    hand-captured entries in config otherwise, so a missing cache costs breadth
    rather than the source. No bettypeIds are sent: the endpoint returns all of
    them, which is where Fanatics' alternate ladders and its NFL player props
    come from.
    """
    wanted = _wanted(cfg)
    discovered = oddschecker_discover.resolve(path=cfg.fanatics_cache_path)
    leagues = discovered or list(cfg.fanatics_leagues)
    stats["fanatics_leagues_known"] = len(leagues)
    quotes = 0
    scanned = 0
    dropped_flat = dropped_rungs = 0
    for league in leagues:
        key = league["sport_key"]
        if wanted is not None and key not in wanted:
            continue
        try:
            payload = fetch_fanatics_league(league["event_id"],
                                            league.get("bettype_ids"))
        except Exception as exc:                   # noqa: BLE001
            log.warning("fanatics %s: %s", league["name"], exc)
            continue
        scanned += 1
        st = books_parse.ingest_oddschecker(
            board, payload, book="fanatics", sport_key=key,
            strict_match=cfg.scrape.strict_event_match)
        quotes += st["quotes"]
        # Reported because they are the count of prices this source served that
        # cannot be real -- a ladder that does not reprice. Not an error: it is
        # how much of Fanatics' depth is placeholder on any given day.
        dropped_flat += st.get("flat_ladders", 0)
        dropped_rungs += st.get("placeholder_rungs", 0)
    stats["fanatics_leagues_scanned"] = scanned
    stats["fanatics_flat_ladders"] = dropped_flat
    stats["fanatics_placeholder_rungs"] = dropped_rungs
    stats["fanatics"] = quotes


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
    _fanduel_pass(board, cfg, stats)

    # 2. DraftKings — every catalogued league, its main lines and its props
    tick(1, "DraftKings")
    dk = DraftKingsLeague(state=cfg.state)
    dk_quotes = _draftkings_pass(board, cfg, stats)

    # 2b. DraftKings golf -- a league PER TOURNAMENT, so the ids are read off
    # the public league page each scan rather than captured weekly by hand.
    # Discovery is HTML scraping and fails soft, so the configured list stays
    # as the fallback: a layout change costs coverage, never the scan.
    want = _wanted(cfg)
    tours = [] if (want is not None and not any(sp.startswith("golf") for sp in want)) else [
        {"name": n, "league_id": i}
        for i, n in sorted(dk_discover("golf").items(), key=lambda kv: kv[1])]
    if tours:
        stats["golf_leagues_discovered"] = len(tours)
    else:
        tours = list(getattr(cfg, "draftkings_golf_leagues", None) or [])
        log.info("golf league discovery returned nothing; using %d configured id(s)",
                 len(tours))
    for tour in tours:
        try:
            head = dk.session.get(f"{dk.base}/leagues/{tour['league_id']}",
                                  headers=dk.headers, timeout=25).json() or {}
        except Exception as exc:
            log.warning("draftkings golf %s: %s", tour.get("name"), exc)
            continue
        events = head.get("events") or []
        if not events:
            # the id has expired: DraftKings retires a tournament league when
            # it finishes, and a stale one is otherwise indistinguishable from
            # "golf had nothing today"
            stats.setdefault("golf_expired", []).append(tour.get("name", ""))
            log.info("draftkings golf %s (%s): no events — id likely expired, recapture",
                     tour.get("name"), tour.get("league_id"))
            continue
        ev = events[0]
        when = _ts_iso(ev.get("startEventDate"))
        # ten golf leagues are listed year-round -- the Masters in April, the
        # Ryder Cup in September. Pulling subcategories for all of them is
        # dozens of calls for markets that do not exist yet, so only
        # tournaments inside the detection window get the extra requests.
        hours_out = (when - datetime.now(timezone.utc)).total_seconds() / 3600.0
        if cfg.detect.max_hours_to_start and hours_out > cfg.detect.max_hours_to_start:
            continue
        target = field_event("golf_pga", when, ev.get("name") or tour.get("name", ""))
        for cid, sid in getattr(cfg, "draftkings_golf_subcategories", None) or []:
            time.sleep(cfg.request_gap_seconds)
            try:
                r = dk.session.get(
                    f"{dk.base}/leagues/{tour['league_id']}/categories/{cid}"
                    f"/subcategories/{sid}", headers=dk.headers, timeout=25)
                if r.status_code != 200:
                    continue
                dk_quotes += books_nash.ingest_sportscontent(
                    board, r.json(), book="draftkings", sport_key="golf_pga",
                    strict_match=False, event=target)["quotes"]
            except Exception as exc:
                log.debug("draftkings golf %s/%s: %s", cid, sid, exc)
    stats["draftkings"] = dk_quotes

    # 2c. DraftKings tennis -- also a league per tournament, but unlike golf
    # each event is a real fixture ("A vs B"), so the ordinary path handles it
    # once the name is parsed. Doubles and qualifying draws are skipped: they
    # price differently and no other book here covers them.
    if _wanted(cfg) is None or any(sp.startswith("tennis") for sp in cfg.sports):
        tl = dk_discover("tennis")
        wanted = [(g, n) for g, n in tl.items()
                  if "Doubles" not in n and "Quals" not in n]
        stats["tennis_leagues_discovered"] = len(wanted)
        for gid, name in sorted(wanted, key=lambda kv: kv[1])[: cfg.tennis_max_leagues]:
            time.sleep(cfg.request_gap_seconds)
            try:
                r = dk.session.get(f"{dk.base}/leagues/{gid}", headers=dk.headers,
                                   timeout=25)
                if r.status_code != 200:
                    continue
                payload_t = r.json() or {}
                if len(payload_t.get("events") or []) < 2:
                    continue          # an outright-only container, months out
                dk_quotes += dk.ingest(board, payload_t, "tennis_atp",
                                       strict_match=False)["quotes"]
            except Exception as exc:
                log.debug("draftkings tennis %s: %s", name, exc)
        stats["draftkings"] = dk_quotes

    # 3. Fanatics via Oddschecker
    tick(2, "Fanatics")
    _fanatics_pass(board, cfg, stats)

    # 4. vig-free anchor
    tick(3, "Anchor")
    anchor_sports = sorted({e.sport_key for e in board.events.values()})
    try:
        stats["anchor"] = FanaticsMarkets(_MarketsShim(cfg)).fetch_all(
            board, anchor_sports)["quotes"]
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

    # The smoke alarm for a mismapped market. In a one-shot scan every quote is
    # seconds old, so one book quoting one side of one group at two prices 25%
    # apart is never a price move -- it is two different bets on one GroupKey,
    # which is how a team total was reported as a game total. Expected to be 0.
    conflicts = [(g.key, c) for g in board.groups.values() for c in g.conflicts]
    stats["price_conflicts"] = len(conflicts)
    for key, (side, book, old_d, new_d) in conflicts[:20]:
        log.warning("price conflict: %s %s %s %s %.3f vs %.3f — two markets on one key?",
                    key.market, key.subject, key.point, f"{book}/{side}", old_d, new_d)

    board.prune()
    opps = engine.scan(board, cfg)
    # recorded so a UI can rescale stakes to a different bankroll
    stats["bankroll"] = int(cfg.bankroll.total)
    stats["events"] = len(board.events)
    stats["groups"] = len(board)
    stats["quotes"] = board.quote_count
    return (opps, stats, board) if return_board else (opps, stats)


def _ts_iso(value) -> datetime:
    """DraftKings sends seven fractional digits; fromisoformat accepts 3 or 6."""
    import re
    if not value:
        return datetime.now(timezone.utc)
    txt = re.sub(r"\.(\d{1,6})\d*", r".\1", str(value).strip().replace("Z", "+00:00"))
    try:
        p = datetime.fromisoformat(txt)
    except ValueError:
        return datetime.now(timezone.utc)
    return p if p.tzinfo else p.replace(tzinfo=timezone.utc)


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
