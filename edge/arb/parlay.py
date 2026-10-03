"""Boosted-parlay builder: which legs a parlay profit boost is worth most on.

DraftKings and FanDuel hand out profit boosts that only work on parlays ("50%
on a 3+ leg MLB parlay", "stepped up: 4 legs 20% ... 11 legs 105%"). A parlay
cannot be hedged the way a boosted single can, so this is the +EV question, not
the arbitrage one: given the token's rules, which legs at THAT book maximise
the expected return?

THE ONE FORMULA EVERYTHING HERE FOLLOWS FROM
For independent legs i (one per game), with the book's decimal price d_i and a
fair win probability p_i taken from the rest of the market:

    r_i = p_i * d_i            the leg's own return per $1 (1.00 = fair)
    R   = prod(r_i)            the parlay's return per $1, unboosted
    P   = prod(p_i)            the chance the whole ticket wins
    EV  = (1 + b) * R  -  b * P  -  1          with profit boost b

(The boost multiplies PROFIT, not the payout: boosted decimal = 1 + (D-1)(1+b),
and P * that - 1 expands to the line above.) What it says, in order:

  1. The vig compounds. A -110 leg has r = 0.955, so 4 of them return 0.83 and
     11 return 0.60. That part of Unabated's "parlays compound the house edge"
     is plain arithmetic and holds.
  2. The boost multiplies R, nearly all of it. So a parlay is +EV once
     (1 + b) * R > 1 -- R just has to beat 1/(1+b). For the 105% step that is
     R > 0.49: eleven ordinary legs (0.955^11 = 0.60) clear it with room. That
     is why "every leg must be +EV on its own", the rule a lot of boost videos
     repeat, is too strict: it is sufficient, not necessary.
  3. On a STEPPED boost, a leg is worth adding whenever its r beats the step:
     r > (1 + b_n) / (1 + b_{n+1}). 10 -> 11 legs on the CFB ladder is
     1.85/2.05 = 0.90, so even a 7%-hold alternate line pays for itself.
     On a FLAT boost there is no step, every extra leg just costs its r, so the
     fewest legs the token allows is usually best.
  4. The "- b * P" term is the boost wasted on a ticket that was likely to win
     anyway -- a boost only pays on profit. It is why a boost is worth more on
     longer odds (the SharpMoney point), and it is small next to term 2 except
     on short, flat-boost parlays.

What the formula does NOT say is how lumpy the result is. Max EV on a stepped
CFB token picks eleven long alternate lines: +97% EV and a 1-in-4,000 ticket.
Eleven -250..-150 favourites give +62% at 1-in-85. Both are "right"; which one
a person should play is a variance choice, so the builder returns a frontier
(the best EV at each hit rate) and caps the default pick at a hit rate the
caller chooses, instead of handing back a lottery ticket labelled "optimal".

INDEPENDENCE
Legs are only combined across DIFFERENT events, so multiplying is exact up to
the small common factors (weather, a slate-wide scoring environment) that link
different games. Same-game legs are an SGP: the book prices them with its own
correlation model plus extra hold (Unabated: 10-20% more), and that price
cannot be rebuilt from single-leg prices -- so SGP/SGPx is out of scope here.

FAIR PRICE
A leg's p is a weighted average of de-vigged probabilities: the vig-free
anchor (Fanatics Markets) at double weight, every other bettable book quoting
both sides, and the betting book itself. Including the book itself is
deliberate shrinkage -- when only one other soft book disagrees with it, the
truth is usually in between, and the stale-line case (one book has moved, the
other has not) is exactly where an unshrunk edge would be largest and least
real. Leg edges above MAX_LEG_RATIO are capped for the same reason and flagged.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import oddsmath as om

# A leg claiming more than this return per $1 is far more often a stale line
# than a gift (seen 2026-10-03: DK's FSU alternate ladder sat 7-12% over
# FanDuel's for hours). It still qualifies; the optimiser just credits it with
# no more than this.
MAX_LEG_RATIO = 1.06
ANCHOR_WEIGHT = 2.0
# EV (per $1) each extra leg must add before the builder prefers the longer
# ticket -- see the parsimony note in build().
PER_LEG_MARGIN = 0.015
OWN_BOOK_WEIGHT = 1.0
BOOK_NAMES = {"draftkings": "DraftKings", "fanduel": "FanDuel", "fanatics": "Fanatics",
              "fanatics_markets": "Fanatics Mkts"}
BOOK_SHORT = {"draftkings": "DK", "fanduel": "FD", "fanatics": "Fan",
              "fanatics_markets": "FMkt"}


# --------------------------------------------------------------------------
# the promotion
# --------------------------------------------------------------------------
@dataclass
class ParlayPromo:
    """One parlay token's rules. A flat boost is a one-step schedule."""
    book: str = "draftkings"
    sports: list[str] = field(default_factory=list)             # empty == any
    # legs -> boost; the largest key <= n applies. {} is "no boost".
    boost_by_legs: dict[int, float] = field(default_factory=dict)
    min_legs: int = 2
    max_legs: int = 8
    min_leg_decimal: float = 1.0        # "-250 or longer per leg" -> 1.40
    min_total_decimal: float = 1.0      # "+300 or longer" -> 4.00
    max_stake: float = 10.0
    label: str = ""

    def boost_for(self, n: int) -> float:
        keys = [k for k in self.boost_by_legs if k <= n]
        return self.boost_by_legs[max(keys)] if keys else 0.0

    @property
    def stepped(self) -> bool:
        return len(set(self.boost_by_legs.values())) > 1

    def describe(self) -> str:
        if self.label:
            return self.label
        if not self.boost_by_legs:
            return "No boost"
        if self.stepped:
            lo, hi = min(self.boost_by_legs), max(self.boost_by_legs)
            return (f"Stepped {self.boost_by_legs[lo]:.0%} ({lo} legs) → "
                    f"{self.boost_by_legs[hi]:.0%} ({hi} legs)")
        return f"{self.boost_for(self.max_legs):.0%} on {self.min_legs}+ legs"


