"""Reading a book's PUBLIC promotions and turning boosts into rules.

The endpoint needs no auth -- verified with a request carrying no cookies at
all -- so the offers on the logged-out homepage are discoverable. Account
tokens in the bet slip's Rewards panel are a different thing and stay
hand-entered.

Parsing terms out of marketing prose is exactly where a wrong answer is
expensive: a boost invented from a misread term sends you at bets that cannot
be placed. These pin the traps found against the live payload.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from edge.arb.promotions import (american_to_decimal_floor, discover,
                                 parse_promotion, parse_response)

DATA = Path(__file__).parent


def _payload():
    return json.loads((DATA / "data_dk_promotions.json").read_text())


def _by_id(pid):
    for zone in _payload()["zones"]:
        for p in zone["promotions"]:
            if p["publicPromotionId"] == pid:
                return p
    raise AssertionError(f"{pid} not in the fixture")


def test_a_single_bet_boost_is_not_marked_parlay_only():
    """The golf token reads 'one single-use 25% Profit Boost token on any Prop
    bet'. Every promotion also carries the boilerplate 'All other bet types are
    excluded', which means "other than the type named above" -- for this one
    that type is a Prop bet. Matching that phrase marked it parlay-only, which
    makes it unusable: a boost that can price a SINGLE is the only kind that
    can be arbitraged at all."""
    p = parse_promotion(_by_id("8W78V5"))
    assert p.boost is not None
    assert p.boost.requires_parlay is False
    assert p.boost.pct == pytest.approx(0.25)
    assert p.boost.sports == ["golf_pga"]


def test_a_genuinely_parlay_only_boost_is_still_caught():
    """'Token only applies to a College Football Parlay, SGP, or SGPx bet' is
    the specific phrasing, and it must still register."""
    p = parse_promotion(_by_id("QRWWE5"))
    assert p.boost.requires_parlay is True
    assert p.boost.pct == pytest.approx(0.50)
    assert p.boost.sports == ["americanfootball_ncaaf"]


def test_the_minimum_odds_floor_is_read_from_the_terms():
    """'Minimum total bet odds of -200 or longer' -> decimal 1.5;
    'Total bet odds must be +300 or longer' -> 4.0."""
    assert parse_promotion(_by_id("8W78V5")).boost.min_decimal == pytest.approx(1.5)
    assert parse_promotion(_by_id("QRWWE5")).boost.min_decimal == pytest.approx(4.0)


def test_a_percentage_in_a_subheadline_is_found():
    """The WNBA body says 'Profit Boost percentage varies'; the subheadline says
    'Get a 30% Profit Boost'. The specific number wins over the vague sentence."""
    p = parse_promotion(_by_id("ZZR1Q2"))
    assert p.boost.pct == pytest.approx(0.30)


def test_offers_with_no_percentage_produce_no_boost():
    """Deposit matches, referral offers and 'percentage varies' copy are not
    boosts. Inventing a number for them would be inventing terms."""
    for pid in ("65XJO1", "NY0W78", "6E22O4"):
        p = parse_promotion(_by_id(pid))
        assert p.boost is None
        assert "percentage" in p.unparsed


def test_the_expiry_comes_through_to_the_boost():
    """This is what stops a token being priced against events it cannot cover."""
    p = parse_promotion(_by_id("8W78V5"))
    assert p.expires_at is not None
    assert p.boost.expires_at == p.expires_at


def test_max_stake_is_always_reported_unread():
    """DraftKings writes 'Max betting limits apply' with no figure -- the real
    cap only appears on the token once claimed. It scales every profit number
    linearly, so it must be flagged rather than quietly defaulted."""
    for pid in ("8W78V5", "QRWWE5", "ZZR1Q2"):
        assert "max stake" in parse_promotion(_by_id(pid)).unparsed


def test_the_sport_words_do_not_shadow_each_other():
    """'College Football' must beat 'Football' and 'WNBA' must beat 'NBA', or a
    college boost is priced against the NFL board."""
    from edge.arb.promotions import SPORT_WORDS
    order = [w for w, _ in SPORT_WORDS]
    assert order.index("college football") < order.index("nfl")
    assert order.index("wnba") < order.index("nba")
    assert parse_promotion(_by_id("QRWWE5")).boost.sports == ["americanfootball_ncaaf"]
    assert parse_promotion(_by_id("ZZR1Q2")).boost.sports == ["basketball_wnba"]


def test_every_promotion_is_seen_once():
    """The same offer appears in several zones."""
    parsed = parse_response(_payload())
    ids = [p.promotion_id for p in parsed]
    assert len(ids) == len(set(ids))


def test_american_floor_conversion():
    assert american_to_decimal_floor(-200) == pytest.approx(1.5)
    assert american_to_decimal_floor(300) == pytest.approx(4.0)
    assert american_to_decimal_floor(-110) == pytest.approx(1.909, abs=1e-3)


def test_discovery_drops_expired_offers_and_fails_soft():
    """An offer that has ended is not a boost you can take, and a network
    failure must return nothing rather than take the scan down."""
    class Boom:
        def post(self, *a, **k):
            raise RuntimeError("network down")
    assert discover(session=Boom()) == []

    class Old:
        def post(self, *a, **k):
            payload = _payload()
            for z in payload["zones"]:
                for p in z["promotions"]:
                    p["expirationDate"] = "2020-01-01T00:00:00.0000000Z"
            class R:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return payload
            return R()
    assert discover(session=Old()) == []
