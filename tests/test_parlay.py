"""Boosted-parlay builder (edge/arb/parlay.py)."""
from __future__ import annotations

import itertools
import math
from datetime import datetime, timedelta, timezone

import pytest

from edge.arb import parlay as PL
from edge.arb import oddsmath as om

NOW = datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc)
LATER = (NOW + timedelta(hours=3)).isoformat()

# The three offers Adam pasted from the DraftKings app on 2026-10-03, trimmed
# of the eligibility boilerplate (which still mentions "NH", "LA" and friends --
# kept in the MLB one to prove they are not read as sports).
CFB_TEXT = """CFB WEEK 5 STEPPED UP
Get a boost on Week 5 CFB parlays up to 105%!
11 hours left
MAX BET: $10
Opt In
Details:
Opt-in and get 1 Stepped-Up Boost for CFB Week 5 games!
Profit Boost Token only applies to CFB Week 5 traditional parlays, SGPs, and/or SGPx's. All other bet types are excluded from this promotion.
Parlays req. min. 4 legs w/ odds of -250 or longer per leg
Max. bet: $10
Stepped Up Profit Boosts:
4 Legs: 20% Boost
5 Legs: 25% Boost
6 Legs: 30% Boost
7 Legs: 40% Boost
8 Legs: 55% Boost
9 Legs: 70% Boost
10 Legs: 85% Boost
11 Legs: 105% Boost"""

MLB_TEXT = """MLB 50% PARLAY BOOST
Get a 50% Profit Boost to use on a 3+ leg MLB Parlay, SGP, or SGPx bet today!
11 hours left
BOOSTED UP TO MAX $10 WAGER
Opt In
Opt-in and get One (1) Profit Boost for all MLB games on 10/3/2026!
Profit Boost: 50% (Profit boost only applies to winnings, excluding original bet amount)
Profit Boost Token only applies to a MLB Parlay, SGP, or SGPx bet. All other bet types are excluded from this promotion.
Parlays req. min. 3 legs.
Total bet odds must be +300 or longer.
Max. Bet: $10
Wagering on this promotion must use funds from your cash balance or DK Dollars.
Eligibility: Must be 21+ and physically located in AR, AZ, CO, CT, IA, IL, IN, KS, KY, LA (select parishes), MA, MD, ME, MI, MO, NC, NJ, NY, OH, OR, PA, TN, VA, VT, WA, or WV. 18+ and physically located in CA-AB/DC/NH/PR (residents only)/WY."""

TENNIS_TEXT = """TENNIS 30% PARLAY BOOST
Get a 30% Profit Boost to use on a 2+ leg Tennis Parlay, SGP, or SGPx bet today!
1 day left
BOOSTED UP TO MAX $10 WAGER
Profit Boost: 30% (Profit boost only applies to winnings, excluding original bet amount)
Parlays req. min. 2 legs.
Total bet odds must be -200 or longer.
Max. Bet: $10
Wagering on this promotion must use funds from your cash balance or DK Dollars."""


# --------------------------------------------------------------------- promos
def test_the_stepped_cfb_offer_is_read_in_full():
    promo, missing = PL.parse_promo_text(CFB_TEXT, book="draftkings")
    assert promo.sports == ["americanfootball_ncaaf"]
    assert promo.boost_by_legs == PL.CFB_STEPPED
    assert (promo.min_legs, promo.max_legs) == (4, 11)
    assert promo.min_leg_decimal == pytest.approx(1.4)        # -250 per leg
    assert promo.min_total_decimal == 1.0
    assert promo.max_stake == 10.0
    assert missing == []


def test_the_flat_mlb_offer_and_its_total_odds_floor():
    promo, missing = PL.parse_promo_text(MLB_TEXT)
    assert promo.book == "draftkings"                         # "DK Dollars"
    assert promo.sports == ["baseball_mlb"]
    assert promo.boost_by_legs == {3: 0.5}
    assert promo.min_legs == 3
    assert promo.min_total_decimal == pytest.approx(4.0)      # +300
    assert promo.min_leg_decimal == 1.0
    assert promo.max_stake == 10.0
    assert missing == []


def test_the_tennis_offer_and_a_negative_total_floor():
    promo, missing = PL.parse_promo_text(TENNIS_TEXT)
    assert promo.sports == ["tennis_atp"]
    assert promo.boost_by_legs == {2: 0.3}
    assert promo.min_legs == 2
    assert promo.min_total_decimal == pytest.approx(1.5)      # -200
    assert missing == []