CFB_STEPPED = {4: .20, 5: .25, 6: .30, 7: .40, 8: .55, 9: .70, 10: .85, 11: 1.05}

# The offers Adam pasted on 2026-10-03. DraftKings reruns these shapes weekly,
# so they are presets rather than one-offs; the numbers are editable in the app.
PRESETS: dict[str, ParlayPromo] = {
    "DK stepped-up CFB (4–11 legs, 20%→105%, −250 or longer per leg)": ParlayPromo(
        book="draftkings", sports=["americanfootball_ncaaf"], boost_by_legs=dict(CFB_STEPPED),
        min_legs=4, max_legs=11, min_leg_decimal=1.4, max_stake=10.0),
    "DK MLB 50% (3+ legs, total +300 or longer)": ParlayPromo(
        book="draftkings", sports=["baseball_mlb"], boost_by_legs={3: .50},
        min_legs=3, max_legs=6, min_total_decimal=4.0, max_stake=10.0),
    "DK Tennis 30% (2+ legs, total −200 or longer)": ParlayPromo(
        book="draftkings", sports=["tennis_atp"], boost_by_legs={2: .30},
        min_legs=2, max_legs=5, min_total_decimal=1.5, max_stake=10.0),
    "FanDuel 25% parlay boost (2+ legs)": ParlayPromo(
        book="fanduel", sports=[], boost_by_legs={2: .25},
        min_legs=2, max_legs=6, max_stake=10.0),
    "No boost (straight parlay)": ParlayPromo(
        book="draftkings", sports=[], boost_by_legs={}, min_legs=2, max_legs=6),
}


def american_to_decimal(american: float) -> float:
    a = float(american)
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


