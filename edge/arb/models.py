"""Canonical data model shared by every provider and the detection engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class EventMeta:
    event_id: str
    sport_key: str
    sport_title: str
    commence_time: datetime
    home_team: str | None
    away_team: str | None

    # sports whose fixtures have no venue, so "@" would imply one
    VS_SPORTS = ("tennis", "golf", "mma", "boxing")

    @property
    def matchup(self) -> str:
        if self.away_team and self.home_team:
            sep = " vs " if self.sport_key.startswith(self.VS_SPORTS) else " @ "
            return f"{self.away_team}{sep}{self.home_team}"
        return self.home_team or self.away_team or self.event_id

    def minutes_to_start(self, now: datetime | None = None) -> float:
        return (self.commence_time - (now or utcnow())).total_seconds() / 60.0


def field_event(sport_key: str, when: datetime, title: str = "") -> EventMeta:
    """One synthetic event for a sport identified by its participants.

    Golf has no fixture to join on. There is no home and away, and the books do
    not even agree on what an event IS: FanDuel splits a tournament into "2
    Balls", "Hole Match Betting" and the tournament itself, DraftKings has a
    single "Tour Championship". Nothing matches, so every pairing stayed in its
    own book's event and no cross-book group ever formed.

    What DOES identify a golf market is the pairing -- two named players, which
    both books agree on. So the event is collapsed to one per tour and the
    subject carries the identity.

    The cost, stated plainly: all golf on a tour shares one start time, taken
    from whichever book created the event first (Board.group keeps the first).
    That only feeds the in-window check, and it is why golf is treated as one
    block rather than per-tournament.
    """
    return EventMeta(event_id=f"{sport_key}:field", sport_key=sport_key,
                     sport_title=sport_key.split("_")[0].title(),
                     commence_time=when, home_team=title or sport_key, away_team=None)


@dataclass(frozen=True)
class GroupKey:
    """Identifies one set of mutually exclusive outcomes.

    Two quotes may only be combined into an arbitrage if their GroupKeys match
    exactly -- same event, same market, same subject (player/team) and same
    line. That equality is the single guard against comparing, say, Over 45.5
    at one book with Under 46.5 at another and calling it risk-free.
    """
    event_id: str
    market: str
    subject: str | None = None
    point: float | None = None

    def label(self, event: EventMeta) -> str:
        bits = [self.market]
        if self.subject:
            bits.append(self.subject)
        if self.point is not None:
            bits.append(f"{self.point:+g}" if self.market.startswith(("spread", "alternate_spread")) else f"{self.point:g}")
        return " ".join(bits)


@dataclass(frozen=True)
class Quote:
    book: str
    side: str
    decimal: float
    point: float | None
    last_update: datetime
    link: str | None = None
    limit: float | None = None

    def age_seconds(self, now: datetime | None = None) -> float:
        """Seconds since this quote was *fetched*.

        Clamped at zero: the wall clock on a laptop or WSL host jumps when the
        machine sleeps and NTP corrects it, which otherwise yields
        future-dated quotes and a negative age that passes every check.
        """
        return max(0.0, ((now or utcnow()) - self.last_update).total_seconds())


# Side sets that are exhaustive and mutually exclusive by construction, at a
# fixed line. Unlike an outright field, these need no inferring.
COMPLEMENTARY_SIDES: tuple[frozenset[str], ...] = (
    frozenset({"over", "under"}),
    frozenset({"home", "away"}),
    frozenset({"yes", "no"}),
)


@dataclass
class MarketGroup:
    key: GroupKey
    event: EventMeta
    quotes: dict[str, dict[str, Quote]] = field(default_factory=dict)   # side -> book -> quote
    book_sides: dict[str, set[str]] = field(default_factory=dict)       # book -> sides offered
    # (side, book, old, new) where one book quoted one side twice, far apart
    conflicts: list = field(default_factory=list)

    # One book quoting the SAME side of the SAME group at two prices this far
    # apart is not a price move, it is two different bets that landed on one
    # key. 1.25 is well outside a refresh and well inside the gaps that have
    # actually been caught: a team total at 4.30 against the game total at
    # 1.32, "Either Pitcher 11.5+ K" at 19.00 against "Combined 11.5+" at 1.61.
    CONFLICT_RATIO = 1.25

    def add(self, q: Quote) -> None:
        prev = self.quotes.setdefault(q.side, {}).get(q.book)
        if prev is not None and prev.decimal > 0:
            ratio = max(q.decimal, prev.decimal) / min(q.decimal, prev.decimal)
            if ratio > self.CONFLICT_RATIO:
                # Counted, never raised: a bad mapping must not take the scan
                # down, and the count is the smoke alarm. In a one-shot scan
                # every quote is seconds old, so this can only be a collision;
                # in a long-running watch loop a real move could trip it, which
                # is why run.scan() reports it rather than acting on it.
                self.conflicts.append((q.side, q.book, prev.decimal, q.decimal))
        # The board is long-lived, so a newer quote must always displace an
        # older one -- otherwise a stale high price would sit here forever and
        # manufacture arbs that no longer exist. Equal timestamps mean the same
        # refresh listed the side twice, and there the better price wins.
        if (prev is None
                or q.last_update > prev.last_update
                or (q.last_update == prev.last_update and q.decimal > prev.decimal)):
            self.quotes[q.side][q.book] = q
        self.book_sides.setdefault(q.book, set()).add(q.side)

    def expected_sides(self) -> set[str]:
        """The complete outcome set. A group is only tradeable if every side of
        it can be priced.

        For a binary market the answer is known a priori, so both sides count
        even when no single book posts both -- DraftKings prices props as
        one-sided thresholds ("2+"), and the opposing Under comes from another
        book entirely. Requiring one book to show the whole market would miss
        every such pairing.

        Anything else (an outright field) still has to be inferred from
        whichever book offers the most sides, because there is no way to know
        how many runners exist.
        """
        seen = set(self.quotes)
        for pair in COMPLEMENTARY_SIDES:
            if seen == set(pair):
                return set(pair)
        if not self.book_sides:
            return set()
        return max(self.book_sides.values(), key=len)

    def best(self, side: str, books: set[str]) -> Quote | None:
        candidates = [q for b, q in self.quotes.get(side, {}).items() if b in books]
        return max(candidates, key=lambda q: q.decimal) if candidates else None


@dataclass
class Board:
    """Everything currently priced, keyed by group."""
    groups: dict[GroupKey, MarketGroup] = field(default_factory=dict)
    events: dict[str, EventMeta] = field(default_factory=dict)
    # (event_id, market, book) -> the point that book's OWN full-game line
    # currently posts, set only by the ingestion call that fetches THAT
    # market (never an alternate-line pull). This is what lets an alternate
    # ladder be checked against the number it is supposed to be built around,
    # rather than trusted just because its own prices are internally
    # consistent -- an internally-consistent ladder can still be one DraftKings
    # has already moved past and not yet pulled or repriced. See
    # engine.stale_alt_ladders.
    main_points: dict[tuple[str, str, str], float] = field(default_factory=dict)

    def record_main_point(self, event_id: str, market: str, book: str,
                          point: float | None) -> None:
        if point is not None:
            self.main_points[(event_id, market, book)] = point

    def group(self, key: GroupKey, event: EventMeta) -> MarketGroup:
        """Groups sharing an event_id share ONE EventMeta.

        Whichever source registers the event first defines it, and later
        callers reuse that rather than attaching their own copy. Two books
        describing one event must not disagree about when it starts.

        This only ever bit where events are synthetic: golf collapses ten
        tournaments onto one id (see field_event), so groups built from the
        Presidents Cup were carrying a start time in late September while
        groups built from this weekend's round carried today's -- and the
        window check then dropped two thirds of the board depending on which
        league happened to be parsed first.
        """
        canonical = self.events.setdefault(event.event_id, event)
        g = self.groups.get(key)
        if g is None:
            g = self.groups[key] = MarketGroup(key=key, event=canonical)
        return g

    def __len__(self) -> int:
        return len(self.groups)

    @property
    def quote_count(self) -> int:
        return sum(len(bk) for g in self.groups.values() for bk in g.quotes.values())

    def prune(self, started_grace_minutes: float = 20.0, max_quote_age_seconds: float = 1800.0,
              now: datetime | None = None) -> int:
        """Drop finished events and quotes nothing has refreshed. Without this
        a long `watch` run accumulates dead lines and slows every scan."""
        now = now or utcnow()
        dropped = 0
        for key in list(self.groups):
            g = self.groups[key]
            if g.event.minutes_to_start(now) < -started_grace_minutes:
                del self.groups[key]; dropped += 1
                continue
            for side in list(g.quotes):
                for book in list(g.quotes[side]):
                    if g.quotes[side][book].age_seconds(now) > max_quote_age_seconds:
                        del g.quotes[side][book]; dropped += 1
                if not g.quotes[side]:
                    del g.quotes[side]
            if not g.quotes:
                del self.groups[key]
                continue
            g.book_sides = {}
            for side, per_book in g.quotes.items():
                for book in per_book:
                    g.book_sides.setdefault(book, set()).add(side)
        live = {g.event.event_id for g in self.groups.values()}
        for eid in list(self.events):
            if eid not in live:
                del self.events[eid]
        return dropped