def test_unreadable_terms_are_named_not_invented():
    promo, missing = PL.parse_promo_text("Big boost this weekend!")
    assert {"book", "sport", "boost %", "min legs", "max bet"} <= set(missing)
    assert promo.boost_by_legs == {}


def test_the_largest_step_at_or_below_n_applies():
    promo = PL.PRESETS["DK stepped-up CFB (4–11 legs, 20%→105%, −250 or longer per leg)"]
    assert promo.boost_for(3) == 0.0
    assert promo.boost_for(4) == 0.20
    assert promo.boost_for(11) == 1.05
    assert promo.boost_for(14) == 1.05
    flat = PL.ParlayPromo(boost_by_legs={3: .5}, min_legs=3)
    assert flat.boost_for(3) == flat.boost_for(6) == 0.5


def test_step_breakevens_match_the_ratio_of_boosts():
    promo = PL.PRESETS["DK stepped-up CFB (4–11 legs, 20%→105%, −250 or longer per leg)"]
    be = PL.step_breakevens(promo)
    assert be[11] == pytest.approx(1.85 / 2.05)
    assert be[5] == pytest.approx(1.20 / 1.25)
    # every step past 6 legs is paid for by a 7%-hold leg (r ~ 0.93)
    assert all(be[n] < 0.93 for n in range(7, 12))


# ---------------------------------------------------------------------- legs
def cand(event, home, away, prices, market="h2h", point=None, ref=None, when=LATER,
         sport="americanfootball_ncaaf"):
    c = {"sport_key": sport, "event_id": event, "matchup": f"{away} @ {home}",
         "commence_time": when, "market": market, "subject": None, "point": point,
         "legs": [{"side": "home", "label": home, "point": point},
                  {"side": "away", "label": away, "point": -point if point else None}],
         "prices": prices}
    if ref:
        c["reference"] = ref
    return c


def test_fair_price_weights_anchor_double_and_shrinks_toward_the_book():
    c = cand("e1", "H", "A",
             {"home": {"draftkings": 2.10, "fanduel": 1.95},
              "away": {"draftkings": 1.80, "fanduel": 1.95}},
             ref={"home": {"fanatics_markets": 2.0}, "away": {"fanatics_markets": 2.0}})
    fair, sources = PL.fair_probs(c, "draftkings")
    assert sources == ("fanatics_markets", "draftkings", "fanduel")
    dk = om.fair_probs_from_decimals([1.80, 2.10], "power")      # away, home
    fd = om.fair_probs_from_decimals([1.95, 1.95], "power")
    want_home = (2 * 0.5 + dk[1] + fd[1]) / 4
    assert fair["home"] == pytest.approx(want_home)
    assert fair["home"] + fair["away"] == pytest.approx(1.0)


def test_legs_respect_the_per_leg_floor_and_skip_started_games():
    promo = PL.ParlayPromo(book="draftkings", sports=["americanfootball_ncaaf"],
                           boost_by_legs={4: .2}, min_legs=4, min_leg_decimal=1.4)
    cands = [
        cand("e1", "Fav", "Dog", {"home": {"draftkings": 1.25, "fanduel": 1.26},
                                  "away": {"draftkings": 4.2, "fanduel": 4.0}}),
        cand("e2", "H", "A", {"home": {"draftkings": 1.91, "fanduel": 1.91},
                              "away": {"draftkings": 1.91, "fanduel": 1.91}},
             when=(NOW - timedelta(minutes=5)).isoformat()),
    ]
    legs = PL.legs_from_candidates(cands, promo, now=NOW)
    assert [(l.event_id, l.side) for l in legs] == [("e1", "away")]  # -400 fav and live game out


def test_a_book_only_market_is_flagged_and_can_be_excluded():
    promo = PL.ParlayPromo(book="draftkings")
    c = cand("e1", "H", "A", {"home": {"draftkings": 1.91}, "away": {"draftkings": 1.91}})
    legs = PL.legs_from_candidates([c], promo, now=NOW)
    assert legs and all(l.book_only for l in legs)
    assert legs[0].raw_ratio < 1.0
    assert PL.legs_from_candidates([c], promo, now=NOW, allow_book_only=False) == []


def test_a_one_sided_leg_priced_off_another_book_is_not_book_only():
    promo = PL.ParlayPromo(book="fanduel")
    c = cand("e1", "H", "A", {"home": {"draftkings": 1.91, "fanduel": 2.10},
                              "away": {"draftkings": 1.91}})
    legs = PL.legs_from_candidates([c], promo, now=NOW, allow_book_only=False)
    assert [(l.side, l.sources, l.book_only) for l in legs] == [("home", ("draftkings",), False)]