# Earliest mention wins, so a headline's sport beats one named in boilerplate;
# longer phrases are tried first at the same position ("college football"
# before "football", "wnba" before "nba").
_SPORT_PATTERNS: list[tuple[str, str]] = [
    (r"college\s+football|\bcfb\b|\bncaaf\b|\bncaa\s+football", "americanfootball_ncaaf"),
    (r"college\s+basketball|\bcbb\b|\bncaab\b", "basketball_ncaab"),
    (r"\bwnba\b", "basketball_wnba"),
    (r"\bnba\b", "basketball_nba"),
    (r"\bnfl\b", "americanfootball_nfl"),
    (r"\bmlb\b|\bbaseball\b", "baseball_mlb"),
    (r"\bnhl\b|\bhockey\b", "icehockey_nhl"),
    (r"\btennis\b|\batp\b|\bwta\b", "tennis_atp"),
    (r"\bufc\b|\bmma\b", "mma_ufc"),
    (r"\bgolf\b|\bpga\b", "golf_pga"),
    (r"\bsoccer\b|\bmls\b|premier\s+league|champions\s+league", "soccer"),
]
_STEP = re.compile(r"(\d{1,2})\s*legs?\s*:\s*(\d{1,3})\s*%", re.I)
_FLAT = re.compile(r"profit\s*boost\s*:?\s*(\d{1,3})\s*%|(\d{1,3})\s*%\s*(?:profit|parlay)\s*boost",
                   re.I)
_MIN_LEGS = re.compile(r"min(?:imum)?\.?\s*(?:of\s*)?(\d{1,2})\s*legs?|(\d{1,2})\s*\+\s*legs?", re.I)
_PER_LEG = re.compile(r"([+-]\d{3,4})\s*or\s*longer\s*per\s*leg|per\s*leg[^.]{0,30}?([+-]\d{3,4})", re.I)
_TOTAL = re.compile(r"total\s*(?:bet\s*)?odds\s*(?:must\s*be|of)\s*([+-]?\d{3,5})", re.I)
_MAX_BET = re.compile(r"max(?:imum)?\.?\s*bet\s*:?\s*\$\s*(\d+(?:\.\d+)?)"
                      r"|max\.?\s*\$\s*(\d+(?:\.\d+)?)\s*wager", re.I)


def parse_promo_text(text: str, book: str | None = None) -> tuple[ParlayPromo, list[str]]:
    """A promo's terms, pasted from the app -> (ParlayPromo, what could not be read).

    Anything missing is left at the no-constraint default and NAMED in the
    second value, so the page can say "check this" instead of quietly
    inventing a term -- a misread minimum turns into a ticket the book refuses.
    """
    t = re.sub(r"\s+", " ", text or "")
    missing: list[str] = []

    if book is None:
        if re.search(r"fanduel", t, re.I):
            book = "fanduel"
        elif re.search(r"draftkings|\bDK\b", t, re.I):
            book = "draftkings"
        else:
            book = "draftkings"
            missing.append("book")

    hits = []
    for pat, key in _SPORT_PATTERNS:
        m = re.search(pat, t, re.I)
        if m:
            hits.append((m.start(), -len(m.group(0)), key))
    sports = [min(hits)[2]] if hits else []
    if not sports:
        missing.append("sport")

    steps = {int(n): int(p) / 100.0 for n, p in _STEP.findall(t)}
    m_legs = _MIN_LEGS.search(t)
    min_legs = int(m_legs.group(1) or m_legs.group(2)) if m_legs else None
    if steps:
        schedule = steps
        min_legs = min_legs or min(steps)
        max_legs = max(steps)
    else:
        m = _FLAT.search(t)
        if m:
            pct = int(m.group(1) or m.group(2)) / 100.0
        else:
            pct = 0.0
            missing.append("boost %")
        if min_legs is None:
            missing.append("min legs")
            min_legs = 2
        schedule = {min_legs: pct} if pct else {}
        max_legs = min(min_legs + 4, 12)

    per_leg = 1.0
    m = _PER_LEG.search(t)
    if m:
        per_leg = american_to_decimal(int(m.group(1) or m.group(2)))
    total = 1.0
    m = _TOTAL.search(t)
    if m:
        total = american_to_decimal(int(m.group(1)))
    stake = 10.0
    m = _MAX_BET.search(t)
    if m:
        stake = float(m.group(1) or m.group(2))
    else:
        missing.append("max bet")

    promo = ParlayPromo(book=book, sports=sports, boost_by_legs=schedule,
                        min_legs=min_legs, max_legs=max_legs,
                        min_leg_decimal=per_leg, min_total_decimal=total, max_stake=stake)
    return promo, missing


