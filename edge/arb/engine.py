"""Detection: two/three-way arbitrage, middles, and +EV against a sharp anchor."""
from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .models import Board, EventMeta, GroupKey, MarketGroup, Quote, utcnow
from .normalize import is_spread_market, side_label
from . import oddsmath as om

log = logging.getLogger("arb.engine")

ET = ZoneInfo("America/New_York")


@dataclass
class Leg:
    book: str
    side: str
    label: str
    decimal: float
    american: str
    point: float | None
    stake: float = 0.0
    payout: float = 0.0
    link: str | None = None
    age_seconds: float = 0.0
    limit: float | None = None
    boost_pct: float = 0.0        # profit boost applied to THIS leg, 0 = none
    raw_decimal: float = 0.0      # the book's own price, before any boost


@dataclass
class Boost:
    """One sportsbook profit-boost token.

    Boosts are what make two-book arbitrage routinely available rather than
    rare. A -110/-110 market sums to 1.048 -- the 4.8% is the vig, and no
    amount of shopping between two books removes it. A 50% boost on one leg
    prices it at 2.364 instead of 1.909, and the same market now sums to
    0.947: a 5.6% guaranteed profit. The boost is not an edge on top of an
    arb, it is what creates the arb.

    Three constraints decide whether one is usable, and all three change the
    answer, so none of them is optional:

      * it applies to ONE bet slip, so exactly one leg of the pair
      * it has a max stake (typically $25-$50), which caps the whole position
        rather than just that leg -- the hedge is sized off it
      * it is usually restricted to a sport, and sometimes a market type
    """
    book: str
    pct: float                                                  # 0.5 == +50%
    max_stake: float = 10.0       # a conservative floor; raise per token
    sports: list[str] = field(default_factory=list)             # empty == any
    markets: list[str] = field(default_factory=list)            # empty == any
    sides: list[str] = field(default_factory=list)              # empty == any
    min_decimal: float = 1.0        # "Min Total Odds of -200" -> 1.5
    requires_parlay: bool = False
    # When the token dies. An event starting after this cannot be boosted --
    # tokens are dated ("valid on Tennis on 8/30") and a match two days out
    # is outside the offer no matter how good the price. Without this the
    # scanner reported a Tuesday match as a boosted arbitrage against a token
    # expiring that night: a bet that cannot be placed.
    expires_at: datetime | None = None
    label: str = ""

    def applies_to(self, book: str, sport_key: str, market: str,
                   side: str | None = None, decimal: float | None = None,
                   event_start: datetime | None = None) -> bool:
        # A parlay-only token cannot price a single leg. Books hand these out
        # alongside straight-bet boosts and they look identical in the app
        # ("25% WNBA boost"), but only one of them can be hedged: a two-leg
        # arbitrage needs each side placed as its own straight bet. Treating
        # one as usable reports a profit that cannot be placed.
        if self.requires_parlay:
            return False
        if self.pct <= 0.0 or book != self.book:
            return False
        if self.sports and sport_key not in self.sports:
            return False
        if self.markets and market not in self.markets:
            return False
        # Which SIDE, not just which market. DraftKings' "Batter Props
        # Milestones" are the over-only threshold ladders (1+, 2+, 3+); the
        # two-sided O/U tabs are a separate offer the token is not valid on.
        # Ignoring this lets the hedge maths pick the DK *under* as the leg to
        # boost -- which it prefers, and which cannot actually be placed.
        if self.sides and side is not None and side not in self.sides:
            return False
        # Nearly every token carries a minimum-odds floor ("Min Total Odds of
        # -200"). A leg priced shorter does not qualify, and boosting it
        # reports a profit the book will refuse at the slip.
        if decimal is not None and float(decimal) < self.min_decimal:
            return False
        if self.expires_at and event_start is not None and event_start > self.expires_at:
            return False
        return True

    def describe(self) -> str:
        return self.label or f"{self.pct:.0%} boost on {self.book} (max ${self.max_stake:g})"


