"""DraftKings 'sportscontent' endpoint — per-event markets including props.

    /sites/US-CT-SB/api/sportscontent/controldata/event/eventSubcategory/v1/markets
      ?templateVars=<eventId>,<subCategoryId>
      &marketsQuery=$filter=eventId eq '<eventId>' AND
                    clientMetadata/subCategoryId eq '<subCategoryId>' AND ...
      &entity=markets

`US-CT-SB` is the Connecticut skin and `x-pe-loc: US-CT` matches it. No
cookies. The host geo/bot-blocks datacenter IPs, so captures must come from a
browser in-state.

The ids appear TWICE — in `templateVars` and again inside the OData filter.
Rewriting only one produces a request whose filter disagrees with its template
vars, so retargeting must rewrite both together.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from .models import Board, GroupKey, Quote
from .normalize import normalize_outcome
from .marketmap import canonical_market, split_player
from .matching import match_event

log = logging.getLogger("arb.dk_nash")

EVENT_ID_RE = re.compile(r"(eventId\s+eq\s+')([^']+)(')")
SUBCAT_RE = re.compile(r"(subCategoryId\s+eq\s+')([^']+)(')")
# the whole "AND clientMetadata/subCategoryId eq 'NNN'" clause, so it can be dropped
SUBCAT_CLAUSE_RE = re.compile(
    r"\s+AND\s+clientMetadata/subCategoryId\s+eq\s+'[^']*'", re.IGNORECASE)


def retarget(url: str, event_id: str | int | None = None,
             subcategory_id: str | int | None = None) -> str:
    """Point a captured request at a different event/subcategory.

    Rewrites `templateVars` and the OData `$filter` in step. Leaving them
    inconsistent is the failure mode this exists to prevent.
    """
    u = urlparse(url)
    pairs = parse_qsl(u.query, keep_blank_values=True)
    out: list[tuple[str, str]] = []
    for k, v in pairs:
        if k == "templateVars":
            parts = v.split(",")
            if event_id is not None and parts:
                parts[0] = str(event_id)
            if subcategory_id is not None and len(parts) > 1:
                parts[1] = str(subcategory_id)
            v = ",".join(parts)
        elif k == "marketsQuery":
            if event_id is not None:
                v = EVENT_ID_RE.sub(lambda m: f"{m.group(1)}{event_id}{m.group(3)}", v)
            if subcategory_id is not None:
                v = SUBCAT_RE.sub(lambda m: f"{m.group(1)}{subcategory_id}{m.group(3)}", v)
        out.append((k, v))
    # the browser sends %20, not '+'; match it for a picky endpoint
    return urlunparse(u._replace(query=urlencode(out, quote_via=quote)))


def all_markets_url(url: str, event_id: str | int) -> str:
    """Build an every-market-on-the-event query by dropping the subcategory.

    WARNING: this endpoint REJECTS it with {"errorStatus":{"code":"MRKTBFF-400"}}.
    `eventSubcategory/v1/markets` requires the subCategoryId clause -- the name
    is the clue. Kept because the query form is right for any sibling endpoint
    that does accept a bare event filter; do not point it at this one.

    Use harvest_template_vars() + retarget() instead: subCategoryId values are
    per-event (17320 returns props on one game and an empty array on another),
    so they must be discovered rather than assumed.
    """
    out = retarget(url, event_id=event_id)
    u = urlparse(out)
    pairs = []
    for k, v in parse_qsl(u.query, keep_blank_values=True):
        if k == "marketsQuery":
            v = SUBCAT_CLAUSE_RE.sub("", v)
        elif k == "templateVars":
            v = str(event_id)
        pairs.append((k, v))
    return urlunparse(u._replace(query=urlencode(pairs, quote_via=quote)))


def harvest_template_vars(urls) -> list[tuple[str, str]]:
    """Pull every (eventId, subCategoryId) pair out of URLs the page already used.

    The browser has, by loading the event page and its prop tabs, already made
    a request per subcategory. Reading the ids back off those URLs discovers
    them exactly, instead of guessing ids that differ per event.
    """
    seen: list[tuple[str, str]] = []
    for u in urls:
        if "eventSubcategory" not in u:
            continue
        ev, sub = ids_in(u)
        if ev and sub and (ev, sub) not in seen:
            seen.append((ev, sub))
    return seen


def ids_in(url: str) -> tuple[str | None, str | None]:
    """The (eventId, subCategoryId) a URL currently targets, or None."""
    q = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
    tv = (q.get("templateVars") or "").split(",")
    return (tv[0] or None) if tv else None, (tv[1] or None) if len(tv) > 1 else None


def is_consistent(url: str) -> bool:
    """True when templateVars and the OData filter name the same ids."""
    q = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
    ev, sub = ids_in(url)
    query = q.get("marketsQuery") or ""
    m_ev, m_sub = EVENT_ID_RE.search(query), SUBCAT_RE.search(query)
    if m_ev and ev and m_ev.group(2) != ev:
        return False
    if m_sub and sub and m_sub.group(2) != sub:
        return False
    return True


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
def looks_like_sportscontent(payload) -> bool:
    return (isinstance(payload, dict)
            and isinstance(payload.get("markets"), list)
            and isinstance(payload.get("selections"), list))


def echoed_query(payload: dict) -> str | None:
    """The filter the server actually ran, from `subscriptionPartials`.

    An empty `markets` array with a populated echo means the filter was valid
    but matched nothing -- usually a subCategoryId that does not exist on this
    event, rather than a malformed request.
    """
    for part in (payload.get("subscriptionPartials") or {}).values():
        if isinstance(part, dict) and part.get("query"):
            return part["query"]
    return None


# DraftKings renders negative American odds with a UNICODE MINUS (U+2212),
# not an ASCII hyphen: "\u2212215". float() raises on it, so any american-odds
# fallback silently dies unless it is normalised first.
UNICODE_MINUSES = {"\u2212": "-", "\u2013": "-", "\u2014": "-", "\u2796": "-"}


def parse_american(text) -> float | None:
    s = str(text).strip()
    for bad, good in UNICODE_MINUSES.items():
        s = s.replace(bad, good)
    s = s.replace("+", "")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _decimal(sel: dict) -> float | None:
    # trueOdds is already a float; the decimal string is a formatted duplicate
    true = sel.get("trueOdds")
    if isinstance(true, (int, float)) and true > 1.0:
        return float(true)
    odds = sel.get("displayOdds") or {}
    for key in ("decimal", "Decimal"):
        if odds.get(key):
            try:
                return float(odds[key])
            except (TypeError, ValueError):
                pass
    american = parse_american(odds.get("american") or odds.get("American") or "")
    if american:
        from . import oddsmath as om
        try:
            return om.american_to_decimal(american)
        except ValueError:
            return None
    return None


def milestone_point(sel: dict) -> float | None:
    """A milestone selection labelled "2+" is Over 1.5.

    DraftKings prices these props as thresholds, not two-sided lines: labels
    read 1+/2+/3+ with a `milestoneValue`. Left alone they never meet a book's
    Over/Under, so N is converted to Over (N - 0.5) -- which is the same bet
    and pairs directly against the aggregator feed and Fanatics Markets.
    """
    n = sel.get("milestoneValue")
    if isinstance(n, (int, float)) and n >= 1:
        return float(n) - 0.5
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*\+\s*$", str(sel.get("label") or ""))
    return float(m.group(1)) - 0.5 if m else None


def ingest_sportscontent(board: Board, payload: dict, book: str = "draftkings",
                         sport_key: str | None = None, strict_match: bool = True,
                         event: object | None = None) -> dict:
    """Join `selections` to `markets` on marketId and fold onto the board.

    Shape is inferred from DraftKings' newer sportscontent API: two parallel
    top-level arrays rather than nested offers. `arb scrape inspect` reports
    what actually parsed, so a mismatch surfaces immediately.
    """
    stats = {"markets": 0, "selections": 0, "quotes": 0,
             "markets_unmapped": set(), "unmatched": 0}
    markets = {str(m.get("id")): m for m in payload.get("markets") or []}
    stats["markets"] = len(markets)
    now = datetime.now(timezone.utc)

    target = event
    if target is None:
        if strict_match:
            stats["unmatched"] = 1
            return stats
        return stats

    by_market: dict[str, list[dict]] = {}
    for sel in payload.get("selections") or []:
        by_market.setdefault(str(sel.get("marketId")), []).append(sel)
    stats["selections"] = sum(len(v) for v in by_market.values())

    for mid, sels in by_market.items():
        market = markets.get(mid)
        if not market:
            continue
        # Prefer the structured fields over parsing the display name.
        # "Yordan Alvarez Home Runs" has no separator for split_player to find,
        # and "Luis Garcia (NYY) Hits" would lose the team suffix. marketType
        # and participants carry both cleanly.
        label = (market.get("marketType") or {}).get("name") or market.get("name") or ""
        _, label_player = split_player(market.get("name") or "")
        mkey = canonical_market(label, player=label_player)
        if mkey is None:
            stats["markets_unmapped"].add(label)
            continue

        for sel in sels:
            price = _decimal(sel)
            if price is None or price <= 1.0:
                continue
            participants = sel.get("participants") or []
            player = (participants[0].get("name") if participants else None) or label_player
            point = sel.get("points")
            if point is not None:
                try:
                    point = float(point)
                except (TypeError, ValueError):
                    point = None

            label_text = sel.get("label") or ""
            ms = milestone_point(sel)
            if ms is not None:
                side, subject, gpoint = "over", player, ms
            else:
                norm = normalize_outcome(mkey, label_text, point, player,
                                         target.home_team, target.away_team)
                if norm is None:
                    continue
                side, subject, gpoint = norm
                point = point if point is not None else gpoint
            board.group(GroupKey(target.event_id, mkey, subject, gpoint), target).add(
                Quote(book=book, side=side, decimal=price, point=gpoint, last_update=now))
            stats["quotes"] += 1
    return stats