# --------------------------------------------------------------------------
# legs
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ParlayLeg:
    event_id: str
    sport_key: str
    matchup: str
    commence_time: str
    market: str
    subject: str | None
    point: float | None
    side: str
    label: str
    decimal: float                 # the betting book's price
    fair_prob: float
    sources: tuple[str, ...]       # who set the fair price
    own_hold: float | None         # the betting book's overround here, if two-sided

    @property
    def raw_ratio(self) -> float:
        return self.fair_prob * self.decimal

    @property
    def ratio(self) -> float:
        """r, capped -- what the optimiser credits."""
        return min(self.raw_ratio, MAX_LEG_RATIO)

    @property
    def edge_pct(self) -> float:
        return (self.raw_ratio - 1.0) * 100.0

    @property
    def suspect(self) -> bool:
        return self.raw_ratio > MAX_LEG_RATIO

    @property
    def book_only(self) -> bool:
        return len(self.sources) == 1

    @property
    def american(self) -> str:
        return om.format_american(self.decimal)

    def describe(self) -> str:
        return leg_text(self.market, self.subject, self.label, self.point)


def _pretty_market(market: str) -> str:
    m = re.sub(r"^(player|batter|pitcher)_", "", market)
    return m.replace("_", " ").title()


def leg_text(market: str, subject: str | None, label: str, point: float | None) -> str:
    if market == "h2h":
        return f"{label} ML" if label != "Draw" else "Draw"
    if market.startswith(("spread", "alternate_spread")) and point is not None:
        return f"{label} {point:+g}"
    pt = f" {point:g}" if point is not None else ""
    if subject:
        return f"{subject} {label}{pt} {_pretty_market(market)}"
    if market.startswith(("total", "alternate_total")):
        return f"{label}{pt}"
    return f"{label}{pt} {_pretty_market(market)}"


def fair_probs(cand: dict, book: str, method: str = "power",
               anchor_weight: float = ANCHOR_WEIGHT,
               own_weight: float = OWN_BOOK_WEIGHT) -> tuple[dict[str, float], tuple[str, ...]] | None:
    """Weighted fair probability per side for one snapshot candidate."""
    sides = sorted(cand.get("prices") or {})
    if len(sides) < 2:
        return None
    prices = cand["prices"]
    ref = cand.get("reference") or {}
    sources: list[tuple[str, float, list[float]]] = []

    def full(book_prices: dict, b: str) -> list[float] | None:
        vals = [book_prices.get(s, {}).get(b) for s in sides]
        return vals if all(v and v > 1.0 for v in vals) else None

    for b in sorted({b for s in sides for b in ref.get(s, {})}):
        vals = full(ref, b)
        if vals:
            imp = [om.implied_prob(v) for v in vals]
            tot = sum(imp)
            sources.append((b, anchor_weight, [x / tot for x in imp]))
    for b in sorted({b for s in sides for b in prices.get(s, {})}):
        vals = full(prices, b)
        if not vals:
            continue
        try:
            probs = om.fair_probs_from_decimals(vals, method)
        except (ValueError, ZeroDivisionError):
            continue
        sources.append((b, own_weight if b == book else 1.0, probs))
    if not sources:
        return None
    wsum = sum(w for _, w, _ in sources)
    fair = {s: sum(w * pr[i] for _, w, pr in sources) / wsum for i, s in enumerate(sides)}
    return fair, tuple(b for b, _, _ in sources)