@dataclass
class Opportunity:
    kind: str                     # arb | middle | ev
    fingerprint: str
    sport_key: str
    sport_title: str
    event_id: str
    matchup: str
    commence_time: datetime
    market: str
    subject: str | None
    description: str
    legs: list[Leg]
    profit_pct: float             # arb: guaranteed %; middle: % if it hits; ev: edge %
    stake_total: float = 0.0
    profit_abs: float = 0.0
    max_loss_pct: float = 0.0     # middles only
    breakeven_hit_pct: float = 0.0    # how often it must land to break even
    hit_values: list[int] = field(default_factory=list)   # results winning BOTH legs
    push_values: list[int] = field(default_factory=list)  # results winning one, pushing one
    pushes: bool = False              # a whole-number line returns a stake
    middle_window: tuple[float, float] | None = None
    fair_prob: float | None = None    # ev: devigged fair prob; middle: P(window)
    # The one number that ranks all three kinds against each other. profit_pct
    # does not: for an arb it is guaranteed, for a middle it is what you get
    # ONLY if it lands, and a middle's headline (+90-130%) therefore buries
    # every real arbitrage when the list is sorted on it. Expected return puts
    # a guaranteed 2% above a 130%-if-it-lands that hits 1.5% of the time,
    # which is the order you would actually bet in.
    expected_pct: float | None = None
    # A middle whose worst case is still a profit -- it is a straight
    # arbitrage AND carries the middle's upside if the window lands. No
    # downside, a higher ceiling than the arb alone: strictly better than an
    # ordinary middle, and the app ranks these first regardless of size.
    free_middle: bool = False
    # The guaranteed floor when free_middle is True (missing the window still
    # profits by this much). max_loss_pct clamps a negative cost to 0 for the
    # "risk" reading everywhere else, which is right for THAT purpose but
    # would otherwise erase the one number that says how large the guarantee
    # actually is here.
    free_middle_floor_pct: float | None = None
    kelly_stake: float | None = None  # ev only
    anchor_book: str | None = None
    boost: str | None = None      # description of the boost this relies on
    max_age_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    found_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["commence_time"] = self.commence_time.isoformat()
        d["found_at"] = self.found_at.isoformat()
        return d


def within_date_bounds(commence_time: datetime, cfg) -> bool:
    """Does this event's US/Eastern calendar date fall inside
    cfg.detect.date_from/date_to? Both are inclusive "YYYY-MM-DD" strings, or
    None for no bound on that side -- the default, and the common case, so
    this is a no-op unless the sidebar's date filter set one.
    """
    d = cfg.detect
    date_from, date_to = getattr(d, "date_from", None), getattr(d, "date_to", None)
    if not (date_from or date_to):
        return True
    et_date = commence_time.astimezone(ET).date().isoformat()
    if date_from and et_date < date_from:
        return False
    if date_to and et_date > date_to:
        return False
    return True


def in_window(event: EventMeta, cfg, now: datetime) -> bool:
    """Is this event close enough to bet, and not already under way?"""
    d = cfg.detect
    mins = event.minutes_to_start(now)
    if mins < 0:
        # min_minutes_to_start is positive by default, so mins < 0 was ALWAYS
        # also mins < min_minutes_to_start -- the check below excluded every
        # live event on its own, regardless of skip_live. That made skip_live
        # dead code: setting it False never actually surfaced a live event.
        # Branching here instead of falling through is the fix.
        if d.skip_live:
            return False
    elif mins < d.min_minutes_to_start:
        return False
    elif d.max_hours_to_start and mins > d.max_hours_to_start * 60.0:
        return False
    return within_date_bounds(event.commence_time, cfg)


def _fingerprint(*parts) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _leg_point(q: Quote, group: MarketGroup) -> float | None:
    """The line to SHOW for this leg: the one its own book posts.

    Spreads are stored folded onto the home axis, so both sides of a group
    carry the same negative number. Displayed raw that reads as both teams
    laying points -- a reported arbitrage showed "FanDuel away -30.5" beside
    "Fanatics home -30.5" when the FanDuel bet is Long Island **+30.5**, which
    is the number you would type into the book. The away side is the negation.
    """
    point = group.key.point
    if point is None:
        return q.point
    if is_spread_market(group.key.market) and q.side == "away":
        return -point
    if is_spread_market(group.key.market) and q.side == "home":
        return point
    return q.point if q.point is not None else point


def _leg(q: Quote, group: MarketGroup, commission: float, now: datetime) -> Leg:
    eff = om.net_of_commission(q.decimal, commission)
    return Leg(
        book=q.book,
        side=q.side,
        raw_decimal=round(eff, 4),
        label=side_label(q.side, group.event.home_team, group.event.away_team, group.key.subject),
        decimal=round(eff, 4),
        american=om.format_american(eff),
        point=_leg_point(q, group),
        link=q.link,
        limit=q.limit,
        age_seconds=round(q.age_seconds(now), 1),
    )


def _describe(group: MarketGroup) -> str:
    k = group.key
    bits = [k.market]
    if k.subject:
        bits.append(k.subject)
    if k.point is not None:
        bits.append(f"{k.point:+g}" if is_spread_market(k.market) else f"{k.point:g}")
    return " ".join(bits)


# --------------------------------------------------------------------------
# arbitrage
# --------------------------------------------------------------------------
def _boost_variants(legs: list[Leg], cfg, sport_key: str, market: str,
                    event_start: datetime | None = None):
    """(boost, leg index, prices) for the plain market and each boostable leg.

    The unboosted case is always first, so a market that arbs on its own still
    reports without spending a token on it.
    """
    base = [l.decimal for l in legs]
    yield None, None, base
    for b in getattr(cfg, "boosts", None) or []:
        for i, leg in enumerate(legs):
            if not b.applies_to(leg.book, sport_key, market,
                                side=leg.side, decimal=leg.decimal,
                                event_start=event_start):
                continue
            priced = list(base)
            # the price the BOOK writes, not the exact product: a boosted bet
            # is booked at whole American odds and settles there
            priced[i] = om.at_book_price(om.boosted(base[i], b.pct))
            yield b, i, priced


