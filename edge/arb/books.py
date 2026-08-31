"""Parsers for the JSON shapes these books' own front-ends consume.

Fanatics runs on a licensed copy of the PointsBet platform (acquired 2024), so
its payloads are expected to carry PointsBet's `fixedOddsMarkets` shape. That
is a strong hypothesis, not a verified fact -- `detect_shape` decides from the
payload itself and `summarize` shows you what was actually found, so a capture
that disagrees is visible immediately rather than silently mis-parsed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .models import Board, EventMeta, GroupKey, Quote
from .normalize import normalize_outcome
from .marketmap import canonical_market, split_player
from .matching import match_event

# Canonical keys whose subject is a person. Shared with fanduel.py, which
# writes its prop runners the same four ways Oddschecker does.
PLAYER_MARKETS = ("pitcher_", "batter_", "player_")

log = logging.getLogger("arb.scrape.books")


def _ts(value) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, (int, float)):
        v = float(value)
        return datetime.fromtimestamp(v / 1000.0 if v > 1e11 else v, timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def detect_shape(payload) -> str:
    """Name the payload shape from its own structure."""
    if isinstance(payload, list):
        return detect_shape(payload[0]) if payload else "empty"
    if not isinstance(payload, dict):
        return "unknown"
    if "subevents" in payload:
        return "oddschecker_subevent_group"
    if "fixedOddsMarkets" in payload:
        return "pointsbet_event"
    if "events" in payload and isinstance(payload.get("events"), list) and payload["events"] \
            and "fixedOddsMarkets" in (payload["events"][0] or {}):
        return "pointsbet_list"
    if "subscriptionPartials" in payload or (
            isinstance(payload.get("markets"), list)
            and isinstance(payload.get("selections"), list)):
        return "draftkings_sportscontent"
    if "eventCategories" in payload:
        return "draftkings_v3_event"
    if "eventGroup" in payload:
        return "draftkings_v5_eventgroup"
    if "selections" in payload or "markets" in payload:
        return "generic_markets"
    if "attachments" in payload or "marketType" in str(payload)[:400]:
        return "fanduel_like"
    return "unknown"


# --------------------------------------------------------------------------
# PointsBet / Fanatics
# --------------------------------------------------------------------------
def _pointsbet_events(payload) -> list[dict]:
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict) and "fixedOddsMarkets" in e]
    if "fixedOddsMarkets" in payload:
        return [payload]
    return [e for e in (payload.get("events") or []) if "fixedOddsMarkets" in (e or {})]


def ingest_pointsbet(board: Board, payload, book: str = "fanatics",
                     sport_key: str | None = None, strict_match: bool = True) -> dict:
    """Fold a PointsBet-shaped payload into the board.

    strict_match: only attach to an event already on the board. Scraped prices
    are only useful next to the API's DK/FD prices, and an unmatched event
    cannot be compared against anything, so it is dropped and counted.
    """
    stats = {"events": 0, "matched": 0, "unmatched": 0, "quotes": 0,
             "markets_seen": set(), "markets_unmapped": set()}

    for ev in _pointsbet_events(payload):
        stats["events"] += 1
        home = ev.get("homeTeam") or ev.get("homeTeamName")
        away = ev.get("awayTeam") or ev.get("awayTeamName")
        if not home and ev.get("name") and " v " in ev["name"]:
            away, home = [p.strip() for p in ev["name"].split(" v ", 1)]
        commence = _ts(ev.get("startsAt") or ev.get("startTime") or ev.get("eventTime"))

        target = match_event(board, home or "", away or "", commence, sport_key)
        if target is None:
            stats["unmatched"] += 1
            if strict_match:
                continue
            target = EventMeta(
                event_id=f"{book}:{ev.get('key') or ev.get('id')}",
                sport_key=sport_key or "unknown", sport_title=sport_key or "unknown",
                commence_time=commence, home_team=home, away_team=away)
        else:
            stats["matched"] += 1

        for market in ev.get("fixedOddsMarkets") or []:
            if market.get("isOpenForBetting") is False:
                continue
            label = market.get("eventName") or market.get("name") or ""
            group = market.get("groupName") or ""
            stats["markets_seen"].add(f"{group} | {label}".strip(" |"))

            outcomes = [o for o in (market.get("outcomes") or [])
                        if not o.get("isHidden") and o.get("isOpenForBetting") is not False]
            if not outcomes:
                continue

            # a player name may sit on the market label or on each outcome
            _, label_player = split_player(label)
            mkey = canonical_market(label, group, player=label_player)
            if mkey is None:
                stats["markets_unmapped"].add(f"{group} | {label}".strip(" |"))
                continue

            last_update = _ts(market.get("lastUpdated") or ev.get("lastUpdated"))
            for oc in outcomes:
                price = oc.get("price") or oc.get("odds")
                if price is None or float(price) <= 1.0:
                    continue
                oc_name = oc.get("name") or ""
                player = label_player
                if player is None:
                    _, player = split_player(oc_name)
                point = oc.get("points")
                if point is None:
                    point = oc.get("line")

                norm = normalize_outcome(mkey, oc_name, point, player,
                                         target.home_team, target.away_team)
                if norm is None:
                    continue
                side, subject, gpoint = norm
                key = GroupKey(target.event_id, mkey, subject, gpoint)
                board.group(key, target).add(Quote(
                    book=book, side=side, decimal=float(price), point=point,
                    last_update=last_update,
                ))
                stats["quotes"] += 1
    return stats


# --------------------------------------------------------------------------
# Oddschecker CX  (what betfanatics.com's own front-end calls)
# --------------------------------------------------------------------------
# Oddschecker's short code for each book. The Fanatics site's api-key is
# entitled to FNP only, so that is all one key returns.
BOOKMAKER_CODES = {
    "FNP": "fanatics", "DK": "draftkings", "FD": "fanduel",
    "BM": "betmgm", "CZ": "williamhill_us",
}


def ingest_oddschecker(board: Board, payload, book: str | None = None,
                       sport_key: str | None = None, strict_match: bool = True,
                       include_live: bool = False) -> dict:
    """Fold an Oddschecker `subevent-group` payload into the board.

    include_live: OFF by default. In-running markets come back with lines that
    do not reconcile -- a live MLB game returned "Total Runs" at both 7 and 4.5
    priced identically, under different marketIds, almost certainly a full-game
    and a partial-game market sharing one label. Nothing in the payload names
    the period, so a 4.5 would be paired against a full-game 4.5 elsewhere and
    invent an arbitrage. Pregame data is internally consistent; live is not
    trustworthy without a period field.
    """
    stats = {"events": 0, "matched": 0, "unmatched": 0, "quotes": 0, "live_skipped": 0,
             "flat_ladders": 0, "markets_seen": set(), "markets_unmapped": set()}

    from .fanduel import parse_player_runner

    for sv in payload.get("subevents") or []:
        stats["events"] += 1
        if sv.get("inRunning") and not include_live:
            stats["live_skipped"] += 1
            continue

        home = (sv.get("homeTeam") or {}).get("name")
        away = (sv.get("awayTeam") or {}).get("name")
        commence = _ts(sv.get("startTime"))
        target = match_event(board, home or "", away or "", commence, sport_key)
        if target is None:
            stats["unmatched"] += 1
            if strict_match:
                continue
            target = EventMeta(
                event_id=f"oc:{sv.get('id')}", sport_key=sport_key or "unknown",
                sport_title=sv.get("eventName") or sport_key or "unknown",
                commence_time=commence, home_team=home, away_team=away)
        else:
            stats["matched"] += 1

        for market in sv.get("markets") or []:
            label = market.get("name") or ""
            stats["markets_seen"].add(label)
            mkey = canonical_market(label)
            if mkey is None:
                stats["markets_unmapped"].add(label)
                continue

            # A LADDER THAT DOES NOT REPRICE IS NOT A LADDER. Oddschecker
            # returns several lines for a market and, on a large minority of
            # NCAAF events, the SAME price on every one of them: Merrimack at
            # Delaware came back with the spread at 23.5, 25, 26 and 26.5 and
            # -110 on both sides of all four. That is arithmetically
            # impossible -- P(cover -23.5) is not P(cover -26.5) -- so the
            # rungs carry no line-specific information, and there is no way to
            # tell which of them (if any) is the real one.
            #
            # Left in, they manufactured four "free middles" on that one game:
            # a -110 attached to the wrong number beat DraftKings' genuine
            # +27.5 and the pair summed under 1.00. Dropped rather than
            # guessed at, the same way an unrecognised market is.
            # Keyed on the ABSOLUTE line, because a spread names its two sides
            # "+23.5" and "-23.5" -- counting those as two rungs would make a
            # two-line ladder look like four.
            rungs: dict = {}
            for bet in market.get("bets") or []:
                line_name = (bet.get("line") or {}).get("name")
                try:
                    rung = abs(float(line_name))
                except (TypeError, ValueError):
                    rung = line_name
                for odd in bet.get("odds") or []:
                    if odd.get("status") == "ACTIVE" and odd.get("decimal"):
                        rungs.setdefault(rung, set()).add(
                            round(float(odd["decimal"]), 4))
            if len(rungs) >= 3:
                distinct = {v for prices in rungs.values() for v in prices}
                if len(distinct) <= 2:
                    stats["markets_unmapped"].add(
                        f"{label} (flat ladder: {len(rungs)} lines, "
                        f"{len(distinct)} price(s))")
                    stats["flat_ladders"] = stats.get("flat_ladders", 0) + 1
                    continue

            for bet in market.get("bets") or []:
                raw_line = (bet.get("line") or {}).get("name")
                try:
                    point = float(raw_line) if raw_line not in (None, "") else None
                except (TypeError, ValueError):
                    point = None
                oc_name = bet.get("name") or ""

                for o in bet.get("odds") or []:
                    if o.get("status") != "ACTIVE":
                        continue
                    price = o.get("decimal")
                    if price is None or float(price) <= 1.0:
                        continue
                    resolved = book or BOOKMAKER_CODES.get(o.get("bookmakerCode"))
                    if not resolved:
                        continue
                    if mkey.startswith(PLAYER_MARKETS):
                        # The player is inside the bet name -- "Drake Maye
                        # Over" at line 220.5 -- and there is no `description`
                        # field to carry it. Left to normalize_outcome the
                        # subject is None, so every quarterback's passing line
                        # lands in ONE group keyed only by the yardage: the
                        # same collapse that put 106 prop groups on `totals`.
                        #
                        # A market whose bets are bare names is a field, not a
                        # two-sided line -- "Anytime Touchdown Scorer" lists
                        # twenty players and no opposite side -- and the parser
                        # returns None for those, so they are dropped rather
                        # than folded into one group of twenty "sides".
                        parsed = parse_player_runner(oc_name, point)
                        if parsed is None:
                            stats["markets_unmapped"].add(f"{label} (no player)")
                            continue
                        side, subject, gpoint = parsed
                    else:
                        norm = normalize_outcome(mkey, oc_name, point, None,
                                                 target.home_team, target.away_team)
                        if norm is None:
                            continue
                        side, subject, gpoint = norm
                    board.group(GroupKey(target.event_id, mkey, subject, gpoint),
                                target).add(Quote(
                        book=resolved, side=side, decimal=float(price), point=point,
                        last_update=datetime.now(timezone.utc),
                    ))
                    stats["quotes"] += 1
    return stats


# --------------------------------------------------------------------------
# DraftKings
# --------------------------------------------------------------------------
def _dk_offers(payload) -> list[tuple[str, dict]]:
    """Yield (subcategory, market) from either DK shape."""
    out = []
    for cat in payload.get("eventCategories") or []:
        for comp in cat.get("componentizedOffers") or []:
            sub = comp.get("subcategoryName") or cat.get("name") or ""
            for block in comp.get("offers") or []:
                for market in (block or []):
                    out.append((sub, market))
    group = payload.get("eventGroup") or {}
    for cat in group.get("offerCategories") or []:
        for desc in cat.get("offerSubcategoryDescriptors") or []:
            sub = desc.get("name") or cat.get("name") or ""
            for block in ((desc.get("offerSubcategory") or {}).get("offers") or []):
                for market in (block or []):
                    out.append((sub, market))
    return out


def ingest_draftkings(board: Board, payload, book: str = "draftkings",
                      sport_key: str | None = None, strict_match: bool = True) -> dict:
    stats = {"events": 0, "matched": 0, "unmatched": 0, "quotes": 0,
             "markets_seen": set(), "markets_unmapped": set()}

    meta = payload.get("event") or {}
    group = payload.get("eventGroup") or {}
    events_by_id: dict[str, dict] = {}
    if meta:
        events_by_id[str(meta.get("eventId") or meta.get("id") or "single")] = meta
    for e in group.get("events") or []:
        events_by_id[str(e.get("eventId") or e.get("id"))] = e

    resolved: dict[str, EventMeta] = {}
    for eid, e in events_by_id.items():
        stats["events"] += 1
        teams = e.get("teamName1"), e.get("teamName2")
        home = e.get("homeTeamName") or teams[1]
        away = e.get("awayTeamName") or teams[0]
        commence = _ts(e.get("startDate") or e.get("startTime"))
        target = match_event(board, home or "", away or "", commence, sport_key)
        if target is None:
            stats["unmatched"] += 1
            if strict_match:
                continue
            target = EventMeta(f"{book}:{eid}", sport_key or "unknown",
                               sport_key or "unknown", commence, home, away)
        else:
            stats["matched"] += 1
        resolved[eid] = target

    if not resolved:
        return stats
    default_target = next(iter(resolved.values()))

    for sub, market in _dk_offers(payload):
        if market.get("isSuspended") or market.get("isOpen") is False:
            continue
        label = market.get("label") or ""
        stats["markets_seen"].add(f"{sub} | {label}".strip(" |"))
        eid = str(market.get("eventId") or "")
        target = resolved.get(eid, default_target)

        _, label_player = split_player(label)
        mkey = canonical_market(label, sub, player=label_player)
        if mkey is None:
            stats["markets_unmapped"].add(f"{sub} | {label}".strip(" |"))
            continue

        for oc in market.get("outcomes") or []:
            if oc.get("hidden"):
                continue
            price = oc.get("oddsDecimal")
            if price is None or float(price) <= 1.0:
                continue
            oc_name = oc.get("label") or ""
            player = label_player or oc.get("participant")
            norm = normalize_outcome(mkey, oc_name, oc.get("line"), player,
                                     target.home_team, target.away_team)
            if norm is None:
                continue
            side, subject, gpoint = norm
            board.group(GroupKey(target.event_id, mkey, subject, gpoint), target).add(Quote(
                book=book, side=side, decimal=float(price), point=oc.get("line"),
                last_update=datetime.now(timezone.utc),
            ))
            stats["quotes"] += 1
    return stats


INGESTERS = {
    "oddschecker_subevent_group": ingest_oddschecker,
    "pointsbet_event": ingest_pointsbet,
    "pointsbet_list": ingest_pointsbet,
    "draftkings_v3_event": ingest_draftkings,
    "draftkings_v5_eventgroup": ingest_draftkings,
}


def ingest(board: Board, payload, book: str, sport_key: str | None = None,
           strict_match: bool = True) -> dict:
    shape = detect_shape(payload)
    fn = INGESTERS.get(shape)
    if fn is None:
        raise ValueError(
            f"unrecognised payload shape {shape!r}. Run `arb scrape inspect <file>` "
            "to see its structure, then extend arb/providers/scrape/books.py")
    stats = fn(board, payload, book=book, sport_key=sport_key, strict_match=strict_match)
    stats["shape"] = shape
    return stats


def summarize(payload) -> dict:
    """What is in this capture, without needing it to parse cleanly."""
    shape = detect_shape(payload)
    info: dict = {"shape": shape, "markets": [], "sample_outcomes": []}
    if shape == "draftkings_sportscontent":
        markets = payload.get("markets") or []
        sels = payload.get("selections") or []
        by_market: dict[str, int] = {}
        for sel in sels:
            by_market[str(sel.get("marketId"))] = by_market.get(str(sel.get("marketId")), 0) + 1
        info["events"] = len({str(m.get("eventId")) for m in markets if m.get("eventId")})
        info["selection_count"] = len(sels)
        for m in markets[:60]:
            label = m.get("name") or ""
            _, pl = split_player(label)
            info["markets"].append({
                "group": str(m.get("clientMetadata", {}).get("subCategoryName")
                             or m.get("marketType", {}).get("name") or ""),
                "label": label,
                "maps_to": canonical_market(label, player=pl),
                "outcomes": by_market.get(str(m.get("id")), 0),
            })
        for sel in sels[:4]:
            odds = sel.get("displayOdds") or {}
            info["sample_outcomes"].append({
                "name": sel.get("label"), "points": sel.get("points"),
                "price": odds.get("decimal") or odds.get("american"),
                "participant": (sel.get("participants") or [{}])[0].get("name"),
            })
        # surface the top-level keys so an unexpected shape is visible at a glance
        info["top_level_keys"] = sorted(payload)[:12]
    elif shape == "oddschecker_subevent_group":
        subs = payload.get("subevents") or []
        info["events"] = len(subs)
        info["live"] = sum(1 for s_ in subs if s_.get("inRunning"))
        codes = set()
        for sv in subs[:6]:
            info.setdefault("event_names", []).append(sv.get("name"))
            for mk in sv.get("markets") or []:
                label = mk.get("name") or ""
                info["markets"].append({
                    "group": f"betTypeId={mk.get('betTypeId')}", "label": label,
                    "maps_to": canonical_market(label),
                    "outcomes": len(mk.get("bets") or []),
                })
                for bet in (mk.get("bets") or [])[:2]:
                    for o in (bet.get("odds") or [])[:1]:
                        codes.add(o.get("bookmakerCode"))
                        info["sample_outcomes"].append({
                            "name": bet.get("name"),
                            "points": (bet.get("line") or {}).get("name"),
                            "price": o.get("decimal"), "book": o.get("bookmakerCode")})
        info["bookmaker_codes"] = sorted(c for c in codes if c)
        # dedupe repeated market labels across events
        seen, uniq = set(), []
        for m in info["markets"]:
            if m["label"] in seen:
                continue
            seen.add(m["label"]); uniq.append(m)
        info["markets"] = uniq
    elif shape.startswith("pointsbet"):
        evs = _pointsbet_events(payload)
        info["events"] = len(evs)
        for ev in evs[:3]:
            info.setdefault("event_names", []).append(ev.get("name"))
            for m in (ev.get("fixedOddsMarkets") or [])[:60]:
                label = m.get("eventName") or m.get("name") or ""
                grp = m.get("groupName") or ""
                _, pl = split_player(label)
                info["markets"].append({
                    "group": grp, "label": label,
                    "maps_to": canonical_market(label, grp, player=pl),
                    "outcomes": len(m.get("outcomes") or []),
                })
                for o in (m.get("outcomes") or [])[:2]:
                    info["sample_outcomes"].append(
                        {"name": o.get("name"), "price": o.get("price"), "points": o.get("points")})
    elif shape.startswith("draftkings"):
        offers = _dk_offers(payload)
        info["events"] = len(payload.get("eventGroup", {}).get("events", [])) or 1
        for sub, m in offers[:60]:
            label = m.get("label") or ""
            _, pl = split_player(label)
            info["markets"].append({
                "group": sub, "label": label,
                "maps_to": canonical_market(label, sub, player=pl),
                "outcomes": len(m.get("outcomes") or []),
            })
            for o in (m.get("outcomes") or [])[:2]:
                info["sample_outcomes"].append(
                    {"name": o.get("label"), "price": o.get("oddsDecimal"), "points": o.get("line")})
    return info