def _as_utc(iso: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def legs_from_candidates(cands: list[dict], promo: ParlayPromo, now: datetime | None = None,
                         min_minutes_to_start: float = 10.0, method: str = "power",
                         allow_book_only: bool = True,
                         max_leg_decimal: float | None = None) -> list[ParlayLeg]:
    """Every leg the token's book offers that the token's rules allow."""
    now = now or datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=min_minutes_to_start)
    out: list[ParlayLeg] = []
    for c in cands:
        if promo.sports and not _sport_ok(c.get("sport_key", ""), promo.sports):
            continue
        start = _as_utc(c.get("commence_time"))
        if start is None or start < cutoff:
            continue
        prices = c.get("prices") or {}
        if not any(promo.book in per_book for per_book in prices.values()):
            continue
        got = fair_probs(c, promo.book, method)
        if got is None:
            continue
        fair, sources = got
        if not allow_book_only and set(sources) <= {promo.book}:
            continue
        own = [prices[s].get(promo.book) for s in sorted(prices)]
        own_hold = (sum(om.implied_prob(d) for d in own) - 1.0) if all(own) else None
        by_side = {l["side"]: l for l in c.get("legs") or []}
        for side, per_book in prices.items():
            d = per_book.get(promo.book)
            if not d or side not in fair:
                continue
            if d < promo.min_leg_decimal - 1e-9:
                continue
            if max_leg_decimal and d > max_leg_decimal + 1e-9:
                continue
            leg = by_side.get(side, {})
            out.append(ParlayLeg(
                event_id=str(c["event_id"]), sport_key=c.get("sport_key", ""),
                matchup=c.get("matchup", ""), commence_time=str(c.get("commence_time")),
                market=c.get("market", ""), subject=c.get("subject"),
                point=leg.get("point", c.get("point")), side=side,
                label=leg.get("label") or side.title(), decimal=float(d),
                fair_prob=fair[side], sources=sources, own_hold=own_hold))
    return out


def _sport_ok(sport_key: str, wanted: list[str]) -> bool:
    for w in wanted:
        if sport_key == w or (w == "soccer" and sport_key.startswith("soccer_")) \
                or (w == "tennis_atp" and sport_key.startswith("tennis")):
            return True
    return False


# --------------------------------------------------------------------------
# a parlay
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Parlay:
    legs: tuple[ParlayLeg, ...]
    boost: float
    stake: float

    @property
    def n(self) -> int:
        return len(self.legs)

    @property
    def decimal(self) -> float:
        return math.prod(l.decimal for l in self.legs)

    @property
    def boosted_decimal(self) -> float:
        return om.boosted(self.decimal, self.boost)

    @property
    def win_prob(self) -> float:
        return math.prod(l.fair_prob for l in self.legs)

    @property
    def ratio(self) -> float:
        """R, from the capped leg ratios: what EV is computed on."""
        return math.prod(l.ratio for l in self.legs)

    @property
    def ev(self) -> float:
        """Expected profit per $1 staked: (1+b)R - bP - 1."""
        return (1 + self.boost) * self.ratio - self.boost * self.win_prob - 1.0

    @property
    def unboosted_ev(self) -> float:
        return self.ratio - 1.0

    @property
    def ev_dollars(self) -> float:
        return self.ev * self.stake

    @property
    def to_win(self) -> float:
        return self.stake * (self.boosted_decimal - 1.0)

    @property
    def one_in(self) -> float:
        return 1.0 / self.win_prob if self.win_prob > 0 else float("inf")

    def growth(self, bankroll: float) -> float:
        """Expected log growth of `bankroll` from this one ticket (Kelly's
        yardstick). Negative means the stake is too large for how long the shot
        is, even when EV is positive."""
        if bankroll <= self.stake:
            return float("-inf")
        p = self.win_prob
        win = math.log1p(self.to_win / bankroll)
        lose = math.log1p(-self.stake / bankroll)
        return p * win + (1 - p) * lose


