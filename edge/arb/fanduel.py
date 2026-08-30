"""FanDuel sbapi — free player props and alternate lines, no auth.

    /api/content-managed-page?page=CUSTOM&customPageId={league}&_ak={KEY}
    /api/event-page?eventId={id}&tab={tab}&_ak={KEY}

`_ak` is a static app-wide key in the site's JS, passed as a query param.
There are no cookies and no rotating token, and the Connecticut host answers
from anywhere. That makes FanDuel props free, where the aggregator charges
credits per event for them.

Endpoint shape credit: github.com/sjhouston23/oddswrap
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from . import http as requests

from .models import Board, EventMeta, GroupKey, Quote, field_event
from .matching import match_event
from .normalize import slug, split_fixture

_FRAC_RE = re.compile(r"\.(\d{1,6})\d*")

log = logging.getLogger("arb.fanduel")

API_KEY = "FhMFpcPWXMeyZxOx"
BOOK = "fanduel"
PLAYER_MARKETS = ("pitcher_", "batter_", "player_")
HOST = "https://sbapi.{state}.sportsbook.fanduel.com/api"
HEADERS = {"Accept": "application/json",
           "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151.0.0.0"}

# league page ids on content-managed-page
# Sports served by page=SPORT&eventTypeId= rather than a customPageId slug.
# Tennis has no slug at all -- every guess 404s -- and this is the shape their
# own app uses.
EVENT_TYPE_IDS = {"tennis_atp": 2}

LEAGUE_PAGES = {"baseball_mlb": "mlb", "americanfootball_nfl": "nfl",
                "basketball_nba": "nba", "icehockey_nhl": "nhl",
                "americanfootball_ncaaf": "ncaaf", "basketball_wnba": "wnba",
                "golf_pga": "pga"}

# game-level markets
GAME_MARKETS = {
    "MONEY_LINE": ("h2h", None),
    "MATCH_BETTING": ("h2h", None),        # tennis names its moneyline this way
    "RUN_LINE": ("spreads", None),
    "ALTERNATE_RUN_LINES": ("spreads", None),
    "SPREAD": ("spreads", None),
    "ALTERNATE_SPREAD": ("spreads", None),
    "TOTAL_RUNS": ("totals", None),
    "ALTERNATE_TOTAL_RUNS": ("totals", None),
    "TOTAL_POINTS": ("totals", None),
    "ALTERNATE_TOTAL_POINTS": ("totals", None),
    "MATCH_HANDICAP_(2-WAY)": ("spreads", None),
    "TOTAL_POINTS_(OVER/UNDER)": ("totals", None),
    "ALTERNATE_MATCH_HANDICAP": ("spreads", None),
    "ALTERNATE_TOTAL_POINTS_(OVER/UNDER)": ("totals", None),
}

# Anything naming a period, a single team, or an inning is NOT the full-game
# market. GroupKey carries no period, so mapping "1ST_HALF_TOTAL_POINTS" to
# `totals` would pair a half line against a full-game line.
PERIOD_MARKERS = ("1ST_", "2ND_", "3RD_", "4TH_", "5TH_", "6TH_", "7TH_", "8TH_",
                  "9TH_", "HALF", "QUARTER", "PERIOD", "INNING", "_TEAM_",
                  "HOME_TEAM", "AWAY_TEAM", "RACE_TO", "FIRST_", "AWAY_", "HOME_")
SPREAD_WORDS = ("HANDICAP", "SPREAD", "RUN_LINE", "PUCK_LINE", "LINE_BETTING")
TOTAL_WORDS = ("TOTAL_POINTS", "TOTAL_RUNS", "TOTAL_GOALS", "OVER/UNDER")

# the statistic named by a threshold market, e.g. TO_RECORD_2+_HITS -> hits
STAT_KEYS = {
    "HITS": "batter_hits", "HOME_RUNS": "batter_home_runs", "HOME_RUN": "batter_home_runs",
    "RBIS": "batter_rbis", "RBI": "batter_rbis", "TOTAL_BASES": "batter_total_bases",
    "RUNS": "batter_runs_scored", "SINGLE": "batter_singles", "DOUBLE": "batter_doubles",
    "TRIPLE": "batter_triples", "STOLEN_BASES": "batter_stolen_bases",
    "STRIKEOUTS": "pitcher_strikeouts", "OUTS": "pitcher_outs",
    "HITS+RUNS+RBIS": "batter_hits_runs_rbis",
}
# Golf markets that are genuinely two-way, and so can be priced against
# another book. Everything else golf offers is a field -- Top 5 (29-67
# runners), Round Leader (33), the outright (150+) -- and a field cannot be
# arbitraged from a partial list of runners, which is what
# `test_truncated_outright_field_is_refused` already guards.
#
# All three settle head-to-head between exactly two players. A tie is normally
# a push rather than a loss, which does not break the arithmetic: both legs
# return their stake, so the position cannot lose. Dead-heat rules differ by
# book, so confirm the settlement rule before staking a thin one.
GOLF_TWO_WAY = {
    "2_BALLS_IMG": "golf_2ball",                       # one round, one pairing
    "TOURNAMENT_MATCHBETS_IMG": "golf_matchup",        # 72 holes, head to head
    "WHO_WILL_WIN_A_GROUP_OF_HOLES_IMG": "golf_hole_group",
}

# markets whose line lives in the runner handicap rather than the name
HANDICAP_PROPS = {
    "PITCHER_D_STRIKEOUTS": "pitcher_strikeouts",
    "PITCHER_STRIKEOUTS": "pitcher_strikeouts",
    "PITCHER_OUTS": "pitcher_outs",
}

# "TO_RECORD_2+_HITS", "TO_HIT_3+_HOME_RUNS", "PLAYER_TO_RECORD_A_HIT".
# `AN?` because FanDuel writes "TO_RECORD_AN_RBI" -- the article agrees with the
# stat, and requiring a bare "A" silently dropped every RBI threshold.
THRESHOLD_RE = re.compile(r"(?:TO_RECORD|TO_HIT)_(?:(\d+)\+|AN?)_(.+)$")
# Pitchers are lettered per event: PITCHER_C_STRIKEOUTS, PITCHER_E_TOTAL_STRIKEOUTS,
# PITCHER_A_OUTS_RECORDED_SB. The trailing qualifiers are FanDuel's own market
# variants, not different stats, so they are absorbed rather than enumerated.
PITCHER_RE = re.compile(
    r"^PITCHER_[A-Z]_(?:TOTAL_)?(STRIKEOUTS|OUTS|WALKS|HITS|EARNED_RUNS)"
    r"(?:_RECORDED)?(?:_SB)?$")
# On alternate ladders the line is in the runner NAME, not the handicap field,
# which stays 0: "Over 2.5", "Minnesota Twins +6.5".
OU_NAME_RE = re.compile(r"^(Over|Under)\s+([+-]?[\d.]+)\s*$", re.I)
TEAM_LINE_RE = re.compile(r"^(.+?)\s+([+-][\d.]+)\s*$")

# Player runners come in four shapes and the line is not always in `handicap`.
SIDE_FIRST_RE = re.compile(r"^(Over|Under)\s+([\d.]+)\s+(.*)$", re.I)
PLAYER_OU_RE = re.compile(r"^(?P<who>.+?)\s+(?P<side>Over|Under)\s+(?P<line>[\d.]+)\s*$", re.I)
PLAYER_SIDE_RE = re.compile(r"^(?P<who>.+?)\s+(?P<side>Over|Under)\s*$", re.I)
PLAYER_LADDER_RE = re.compile(r"^(?P<who>.+?)\s+(?P<n>\d+)\+\s+(?P<stat>.+)$")


def parse_player_runner(name: str, handicap) -> tuple[str, str, float] | None:
    """(side, player, line) from one player runner.

    FanDuel writes the same idea four ways, and which one it uses varies by
    market rather than by sport:

        "Over 15.5 Dean Kremer"      handicap 0     side and line first
        "Dean Kremer Over 15.5"      handicap 0     player first (outs recorded)
        "Luis Castillo Over"         handicap 3.5   line in the handicap field
        "Luis Castillo 3+ Strikeouts" handicap 0    one rung of an alt ladder

    Only the first was handled. The rest fell to a default that made the WHOLE
    runner name the player, so the board carried 53 groups keyed
    'Dean Kremer 3+ Strikeouts' with no line -- and, worse, 'Luis Castillo
    Over' and 'Luis Castillo Under' as two separate subjects, which is why the
    two sides of a strikeout line never met each other, let alone another book.

    A ladder rung is converted the same way the threshold markets are: "3+" is
    Over 2.5, so it lands on the line another book actually prices.
    """
    mo = SIDE_FIRST_RE.match(name)
    if mo:
        return mo.group(1).lower(), mo.group(3).strip(), float(mo.group(2))
    mo = PLAYER_OU_RE.match(name)
    if mo:
        return mo.group("side").lower(), mo.group("who").strip(), float(mo.group("line"))
    mo = PLAYER_SIDE_RE.match(name)
    if mo and handicap:
        return mo.group("side").lower(), mo.group("who").strip(), float(handicap)
    mo = PLAYER_LADDER_RE.match(name)
    if mo:
        return "over", mo.group("who").strip(), float(mo.group("n")) - 0.5
    if handicap:
        return "over", name, float(handicap)
    return None


def classify(market_type: str) -> tuple[str, float | None] | None:
    """Map a FanDuel marketType to (canonical market, line) or None.

    Threshold markets are converted the same way DraftKings milestones are:
    "2+ hits" is Over 1.5, "A hit" is Over 0.5. Without that they never meet
    another book's Over/Under and cannot be compared.
    """
    mt = (market_type or "").upper().strip()
    if mt in GOLF_TWO_WAY:
        return GOLF_TWO_WAY[mt], None
    if mt in GAME_MARKETS:
        return GAME_MARKETS[mt]
    if mt in HANDICAP_PROPS:
        return HANDICAP_PROPS[mt], None
    pm = PITCHER_RE.match(mt)
    if pm:
        return {"STRIKEOUTS": "pitcher_strikeouts", "OUTS": "pitcher_outs",
                "WALKS": "pitcher_walks", "HITS": "pitcher_hits_allowed",
                "EARNED_RUNS": "pitcher_earned_runs"}[pm.group(1)], None
    # tolerant fallback for game markets whose exact name varies by sport,
    # but never for a period or team-specific variant
    if not any(marker in mt for marker in PERIOD_MARKERS):
        if any(w in mt for w in SPREAD_WORDS):
            return "spreads", None
        if any(w in mt for w in TOTAL_WORDS):
            return "totals", None

    m = THRESHOLD_RE.search(mt)
    if m:
        n = float(m.group(1)) if m.group(1) else 1.0
        stat = m.group(2).strip("_")
        # singular and plural both occur: "A_HIT" vs "2+_HITS"
        key = (STAT_KEYS.get(stat) or STAT_KEYS.get(stat + "S")
               or STAT_KEYS.get(stat.rstrip("S")))
        if key:
            return key, n - 0.5
    return None


def _decimal(runner: dict) -> float | None:
    odds = (runner.get("winRunnerOdds") or {})
    d = ((odds.get("trueOdds") or {}).get("decimalOdds") or {}).get("decimalOdds")
    if isinstance(d, (int, float)) and d > 1.0:
        return float(d)
    a = (odds.get("americanDisplayOdds") or {}).get("americanOddsInt")
    if isinstance(a, (int, float)) and a:
        from . import oddsmath as om
        return om.american_to_decimal(float(a))
    return None


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

class FanDuelScrape:
    def __init__(self, state: str = "ct", session: requests.Session | None = None):
        self.base = HOST.format(state=state.lower())
        self.session = session or requests.Session()

    def _get(self, path: str, **params) -> dict:
        params["_ak"] = API_KEY
        r = self.session.get(f"{self.base}/{path}", params=params,
                             headers=HEADERS, timeout=25)
        r.raise_for_status()
        return r.json() or {}

    def league_page(self, sport_key: str) -> dict:
        etid = EVENT_TYPE_IDS.get(sport_key)
        if etid is not None:
            return self._get("content-managed-page", page="SPORT",
                             eventTypeId=etid, timezone="America/New_York")
        """One call returning every event in the league AND its main-line
        markets. Per-event calls are then only needed for props."""
        page = LEAGUE_PAGES.get(sport_key)
        if not page:
            return {}
        return self._get("content-managed-page", page="CUSTOM", customPageId=page)

    def list_events(self, sport_key: str, data: dict | None = None) -> list[tuple[str, str, datetime]]:
        data = data if data is not None else self.league_page(sport_key)
        out = []
        for eid, ev in ((data.get("attachments") or {}).get("events") or {}).items():
            name, open_date = ev.get("name") or "", ev.get("openDate") or ""
            if " @ " not in name or open_date.startswith("2099"):
                continue          # "MLB Player Markets" and similar containers
            out.append((str(eid), name, _ts(open_date)))
        return out

    def event_markets(self, event_id: str, tab: str = "popular") -> dict:
        return self._get("event-page", eventId=event_id, tab=tab)

    def ingest_event(self, board: Board, payload: dict, sport_key: str,
                     strict_match: bool = True) -> dict:
        stats = {"markets": 0, "quotes": 0, "unmapped": set(), "unmatched": 0}
        att = payload.get("attachments") or {}
        events, markets = att.get("events") or {}, att.get("markets") or {}
        now = datetime.now(timezone.utc)

        targets: dict[str, EventMeta] = {}
        for eid, ev in events.items():
            name = ev.get("name") or ""
            pair = split_fixture(name)
            if pair is not None:
                away, home = [re.sub(r"\s*\([^)]*\)", "", p).strip() for p in pair]
                t = match_event(board, home, away, _ts(ev.get("openDate")), sport_key)
                if t is None:
                    stats["unmatched"] += 1
                    if strict_match:
                        continue
                    t = EventMeta(f"fd:{eid}", sport_key, sport_key,
                                  _ts(ev.get("openDate")), home, away)
            else:
                # Golf and other non-team sports: the event IS the tournament,
                # so there is no "away @ home" to split and nothing for
                # match_event to key on. Requiring that shape dropped every
                # golf market on the floor -- 37 two-way head-to-heads among
                # them. Such an event can only be created, never matched onto
                # an existing one, so it is skipped under strict_match.
                if strict_match:
                    continue
                if sport_key.startswith("golf"):
                    # collapse to one event per tour: the pairing is the join
                    # key, and the books do not agree on what an event is
                    t = field_event(sport_key, _ts(ev.get("openDate")), name.strip())
                else:
                    t = EventMeta(f"fd:{eid}", sport_key, sport_key,
                                  _ts(ev.get("openDate")), name.strip(), None)
            targets[str(eid)] = t
        if not targets:
            return stats

        for m in markets.values():
            target = targets.get(str(m.get("eventId")))
            if target is None or m.get("marketStatus") not in (None, "OPEN"):
                continue
            stats["markets"] += 1
            hit = classify(m.get("marketType") or "")
            if hit is None:
                stats["unmapped"].add(m.get("marketType"))
                continue
            mkey, fixed_line = hit

            # Golf head-to-heads are all one market key on one event, so
            # without a subject every pairing in the field collapses into a
            # single group -- 14 two-balls became one group of 28 "sides".
            # The subject is the pairing itself, built from the runner names
            # sorted, so it is derived from the players rather than from the
            # market label: FanDuel writes "2 Ball (Round 3) - Smalley / T.
            # Kim" where another book will write it differently, and the pair
            # of full names is the part both agree on.
            golf_subject = None
            if mkey.startswith("golf_"):
                names = sorted(slug(r.get("runnerName") or "")
                               for r in (m.get("runners") or [])
                               if r.get("runnerName"))
                if len(names) != 2:
                    stats["unmapped"].add(f"{m.get('marketType')} ({len(names)} runners)")
                    continue          # not a head-to-head; a field cannot be paired
                golf_subject = "|".join(names)

            for r in m.get("runners") or []:
                if r.get("runnerStatus") not in (None, "ACTIVE"):
                    continue
                price = _decimal(r)
                if price is None:
                    continue
                name = (r.get("runnerName") or "").strip()
                handicap = r.get("handicap")
                # deliberately NOT used to route: see the market-key branch below
                is_player = bool(r.get("isPlayerSelection"))

                if golf_subject is not None:          # golf head-to-head
                    side, subject, point = slug(name), golf_subject, None
                elif fixed_line is not None:          # threshold prop
                    side, subject, point = "over", name, fixed_line
                elif mkey.startswith(PLAYER_MARKETS):
                    # Route on the MARKET, never on isPlayerSelection. In a
                    # sport played by individuals the runners of an ordinary
                    # moneyline are people too, and FanDuel flags them -- so
                    # keying on the flag sent every tennis h2h into the prop
                    # parser, which found no Over/Under and no ladder rung and
                    # dropped it. 140 of 144 markets, silently. The market key
                    # is what actually says whether a line is a prop.
                    parsed = parse_player_runner(name, handicap)
                    if parsed is None:
                        continue
                    side, subject, point = parsed
                else:                                  # game market
                    from .normalize import normalize_outcome
                    label, line = name, handicap
                    ou = OU_NAME_RE.match(name)
                    tl = TEAM_LINE_RE.match(name)
                    if ou:
                        label, line = ou.group(1), float(ou.group(2))
                    elif tl:
                        label, line = tl.group(1), float(tl.group(2))
                    norm = normalize_outcome(mkey, label, line, None,
                                             target.home_team, target.away_team)
                    if norm is None:
                        continue
                    side, subject, point = norm

                board.group(GroupKey(target.event_id, mkey, subject, point),
                            target).add(Quote(book=BOOK, side=side, decimal=price,
                                              point=point, last_update=now))
                stats["quotes"] += 1
        return stats
