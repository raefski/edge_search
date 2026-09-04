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
from .normalize import is_spread_market, normalize_outcome, pick_team_side
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


# How far a rung's price may sit from the feed's own model before it is not a
# price. 10pp above the market's OWN median residual: on a sound ladder the
# residuals cluster inside a few points of each other, and a phantom sat six
# times the median out.
AI_RESIDUAL_MARGIN = 0.10
_MIN_AI_RUNGS = 3


# How far a run of identical prices may span before it is not a ladder.
STALLED_RUN_LINES = 2.0


# How far this ladder's OWN implied centre may sit from another book's ALREADY
# on the board for the same event and market before the whole ladder is not
# trusted. Reasoned, not measured the way STALLED_RUN_LINES was against
# 11,006 real rungs -- a proper calibration needs a broad live cross-book
# sample this session did not have time to gather cleanly. Set with a wide
# safety margin over the ~0.4pp median / ~1.3pp p90 cross-book PRICE agreement
# already measured on matched rungs elsewhere in this file: real books
# occasionally shade a line by a point or two, essentially never by six.
# `offset_ladders` is counted so this can be tightened, loosened, or replaced
# once real scans accumulate evidence either way.
LADDER_CENTER_DRIFT_MAX = 6.0


def _ladder_center(rungs: dict) -> float | None:
    """The point where this ladder's OWN devigged PRICE crosses pick'em
    (50/50) -- the feed's implied read of the market's true number, from the
    price alone.

    Deliberately not `aiProbability`: on a ladder wide enough off that this
    check exists to catch, the model is not a clean reference either -- Long
    Island at Kansas's spread carried a 10pp median residual between its own
    aiProbability and its own price, an offset the same order as the defect.
    Interpolates between the two rungs straddling 50%, so it is not limited to
    landing exactly on a priced rung.
    """
    priced = []
    for rung, prices in rungs.items():
        if len(prices) != 2 or not isinstance(rung, (int, float)):
            continue
        (_na, (da, _ai_a)), (_nb, (db, _ai_b)) = sorted(prices.items())
        total = 1.0 / da + 1.0 / db
        if total <= 0:
            continue
        priced.append((rung, (1.0 / da) / total))
    priced.sort()
    for (r0, p0), (r1, p1) in zip(priced, priced[1:]):
        if (p0 - 0.5) * (p1 - 0.5) <= 0 and p1 != p0:
            frac = (0.5 - p0) / (p1 - p0)
            return r0 + frac * (r1 - r0)
    return None      # this ladder never crosses 50/50 in the fetched range


def _other_book_points(board: Board, event_id: str, market: str,
                       exclude_book: str) -> list[float]:
    """Points already on the board for this event+market from books OTHER
    than the one being ingested -- independently sourced ground truth to
    check a ladder's own centre against, rather than trusting one feed's
    internal consistency alone. FanDuel and DraftKings are always ingested
    first (see run.py), so their main and alternate lines are already here
    by the time Fanatics is."""
    return [k.point for k, g in board.groups.items()
            if k.event_id == event_id and k.market == market
            and k.point is not None
            and set(g.book_sides) - {exclude_book}]


def _stalled_runs(rungs: dict) -> set:
    """Rungs inside a run of consecutive lines carrying one identical price.

    Only two-sided numeric rungs are considered, and only runs spanning
    STALLED_RUN_LINES or more -- two adjacent half-points at one price is just
    the American odds grid, and there are ~1,800 of those to every 7 of these.
    """
    priced = {}
    for rung, prices in rungs.items():
        if len(prices) != 2 or not isinstance(rung, (int, float)):
            continue
        # Ordered by SIDE, not sorted by value. A European handicap's two
        # directions mirror each other -- (1.16, 5.00) and (5.00, 1.16) -- so
        # sorting makes them identical and the run check would drop both
        # genuine markets.
        priced[rung] = tuple(prices[name][0] for name in sorted(prices))
    order = sorted(priced)
    out: set = set()
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and priced[order[j + 1]] == priced[order[i]]:
            j += 1
        if j > i and abs(order[j] - order[i]) >= STALLED_RUN_LINES:
            out.update(order[i:j + 1])
        i = j + 1
    return out