def find_arbitrages(board: Board, cfg, now: datetime | None = None) -> list[Opportunity]:
    now = now or utcnow()
    d = cfg.detect
    books = set(cfg.books.bettable)
    out: list[Opportunity] = []

    for group in board.groups.values():
        if not in_window(group.event, cfg, now):
            continue
        sides = group.expected_sides()
        if not (2 <= len(sides) <= d.max_legs):
            continue

        best = {s: group.best(s, books) for s in sides}
        if any(q is None for q in best.values()):
            continue                                   # incomplete market: not tradeable

        ordered = sorted(sides)
        quotes = [best[s] for s in ordered]
        if len({q.book for q in quotes}) < d.min_books:
            continue                                   # all legs at one book: data artifact

        ages = [q.age_seconds(now) for q in quotes]
        if max(ages) > d.max_quote_age_seconds:
            continue

        legs = [_leg(q, group, cfg.books.commission.get(q.book, 0.0), now) for q in quotes]
        ev = group.event

        # A boost applies to ONE slip, so each leg is a separate candidate and
        # the better one wins. Evaluating only the longest leg would be wrong:
        # the boost is worth more on the leg carrying more of the stake.
        best = None
        for boost, idx, priced in _boost_variants(
                legs, cfg, ev.sport_key, group.key.market,
                event_start=ev.commence_time):
            s = om.arb_sum(priced)
            if s >= 1.0:
                continue
            profit_pct = (1.0 / s - 1.0) * 100.0
            if profit_pct < d.min_profit_pct:
                continue
            caps = [cfg.books.max_stake.get(l.book) for l in legs]
            if boost is not None:
                # the token's max stake bounds the whole position, not just its
                # own leg -- allocate() shrinks the total to keep the ratio
                caps[idx] = min(c for c in (caps[idx], boost.max_stake) if c)
            alloc = om.allocate(priced, bankroll=cfg.bankroll.total,
                                round_to=cfg.bankroll.round_to, max_stakes=caps)
            if best is None or alloc.worst_profit_pct > best[0].worst_profit_pct:
                best = (alloc, boost, idx, priced, profit_pct)
        if best is None:
            continue
        alloc, boost, boost_idx, priced, profit_pct = best

        for i, (leg, stake, payout) in enumerate(zip(legs, alloc.stakes, alloc.payouts)):
            leg.stake, leg.payout = stake, payout
            leg.decimal = round(priced[i], 4)
            leg.american = om.format_american(priced[i])
            if boost is not None and i == boost_idx:
                leg.boost_pct = boost.pct

        warnings = []
        if profit_pct > d.max_profit_pct and boost is None:
            warnings.append(
                f"{profit_pct:.1f}% exceeds max_profit_pct ({d.max_profit_pct}%) -- "
                "usually a stale or mispublished line, verify both prices before staking"
            )
        if boost is not None:
            warnings.append(
                f"needs the {boost.describe()} applied to the "
                f"{legs[boost_idx].book} leg -- without it this is "
                f"{(1.0 / om.arb_sum([l.raw_decimal for l in legs]) - 1.0) * 100.0:+.2f}%")
        if alloc.capped:
            warnings.append(
                "stake reduced to respect the boost's max stake" if boost is not None
                else "stake reduced to respect a per-book limit")
        if alloc.worst_profit_pct <= 0:
            warnings.append("rounding erases the edge at this bankroll")
        if max(ages) > d.max_quote_age_seconds / 2:
            warnings.append(f"oldest quote is {max(ages):.0f}s old")

        out.append(Opportunity(
            kind="arb",
            fingerprint=_fingerprint("arb", ev.event_id, group.key.market, group.key.subject,
                                     group.key.point, *[f"{l.book}:{l.side}" for l in legs],
                                     boost.describe() if boost else ""),
            sport_key=ev.sport_key, sport_title=ev.sport_title, event_id=ev.event_id,
            matchup=ev.matchup, commence_time=ev.commence_time,
            market=group.key.market, subject=group.key.subject, description=_describe(group),
            legs=legs, profit_pct=round(alloc.worst_profit_pct, 3),
            # An arbitrage's guaranteed return IS its expected return.
            expected_pct=round(alloc.worst_profit_pct, 3),
            stake_total=alloc.total, profit_abs=alloc.worst_profit,
            boost=boost.describe() if boost else None,
            max_age_seconds=round(max(ages), 1), warnings=warnings,
        ))

    out.sort(key=lambda o: o.profit_pct, reverse=True)
    return out


