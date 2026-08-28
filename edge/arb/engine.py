"""Detection: two/three-way arbitrage, middles, and +EV against a sharp anchor."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime

from .models import Board, EventMeta, GroupKey, MarketGroup, Quote, utcnow
from .normalize import is_spread_market, side_label
from . import oddsmath as om


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
    hit_values: list[int] = field(default_factory=list)   # results that pay
    pushes: bool = False              # a whole-number line returns a stake
    middle_window: tuple[float, float] | None = None
    fair_prob: float | None = None    # ev only
    kelly_stake: float | None = None  # ev only
    anchor_book: str | None = None
    max_age_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    found_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["commence_time"] = self.commence_time.isoformat()
        d["found_at"] = self.found_at.isoformat()
        return d


def in_window(event: EventMeta, cfg, now: datetime) -> bool:
    """Is this event close enough to bet, and not already under way?"""
    d = cfg.detect
    mins = event.minutes_to_start(now)
    if d.skip_live and mins < 0:
        return False
    if mins < d.min_minutes_to_start:
        return False
    if d.max_hours_to_start and mins > d.max_hours_to_start * 60.0:
        return False
    return True


def _fingerprint(*parts) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _leg(q: Quote, group: MarketGroup, commission: float, now: datetime) -> Leg:
    eff = om.net_of_commission(q.decimal, commission)
    return Leg(
        book=q.book,
        side=q.side,
        label=side_label(q.side, group.event.home_team, group.event.away_team, group.key.subject),
        decimal=round(eff, 4),
        american=om.format_american(eff),
        point=q.point,
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
        s = om.arb_sum([l.decimal for l in legs])
        if s >= 1.0:
            continue
        profit_pct = (1.0 / s - 1.0) * 100.0
        if profit_pct < d.min_profit_pct:
            continue

        alloc = om.allocate(
            [l.decimal for l in legs],
            bankroll=cfg.bankroll.total,
            round_to=cfg.bankroll.round_to,
            max_stakes=[cfg.books.max_stake.get(l.book) for l in legs],
        )
        for leg, stake, payout in zip(legs, alloc.stakes, alloc.payouts):
            leg.stake, leg.payout = stake, payout

        warnings = []
        if profit_pct > d.max_profit_pct:
            warnings.append(
                f"{profit_pct:.1f}% exceeds max_profit_pct ({d.max_profit_pct}%) -- "
                "usually a stale or mispublished line, verify both prices before staking"
            )
        if alloc.capped:
            warnings.append("stake reduced to respect a per-book limit")
        if alloc.worst_profit_pct <= 0:
            warnings.append("rounding erases the edge at this bankroll")
        if max(ages) > d.max_quote_age_seconds / 2:
            warnings.append(f"oldest quote is {max(ages):.0f}s old")

        ev = group.event
        out.append(Opportunity(
            kind="arb",
            fingerprint=_fingerprint("arb", ev.event_id, group.key.market, group.key.subject,
                                     group.key.point, *[f"{l.book}:{l.side}" for l in legs]),
            sport_key=ev.sport_key, sport_title=ev.sport_title, event_id=ev.event_id,
            matchup=ev.matchup, commence_time=ev.commence_time,
            market=group.key.market, subject=group.key.subject, description=_describe(group),
            legs=legs, profit_pct=round(alloc.worst_profit_pct, 3),
            stake_total=alloc.total, profit_abs=alloc.worst_profit,
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
    import math
    total = s_lo + s_hi
    out: dict[int, float] = {}
    lo_i, hi_i = int(math.floor(lo_line)) - 1, int(math.ceil(hi_line)) + 1
    for x in range(lo_i, hi_i + 1):
        pay = 0.0
        pay += s_lo * (d_lo if x > lo_line else (1.0 if x == lo_line else 0.0))
        pay += s_hi * (d_hi if x < hi_line else (1.0 if x == hi_line else 0.0))
        out[x] = pay - total
    return out


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


def find_middles(board: Board, cfg, now: datetime | None = None) -> list[Opportunity]:
    now = now or utcnow()
    d = cfg.detect
    if not d.middles_enabled:
        return []
    books = set(cfg.books.bettable)
    out: list[Opportunity] = []

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
                if width < d.middle_min_width or width > d.middle_max_width:
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
                axis_lo = -glo.key.point if spread else glo.key.point
                axis_hi = -ghi.key.point if spread else ghi.key.point
                lo_line, hi_line = min(axis_lo, axis_hi), max(axis_lo, axis_hi)
                d_lo = legs[0].decimal if axis_lo <= axis_hi else legs[1].decimal
                d_hi = legs[1].decimal if axis_lo <= axis_hi else legs[0].decimal
                s_lo = alloc0.stakes[0] if axis_lo <= axis_hi else alloc0.stakes[1]
                s_hi = alloc0.stakes[1] if axis_lo <= axis_hi else alloc0.stakes[0]
                staked = s_lo + s_hi
                scenarios = middle_scenarios(d_lo, d_hi, lo_line, hi_line, s_lo, s_hi)
                wins = {x: p for x, p in scenarios.items() if p > 0}
                if not wins:
                    continue          # cannot profit on any result -- not a middle
                worst = min(scenarios.values())
                hit_pct = max(wins.values()) / staked * 100.0
                min_hit_pct = min(wins.values()) / staked * 100.0
                cost_pct = -worst / staked * 100.0
                if cost_pct > d.middle_max_cost_pct:
                    continue
                # break even on the least generous winning result
                breakeven = (cost_pct / (cost_pct + min_hit_pct) * 100.0
                             if (cost_pct + min_hit_pct) > 0 else 100.0)
                hit_values = sorted(wins)

                alloc = om.allocate([l.decimal for l in legs], bankroll=cfg.bankroll.total,
                                    round_to=cfg.bankroll.round_to,
                                    max_stakes=[cfg.books.max_stake.get(l.book) for l in legs])
                for leg, stake, payout in zip(legs, alloc.stakes, alloc.payouts):
                    leg.stake, leg.payout = stake, payout

                # report the window in the units the result is settled in:
                # total points for O/U, home margin of victory for spreads
                window = (-plo, -phi) if spread else (plo, phi)
                window = (min(window), max(window))
                outcomes = [n for n in range(int(window[0]) + 1, int(window[1]) + 2)
                            if window[0] < n < window[1]]
                warnings = []
                if any(float(x) in (lo_line, hi_line) for x in hit_values):
                    warnings.append(
                        "a whole-number line pushes: that result returns one stake "
                        "instead of paying it, so the gain is roughly halved")
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
                    description=f"{market}{' ' + subject if subject else ''} middle {window[0]:g}-{window[1]:g}",
                    legs=legs, profit_pct=round(hit_pct, 3),
                    stake_total=alloc.total,
                    profit_abs=round(alloc.total * hit_pct / 100.0, 2),
                    max_loss_pct=round(max(cost_pct, 0.0), 3),
                    breakeven_hit_pct=round(max(breakeven, 0.0), 2),
                    hit_values=hit_values,
                    pushes=any(float(x) in (lo_line, hi_line) for x in hit_values),
                    middle_window=window, max_age_seconds=round(max(ages), 1),
                    warnings=warnings,
                ))

    out.sort(key=lambda o: (o.profit_pct / (o.max_loss_pct + 0.5)), reverse=True)
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
                    profit_abs=round(stake * edge / 100.0, 2),
                    fair_prob=round(fair, 5),
                    kelly_stake=stake, anchor_book=anchor_name,
                    max_age_seconds=leg.age_seconds,
                    warnings=[] if anchor_name != "consensus"
                             else ["priced off book consensus, not a sharp anchor"],
                ))

    out.sort(key=lambda o: o.profit_pct, reverse=True)
    return out[: d.ev_max_results]


def scan(board: Board, cfg, now: datetime | None = None) -> list[Opportunity]:
    now = now or utcnow()
    return find_arbitrages(board, cfg, now) + find_middles(board, cfg, now) + find_ev(board, cfg, now)