def _contradicted_rungs(rungs: dict) -> set:
    """Rungs whose devigged price disagrees with their own `aiProbability`.

    Returns an empty set unless at least `_MIN_AI_RUNGS` two-sided rungs carry
    the field -- a median taken over one or two rungs says nothing, and a
    market with no model attached must not be condemned for lacking one.
    """
    residuals = {}
    for rung, prices in rungs.items():
        if len(prices) != 2:
            continue
        (_na, (da, ai_a)), (_nb, (db, ai_b)) = sorted(prices.items())
        total = 1.0 / da + 1.0 / db
        if total <= 0:
            continue
        pa, pb = (1.0 / da) / total, (1.0 / db) / total
        seen = [abs(p - ai / 100.0)
                for p, ai in ((pa, ai_a), (pb, ai_b))
                if isinstance(ai, (int, float))]
        if seen:
            residuals[rung] = max(seen)
    if len(residuals) < _MIN_AI_RUNGS:
        return set()
    ordered = sorted(residuals.values())
    mid = len(ordered) // 2
    median = (ordered[mid] if len(ordered) % 2
              else (ordered[mid - 1] + ordered[mid]) / 2.0)
    return {rung for rung, r in residuals.items() if r > median + AI_RESIDUAL_MARGIN}


_GENERIC_SIDES = {"HOME": "home", "AWAY": "away", "DRAW": "draw"}


def side_label_of(bet: dict) -> str | None:
    """The side this bet is on, straight from the feed's own `genericName`."""
    return _GENERIC_SIDES.get((bet.get("genericName") or "").strip().upper())