# --------------------------------------------------------------------------
# middles -- both legs can win; costs a small fixed amount when they don't
# --------------------------------------------------------------------------
def middle_scenarios(d_lo: float, d_hi: float, lo_line: float, hi_line: float,
                     s_lo: float, s_hi: float) -> dict[int, float]:
    """Profit for every integer result, treating pushes as pushes.

    The low leg is Over `lo_line`: it wins above, pushes exactly on it, loses
    below. The high leg is Under `hi_line`: wins below, pushes on it, loses
    above. When a line is a whole number the "middle" outcome returns one
    stake rather than paying it -- roughly halving the gain, which the
    both-legs-win formula silently overstated.
    """
    total = s_lo + s_hi
    out: dict[int, float] = {}
    lo_i, hi_i = int(math.floor(lo_line)) - 1, int(math.ceil(hi_line)) + 1
    for x in range(lo_i, hi_i + 1):
        pay = 0.0
        pay += s_lo * (d_lo if x > lo_line else (1.0 if x == lo_line else 0.0))
        pay += s_hi * (d_hi if x < hi_line else (1.0 if x == hi_line else 0.0))
        out[x] = pay - total
    return out


def middle_results(lo_line: float, hi_line: float) -> list[int]:
    """The whole-number results that fall strictly between two lines.

    Games settle on whole numbers -- points, runs, goals, rebounds -- so only
    integers can decide a bet, and a window has to contain one to be a middle.
    Over 53.5 against Under 54 is a real interval and the both-legs-win
    arithmetic over the reals is sound, but no game finishes on 53.7: there is
    nothing in that window to win. Over 53 against Under 54 is the same trap
    with whole lines, where both ends push instead.

    floor/ceil rather than int(): truncation rounds toward zero, which walks
    the wrong way on the negative half of the spread axis.
    """
    return [n for n in range(math.floor(lo_line) + 1, math.ceil(hi_line))
            if lo_line < n < hi_line]


def _middle_families(board: Board) -> dict[tuple, list[MarketGroup]]:
    """Group every priced line of the same market onto one axis, so a low leg
    at one number can be paired against a high leg at another."""
    fams: dict[tuple, list[MarketGroup]] = {}
    for g in board.groups.values():
        if g.key.point is None:
            continue
        sides = g.expected_sides()
        if not (sides & {"over", "under"} or sides & {"home", "away"}):
            continue
        fams.setdefault((g.key.event_id, g.key.market, g.key.subject), []).append(g)
    return {k: v for k, v in fams.items() if len(v) > 1}


def ladder_cdfs(board: Board, books: set[str],
                min_rungs: int = 3) -> dict[tuple, dict[float, float]]:
    """(event, market) -> {line: devigged P(over / home covers)}.

    Pooled across books -- best price per side per line, then devigged. One
    book often posts only its main line on a given game; between the three of
    them the axis gets covered. Markets with fewer than `min_rungs` two-sided
    lines are omitted, because two points cannot describe a distribution.
    """
    pooled: dict[tuple, dict[float, dict[str, float]]] = {}
    for g in board.groups.values():
        if g.key.subject is not None or g.key.point is None:
            continue
        rungs = pooled.setdefault((g.key.event_id, g.key.market), {})
        sides = rungs.setdefault(g.key.point, {})
        for side, bybook in g.quotes.items():
            for bk, q in bybook.items():
                if bk in books and q.decimal > sides.get(side, 0.0):
                    sides[side] = q.decimal
    out: dict[tuple, dict[float, float]] = {}
    for key, rungs in pooled.items():
        cdf = {}
        for point, sides in rungs.items():
            a = sides.get("over") or sides.get("home")
            b = sides.get("under") or sides.get("away")
            if not a or not b:
                continue
            cdf[point] = (1.0 / a) / (1.0 / a + 1.0 / b)
        if len(cdf) >= min_rungs:
            out[key] = cdf
    return out


def p_above(cdf: dict[float, float], value: float, spread: bool) -> float | None:
    """P(the settled number exceeds `value`), interpolated between rungs.

    Totals sit on the axis directly; a spread's group point is the home line,
    and home covers when the margin exceeds its negation.
    """
    points = sorted(((-p if spread else p), v) for p, v in cdf.items())
    for x, v in points:
        if abs(x - value) < 1e-9:
            return v
    below = [(x, v) for x, v in points if x < value]
    above = [(x, v) for x, v in points if x > value]
    if not below or not above:
        return None
    (x0, v0), (x1, v1) = below[-1], above[0]
    return v0 + (v1 - v0) * (value - x0) / (x1 - x0)