def test_other_sports_and_other_books_are_ignored():
    promo = PL.ParlayPromo(book="fanduel", sports=["baseball_mlb"])
    c = cand("e1", "H", "A", {"home": {"draftkings": 1.91, "fanduel": 1.9},
                              "away": {"draftkings": 1.91, "fanduel": 1.9}})
    assert PL.legs_from_candidates([c], promo, now=NOW) == []
    c["sport_key"] = "baseball_mlb"
    assert {l.side for l in PL.legs_from_candidates([c], promo, now=NOW)} == {"home", "away"}


# --------------------------------------------------------------------- maths
def leg(event, p, d, side="home"):
    return PL.ParlayLeg(event_id=event, sport_key="x", matchup=event, commence_time=LATER,
                        market="h2h", subject=None, point=None, side=side, label=side,
                        decimal=d, fair_prob=p, sources=("draftkings", "fanduel"),
                        own_hold=0.045)


def test_ev_is_the_boosted_payout_times_the_win_chance():
    legs = (leg("a", .5, 1.91), leg("b", .6, 1.6), leg("c", .3, 3.2))
    p = PL.Parlay(legs=legs, boost=0.5, stake=10)
    assert p.ev == pytest.approx(p.win_prob * p.boosted_decimal - 1)
    assert p.boosted_decimal == pytest.approx(1 + (p.decimal - 1) * 1.5)
    assert p.unboosted_ev == pytest.approx(p.win_prob * p.decimal - 1)
    assert p.ev_dollars == pytest.approx(10 * p.ev)


def test_capped_legs_never_inflate_ev():
    p = PL.Parlay(legs=(leg("a", .5, 2.6),), boost=0.0, stake=10)     # r = 1.30
    assert p.ev == pytest.approx(PL.MAX_LEG_RATIO - 1)
    assert p.legs[0].suspect


def _pool():
    """Six games, three legs each, priced so the answer is not obvious."""
    out = []
    specs = [(.70, 1.40), (.50, 1.93), (.30, 3.10)]
    tweak = [1.00, 0.97, 1.02, 0.95, 0.99, 1.01]
    for i, t in enumerate(tweak):
        for j, (p, d) in enumerate(specs):
            out.append(leg(f"g{i}", p, round(d * t * (1 - 0.01 * j), 3), side=f"s{j}"))
    return out


@pytest.mark.parametrize("schedule,min_legs,max_legs,min_total", [
    ({2: .30}, 2, 4, 1.5),
    ({3: .50}, 3, 5, 4.0),
    ({2: .10, 3: .25, 4: .60, 5: 1.0}, 2, 5, 1.0),
])
def test_the_search_matches_brute_force(schedule, min_legs, max_legs, min_total):
    legs = _pool()
    promo = PL.ParlayPromo(boost_by_legs=schedule, min_legs=min_legs, max_legs=max_legs,
                           min_total_decimal=min_total)
    res = PL.build(legs, promo, max_one_in=None, bucket=0.02, leg_band=1e-3, per_event=50)
    by_event = {}
    for l in legs:
        by_event.setdefault(l.event_id, []).append(l)
    best = -1.0
    for n in range(min_legs, max_legs + 1):
        for games in itertools.combinations(sorted(by_event), n):
            for combo in itertools.product(*(by_event[g] for g in games)):
                p = PL.Parlay(legs=combo, boost=promo.boost_for(n), stake=10)
                if p.decimal >= min_total:
                    best = max(best, p.ev)
    assert res.best_ev.ev == pytest.approx(best, abs=1e-9)


def test_one_leg_per_game_and_the_total_floor_hold():
    legs = _pool()
    promo = PL.ParlayPromo(boost_by_legs={3: .5}, min_legs=3, max_legs=5, min_total_decimal=4.0)
    res = PL.build(legs, promo, max_one_in=None)
    for p in [res.pick, res.best_ev, res.best_growth, *res.frontier, *res.by_legs.values()]:
        assert len({l.event_id for l in p.legs}) == p.n
        assert p.decimal >= 4.0
        assert 3 <= p.n <= 5