def _labelled_outcome(side: str, market: str, point):
    """(side, subject, group point) for a bet the feed has already labelled.

    A spread folds onto the home line exactly as normalize_outcome does, so
    "Away +1.5" and "Home -1.5" meet in one group. A spread with no line is
    refused for the reason normalize_outcome refuses it: it is not a moneyline.
    """
    if is_spread_market(market):
        if point is None:
            return None
        value = round(float(point), 2)
        return side, None, (value if side == "home" else -value)
    return side, None, None


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
             "flat_ladders": 0, "placeholder_rungs": 0, "contradicted_rungs": 0,
             "bad_overround_rungs": 0, "stalled_rungs": 0, "offset_ladders": 0,
             "markets_seen": set(), "markets_unmapped": set()}

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
            # Fanatics names every prop "Player <stat>", and canonical_market
            # needs to know a player is in play or RULES wins over
            # PLAYER_STATS: "Player Total Bases" matched the bare `total` in
            # the game-totals rule and landed on `totals`, alongside real game
            # totals. Same shape as the PLAYER_A_TOTAL_POINTS bug.
            player_hint = "x" if label.lower().startswith("player ") else None
            mkey = canonical_market(label, player=player_hint)
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
            # THESE LADDER CHECKS ARE FOR GAME LINES ONLY. They assume one
            # subject per market and a line grid fine enough that a price must
            # move between rungs; a player prop has neither. Keyed by line,
            # several PLAYERS pool onto one rung (35% of NFL prop rungs hold
            # two or more), and a yardage ladder legitimately repeats a price
            # across five yards, which the stalled-run check would read as a
            # ladder that had stopped updating. Oddschecker's aiProbability is
            # unreliable there too -- p90 residual 17pp against 4pp on game
            # lines. Props are protected instead by parse_player_runner, which
            # refuses anything it cannot attach to one named player.
            rungs: dict = {}
            placeholder: set = set()
            bets_iter = ([] if mkey.startswith(PLAYER_MARKETS)
                         else (market.get("bets") or []))

            # rung -> {side: (decimal, aiProbability)}, keyed on the line as
            # the HOME side sees it.
            #
            # It keyed on the ABSOLUTE line, because a US spread names its two
            # sides "+23.5" and "-23.5" and counting those separately would
            # make a two-line ladder look like four. But a European handicap
            # ships BOTH DIRECTIONS in one market -- Bournemouth +1.5,
            # Bournemouth -1.5, Brentford +1.5, Brentford -1.5 -- and abs()
            # collapsed all four onto one key, where the second direction
            # overwrote the first and left a same-sign pair whose overround was
            # 0.400. The overround guard then killed both genuine markets.
            #
            # `genericName` gives the side outright, so negating the away line
            # folds each direction onto its own home-axis key.
            for bet in bets_iter:
                line_name = (bet.get("line") or {}).get("name")
                generic = (bet.get("genericName") or "").strip().upper()
                try:
                    value = float(line_name)
                    rung = (value if generic == "HOME"
                            else -value if generic == "AWAY" else abs(value))
                except (TypeError, ValueError):
                    rung = line_name
                ai = bet.get("aiProbability")
                for odd in bet.get("odds") or []:
                    if odd.get("status") == "ACTIVE" and odd.get("decimal"):
                        rungs.setdefault(rung, {})[bet.get("name")] = (
                            round(float(odd["decimal"]), 4), ai)
            if len(rungs) >= 3:
                distinct = {d for prices in rungs.values() for d, _ai in prices.values()}
                if len(distinct) <= 2:
                    stats["markets_unmapped"].add(
                        f"{label} (flat ladder: {len(rungs)} lines, "
                        f"{len(distinct)} price(s))")
                    stats["flat_ladders"] = stats.get("flat_ladders", 0) + 1
                    continue

            # THE WHOLE LADDER, NOT JUST A RUNG WITHIN IT, CAN BE MISPLACED.
            # Rhode Island at Temple's spread crossed pick'em around line 15 --
            # its OWN price says Temple -15 is a coinflip -- while FanDuel had
            # Temple -5.5 and the book's own app showed -7.5/-8. Every rung
            # repriced smoothly; none of the per-rung checks below can see
            # this, because nothing here is internally inconsistent, only
            # externally wrong. Checked against whichever other book already
            # has this event+market (FanDuel and DraftKings are always
            # ingested first), not against this feed's own aiProbability --
            # a whole-ladder offset large enough to move every price can move
            # a model trained alongside it too.
            refs = _other_book_points(board, target.event_id, mkey, book or "")
            if refs:
                center = _ladder_center(rungs)
                if center is not None:
                    drift = min(abs(center - r) for r in refs)
                    if drift > LADDER_CENTER_DRIFT_MAX:
                        stats["markets_unmapped"].add(
                            f"{label} (ladder centres on {center:g}, other "
                            f"book(s) have {sorted(refs)}, drift {drift:.1f})")
                        stats["offset_ladders"] = stats.get("offset_ladders", 0) + 1
                        continue

            # ...and the PARTIAL version of the same thing, which the check
            # above is too blunt to see. North Carolina A&T at Georgia State
            # came back with Total Points at 63.5, 56 and 55.5 all -110/-110
            # plus a real 64.5 at -105/-115. Three distinct prices in the
            # market, so nothing fired, and Over 55.5 at -110 went on the board
            # against a genuine DraftKings Under 56.5 -- reported as a free
            # middle. Over 55.5 and Over 63.5 cannot both be -110; they are
            # eight points apart. The book's own app had the total at 56.5 with
            # the over at -235, a line the feed does not even carry.
            #
            # A real ladder prices at most ONE rung symmetrically -- the main
            # line. Two or more rungs at identical prices on both sides are
            # placeholders, and there is no way to tell which of them (if any)
            # is the real one, so they all go.
            symmetric = {rung for rung, prices in rungs.items()
                         if len(prices) >= 2
                         and len({d for d, _ai in prices.values()}) == 1}
            placeholder = symmetric if len(symmetric) >= 2 else set()
            if placeholder:
                stats["markets_unmapped"].add(
                    f"{label} (placeholder rungs at {sorted(placeholder)})")
                stats["placeholder_rungs"] = (stats.get("placeholder_rungs", 0)
                                              + len(placeholder))

            # A rung whose price contradicts the feed's OWN model.
            #
            # Every bet carries `aiProbability`, which the parser used to throw
            # away. On a sound ladder it tracks the devigged price closely --
            # median 2.4pp on the North Carolina A&T total. On the phantom
            # rungs of that same market it was out by 15-16pp, six times the
            # market's own median, which is a clean separation and the only
            # signal that identifies WHICH rung is wrong rather than
            # condemning the whole market.
            #
            # Judged against the market's own median, never an absolute
            # threshold: the offset between Oddschecker's model and its price
            # varies by market, so a fixed cut-off would fire on whole sound
            # ladders.
            contradicted = _contradicted_rungs(rungs)
            if contradicted:
                stats["markets_unmapped"].add(
                    f"{label} (price contradicts aiProbability at "
                    f"{sorted(contradicted)})")
                stats["contradicted_rungs"] = (stats.get("contradicted_rungs", 0)
                                               + len(contradicted))

            # A two-sided price that is not a price: outside this band the pair
            # is either arbing the book against itself or carrying impossible
            # margin. Rare, but the deeper fetch now reaches the tail where it
            # lives (seen once: +200/+140, an overround of 0.750).
            overround_bad = set()
            for rung, prices in rungs.items():
                if len(prices) != 2:
                    continue
                total = sum(1.0 / d for d, _ai in prices.values())
                if not (1.015 <= total <= 1.12):
                    overround_bad.add(rung)
            if overround_bad:
                stats["markets_unmapped"].add(
                    f"{label} (impossible overround at {sorted(overround_bad)})")
                stats["bad_overround_rungs"] = (stats.get("bad_overround_rungs", 0)
                                                + len(overround_bad))
            # A LADDER MUST REPRICE AS THE LINE MOVES. A run of consecutive
            # rungs carrying one identical price pair across two or more points
            # of line is not odds-grid coarseness, it is a ladder that stopped
            # updating: Long Island at Kansas came back with the spread at
            # -33.0, -32.5, -32.0, -31.5 and -31.0 all at +190/-250, on a game
            # whose real line is -41.5. The owner could not find Kansas -31 on
            # the book, because in any meaningful sense it is not there.
            #
            # Threshold measured, not guessed: across 11,006 two-sided rungs,
            # 1,781 runs span 0.5 lines (ordinary rounding) and 77 span 1.0,
            # but only 7 span 2.0 or more. What that drops is either this
            # defect or a deep tail priced at +1200, which is unbettable
            # anyway. It is the check `aiProbability` cannot make here: on this
            # market the whole model ladder is offset ~12pp from the price, so
            # the residual rule is inert.
            stalled = _stalled_runs(rungs)
            if stalled:
                stats["markets_unmapped"].add(
                    f"{label} (ladder does not reprice across {sorted(stalled)})")
                stats["stalled_rungs"] = stats.get("stalled_rungs", 0) + len(stalled)

            placeholder = placeholder | contradicted | overround_bad | stalled

            for bet in market.get("bets") or []:
                raw_line = (bet.get("line") or {}).get("name")
                try:
                    point = float(raw_line) if raw_line not in (None, "") else None
                except (TypeError, ValueError):
                    point = None
                if point is not None and placeholder:
                    _gn = (bet.get("genericName") or "").strip().upper()
                    _rung = (point if _gn == "HOME"
                             else -point if _gn == "AWAY" else abs(point))
                    if _rung in placeholder:
                        continue
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
                        # The line often lives in the bet NAME ("Shohei Ohtani
                        # Under 2.5") with the line field empty, and the quote
                        # kept that empty value -- so the app showed a leg with
                        # a blank Line column, which is exactly the cell you
                        # would check to notice the market was wrong.
                        if point is None:
                            point = gpoint
                    else:
                        # The feed labels every h2h and handicap bet
                        # HOME/AWAY/DRAW. Taking that is exact, where the fuzzy
                        # team match is not: on soccer it mis-sided 20 of 451
                        # moneyline bets and lost 79 of 1,843 handicap ones
                        # (4.4% each), including the Man Utd / Man City pair
                        # pick_team_side refuses. Refusing is safe, but the
                        # price is thrown away either way.
                        # genericName is relative to FANATICS' OWN
                        # orientation, and the board's event may be the other
                        # way round -- match_event pairs a fixture regardless
                        # of which book calls which side home. Sydney v
                        # Brisbane is Fanatics' home/away and FanDuel's
                        # away/home, so trusting the label directly inverted
                        # both prices and reported an 84% arbitrage on a
                        # two-way moneyline.
                        #
                        # So the label resolves the TEAM, and the team is then
                        # matched to the board the same way any other name is.
                        # That keeps what genericName is good for -- knowing
                        # exactly which side a bet is on without parsing its
                        # label -- without inheriting Fanatics' orientation.
                        labelled = side_label_of(bet)
                        norm = None
                        if labelled == "draw":
                            norm = ("draw", None, None)
                        elif labelled:
                            named = home if labelled == "home" else away
                            board_side = (pick_team_side(named, target.home_team,
                                                         target.away_team)
                                          if named else None)
                            if board_side:
                                norm = _labelled_outcome(board_side, mkey, point)
                        if norm is None:
                            norm = normalize_outcome(mkey, oc_name, point, None,
                                                     target.home_team,
                                                     target.away_team)
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