def find_middles(board: Board, cfg, now: datetime | None = None) -> list[Opportunity]:
    now = now or utcnow()
    d = cfg.detect
    if not d.middles_enabled:
        return []
    books = set(cfg.books.bettable)
    out: list[Opportunity] = []
    cdfs = ladder_cdfs(board, books)

    for (event_id, market, subject), groups in _middle_families(board).items():
        if not in_window(groups[0].event, cfg, now):
            continue
        spread = is_spread_market(market)
        lo_side, hi_side = ("home", "away") if spread else ("over", "under")
        # low leg wants the smaller number, high leg the larger, in the same axis
        lows = [(g, g.best(lo_side, books)) for g in groups if g.best(lo_side, books)]
        highs = [(g, g.best(hi_side, books)) for g in groups if g.best(hi_side, books)]
        if not lows or not highs:
            continue

        for glo, qlo in lows:
            for ghi, qhi in highs:
                plo, phi = glo.key.point, ghi.key.point
                # spreads live on the home-margin axis with the sign flipped
                width = (plo - phi) if spread else (phi - plo)
                if width <= 0 or width < d.middle_min_width or width > d.middle_max_width:
                    continue          # width <= 0 inverts the legs: a gap, not a middle
                # A positive width is not enough. The bet settles on a whole
                # number, so the window has to hold one: Over 53.5 / Under 54
                # spans half a point no score can land on, and Over 53 /
                # Under 54 pushes at both ends. Neither can win both legs.
                axis_lo = -plo if spread else plo
                axis_hi = -phi if spread else phi
                lo_line, hi_line = axis_lo, axis_hi        # ordered: width > 0
                landing = middle_results(lo_line, hi_line)
                if not landing:
                    log.debug("%s %s: no whole number lies between %g and %g",
                              market, subject or "game", lo_line, hi_line)
                    continue
                if qlo.book == qhi.book:
                    continue
                ages = [qlo.age_seconds(now), qhi.age_seconds(now)]
                if max(ages) > d.max_quote_age_seconds:
                    continue

                legs = [_leg(q, g, cfg.books.commission.get(q.book, 0.0), now)
                        for q, g in ((qlo, glo), (qhi, ghi))]
                if any(l.decimal > d.middle_max_leg_decimal for l in legs):
                    continue          # a longshot price is not a main line
                # Enumerate the real outcomes instead of assuming both legs
                # win: a whole-number line pushes, returning the stake rather
                # than paying it.
                alloc0 = om.allocate([l.decimal for l in legs],
                                     bankroll=cfg.bankroll.total,
                                     round_to=cfg.bankroll.round_to)
                # legs[0] sits on lo_line and legs[1] on hi_line: width > 0
                d_lo, d_hi = legs[0].decimal, legs[1].decimal
                s_lo, s_hi = alloc0.stakes[0], alloc0.stakes[1]
                staked = s_lo + s_hi
                scenarios = middle_scenarios(d_lo, d_hi, lo_line, hi_line, s_lo, s_hi)
                wins = {x: p for x, p in scenarios.items() if p > 0}
                worst = min(scenarios.values())
                # the headline is what a landing pays -- every number inside
                # the window wins both legs for the same amount
                hit_pct = min(scenarios[n] for n in landing) / staked * 100.0
                # a whole-number end returns one stake instead of paying it,
                # so it profits far less; break even on that weaker result
                min_hit_pct = min(wins.values()) / staked * 100.0
                cost_pct = -worst / staked * 100.0
                if cost_pct > d.middle_max_cost_pct:
                    continue
                breakeven = (cost_pct / (cost_pct + min_hit_pct) * 100.0
                             if (cost_pct + min_hit_pct) > 0 else 100.0)
                hit_values = landing
                push_values = sorted(x for x in wins if float(x) in (lo_line, hi_line))

                alloc = om.allocate([l.decimal for l in legs], bankroll=cfg.bankroll.total,
                                    round_to=cfg.bankroll.round_to,
                                    max_stakes=[cfg.books.max_stake.get(l.book) for l in legs])
                for leg, stake, payout in zip(legs, alloc.stakes, alloc.payouts):
                    leg.stake, leg.payout = stake, payout

                # report the window in the units the result is settled in:
                # total points for O/U, home margin of victory for spreads
                window = (-plo, -phi) if spread else (plo, phi)
                window = (min(window), max(window))
                # How often the window actually lands, read off the books' own
                # alternate ladders rather than assumed. Without it the list
                # ranks on "+131% if it lands" and a middle that hits 1.5% of
                # the time outranks every real arbitrage. Pushes are ignored,
                # which understates rather than flatters: a push returns a
                # stake instead of losing it.
                cdf = cdfs.get((event_id, market))
                p_hit = None
                if cdf and subject is None:
                    lo_p = p_above(cdf, window[0], spread)
                    hi_p = p_above(cdf, window[1], spread)
                    if lo_p is not None and hi_p is not None:
                        p_hit = max(0.0, min(1.0, abs(lo_p - hi_p)))

                lands_on = ("/".join(str(n) for n in hit_values) if len(hit_values) <= 4
                            else f"{hit_values[0]}-{hit_values[-1]}")
                warnings = []
                if push_values:
                    warnings.append(
                        f"{'/'.join(str(x) for x in push_values)} pushes a leg: that result "
                        "returns one stake instead of paying it, so it earns roughly half")
                if cost_pct <= 0:
                    warnings.append("free middle: this is also a straight arbitrage")

                ev = glo.event
                out.append(Opportunity(
                    kind="middle",
                    fingerprint=_fingerprint("mid", event_id, market, subject, plo, phi,
                                             qlo.book, qhi.book),
                    sport_key=ev.sport_key, sport_title=ev.sport_title, event_id=event_id,
                    matchup=ev.matchup, commence_time=ev.commence_time,
                    market=market, subject=subject,
                    description=f"{market}{' ' + subject if subject else ''} middle "
                                f"{window[0]:g}-{window[1]:g} (wins both on {lands_on})",
                    legs=legs, profit_pct=round(hit_pct, 3),
                    fair_prob=(round(p_hit, 4) if p_hit is not None else None),
                    expected_pct=(round(p_hit * hit_pct - (1.0 - p_hit) * cost_pct, 3)
                                  if p_hit is not None else None),
                    free_middle=cost_pct <= 0,
                    free_middle_floor_pct=(round(-cost_pct, 3) if cost_pct <= 0 else None),
                    stake_total=alloc.total,
                    profit_abs=round(alloc.total * hit_pct / 100.0, 2),
                    max_loss_pct=round(max(cost_pct, 0.0), 3),
                    breakeven_hit_pct=round(max(breakeven, 0.0), 2),
                    hit_values=hit_values, push_values=push_values,
                    pushes=bool(push_values),
                    middle_window=window, max_age_seconds=round(max(ages), 1),
                    warnings=warnings,
                ))

    # Free middles first, regardless of size -- no downside means one must
    # not be truncated away by middle_max_results in favour of an ordinary
    # middle that merely scores higher on the cost/reward heuristic below.
    out.sort(key=lambda o: (o.free_middle, o.profit_pct / (o.max_loss_pct + 0.5)),
            reverse=True)
    return out[: d.middle_max_results]