# --------------------------------------------------------------------------
# the optimiser
# --------------------------------------------------------------------------
HIT_BANDS: tuple[tuple[float, float], ...] = (
    (1, 3), (3, 6), (6, 12), (12, 25), (25, 50), (50, 100), (100, 250),
    (250, 1000), (1000, float("inf")))


@dataclass
class BuildResult:
    promo: ParlayPromo
    legs_considered: int
    events_considered: int
    pick: Parlay | None                  # best EV within the hit-rate floor
    best_ev: Parlay | None               # best EV, any hit rate
    best_growth: Parlay | None           # best for the bankroll, Kelly-wise
    by_legs: dict[int, Parlay]           # best EV at each leg count (within floor)
    frontier: list[Parlay]               # best EV per hit-rate band
    note: str = ""


def _diverse(legs: list[ParlayLeg], per_event: int, band: float) -> list[ParlayLeg]:
    """Keep each event's best leg in every hit-rate band, so the search can
    trade EV for hit rate instead of only ever seeing the longest legs."""
    best: dict[int, ParlayLeg] = {}
    for l in legs:
        k = int(-math.log(l.fair_prob) / band)
        if k not in best or l.ratio > best[k].ratio:
            best[k] = l
    return sorted(best.values(), key=lambda l: -l.ratio)[:per_event]


def build(legs: list[ParlayLeg], promo: ParlayPromo, n_legs: int | None = None,
          stake: float | None = None, max_one_in: float | None = 100.0,
          bankroll: float = 1000.0, per_event: int = 12, leg_band: float = 0.12,
          bucket: float = 0.12, keep: int = 2,
          per_leg_margin: float = PER_LEG_MARGIN) -> BuildResult:
    """Search one-leg-per-event parlays; return the pick and the frontier.

    Dynamic programme over events. A state is (legs so far, bucket of log P),
    and each cell keeps its `keep` best log R. EV only depends on R, P and the
    leg count, so the best R in every (n, P) cell is all the final answer needs
    -- and keeping P as a dimension is what yields the whole EV-vs-hit-rate
    frontier and lets the total-odds minimum (D = R/P) be checked exactly.
    """
    if stake is None:
        stake = promo.max_stake
    elif promo.max_stake:
        stake = min(stake, promo.max_stake)
    kmax = n_legs or promo.max_legs
    kmin = n_legs or promo.min_legs
    by_event: dict[str, list[ParlayLeg]] = {}
    for l in legs:
        if l.fair_prob <= 0 or l.decimal <= 1.0:
            continue
        by_event.setdefault(l.event_id, []).append(l)
    events = []
    for eid, ls in by_event.items():
        kept = _diverse(ls, per_event, leg_band)
        events.append([(math.log(l.ratio), math.log(l.fair_prob), l) for l in kept])
    events.sort(key=lambda opts: -max(o[0] for o in opts))

    states: dict[tuple[int, int], list[tuple[float, float, tuple]]] = {(0, 0): [(0.0, 0.0, ())]}
    for opts in events:
        # Copy every cell's CONTENTS first: a state created from this event
        # must not be extended by another leg of the same event in this pass.
        before = [(k, list(cell)) for (k, _), cell in states.items() if k < kmax]
        for k, cell in before:
            for lr0, lp0, chosen in cell:
                for lr, lp, leg in opts:
                    nlr, nlp = lr0 + lr, lp0 + lp
                    key = (k + 1, int(-nlp / bucket))
                    tgt = states.setdefault(key, [])
                    if len(tgt) < keep:
                        tgt.append((nlr, nlp, chosen + (leg,)))
                        tgt.sort(key=lambda s: -s[0])
                    elif nlr > tgt[-1][0]:
                        tgt[-1] = (nlr, nlp, chosen + (leg,))
                        tgt.sort(key=lambda s: -s[0])

    found: list[Parlay] = []
    for (k, _), cell in states.items():
        if k < max(kmin, promo.min_legs, 1) or k > kmax:
            continue
        for lr, lp, chosen in cell:
            if math.exp(lr - lp) < promo.min_total_decimal - 1e-9:
                continue
            found.append(Parlay(legs=chosen, boost=promo.boost_for(k), stake=stake))

    result = BuildResult(promo=promo, legs_considered=len(legs), events_considered=len(events),
                         pick=None, best_ev=None, best_growth=None, by_legs={}, frontier=[])
    if not found:
        need = max(kmin, promo.min_legs)
        result.note = (f"Not enough games: the token needs {need} legs from different games "
                       f"and only {len(events)} qualify." if len(events) < need
                       else "No combination meets the token's odds rules.")
        return result

    def within(p: Parlay) -> bool:
        return max_one_in is None or p.one_in <= max_one_in * (1 + 1e-9)

    result.best_ev = max(found, key=lambda p: p.ev)
    result.best_growth = max(found, key=lambda p: p.growth(bankroll))
    ok = [p for p in found if within(p)]
    result.pick = max(ok, key=lambda p: p.ev) if ok else None
    if result.pick is None:
        result.note = (f"Nothing hits more often than 1 in {max_one_in:,.0f} under these rules; "
                       f"showing the most likely ticket instead.")
        result.pick = max(found, key=lambda p: p.win_prob)
    for n in sorted({p.n for p in found}):
        pool = [p for p in found if p.n == n]
        pool_ok = [p for p in pool if within(p)] or pool
        result.by_legs[n] = max(pool_ok, key=lambda p: p.ev)
    # Parsimony: a leg's edge is an estimate, good to maybe a point or two, so
    # a longer ticket has to beat a shorter one by more than that per extra
    # leg. Otherwise a flat boost pads the slip with -1000 alternate-line
    # favourites "worth" +0.5% each -- noise, and one more price to go stale.
    if n_legs is None and per_leg_margin > 0:
        for n, q in sorted(result.by_legs.items()):
            if n >= result.pick.n:
                break
            if within(q) and q.ev >= result.pick.ev - per_leg_margin * (result.pick.n - n):
                result.pick = q
                break
    for lo, hi in HIT_BANDS:
        band = [p for p in found if lo <= p.one_in < hi]
        if band:
            result.frontier.append(max(band, key=lambda p: p.ev))
    return result


