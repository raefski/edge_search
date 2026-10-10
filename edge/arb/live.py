"""One live game, DraftKings and FanDuel only, read twice before anything is shown.

The pregame scan cannot serve a game in progress (HANDOFF.md §4 and §7): it
prunes anything 20 minutes past start, takes ~200s, and reaches the phone ~2.5
minutes later as a committed snapshot. A live price lasts seconds. So this
reads ONE game, from the two books a live bet actually gets placed at:

    FanDuel     event-page, one call per tab
    DraftKings  league feed + that league's line/prop subcategories,
                every payload filtered down to the one game

Two things measured on Cowboys @ Giants (2026-09-13) shape it:

* FanDuel answers from CloudFront with `max-age=15, stale-while-revalidate=60`,
  so a price can be ~75s older than the moment we fetched it, and
  `Quote.last_update` is fetch time. Each FanDuel quote is backdated by the
  response's `Age` header before the age cap sees it. DraftKings sends
  `max-age=1` and is taken at fetch time.
* Every one of the five arbitrages the first live dry run reported was gone
  within ~2 minutes -- the FanDuel legs had moved or been SUSPENDED. So nothing
  is reported off one read: an arbitrage has to survive an immediate second
  read, and only the second read's prices are shown.

Breaks -- between quarters, halftime, between innings -- are when this is most
usable: prices stop moving, so both the cache age and the time between placing
the two legs stop mattering as much. `GameState.between_periods` flags them.

NOT HANDLED: DraftKings' league feed carries no suspension flag that could be
found, so a suspended DraftKings market still reads as a price. The second read
narrows that; it does not close it. Check the DraftKings app before staking.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from . import engine
from . import oddsmath as om
from .config import ArbConfig
from .draftkings_league import (LEAGUE_IDS, DraftKingsLeague,
                                main_line_subcategories)
from .fanduel import API_KEY as FD_API_KEY, HEADERS as FD_HEADERS
from .fanduel import SPORT_EVENT_TYPES, FanDuelScrape, _ts
from .matching import mascot
from .models import Board

LIVE_BOOKS = ["draftkings", "fanduel"]
# Well under FanDuel's worst case (15s max-age + 60s stale-while-revalidate),
# so a response served deep into its stale window is refused rather than
# paired against a DraftKings price read a second ago.
DEFAULT_MAX_AGE_SECONDS = 45.0
# A handful of parallel calls per host is what the books' own pages make, and
# it keeps one read to seconds. Serial at the scan's gap, a read with props
# spread over long enough for the first leg to be stale by the last.
WORKERS = 4
# FanDuel's own max-age. A copy older than this was served stale and is
# refetched once -- see _fd_tab.
STALE_REFETCH_SECONDS = 15.0


@dataclass
class Game:
    sport_key: str
    name: str
    start: datetime
    fd_event_id: str
    dk_league_id: int | None = None
    dk_event_id: str | None = None


@dataclass
class GameState:
    status: str | None = None
    period: str | None = None
    clock_seconds: int | None = None
    clock_running: bool | None = None
    away: str | None = None
    home: str | None = None
    away_score: str | None = None
    home_score: str | None = None

    @property
    def between_periods(self) -> bool:
        """A stoppage that is a BREAK, not a timeout or an incomplete pass.

        Football's clock stops constantly, so a stopped clock alone says
        nothing. What marks the end of a quarter is the clock stopped at 0
        (DraftKings' `gameTime` counts DOWN from 900) or at 900 before the next
        one starts. DraftKings OMITS `isClockRunning` at that point -- read at
        "4th Quarter", gameTime 900, with no flag at all -- so only an explicit
        True rules a break out. The period text catches halftime and baseball's
        "Middle/End" -- the baseball wording is UNVERIFIED; no live MLB payload
        has been read yet.
        """
        text = (self.period or "").lower()
        if any(w in text for w in ("half", "break", "middle", "end ")):
            return True
        return self.clock_running is not True and self.clock_seconds in (0, 900)

    def describe(self) -> str:
        score = (f"{self.away} {self.away_score or 0} - {self.home} {self.home_score or 0}"
                 if self.away and self.home else "score unknown")
        clock = ""
        if self.clock_seconds is not None:
            m, s = divmod(int(self.clock_seconds), 60)
            clock = f" {m}:{s:02d}"
        running = {True: "clock running", False: "clock stopped"}.get(self.clock_running, "")
        bits = [score, f"{self.period or self.status or '?'}{clock}"]
        if running:
            bits.append(running)
        if self.between_periods:
            bits.append("BREAK")
        return " · ".join(bits)


@dataclass
class Read:
    board: Board
    state: GameState
    seconds: float
    fd_ages: list[float] = field(default_factory=list)
    quotes: dict[str, int] = field(default_factory=dict)
    # requests that raised, per book -- a read missing a tab prices fewer
    # markets and otherwise looks exactly like a book that suspended them
    failed: dict[str, int] = field(default_factory=dict)


def live_config(cfg: ArbConfig | None = None,
                max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> ArbConfig:
    cfg = cfg or ArbConfig()
    cfg.books.legal = list(LIVE_BOOKS)
    cfg.detect.skip_live = False
    cfg.detect.max_quote_age_seconds = max_age_seconds
    # No anchor is read, and a vig-free anchor lags in-play anyway -- +EV off
    # it is exactly the false signal HANDOFF.md warns about.
    cfg.detect.ev_enabled = False
    return cfg


# ---------------------------------------------------------------- finding it

def find_game(query: str, sport_key: str, fd: FanDuelScrape,
              dk: DraftKingsLeague, now: datetime | None = None) -> Game:
    """The FanDuel event whose name contains `query`, and its DraftKings twin.

    Prefers a game already under way, then the soonest -- "giants" on a
    Sunday night means the one being played, not next week's.
    """
    now = now or datetime.now(timezone.utc)
    etid = SPORT_EVENT_TYPES.get(sport_key.split("_")[0])
    if etid is None:
        raise ValueError(f"no FanDuel sport page for {sport_key}")
    events = (fd.sport_page(etid).get("attachments") or {}).get("events") or {}
    q = query.lower().strip()
    hits = [(str(eid), ev["name"], _ts(ev.get("openDate")))
            for eid, ev in events.items()
            if q in (ev.get("name") or "").lower() and " @ " in (ev.get("name") or "")]
    if not hits:
        raise LookupError(f"no FanDuel {sport_key} game matching {query!r}")
    hits.sort(key=lambda h: (h[2] > now, abs((h[2] - now).total_seconds())))
    eid, name, start = hits[0]
    game = Game(sport_key=sport_key, name=name, start=start, fd_event_id=eid)

    league = LEAGUE_IDS.get(sport_key)
    if league is not None:
        game.dk_league_id = league
        game.dk_event_id = dk_event_for(dk.fetch_league(league), name, start)
    return game


def dk_event_for(payload: dict, fd_name: str, start: datetime) -> str | None:
    """DraftKings' id for the same game: both mascots named, start within 3h.

    DraftKings writes "DAL Cowboys @ NY Giants" where FanDuel writes "Dallas
    Cowboys @ New York Giants" -- the city is abbreviated, the mascot is not.
    Both books also rewrite the start to the actual kickoff once play begins
    (00:26 and 00:24:50 against a scheduled 00:20), so the window is loose;
    two mascots on the same day is already one game.
    """
    away, _, home = fd_name.partition(" @ ")
    want = {mascot(away), mascot(home)} - {""}
    for ev in payload.get("events") or []:
        names = {mascot(p.get("name") or "") for p in ev.get("participants") or []}
        if not want or not want <= names:
            continue
        when = _ts(ev.get("startEventDate"))
        if abs((when - start).total_seconds()) <= 3 * 3600:
            return str(ev.get("id"))
    return None


def game_state(ev: dict | None) -> GameState:
    if not ev:
        return GameState()
    lgs = ev.get("liveGameState") or {}
    parts = sorted(ev.get("participants") or [], key=lambda p: p.get("sortOrder") or 0)
    by_role = {(p.get("venueRole") or "").lower(): p.get("name") for p in parts}
    main = (ev.get("eventScorecard") or {}).get("mainScorecard") or {}
    # firstTeamScore follows participant sortOrder, which put the away team
    # first on the one game read (DAL sortOrder 1, NYG 2) -- mapped through
    # sortOrder rather than assumed to be away.
    first = (parts[0].get("venueRole") or "").lower() if parts else "away"
    scores = {first: main.get("firstTeamScore"),
              ("home" if first == "away" else "away"): main.get("secondTeamScore")}
    return GameState(status=ev.get("status"), period=lgs.get("period"),
                     clock_seconds=lgs.get("gameTime"),
                     clock_running=lgs.get("isClockRunning"),
                     away=by_role.get("away"), home=by_role.get("home"),
                     away_score=scores.get("away"), home_score=scores.get("home"))


# ------------------------------------------------------------------- reading

def only_event(payload: dict, event_id: str) -> dict:
    """A DraftKings league or subcategory payload cut down to one game.

    The league feed prices every game in the league; left whole, the other
    games' markets are ingested only to be thrown away by strict matching.
    """
    markets = [m for m in payload.get("markets") or [] if str(m.get("eventId")) == event_id]
    ids = {m.get("id") for m in markets}
    return dict(payload, markets=markets,
                selections=[s for s in payload.get("selections") or []
                            if s.get("marketId") in ids],
                events=[e for e in payload.get("events") or []
                        if str(e.get("id")) == event_id])


# DraftKings prop tabs that can never pair with FanDuel on a full-game key, so a
# live read does not spend a request on them every pass: period props
# ("Rec Yards - 1H O/U") are refused by is_full_game anyway, and the
# multi-player and race markets have no single subject or no line. DraftKings
# renames and reshuffles these tabs during a game ("Each Player Pass Yds in Each
# Half" at one read, "Player Pass Yds in Each Half" two minutes later), so the
# patterns key on the scoping words, not on whole names.
_LIVE_SKIP_TABS = re.compile(r"- 1[HQ]\b|\bin each (half|quarter)\b|\beither player\b"
                             r"|\bcombined\b|^most\b|^1st\b|^race to\b", re.I)


def live_prop_subcategories(dk: DraftKingsLeague, league: dict,
                            limit: int) -> list[tuple[int, int, str]]:
    return [t for t in dk.prop_subcategories(league)
            if not _LIVE_SKIP_TABS.search(t[2])][:limit]


def cache_age(headers: dict) -> float:
    """Seconds a CDN says this response sat in cache. The http shim keeps the
    server's header casing, so this looks the name up case-insensitively."""
    for k, v in (headers or {}).items():
        if k.lower() == "age":
            try:
                return max(0.0, float(v))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def backdate(board: Board, book: str, since: datetime, to: datetime) -> int:
    """Re-stamp `book`'s quotes written at or after `since` as fetched at `to`."""
    n = 0
    for g in board.groups.values():
        for per_book in g.quotes.values():
            q = per_book.get(book)
            if q is not None and q.last_update >= since:
                per_book[book] = replace(q, last_update=to)
                n += 1
    return n


def _fd_tab(fd: FanDuelScrape, event_id: str, tab: str):
    """One FanDuel tab as (payload, cache age, fetched at), fetched at most twice.

    stale-while-revalidate means CloudFront hands back its old copy AND starts
    refreshing it, so the request straight after a stale one is usually fresh:
    a live pass read a 55s-old copy that put every FanDuel price past the age
    cap. One retry of the same URL the app uses -- not a cache-busting
    parameter, which would be traffic no browser sends.
    """
    best = None
    for attempt in range(2):
        r = fd.session.get(f"{fd.base}/event-page",
                           params={"eventId": event_id, "tab": tab, "_ak": FD_API_KEY},
                           headers=FD_HEADERS, timeout=25)
        r.raise_for_status()
        got = (r.json() or {}, cache_age(r.headers), datetime.now(timezone.utc))
        # fresher CONTENT wins, which is fetch time minus cache age
        if best is None or got[2] - timedelta(seconds=got[1]) > best[2] - timedelta(seconds=best[1]):
            best = got
        if got[1] <= STALE_REFETCH_SECONDS or attempt:
            break
        time.sleep(1.0)
    return best


def read_game(game: Game, cfg: ArbConfig, fd: FanDuelScrape, dk: DraftKingsLeague,
              props: bool = True) -> Read:
    t0 = time.monotonic()
    board = Board()
    tabs = ["popular"] + [t for t in cfg.tabs_for(game.sport_key) if t != "popular"]
    if not props:
        tabs = ["popular"]

    with ThreadPoolExecutor(WORKERS) as ex:
        fd_jobs = [ex.submit(_fd_tab, fd, game.fd_event_id, t) for t in tabs]
        league = (dk.fetch_league(game.dk_league_id)
                  if game.dk_league_id and game.dk_event_id else {})
        subcats = []
        if league:
            subcats = [(c, s, n, "alt" not in n.lower()) for c, s, n in
                       main_line_subcategories(league, cfg.draftkings_main_line_subcategories)]
            if props:
                subcats += [(c, s, n, False) for c, s, n in live_prop_subcategories(
                    dk, league, cfg.draftkings_max_prop_subcategories)]
        dk_jobs = [(ex.submit(dk.fetch_league_subcategory, game.dk_league_id, c, s), main)
                   for c, s, _n, main in subcats]
        failed = {"fanduel": 0, "draftkings": 0}
        fd_results = []
        for j in fd_jobs:
            try:
                fd_results.append(j.result())
            except Exception:                      # noqa: BLE001
                failed["fanduel"] += 1
        dk_results = []
        for j, main in dk_jobs:
            try:
                dk_results.append((j.result(), main))
            except Exception:                      # noqa: BLE001
                failed["draftkings"] += 1

    # FanDuel first: its event is the one DraftKings matches onto. Oldest
    # CONTENT first, so where two tabs carry the same market the fresher
    # response is the one left standing.
    ages = []
    for payload, age, fetched in sorted(fd_results, key=lambda r: r[1], reverse=True):
        since = datetime.now(timezone.utc)
        fd.ingest_event(board, payload, game.sport_key, strict_match=False)
        backdate(board, "fanduel", since, fetched - timedelta(seconds=age))
        ages.append(age)

    ev = None
    if league:
        cut = only_event(league, game.dk_event_id)
        ev = (cut.get("events") or [None])[0]
        dk.ingest(board, cut, game.sport_key, strict_match=True, is_main_line=True)
        for sub, main in dk_results:
            sub = dict(only_event(sub, game.dk_event_id), events=cut.get("events") or [])
            dk.ingest(board, sub, game.sport_key, strict_match=True, is_main_line=main)

    quotes: dict[str, int] = {}
    for g in board.groups.values():
        for per_book in g.quotes.values():
            for b in per_book:
                quotes[b] = quotes.get(b, 0) + 1
    return Read(board=board, state=game_state(ev), seconds=time.monotonic() - t0,
                fd_ages=ages, quotes=quotes, failed=failed)


# ----------------------------------------------------------------- detecting

def _key(o: engine.Opportunity) -> tuple:
    return (o.market, o.subject, tuple(sorted((l.book, l.side, l.point) for l in o.legs)))


def confirm(first: list, second: list) -> tuple[list, list]:
    """(still there on the second read, gone). Matched on market, subject and
    each leg's book/side/line -- NOT on price, which is allowed to move as long
    as it is still an arbitrage. The second read's opportunity is kept."""
    later = {_key(o): o for o in second}
    kept, gone = [], []
    for o in first:
        (kept.append(later[_key(o)]) if _key(o) in later else gone.append(o))
    return kept, gone


def closest(board: Board, cfg: ArbConfig, n: int = 5) -> list[tuple]:
    """The two-book pairs nearest to an arbitrage, for when there is none --
    so an empty result reads as "priced, nothing there" rather than "broken"."""
    books = set(cfg.books.bettable)
    now = datetime.now(timezone.utc)
    rows = []
    for g in board.groups.values():
        sides = g.expected_sides()
        if len(sides) != 2:
            continue
        best = {s: g.best(s, books) for s in sides}
        if any(q is None for q in best.values()) or len({q.book for q in best.values()}) < 2:
            continue
        if max(q.age_seconds(now) for q in best.values()) > cfg.detect.max_quote_age_seconds:
            continue
        rows.append((om.arb_sum([q.decimal for q in best.values()]), g,
                     sorted(best.items())))
    rows.sort(key=lambda r: r[0])
    return rows[:n]


@dataclass
class Result:
    game: Game
    read: Read
    confirmed: list
    vanished: list
    second_read: Read | None = None


def scan_game(game: Game, cfg: ArbConfig, fd: FanDuelScrape, dk: DraftKingsLeague,
              props: bool = True) -> Result:
    first = read_game(game, cfg, fd, dk, props=props)
    arbs = engine.find_arbitrages(first.board, cfg)
    if not arbs:
        return Result(game, first, [], [])
    second = read_game(game, cfg, fd, dk, props=props)
    kept, gone = confirm(arbs, engine.find_arbitrages(second.board, cfg))
    return Result(game, second, kept, gone, second_read=second)
