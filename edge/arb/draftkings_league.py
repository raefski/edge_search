"""DraftKings league feed — main lines and props for a whole league per call.

    /api/sportscontent/dkus{state}/v1/leagues/{leagueId}
    /api/sportscontent/dkus{state}/v1/leagues/{leagueId}/categories/{categoryId}

Response is `{events: [...], markets: [...], selections: [...]}` — the same
markets/selections join as the per-event endpoint, with the event list
included, so one request covers the slate.

This host refuses datacenter IPs (403). It answers normally from a residential
connection in-state, so this runs on your machine and not from a server.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from . import http as requests

from .models import Board, EventMeta
from .draftkings_nash import ingest_sportscontent
from .matching import match_event
from .normalize import split_fixture  # noqa: F401  (re-exported)

_FRAC_RE = re.compile(r"\.(\d{1,6})\d*")

log = logging.getLogger("arb.dk_league")

HOST = "https://sportsbook-nash.draftkings.com/api/sportscontent/dkus{state}/v1"
LEAGUE_IDS = {
    "baseball_mlb": 84240, "basketball_nba": 42648, "basketball_wnba": 94682,
    "americanfootball_nfl": 88808, "icehockey_nhl": 42133,
    "americanfootball_ncaaf": 87637, "basketball_ncaab": 92483,
}
FULL_GAME_CATEGORY = 493       # "Game Lines"
# Prop categories worth pulling. The league payload lists all 27 categories and
# 241 subcategories for free, so these are filtered from it rather than guessed.
PROP_CATEGORIES = {
    743: "Batter Props", 1031: "Pitcher Props",
    1342: "Passing Props", 1343: "Rushing Props", 1344: "Receiving Props",
    1215: "Player Points", 1216: "Player Rebounds", 1217: "Player Assists",
}
HEADERS = {
    "Accept": "application/json",
    "Referer": "https://sportsbook.draftkings.com/",
    "Origin": "https://sportsbook.draftkings.com",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
    "x-client-name": "web", "x-client-page": "event", "x-pe-ep": "SB",
}


# Two-sided tabs, which a cap must never drop. "O/U" names both sides; the
# rest are over-only ladders. "To Record a Win" is Yes/No -- two-sided, needed
# by edge/dfs.py, and not identifiable from the name pattern alone.
TWO_SIDED_EXTRA = {"to record a win"}


def _two_sided(name: str) -> bool:
    n = (name or "").strip()
    return n.endswith("O/U") or n.lower() in TWO_SIDED_EXTRA


# Full-game sides and totals, in the order they are worth spending a call on.
# The league feed already carries the moneyline for every sport, but only the
# US ones get spreads and totals with it -- soccer keeps them in subcategories
# ("Match Lines" 490: Spread 13170, Total Goals 13171), so without this a
# soccer league arrives as a moneyline and nothing else.
# Moneyline is absent on purpose: every league feed already carries it, for
# soccer as well as the US sports, so asking for it again is a wasted request.
# So are the Asian lines -- a quarter handicap settles half-win/half-push,
# which is not the bet a European spread at the same number is, and
# marketmap's `handicap` rule would file them together.
MAIN_LINE_ORDER = (
    r"^(point )?spread$", r"^total( goals| points| runs)?$", r"^run line$",
    r"^puck line$", r"^alt(ernate)? (spread|run line|puck line)",
    r"^alt(ernate)? total",
)


def main_line_subcategories(payload: dict,
                            limit: int = 4) -> list[tuple[int, int, str]]:
    """(categoryId, subcategoryId, name) for the full-game sides and totals.

    Ordered by MAIN_LINE_ORDER rather than by DraftKings' own ordering, which
    is arbitrary -- the same trap prop_subcategories documents. `limit` is what
    keeps a 136-subcategory soccer league from costing 136 requests.

    THE CATEGORY IS CHECKED AS WELL AS THE SUBCATEGORY, because a subcategory
    name alone does not say what it is scoped to. MLB category 1674 is "Team
    Totals" and its subcategories are called, in full, "Total Runs" and
    "Alternate Total Runs" -- indistinguishable by name from the game totals in
    category 493, and they carry one team's runs. Matching on the subcategory
    alone pulled them in and a team total was reported as a game total.
    """
    from .marketmap import is_full_game

    categories = {c.get("id"): (c.get("name") or "")
                  for c in (payload.get("categories") or [])}
    ranked: list[tuple[int, int, int, str]] = []
    for sc in payload.get("subcategories") or []:
        name = (sc.get("name") or "").strip()
        cid, sid = sc.get("categoryId"), sc.get("id")
        if not name or cid is None or sid is None or not is_full_game(name):
            continue
        if not is_full_game(categories.get(cid) or categories.get(str(cid)) or ""):
            continue
        for rank, pattern in enumerate(MAIN_LINE_ORDER):
            if re.match(pattern, name, re.I):
                ranked.append((rank, int(cid), int(sid), name))
                break
    ranked.sort(key=lambda t: (t[0], t[3]))
    return [(c, s, n) for _, c, s, n in ranked[:limit]]


# The public league page for a sport. Its HTML embeds the whole league list,
# which is the only place DraftKings exposes it -- the sportscontent API has no
# listing endpoint, the v5 API is Akamai blocked, and pagedata offers only
# id -> slug. displayGroupId is DraftKings' own sport number.
LEAGUE_PAGE = "https://sportsbook.draftkings.com/leagues/{slug}"
# DraftKings' own sport numbers. Golf and tennis were the first two because
# they are served as a league PER TOURNAMENT; the rest are here because ONE
# page carries the whole catalog -- 478 rows across 25 display groups -- so
# every league id in the book costs a single request to learn. Verified
# 2026-08-30: discovery reproduces all seven previously hardcoded LEAGUE_IDS
# exactly, which is what makes it safe to prefer over them.
DISPLAY_GROUPS = {
    "soccer": 1, "basketball": 2, "americanfootball": 3, "tennis": 6,
    "baseball": 7, "icehockey": 8, "handball": 10, "rugbyleague": 11,
    "golf": 12, "snooker": 13, "motorsport": 14, "darts": 15, "boxing": 20,
    "tabletennis": 26, "rugbyunion": 35, "aussierules": 41, "mma": 43,
    "cricket": 59, "esports": 64, "lacrosse": 245,
}
# Any league page serves the whole catalog, so discovery does not depend on
# the slug matching the sport being looked up. MLB's is used because it is the
# one page proven to answer year-round.
CATALOG_PAGE_SLUG = "baseball/mlb"

_LEAGUE_ROW = re.compile(
    r'"displayGroupId"\s*:\s*"?(\d+)"?\s*,\s*"eventGroupId"\s*:\s*"?(\d+)"?\s*,'
    r'\s*"eventGroupName"\s*:\s*"([^"]+)"')


def parse_league_page(html: str, display_group: int | None = None) -> dict[int, str]:
    """{league_id: name} out of a league page's embedded JSON.

    `display_group` filters to one sport; None returns every league on the
    page, which is the whole book.

    Split from the fetch so the parsing can be tested without the network --
    this is the part that breaks when DraftKings changes their page, and a
    test that needs a live request would not be run often enough to catch it.
    """
    out: dict[int, str] = {}
    for disp, gid, name in _LEAGUE_ROW.findall(html or ""):
        if display_group is None or disp == str(display_group):
            out[int(gid)] = name
    return out


def parse_catalog(html: str) -> dict[int, dict[int, str]]:
    """{display_group: {league_id: name}} for every sport on the page."""
    out: dict[int, dict[int, str]] = {}
    for disp, gid, name in _LEAGUE_ROW.findall(html or ""):
        out.setdefault(int(disp), {})[int(gid)] = name
    return out


def fetch_catalog(session=None, timeout: float = 30.0) -> dict[int, dict[int, str]]:
    """Every league DraftKings lists, {display_group: {league_id: name}}.

    One request. The page is ~2 MB of HTML, which is still cheaper than the
    dozens of calls a per-sport walk would cost, and it is the only place the
    book exposes a league list at all -- see the dead ends in HANDOFF.md §5.

    Fails soft to an empty dict, exactly as discover_leagues does, so a layout
    change costs coverage rather than the scan.
    """
    sess = session or requests.Session()
    try:
        r = sess.get(LEAGUE_PAGE.format(slug=CATALOG_PAGE_SLUG),
                     headers={"User-Agent": HEADERS["User-Agent"],
                              "Accept": "text/html,application/xhtml+xml"},
                     timeout=timeout)
        if r.status_code != 200:
            log.warning("dk catalog page: HTTP %s", r.status_code)
            return {}
        out = parse_catalog(r.text)
        if not out:
            log.warning("dk catalog page: parsed 0 leagues — page layout changed?")
        return out
    except Exception as exc:                       # noqa: BLE001
        log.warning("dk catalog discovery: %s", exc)
        return {}


def discover_leagues(sport_slug: str, session=None, timeout: float = 30.0) -> dict[int, str]:
    """{league_id: name} for a sport, read off its public league page.

    Golf is served as a league PER TOURNAMENT, so its id changes every week and
    hardcoding one means recapturing it by hand every week. The ids are not
    discoverable through any API -- but the league page's HTML carries them,
    and unlike the API on that host it is not Akamai blocked.

    This is HTML scraping and will break when DraftKings changes their page, so
    it is written to fail soft: an empty dict on any error, leaving the caller
    to fall back to whatever is configured. Never let a layout change take the
    scan down.
    """
    group = DISPLAY_GROUPS.get(sport_slug)
    if group is None:
        return {}
    sess = session or requests.Session()
    try:
        r = sess.get(LEAGUE_PAGE.format(slug=sport_slug),
                     headers={"User-Agent": HEADERS["User-Agent"],
                              "Accept": "text/html,application/xhtml+xml"},
                     timeout=timeout)
        if r.status_code != 200:
            log.warning("dk league page %s: HTTP %s", sport_slug, r.status_code)
            return {}
        out = parse_league_page(r.text, group)
        if not out:
            log.warning("dk league page %s: parsed 0 leagues — page layout changed?",
                        sport_slug)
        return out
    except Exception as exc:                       # noqa: BLE001
        log.warning("dk league discovery %s: %s", sport_slug, exc)
        return {}


def _ts(value) -> datetime:
    """Parse an ISO timestamp, tolerating .NET-style sub-second precision.

    DraftKings sends "2026-08-29T16:00:00.0000000Z" -- seven fractional
    digits. Python's fromisoformat accepts only 3 or 6, so this silently
    fell back to "now" and every event failed to match on start time.
    """
    if value is None:
        return datetime.now(timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    text = _FRAC_RE.sub(lambda m: "." + m.group(1), text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

class DraftKingsLeague:
    def __init__(self, state: str = "ct", session: requests.Session | None = None):
        self.base = HOST.format(state=state.lower())
        self.session = session or requests.Session()
        self.headers = dict(HEADERS, **{"x-pe-loc": f"US-{state.upper()}"})

    def fetch(self, sport_key: str, category_id: int | None = None) -> dict:
        league = LEAGUE_IDS.get(sport_key)
        if league is None:
            return {}
        return self.fetch_league(league, category_id)

    def fetch_league(self, league_id: int, category_id: int | None = None) -> dict:
        """One league by id, for the leagues discovered rather than hardcoded.

        LEAGUE_IDS names seven; the catalog page names 478. Anything outside
        the original seven has no sport_key to look up, so the id is passed
        straight through.
        """
        url = f"{self.base}/leagues/{league_id}"
        if category_id:
            url += f"/categories/{category_id}"
        r = self.session.get(url, headers=self.headers, timeout=25)
        if r.status_code == 403:
            raise PermissionError(
                "DraftKings returned 403. This host blocks datacenter IPs; run "
                "from a residential connection in Connecticut.")
        r.raise_for_status()
        return r.json() or {}

    def fetch_league_subcategory(self, league_id: int, category_id: int,
                                 subcategory_id: int) -> dict:
        url = (f"{self.base}/leagues/{league_id}/categories/{category_id}"
               f"/subcategories/{subcategory_id}")
        r = self.session.get(url, headers=self.headers, timeout=25)
        if r.status_code == 403:
            raise PermissionError("DraftKings 403 on the subcategory path")
        r.raise_for_status()
        return r.json() or {}

    def prop_subcategories(self, payload: dict,
                           categories: set[int] | None = None) -> list[tuple[int, int, str]]:
        """(categoryId, subcategoryId, name) for every prop tab in the league.

        Discovered from the league payload, which already carries `categories`
        and `subcategories` -- no per-event capture and no guessing at ids.

        Ordered so the two-sided "O/U" tabs come first. Those carry BOTH sides
        of a line, which is what makes a prop comparable against another book
        and what edge/dfs.py projects a pitcher from; the "Milestones" tabs are
        over-only ladders. DraftKings' own ordering is arbitrary, so a caller
        capping this list was dropping tabs by position: at the default cap of
        12 that silently lost Outs Recorded, Earned Runs Allowed, Hits Allowed
        and To Record a Win -- four of the six markets DFS needs -- plus the
        Hits / Total Bases / RBIs O/U tabs that supply the `under` side no
        other free book posts.
        """
        wanted = categories if categories is not None else set(PROP_CATEGORIES)
        out = []
        for sc in payload.get("subcategories") or []:
            cid = sc.get("categoryId")
            if cid in wanted and sc.get("id"):
                out.append((int(cid), int(sc["id"]), sc.get("name") or ""))
        out.sort(key=lambda t: (0 if _two_sided(t[2]) else 1, t[2]))
        return out

    def fetch_subcategory(self, sport_key: str, category_id: int,
                          subcategory_id: int) -> dict:
        league = LEAGUE_IDS.get(sport_key)
        if league is None:
            return {}
        url = (f"{self.base}/leagues/{league}/categories/{category_id}"
               f"/subcategories/{subcategory_id}")
        r = self.session.get(url, headers=self.headers, timeout=25)
        if r.status_code == 403:
            raise PermissionError("DraftKings 403 on the subcategory path")
        r.raise_for_status()
        return r.json() or {}

    def ingest(self, board: Board, payload: dict, sport_key: str,
               strict_match: bool = True, is_main_line: bool = False) -> dict:
        """Resolve every event in the payload, then reuse the markets/selections
        join once per event.

        `is_main_line` says whether THIS payload is the book's own full-game
        Spread/Total (the base league feed, or a main-line subcategory) as
        opposed to an alternate-line pull -- see ingest_sportscontent."""
        stats = {"events": 0, "matched": 0, "unmatched": 0, "quotes": 0,
                 "markets_unmapped": set()}
        events = payload.get("events") or []
        markets = payload.get("markets") or []
        selections = payload.get("selections") or []
        if not events:
            return stats

        by_event: dict[str, EventMeta] = {}
        for ev in events:
            stats["events"] += 1
            name = ev.get("name") or ""
            pair = split_fixture(name)
            if pair is None:
                continue
            away, home = pair
            when = _ts(ev.get("startEventDate") or ev.get("startDate"))
            target = match_event(board, home, away, when, sport_key)
            if target is None:
                stats["unmatched"] += 1
                if strict_match:
                    continue
                target = EventMeta(f"dk:{ev.get('id')}", sport_key, sport_key,
                                   when, home, away)
            else:
                stats["matched"] += 1
            by_event[str(ev.get("id"))] = target

        # split the flat market/selection lists per event, then reuse the parser
        for eid, target in by_event.items():
            ev_markets = [m for m in markets if str(m.get("eventId")) == eid]
            if not ev_markets:
                continue
            ids = {str(m.get("id")) for m in ev_markets}
            ev_sels = [s for s in selections if str(s.get("marketId")) in ids]
            st = ingest_sportscontent(
                board, {"markets": ev_markets, "selections": ev_sels},
                book="draftkings", sport_key=sport_key,
                strict_match=False, event=target, is_main_line=is_main_line)
            stats["quotes"] += st["quotes"]
            stats["markets_unmapped"] |= st["markets_unmapped"]
        return stats