# --------------------------------------------------------------------------
# +EV -- price a bettable book against a sharp anchor's devigged number
# --------------------------------------------------------------------------
def _fair_probs(group: MarketGroup, anchor_books: list[str], method: str,
                sides: list[str]) -> tuple[dict[str, float], str] | None:
    for book in anchor_books:
        prices = []
        for s in sides:
            q = group.quotes.get(s, {}).get(book)
            if q is None:
                break
            prices.append(q.decimal)
        else:
            return dict(zip(sides, om.fair_probs_from_decimals(prices, method))), book
    return None


def _consensus_probs(group: MarketGroup, sides: list[str], exclude: str,
                     method: str, min_books: int) -> dict[str, float] | None:
    """Fallback anchor: average the field's implied probabilities, minus the
    book being evaluated, then remove the vig from that average."""
    contributors = [b for b in group.book_sides
                    if b != exclude and set(sides) <= group.book_sides[b]]
    if len(contributors) < min_books:
        return None
    avg = []
    for s in sides:
        ps = [om.implied_prob(group.quotes[s][b].decimal) for b in contributors]
        avg.append(sum(ps) / len(ps))
    return dict(zip(sides, om.devig(avg, method)))


def find_ev(board: Board, cfg, now: datetime | None = None) -> list[Opportunity]:
    now = now or utcnow()
    d = cfg.detect
    if not d.ev_enabled:
        return []
    books = set(cfg.books.bettable)
    out: list[Opportunity] = []

    for group in board.groups.values():
        if not in_window(group.event, cfg, now):
            continue
        sides = sorted(group.expected_sides())
        if not (2 <= len(sides) <= d.max_legs):
            continue

        anchored = _fair_probs(group, cfg.books.reference, d.ev_method, sides)
        for side in sides:
            for book, q in group.quotes.get(side, {}).items():
                if book not in books:
                    continue
                if q.age_seconds(now) > d.max_quote_age_seconds:
                    continue
                if anchored:
                    fair, anchor_name = anchored[0][side], anchored[1]
                elif d.ev_allow_consensus:
                    probs = _consensus_probs(group, sides, book, d.ev_method, d.ev_consensus_min_books)
                    if not probs:
                        continue
                    fair, anchor_name = probs[side], "consensus"
                else:
                    continue

                eff = om.net_of_commission(q.decimal, cfg.books.commission.get(book, 0.0))
                edge = om.ev_pct(eff, fair)
                if edge < d.ev_min_pct or edge > d.ev_max_pct:
                    continue

                k = om.kelly_fraction(eff, fair) * cfg.detect.kelly_fraction
                stake = round(min(k * cfg.bankroll.total,
                                  cfg.books.max_stake.get(book) or float("inf")), 2)
                leg = _leg(q, group, cfg.books.commission.get(book, 0.0), now)
                leg.stake = stake
                leg.payout = round(stake * eff, 2)

                ev_meta = group.event
                out.append(Opportunity(
                    kind="ev",
                    fingerprint=_fingerprint("ev", ev_meta.event_id, group.key.market,
                                             group.key.subject, group.key.point, book, side),
                    sport_key=ev_meta.sport_key, sport_title=ev_meta.sport_title,
                    event_id=ev_meta.event_id, matchup=ev_meta.matchup,
                    commence_time=ev_meta.commence_time, market=group.key.market,
                    subject=group.key.subject, description=_describe(group),
                    legs=[leg], profit_pct=round(edge, 3), stake_total=stake,
                    # +EV's headline is already an expected return.
                    expected_pct=round(edge, 3),
                    profit_abs=round(stake * edge / 100.0, 2),
                    fair_prob=round(fair, 5),
                    kelly_stake=stake, anchor_book=anchor_name,
                    max_age_seconds=leg.age_seconds,
                    warnings=[] if anchor_name != "consensus"
                             else ["priced off book consensus, not a sharp anchor"],
                ))

    out.sort(key=lambda o: o.profit_pct, reverse=True)
    return out[: d.ev_max_results]