def bench(parlay: Parlay, legs: list[ParlayLeg], per_leg: int = 2,
          band: float = 0.30) -> dict[int, list[ParlayLeg]]:
    """Swaps for each leg, from games not already on the ticket, at a similar
    hit rate -- for when the book has moved or pulled a line by bet time."""
    used = {l.event_id for l in parlay.legs}
    out: dict[int, list[ParlayLeg]] = {}
    taken: set[tuple] = set()
    for i, leg in enumerate(parlay.legs):
        lp = math.log(leg.fair_prob)
        pool = [l for l in legs if l.event_id not in used
                and abs(math.log(l.fair_prob) - lp) <= band
                and (l.event_id, l.market, l.point, l.side) not in taken]
        pool.sort(key=lambda l: -l.ratio)
        picks, seen_ev = [], set()
        for l in pool:
            if l.event_id in seen_ev:
                continue
            picks.append(l)
            seen_ev.add(l.event_id)
            taken.add((l.event_id, l.market, l.point, l.side))
            if len(picks) >= per_leg:
                break
        out[i] = picks
    return out


def step_breakevens(promo: ParlayPromo) -> dict[int, float]:
    """For a stepped token: the leg ratio r that makes going from n-1 to n
    legs worth it, (1 + b_{n-1}) / (1 + b_n). Ignores the small -bP term."""
    out = {}
    for n in range(promo.min_legs + 1, promo.max_legs + 1):
        b0, b1 = promo.boost_for(n - 1), promo.boost_for(n)
        out[n] = (1 + b0) / (1 + b1)
    return out