def test_the_hit_rate_cap_trades_ev_for_a_likelier_ticket():
    legs = _pool()
    promo = PL.ParlayPromo(boost_by_legs={2: .10, 3: .25, 4: .60, 5: 1.0}, min_legs=2, max_legs=5)
    free = PL.build(legs, promo, max_one_in=None)
    capped = PL.build(legs, promo, max_one_in=5)
    assert capped.pick.one_in <= 5
    assert free.best_ev.one_in > 5                   # the uncapped answer is a longer shot
    assert capped.pick.ev <= free.best_ev.ev
    bands = [p.one_in for p in capped.frontier]
    assert bands == sorted(bands)


def test_a_fixed_leg_count_is_honoured():
    res = PL.build(_pool(), PL.ParlayPromo(boost_by_legs={2: .3}, min_legs=2, max_legs=6),
                   n_legs=4, max_one_in=None)
    assert res.pick.n == 4 and set(res.by_legs) == {4}


def test_too_few_games_says_so():
    promo = PL.ParlayPromo(boost_by_legs={4: .2}, min_legs=4, max_legs=11)
    res = PL.build(_pool()[:6], promo)                 # two games
    assert res.pick is None and "only 2 qualify" in res.note


def test_bench_swaps_come_from_other_games_at_a_similar_hit_rate():
    legs = _pool()
    res = PL.build(legs, PL.ParlayPromo(boost_by_legs={2: .3}, min_legs=2, max_legs=2),
                   max_one_in=None)
    swaps = PL.bench(res.pick, legs)
    used = {l.event_id for l in res.pick.legs}
    for i, alts in swaps.items():
        assert alts
        for a in alts:
            assert a.event_id not in used
            assert abs(math.log(a.fair_prob) - math.log(res.pick.legs[i].fair_prob)) <= 0.30


def test_growth_punishes_a_long_shot_ev_does_not():
    # same r = 0.99 on every leg, so only the hit rate differs
    long_shot = PL.Parlay(legs=tuple(leg(f"g{i}", .3, 3.3) for i in range(8)), boost=1.05, stake=10)
    likely = PL.Parlay(legs=tuple(leg(f"g{i}", .7, 0.99 / .7) for i in range(8)), boost=1.05, stake=10)
    assert long_shot.ev > likely.ev
    assert likely.growth(1000) > long_shot.growth(1000)


def test_candidates_carry_the_anchor_separately_from_bettable_prices():
    from edge.arb import ArbConfig
    from edge.arb.models import Board, EventMeta, GroupKey, Quote
    from edge.arb.run import candidates

    cfg = ArbConfig()
    ev = EventMeta("e1", "americanfootball_ncaaf", "NCAAF",
                   datetime.now(timezone.utc) + timedelta(hours=5), "Home", "Away")
    board = Board()
    g = board.group(GroupKey("e1", "h2h"), ev)
    ts = datetime.now(timezone.utc)
    for book, h, a in [("draftkings", 1.9, 1.95), ("fanduel", 1.92, 1.92),
                       ("fanatics_markets", 2.0, 2.0)]:
        g.add(Quote(book, "home", h, None, ts))
        g.add(Quote(book, "away", a, None, ts))
    row = candidates(board, cfg)[0]
    assert "fanatics_markets" not in row["prices"]["home"]
    assert row["reference"] == {"home": {"fanatics_markets": 2.0}, "away": {"fanatics_markets": 2.0}}


def test_filler_legs_must_earn_their_place():
    """A flat boost with near-fair heavy favourites available: adding them
    nudges EV up by noise. The builder should keep the shorter ticket."""
    core = [leg("a", .5, 1.95), leg("b", .5, 1.95)]
    filler = [leg(f"f{i}", .95, 1.06) for i in range(4)]      # r = 1.007 each
    promo = PL.ParlayPromo(boost_by_legs={2: .25}, min_legs=2, max_legs=6)
    res = PL.build(core + filler, promo, max_one_in=None)
    assert res.best_ev.n > 2                                    # EV-max pads it
    assert res.pick.n == 2                                      # the pick does not
    no_rule = PL.build(core + filler, promo, max_one_in=None, per_leg_margin=0)
    assert no_rule.pick.n == res.best_ev.n


def test_a_real_step_still_buys_the_extra_legs():
    legs = [leg(f"g{i}", .5, 1.91) for i in range(11)]          # -110 legs, r = .955
    promo = PL.PRESETS["DK stepped-up CFB (4–11 legs, 20%→105%, −250 or longer per leg)"]
    promo = PL.ParlayPromo(**{**promo.__dict__, "min_leg_decimal": 1.0})
    res = PL.build(legs, promo, max_one_in=None)
    assert res.pick.n == 11