def both_sides_plus(decimals: list[float]) -> bool:
    """Is every leg at positive American odds?

    Worth naming because it is the screen you can run by eye. Positive American
    odds means decimal >= 2.0, so 1/d <= 0.5, and two of those sum to <= 1.0 --
    an arbitrage by definition, with no arithmetic needed. (+100/+100 is the
    boundary: it sums to exactly 1.0 and locks nothing, so one leg has to be
    strictly longer.)

    A boost is what usually puts a leg over that line: 25% turns -110 into
    +114 and 50% turns it into +136, so if the other book already has the
    other side at +money the pair is an arbitrage on sight.
    """
    return len(decimals) >= 2 and all(float(d) >= 2.0 for d in decimals) \
        and any(float(d) > 2.0 for d in decimals)


def _start_of(cand: dict) -> datetime | None:
    """A candidate's start time, or None if it cannot be read.

    None means "do not apply the expiry rule" rather than "reject": a snapshot
    written before commence_time was carried should not silently stop every
    boost from applying.
    """
    try:
        ts = datetime.fromisoformat(cand.get("commence_time") or "")
    except (ValueError, TypeError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def price_candidates(cands: list[dict], boosts: list[Boost], cfg,
                     min_profit_pct: float | None = None) -> list[dict]:
    """Re-price snapshotted candidates under a set of boosts.

    This is what the boost slider drives. It is deliberately the same shape as
    `find_arbitrages` -- try each leg as the boosted one, keep the better, size
    the position off the token's max stake -- so the number on the screen is
    the number the scanner would report, rather than a second implementation
    that drifts from it.

    Returns rows sorted by profit, each carrying the staking plan.
    """
    floor = cfg.detect.min_profit_pct if min_profit_pct is None else min_profit_pct
    out = []
    for c in cands:
        legs = c["legs"]
        base = [l["decimal"] for l in legs]
        # One book on both sides is a data artifact on its own -- min_books
        # exists to reject it -- but a boost can beat that book's own vig, and
        # then it is a real position. So it is priced only WITH a boost.
        plain = [] if c.get("single_book") else [(None, None, base)]
        best = None
        for boost, idx, priced in plain + [
                (b, i, [om.at_book_price(om.boosted(d, b.pct)) if i == j else d
                        for j, d in enumerate(base)])
                for b in boosts
                for i, l in enumerate(legs)
                if b.applies_to(l["book"], c["sport_key"], c["market"],
                                side=l.get("side"), decimal=l.get("decimal"),
                                event_start=_start_of(c))]:
            s = om.arb_sum(priced)
            if s >= 1.0:
                continue
            caps = [cfg.books.max_stake.get(l["book"]) for l in legs]
            if boost is not None:
                caps[idx] = min(x for x in (caps[idx], boost.max_stake) if x)
            alloc = om.allocate(priced, bankroll=cfg.bankroll.total,
                                round_to=cfg.bankroll.round_to, max_stakes=caps)
            if alloc.worst_profit_pct < floor:
                continue
            if best is None or alloc.worst_profit_pct > best[0].worst_profit_pct:
                best = (alloc, boost, idx, priced)
        if best is None:
            continue
        alloc, boost, idx, priced = best
        out.append({
            **{k: c[k] for k in ("sport_key", "sport_title", "matchup", "market",
                                 "subject", "point", "commence_time")},
            "profit_pct": round(alloc.worst_profit_pct, 3),
            "profit_abs": alloc.worst_profit,
            "stake_total": alloc.total,
            "unboosted_pct": round((1.0 / om.arb_sum(base) - 1.0) * 100.0, 3),
            "boost": boost.describe() if boost else None,
            "both_plus": both_sides_plus(priced),
            # both prices: `raw_american` is what the book posts and what you
            # verify against before placing, `american` is what it pays after
            # the boost. Showing only the boosted price makes the slip look
            # wrong at the counter.
            "legs": [{**legs[i], "stake": alloc.stakes[i],
                      "payout": alloc.payouts[i],
                      "priced": round(priced[i], 4),
                      "raw_american": om.format_american(legs[i]["decimal"]),
                      "american": om.format_american(priced[i]),
                      "boost_pct": boost.pct if (boost and i == idx) else 0.0}
                     for i in range(len(legs))],
        })
    out.sort(key=lambda r: r["profit_pct"], reverse=True)
    return out


def price_boosted_ev(cands: list[dict], boosts: list[Boost], cfg,
                     min_ev_pct: float = 0.0) -> list[dict]:
    """Expected value of a boosted bet that cannot be hedged.

    A boost no second book can cover is not wasted -- it stops being an
    arbitrage and becomes an +EV bet, and the question changes from "is this
    risk-free" to "which single bet is it worth most on". That is the normal
    case, not the exception: DraftKings' batter-props token is valid only on
    over-only Milestones, and FanDuel posts no under on any batter market, so
    nothing can hedge it.

    Fair probability comes from devigging the best over/under pair. Across two
    books that is a better estimate than either book alone, since each side is
    the sharpest price available. Where the boosted book's own price on that
    side is shorter than the devigged fair, the shorter one wins: the estimate
    should never be more generous than what a book is willing to lay.
    """
    out = []
    method = getattr(cfg.detect, "ev_method", "power")
    for c in cands:
        prices = c.get("prices") or {}
        legs = c["legs"]
        if len(legs) != 2:
            continue
        # Per-SIDE, not `c["point"]` -- that is the group's home-axis-folded
        # point, and a spread's away side needs it negated. Falls back to the
        # folded point for a snapshot written before legs carried their own.
        point_by_side = {l["side"]: l.get("point") for l in legs}
        try:
            fair_by_side = dict(zip(
                [l["side"] for l in legs],
                om.fair_probs_from_decimals([l["decimal"] for l in legs], method)))
        except (ValueError, ZeroDivisionError):
            continue
        for b in boosts:
            for side, per_book in prices.items():
                raw = per_book.get(b.book)
                if raw is None:
                    continue
                if not b.applies_to(b.book, c["sport_key"], c["market"],
                                    side=side, decimal=raw,
                                    event_start=_start_of(c)):
                    continue
                fair = fair_by_side.get(side)
                if not fair:
                    continue
                # never assume a longer price than the market's own read
                fair = min(fair, om.implied_prob(raw))
                boosted = om.at_book_price(om.boosted(raw, b.pct))
                ev = om.ev_pct(boosted, fair)
                if ev < min_ev_pct:
                    continue
                stake = min(b.max_stake, cfg.bankroll.total)
                out.append({
                    **{k: c[k] for k in ("sport_key", "sport_title", "matchup",
                                         "market", "subject", "commence_time")},
                    "point": point_by_side.get(side, c.get("point")),
                    "book": b.book, "side": side,
                    "raw_decimal": raw,
                    "raw_american": om.format_american(raw),
                    "boosted_decimal": round(boosted, 4),
                    "american": om.format_american(boosted),
                    "boost": b.describe(), "boost_pct": b.pct,
                    "fair_prob": round(fair, 5),
                    "ev_pct": round(ev, 3),
                    "stake": round(stake, 2),
                    "ev_abs": round(stake * ev / 100.0, 2),
                    "other_books": {bk: v for bk, v in per_book.items() if bk != b.book},
                })
    out.sort(key=lambda r: r["ev_pct"], reverse=True)
    return out


def top_rows_per_sport(rows: list[dict], n: int = 3) -> list[dict]:
    """`top_per_sport` for the dict rows `price_candidates` returns.

    `n <= 0` means NO CAP, and returns every row best-first. The app's control
    reads "0 = no cap"; passing that through to a plain top-N asked for zero
    rows per sport, which returned an empty list and crashed the boost panel on
    `shown[0]` the moment a boost was entered.
    """
    def score(r: dict) -> float:
        # price_candidates rows carry profit_pct; price_boosted_ev rows carry
        # ev_pct. Reading only the first raised KeyError on the +EV panel.
        for field in ("profit_pct", "ev_pct"):
            if field in r:
                return float(r[field])
        return 0.0

    if n <= 0:
        # Both callers hand these over already sorted best-first, and that
        # order is the point: one ranking across every sport.
        return list(rows)
    ordered = sorted(rows, key=score, reverse=True)
    best: dict[str, list[dict]] = {}
    for r in ordered:
        bucket = best.setdefault(r.get("sport_key", ""), [])
        if len(bucket) < n:
            bucket.append(r)
    out = [r for bucket in best.values() for r in bucket]
    out.sort(key=lambda r: (r.get("sport_title", ""), -score(r)))
    return out


def top_per_sport(opps: list[Opportunity], n: int = 3,
                  kinds: tuple[str, ...] | None = None) -> list[Opportunity]:
    """The best `n` per sport, still ranked within each sport.

    A boosted scan does not return a handful of finds -- a 50% boost clears the
    vig on essentially every two-way market it touches, so one MLB slate alone
    can produce hundreds. Ranking globally then buries a whole sport under
    whichever one happens to price widest, so the cut is per sport.
    """
    if kinds:
        opps = [o for o in opps if o.kind in kinds]
    best: dict[str, list[Opportunity]] = {}
    for o in sorted(opps, key=lambda x: x.profit_pct, reverse=True):
        bucket = best.setdefault(o.sport_key, [])
        if len(bucket) < n:
            bucket.append(o)
    out = [o for bucket in best.values() for o in bucket]
    out.sort(key=lambda o: (o.sport_title, -o.profit_pct))
    return out


def scan(board: Board, cfg, now: datetime | None = None) -> list[Opportunity]:
    now = now or utcnow()
    return find_arbitrages(board, cfg, now) + find_middles(board, cfg, now) + find_ev(board, cfg, now)
