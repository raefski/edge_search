"""Detection behaviour for the arbitrage tool, against real captured payloads.

These exercise `edge/arb` directly -- the code the Streamlit page and
scripts/arb_scan.py actually run. The standalone project's suite covered its
own CLI, scheduler and Odds API provider, none of which ship here.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from edge.arb import oddsmath as om
from edge.arb.config import ArbConfig
from edge.arb.engine import (find_arbitrages, find_ev, find_middles,
                             middle_scenarios, stale_alt_ladders)
from edge.arb.models import Board, EventMeta, GroupKey, Quote

DATA = Path(__file__).parent


def cfg() -> ArbConfig:
    c = ArbConfig()
    c.detect.min_profit_pct = 0.1
    return c


def board_with(*quotes, hours_ahead: float = 3.0) -> tuple[Board, EventMeta]:
    b = Board()
    ev = EventMeta("e1", "baseball_mlb", "MLB",
                   datetime.now(timezone.utc) + timedelta(hours=hours_ahead),
                   "Home Team", "Away Team")
    b.events["e1"] = ev
    now = datetime.now(timezone.utc)
    for market, subject, point, side, book, dec in quotes:
        b.group(GroupKey("e1", market, subject, point), ev).add(
            Quote(book=book, side=side, decimal=dec, point=point, last_update=now))
    return b, ev


# --- arbitrage --------------------------------------------------------------
def test_two_book_arbitrage_is_found_and_sized():
    b, _ = board_with(("totals", None, 8.5, "over", "draftkings", 2.10),
                      ("totals", None, 8.5, "under", "fanduel", 2.05))
    opps = find_arbitrages(b, cfg())
    assert len(opps) == 1
    o = opps[0]
    assert o.profit_pct == pytest.approx(3.73, abs=0.15)
    assert {l.book for l in o.legs} == {"draftkings", "fanduel"}
    assert min(l.payout for l in o.legs) > o.stake_total


def test_both_legs_at_one_book_is_not_an_arbitrage():
    """Same book on both sides is a data artefact, not an edge."""
    b, _ = board_with(("totals", None, 8.5, "over", "draftkings", 2.10),
                      ("totals", None, 8.5, "under", "draftkings", 2.05))
    assert find_arbitrages(b, cfg()) == []


def test_different_lines_do_not_pair_as_an_arbitrage():
    """Over 8.5 and Under 9.5 are a middle, never a guaranteed win."""
    b, _ = board_with(("totals", None, 8.5, "over", "draftkings", 2.10),
                      ("totals", None, 9.5, "under", "fanduel", 2.05))
    assert find_arbitrages(b, cfg()) == []


def test_incomplete_market_is_skipped():
    b, _ = board_with(("totals", None, 8.5, "over", "draftkings", 2.10))
    assert find_arbitrages(b, cfg()) == []


# --- time window ------------------------------------------------------------
def test_far_future_games_are_ignored():
    c = cfg()
    b, _ = board_with(("totals", None, 8.5, "over", "draftkings", 2.10),
                      ("totals", None, 8.5, "under", "fanduel", 2.05),
                      hours_ahead=c.detect.max_hours_to_start + 24)
    assert find_arbitrages(b, c) == []


def test_in_progress_games_are_ignored():
    b, _ = board_with(("totals", None, 8.5, "over", "draftkings", 2.10),
                      ("totals", None, 8.5, "under", "fanduel", 2.05),
                      hours_ahead=-1)
    assert find_arbitrages(b, cfg()) == []


# --- middles ----------------------------------------------------------------
def test_whole_number_line_pushes_rather_than_paying():
    d1, d2 = om.american_to_decimal(-112), om.american_to_decimal(-110)
    sc = middle_scenarios(d1, d2, 48.5, 49.0, 502, 498)
    assert sc[48] < 0 and sc[50] < 0 and sc[49] > 0
    both_win = (2.0 / om.arb_sum([d1, d2]) - 1.0) * 1000
    assert sc[49] < both_win * 0.6, "a push must cost roughly half the gain"


def test_true_middle_pays_on_every_number_inside():
    d = om.american_to_decimal(-110)
    sc = middle_scenarios(d, d, 45.5, 47.5, 500, 500)
    assert sc[46] > 0 and sc[47] > 0
    assert sc[45] < 0 and sc[48] < 0


def test_a_free_middle_is_flagged_with_its_true_floor():
    """When the two legs alone already sum under 1 (a straight arbitrage on
    their own), missing the window still profits -- this is that arbitrage
    PLUS the middle's upside if the window lands too. No downside, so it
    must be flagged distinctly from an ordinary middle, and the guarantee's
    actual size preserved rather than clamped to 0 like max_loss_pct is."""
    b, _ = board_with(("totals", None, 45.5, "over", "draftkings", 2.5),
                      ("totals", None, 47.5, "under", "fanduel", 2.5))
    mids = find_middles(b, cfg())
    assert mids, "generous enough prices on both sides must still find the middle"
    m = mids[0]
    assert m.free_middle
    assert m.max_loss_pct == 0.0, "the clamped reading must still be 0, not negative"
    assert m.free_middle_floor_pct is not None and m.free_middle_floor_pct > 0, \
        "the true guarantee must survive somewhere, not just get thrown away"


def test_an_ordinary_middle_is_not_flagged_free():
    b, _ = board_with(("totals", None, 45.5, "over", "draftkings", 1.91),
                      ("totals", None, 47.5, "under", "fanduel", 1.91))
    mids = find_middles(b, cfg())
    assert mids and not mids[0].free_middle
    assert mids[0].free_middle_floor_pct is None


def test_middle_is_detected_across_two_books():
    b, _ = board_with(("totals", None, 45.5, "over", "draftkings", 1.91),
                      ("totals", None, 45.5, "under", "draftkings", 1.91),
                      ("totals", None, 47.5, "over", "fanduel", 1.91),
                      ("totals", None, 47.5, "under", "fanduel", 1.91))
    mids = find_middles(b, cfg())
    assert mids, "45.5/47.5 across two books is a middle"
    m = mids[0]
    assert m.hit_values == [46, 47]
    assert 0 < m.breakeven_hit_pct < 100


def test_stale_alt_ladders_ignores_a_lone_main_line():
    """One point that IS the recorded main line -- nothing else to check."""
    b, _ = board_with(("totals", None, 62.5, "over", "draftkings", 1.90),
                      ("totals", None, 62.5, "under", "draftkings", 1.90))
    b.record_main_point("e1", "totals", "draftkings", 62.5)
    assert stale_alt_ladders(b, max_drift=3.0) == {}


def test_stale_alt_ladders_respects_the_configured_drift():
    b, _ = board_with(("totals", None, 62.5, "over", "draftkings", 1.90),
                      ("totals", None, 62.5, "under", "draftkings", 1.90),
                      ("totals", None, 60.5, "over", "draftkings", 1.95),
                      ("totals", None, 60.5, "under", "draftkings", 1.95))
    b.record_main_point("e1", "totals", "draftkings", 62.5)
    assert stale_alt_ladders(b, max_drift=3.0) == {}, "2 points off is within a generous drift"
    assert stale_alt_ladders(b, max_drift=1.0) == {
        ("e1", "totals", "draftkings", 60.5): (62.5, False)}, \
        "the same 2-point drift must trip a tighter setting"


def test_stale_alt_ladders_checks_the_far_rungs_own_vig_not_the_ladders_tightest():
    """Reproduces the actual bug in the first version of this check, with the
    real numbers: comparing vig ACROSS the ladder to find its "implied
    center" picked the genuine main line here (62.5, vig 1.0462) over the
    stale rung (52.5, vig 1.0475) by a margin of 0.0013 -- a coin flip that
    happened to land right. Checking the far rung's own vig against a fixed
    bar does not depend on that coin flip at all."""
    b, _ = board_with(
        ("totals", None, 62.5, "over", "draftkings", 1.98039216),
        ("totals", None, 62.5, "under", "draftkings", 1.84745763),
        ("totals", None, 52.5, "over", "draftkings", 1.89285715),
        ("totals", None, 52.5, "under", "draftkings", 1.92592593),
    )
    b.record_main_point("e1", "totals", "draftkings", 62.5)
    assert stale_alt_ladders(b, max_drift=3.0) == {
        ("e1", "totals", "draftkings", 52.5): (62.5, False)}


def test_stale_alt_ladders_confirmed_via_draftkings_own_tag_bypasses_vig():
    """When the ladder's own "main" tag disagrees with the recorded main
    line, that is DraftKings' data contradicting itself -- every non-main
    rung is flagged, INCLUDING one whose own vig would pass the heuristic
    (a properly long-shot -2400/+800 price, the exact shape the inferred
    check is designed to leave alone). Confirmed does not need that
    protection: once the center is known to be wrong, a rung looking
    properly priced from that wrong center proves nothing."""
    b, _ = board_with(
        ("totals", None, 62.5, "over", "draftkings", 1.90),
        ("totals", None, 62.5, "under", "draftkings", 1.90),
        ("totals", None, 45.5, "over", "draftkings", 1.98),
        ("totals", None, 45.5, "under", "draftkings", 1.85),
        ("totals", None, 30.5, "over", "draftkings", 1.04167),   # -2400, would pass unconfirmed
        ("totals", None, 30.5, "under", "draftkings", 9.0),      # +800
    )
    b.record_main_point("e1", "totals", "draftkings", 62.5)
    b.record_ladder_main_point("e1", "totals", "draftkings", 45.5)
    stale = stale_alt_ladders(b, max_drift=3.0, max_vig=1.06)
    assert stale[("e1", "totals", "draftkings", 45.5)] == (62.5, True)
    assert stale[("e1", "totals", "draftkings", 30.5)] == (62.5, True), \
        "confirmed disagreement flags every non-main rung, vig or not"


def test_stale_alt_ladders_trusts_a_ladder_that_agrees_with_itself():
    """The ladder's own "main" tag matching the recorded main line is
    DraftKings confirming its OWN data is in sync -- nothing to flag from
    the confirmed path, so this falls through to (and passes) the inferred
    check same as if no tag had been read at all."""
    b, _ = board_with(
        ("totals", None, 62.5, "over", "draftkings", 1.90),
        ("totals", None, 62.5, "under", "draftkings", 1.90),
        ("totals", None, 30.5, "over", "draftkings", 1.04167),
        ("totals", None, 30.5, "under", "draftkings", 9.0),
    )
    b.record_main_point("e1", "totals", "draftkings", 62.5)
    b.record_ladder_main_point("e1", "totals", "draftkings", 62.5)
    assert stale_alt_ladders(b, max_drift=3.0, max_vig=1.06) == {}


def test_a_genuine_tail_rung_is_not_flagged_just_for_being_far_from_main():
    """A rung far from main SHOULD be priced worse -- that is the product.
    -2400/+800 is what a real alternate 32 points from a 62.5 main looks
    like, and it must survive even a very loose vig bar."""
    b, _ = board_with(
        ("totals", None, 62.5, "over", "draftkings", 1.90),
        ("totals", None, 62.5, "under", "draftkings", 1.90),
        ("totals", None, 30.5, "over", "draftkings", 1.04167),   # -2400
        ("totals", None, 30.5, "under", "draftkings", 9.0),      # +800
    )
    b.record_main_point("e1", "totals", "draftkings", 62.5)
    assert stale_alt_ladders(b, max_drift=3.0, max_vig=1.06) == {}


def test_a_middle_on_a_drifted_alt_ladder_is_flagged_and_warns():
    """Reproduces the real failure end to end: a DraftKings alternate-total
    rung sits at 52.5, tightly priced, while the board's known main line for
    the same book/event/market is 62.5 -- ten points off, the exact shape
    that reported a 52.5-55 free middle against Fanatics on a game whose
    real total DraftKings' own app showed as 62.5."""
    b, _ = board_with(
        ("totals", None, 62.5, "over", "draftkings", 1.90),
        ("totals", None, 62.5, "under", "draftkings", 1.90),
        ("totals", None, 52.5, "over", "draftkings", 1.95),
        ("totals", None, 52.5, "under", "draftkings", 1.95),
        ("totals", None, 55.0, "under", "fanduel", 1.91),
    )
    b.record_main_point("e1", "totals", "draftkings", 62.5)
    mids = find_middles(b, cfg())
    assert mids, "52.5 over / 55.0 under is still a middle on its face"
    m = mids[0]
    assert m.stale_alt_line
    dk_leg = next(l for l in m.legs if l.book == "draftkings")
    assert dk_leg.off_main_line and not dk_leg.stale_confirmed
    assert any("looks stale" in w for w in m.warnings)


def test_a_middle_confirmed_stale_by_draftkings_own_tag_says_so_in_the_warning():
    """Same shape, but this time DraftKings' own alternate-ladder tag says
    the ladder's center is 45.5 while the base Game market says 62.5 --
    confirmed, not inferred, and the warning text says which."""
    b, _ = board_with(
        ("totals", None, 62.5, "over", "draftkings", 1.90),
        ("totals", None, 62.5, "under", "draftkings", 1.90),
        ("totals", None, 45.5, "over", "draftkings", 1.98),
        ("totals", None, 45.5, "under", "draftkings", 1.85),
        ("totals", None, 48.0, "under", "fanduel", 1.91),
    )
    b.record_main_point("e1", "totals", "draftkings", 62.5)
    b.record_ladder_main_point("e1", "totals", "draftkings", 45.5)
    mids = find_middles(b, cfg())
    assert mids, "45.5 over / 48.0 under is still a middle on its face"
    m = mids[0]
    assert m.stale_alt_line
    dk_leg = next(l for l in m.legs if l.book == "draftkings")
    assert dk_leg.off_main_line and dk_leg.stale_confirmed
    assert any("DraftKings' own alternate-line feed" in w for w in m.warnings)


def test_a_middle_is_not_flagged_when_the_ladder_tracks_its_main_line():
    """The same shape, but the alternate rung used is close to the known main
    line -- a healthy, freshly-repriced ladder, which must not be flagged
    just for having other rungs further out (that is what alt ladders are
    FOR)."""
    b, _ = board_with(
        ("totals", None, 62.5, "over", "draftkings", 1.90),
        ("totals", None, 62.5, "under", "draftkings", 1.90),
        ("totals", None, 61.5, "over", "draftkings", 1.95),
        ("totals", None, 61.5, "under", "draftkings", 1.95),
        ("totals", None, 63.5, "under", "fanduel", 1.91),
    )
    b.record_main_point("e1", "totals", "draftkings", 62.5)
    mids = find_middles(b, cfg())
    assert mids
    assert not mids[0].stale_alt_line
    assert not any(l.off_main_line for l in mids[0].legs)


def test_a_stale_spread_on_the_away_side_does_not_crash_the_warning():
    """Leg.point on a spread is SIGNED per side (away is the negation of the
    stored home-axis point -- see _leg_point), while stale_alt_ladders and
    main_points key on the raw, unsigned group point. Looking a stale leg's
    warning up by its (signed) `point` instead of the value already resolved
    onto the Leg would KeyError here, since -40.5 is never a key while 40.5
    is. draftkings' main spread is -39.5 (home); the alternate leg used is
    the AWAY side of a drifted rung, which displays as +40.5."""
    b, _ = board_with(
        ("spreads", None, -39.5, "home", "draftkings", 1.90),
        ("spreads", None, -39.5, "away", "draftkings", 1.90),
        ("spreads", None, -50.5, "home", "draftkings", 1.95),
        ("spreads", None, -50.5, "away", "draftkings", 1.95),
        ("spreads", None, -48.0, "home", "fanduel", 1.91),
    )
    b.record_main_point("e1", "spreads", "draftkings", -39.5)
    mids = find_middles(b, cfg())
    assert mids, "away +50.5 / fanduel home -48.0 is still a middle on its face"
    m = mids[0]
    assert m.stale_alt_line
    dk_leg = next(l for l in m.legs if l.book == "draftkings")
    assert dk_leg.off_main_line and dk_leg.main_line == -39.5
    assert any("looks stale" in w for w in m.warnings)


def test_a_book_with_no_recorded_main_line_is_never_flagged():
    """Fanatics has no is_main_line signal yet (see stale_alt_ladders), so its
    ladders must not be checked against a main line the board never
    recorded -- silence here, not a false positive."""
    b, _ = board_with(
        ("totals", None, 52.5, "over", "fanatics", 1.95),
        ("totals", None, 52.5, "under", "fanatics", 1.95),
        ("totals", None, 30.5, "over", "fanatics", 1.90),
        ("totals", None, 30.5, "under", "fanatics", 1.90),
        ("totals", None, 55.0, "under", "fanduel", 1.91),
    )
    mids = find_middles(b, cfg())
    assert mids
    assert not mids[0].stale_alt_line


def test_boosts_stack_on_a_middle_across_two_books():
    """DraftKings' and FanDuel's tokens each land on their own leg of a
    middle, same as they would on a straight arbitrage -- an ordinary
    (costly) middle can turn free once both are applied, since boosting
    either leg only ever helps the worst case."""
    from edge.arb.engine import Boost
    b, _ = board_with(("totals", None, 45.5, "over", "draftkings", 1.91),
                      ("totals", None, 47.5, "under", "fanduel", 1.91))
    c = cfg()
    plain = find_middles(b, c)[0]
    assert not plain.free_middle, "the unboosted baseline must still be an ordinary middle"

    c.boosts = [Boost(book="draftkings", pct=0.5, max_stake=500.0),
                Boost(book="fanduel", pct=0.3, max_stake=500.0)]
    boosted = find_middles(b, c)[0]
    boosted_books = {l.book for l in boosted.legs if l.boost_pct}
    assert boosted_books == {"draftkings", "fanduel"}, \
        "two tokens on two different books' legs should both be used"
    assert boosted.boost and "draftkings" in boosted.boost and "fanduel" in boosted.boost
    assert boosted.floor_pct > plain.floor_pct, "stacking both tokens must raise the floor"
    assert boosted.ceiling_pct >= plain.ceiling_pct


# --- +EV --------------------------------------------------------------------
def test_ev_prices_a_book_against_the_vig_free_anchor():
    c = cfg()
    c.detect.ev_min_pct = 0.0
    b, _ = board_with(("h2h", None, None, "home", "draftkings", 2.30),
                      ("h2h", None, None, "away", "draftkings", 1.70),
                      ("h2h", None, None, "home", "fanatics_markets", 1.0 / 0.45),
                      ("h2h", None, None, "away", "fanatics_markets", 1.0 / 0.55))
    evs = find_ev(b, c)
    assert evs, "2.30 against a 45% fair price is +EV"
    assert all(e.legs[0].book in c.books.bettable for e in evs)
    assert all(e.legs[0].book != "fanatics_markets" for e in evs)


# --- real captured payloads -------------------------------------------------
def test_oddschecker_payload_parses_into_paired_lines():
    from edge.arb.books import ingest_oddschecker
    payload = json.loads((DATA / "data_oddschecker_mlb.json").read_text())
    board = Board()
    stats = ingest_oddschecker(board, payload, book="fanatics",
                               sport_key="baseball_mlb", strict_match=False)
    assert stats["quotes"] > 0
    assert stats["live_skipped"] >= 1, "in-running games must be skipped"
    totals = [g for g in board.groups.values()
              if g.key.market == "totals" and {"over", "under"} <= set(g.quotes)]
    assert totals, "over and under must land on one group"


def test_draftkings_milestones_become_over_lines():
    from edge.arb.draftkings_nash import ingest_sportscontent
    payload = json.loads((DATA / "data_dk_sportscontent.json").read_text())
    ev = EventMeta("e1", "baseball_mlb", "MLB",
                   datetime.now(timezone.utc) + timedelta(hours=2),
                   "New York Yankees", "Houston Astros")
    board = Board(); board.events["e1"] = ev
    stats = ingest_sportscontent(board, payload, sport_key="baseball_mlb", event=ev)
    assert stats["quotes"] == 6
    hr = sorted(k.point for k in board.groups
                if k.market == "batter_home_runs" and k.subject == "Yordan Alvarez")
    assert hr == [0.5, 1.5, 2.5], "1+/2+/3+ must become Over 0.5/1.5/2.5"


def _dk_total_payload(point: float) -> dict:
    return {
        "markets": [{"id": "m1", "eventId": "e1", "name": "Total",
                    "marketType": {"name": "Total"}}],
        "selections": [
            {"id": "s1", "marketId": "m1", "label": "Over", "points": point, "trueOdds": 1.90},
            {"id": "s2", "marketId": "m1", "label": "Under", "points": point, "trueOdds": 1.90},
        ],
    }


def test_main_line_ingestion_records_the_books_own_point():
    from edge.arb.draftkings_nash import ingest_sportscontent
    ev = EventMeta("e1", "baseball_mlb", "MLB",
                   datetime.now(timezone.utc) + timedelta(hours=2), "H", "A")
    board = Board(); board.events["e1"] = ev
    ingest_sportscontent(board, _dk_total_payload(62.5), sport_key="baseball_mlb",
                         event=ev, is_main_line=True)
    assert board.main_points[("e1", "totals", "draftkings")] == 62.5


def test_an_alternate_line_pull_does_not_get_mistaken_for_the_main_line():
    """The default -- a caller that forgets to say is_main_line=True must not
    silently record whatever it happened to fetch as gospel."""
    from edge.arb.draftkings_nash import ingest_sportscontent
    ev = EventMeta("e1", "baseball_mlb", "MLB",
                   datetime.now(timezone.utc) + timedelta(hours=2), "H", "A")
    board = Board(); board.events["e1"] = ev
    ingest_sportscontent(board, _dk_total_payload(52.5), sport_key="baseball_mlb", event=ev)
    assert board.main_points == {}


def _dk_alt_ladder_payload(points_and_main: list[tuple[float, bool]]) -> dict:
    """One 'Total Alternate'-shaped market with several rungs, at most one of
    which DraftKings tags "main": true, matching what the real API sends."""
    selections = []
    for i, (point, is_main) in enumerate(points_and_main):
        for side in ("Over", "Under"):
            sel = {"id": f"s{side}{i}", "marketId": "m1", "label": side,
                  "points": point, "trueOdds": 1.90}
            if is_main:
                sel["main"] = True
            selections.append(sel)
    return {"markets": [{"id": "m1", "eventId": "e1", "name": "Total Alternate",
                         "marketType": {"name": "Total Alternate"}}],
           "selections": selections}


def test_an_alternate_ladders_own_main_tag_is_recorded_separately():
    """DraftKings marks exactly one rung per alternate ladder "main": true --
    the one its own app currently treats as equal to the main line. This
    must land in ladder_main_points, NOT main_points: it is a claim from the
    ladder itself, not from the base Game market is_main_line=True records,
    and the whole point is to be able to compare the two."""
    from edge.arb.draftkings_nash import ingest_sportscontent
    ev = EventMeta("e1", "baseball_mlb", "MLB",
                   datetime.now(timezone.utc) + timedelta(hours=2), "H", "A")
    board = Board(); board.events["e1"] = ev
    payload = _dk_alt_ladder_payload([(45.5, True), (52.5, False), (30.5, False)])
    ingest_sportscontent(board, payload, sport_key="baseball_mlb", event=ev)
    assert board.ladder_main_points[("e1", "totals", "draftkings")] == 45.5
    assert board.main_points == {}, "an alternate pull must still never set main_points"


def test_truncated_outright_field_is_refused():
    """The list endpoint returns only the top few outright outcomes; a 5-of-32
    field summing to 0.495 would read as a 100% arbitrage."""
    from edge.arb import fanaticsmarkets as fm
    events = json.loads((DATA / "data_fanaticsmarkets_nhl.json").read_text())["data"]
    stats = fm.ingest_events(Board(), events, sport_key="icehockey_nhl",
                             strict_match=False, min_trades=0)
    assert stats["quotes"] == 0 and stats["truncated_skipped"] >= 1


# --- clock robustness -------------------------------------------------------
def test_future_dated_quote_does_not_read_as_negative_age():
    """This host's wall clock jumps (WSL sleeps, NTP corrects); 292 quotes in
    one real scan were stamped up to 49s ahead. A negative age would sail
    through every freshness check."""
    ev = EventMeta("e1", "baseball_mlb", "MLB",
                   datetime.now(timezone.utc) + timedelta(hours=2), "H", "A")
    ahead = Quote(book="fanduel", side="over", decimal=2.0, point=8.5,
                  last_update=datetime.now(timezone.utc) + timedelta(seconds=50))
    assert ahead.age_seconds() == 0.0


def test_quote_age_guard_still_rejects_a_genuinely_old_quote():
    c = cfg()
    b, ev = board_with(("totals", None, 8.5, "under", "fanduel", 2.05))
    stale = Quote(book="draftkings", side="over", decimal=2.10, point=8.5,
                  last_update=datetime.now(timezone.utc)
                  - timedelta(seconds=c.detect.max_quote_age_seconds + 60))
    b.group(GroupKey("e1", "totals", None, 8.5), ev).add(stale)
    assert find_arbitrages(b, c) == [], "a quote older than the cap must not be staked"


# --- middle sanity bounds ---------------------------------------------------
def test_absurdly_wide_middle_is_rejected():
    """A 31-point spread 'middle' pairs two different markets, not two
    readings of one. Seen live reporting +255% across 34 winning outcomes."""
    b, _ = board_with(("spreads", None, -31.0, "home", "fanatics", 2.00),
                      ("spreads", None, 0.0, "away", "fanduel", 2.00))
    assert find_middles(b, cfg()) == []


def test_longshot_leg_is_not_a_main_line():
    """+1500 on a spread is a misparse, not a price to middle against."""
    c = cfg()
    # home -3.5 vs away +4.5: a real 1-point middle on a margin of exactly 4
    b, _ = board_with(("spreads", None, -3.5, "home", "fanatics", 1.91),
                      ("spreads", None, -4.5, "away", "fanduel", 16.0))
    assert find_middles(b, c) == [], "a +1500 leg must not be treated as a line"
    b2, _ = board_with(("spreads", None, -3.5, "home", "fanatics", 1.91),
                       ("spreads", None, -4.5, "away", "fanduel", 1.91))
    assert find_middles(b2, c), "the same geometry at normal prices is a middle"


# --- gaps: crossed lines, the mirror image of a middle ----------------------
def test_gap_between_crossed_lines_is_detected():
    """Oklahoma -44.5 (+105) against UTEP +42.5 (+100), two different books:
    outside the 43/44 gap exactly one leg wins as always, but a final margin
    of 43 or 44 loses BOTH -- the exact scenario reported live. Not a
    guaranteed position, so it is its own kind, not folded into 'middle'."""
    b, _ = board_with(("spreads", None, -44.5, "home", "draftkings", 2.05),
                      ("spreads", None, -42.5, "away", "fanduel", 2.00))
    c = cfg()
    gaps = [o for o in find_middles(b, c) if o.kind == "gap"]
    assert len(gaps) == 1
    o = gaps[0]
    assert o.hit_values == [43, 44]
    assert o.floor_pct == -100.0
    assert o.max_loss_pct == 100.0
    expected = om.allocate([2.05, 2.00], bankroll=c.bankroll.total, round_to=c.bankroll.round_to,
                           max_stakes=[c.books.max_stake.get("draftkings"),
                                      c.books.max_stake.get("fanduel")])
    assert o.ceiling_pct == pytest.approx(expected.worst_profit_pct, abs=1e-6)
    assert o.profit_pct == pytest.approx(expected.worst_profit_pct, abs=1e-6)
    assert any("NOT a guaranteed" in w for w in o.warnings)


def test_boost_helps_the_normal_side_of_a_gap_but_not_the_gap_itself():
    """A boost cannot rescue landing in the gap -- both legs pay exactly 0
    there regardless of price -- but it must still improve the ordinary
    (one-wins) case, the same as it would for a real hedge."""
    from edge.arb.engine import Boost
    b, _ = board_with(("spreads", None, -44.5, "home", "draftkings", 2.05),
                      ("spreads", None, -42.5, "away", "fanduel", 2.00))
    c = cfg()
    plain = next(o for o in find_middles(b, c) if o.kind == "gap")

    c.boosts = [Boost(book="draftkings", pct=0.5, max_stake=500.0)]
    boosted = next(o for o in find_middles(b, c) if o.kind == "gap")
    assert boosted.floor_pct == -100.0
    assert boosted.ceiling_pct > plain.ceiling_pct
    assert boosted.hit_values == [43, 44]


def test_gap_with_a_whole_number_boundary_is_not_reported():
    """A push at the exact line is not modeled here (the 'outside' reading
    assumes a clean win/loss) -- skip rather than risk an optimistic
    ceiling. Half-point lines, the ordinary case, never trigger this."""
    b, _ = board_with(("spreads", None, -44.0, "home", "draftkings", 2.05),
                      ("spreads", None, -42.5, "away", "fanduel", 2.00))
    assert [o for o in find_middles(b, cfg()) if o.kind == "gap"] == []


def test_absurdly_wide_gap_is_rejected():
    """Same sanity-bound reasoning as an absurdly wide middle: a real gap
    from genuine line movement between books is narrow."""
    b, _ = board_with(("spreads", None, -50.5, "home", "draftkings", 2.05),
                      ("spreads", None, -40.5, "away", "fanduel", 2.00))
    assert [o for o in find_middles(b, cfg()) if o.kind == "gap"] == []


def test_a_gap_with_a_losing_normal_case_is_rejected():
    """A gap is only worth surfacing if the OUTSIDE-the-gap case doesn't
    itself lose money -- otherwise it is a worse bet than a plain one, with
    extra risk on top."""
    b, _ = board_with(("spreads", None, -44.5, "home", "draftkings", 1.91),
                      ("spreads", None, -42.5, "away", "fanduel", 1.91))
    assert [o for o in find_middles(b, cfg()) if o.kind == "gap"] == []


# --- DraftKings league feed: props must not read as game markets ------------
DFS_PITCHER_MARKETS = ["pitcher_outs", "pitcher_strikeouts", "pitcher_earned_runs",
                       "pitcher_hits_allowed", "pitcher_walks", "pitcher_record_a_win"]


def _dk_league_board():
    """Real capture from sportsbook-nash league 84240 (MLB). Before the
    participants-first fix every prop here mapped to `totals`: the league feed
    names markets "<Player> <Stat> O/U" with no separator for split_player, so
    canonical_market ran with player=None and the trailing "O/U" matched the
    game-totals rule. 106 prop groups landed on `totals` in one live scan, and
    edge/dfs.py -- which keys pitchers by pitcher_outs/pitcher_strikeouts --
    saw no props at all from the free path."""
    from edge.arb.draftkings_nash import ingest_sportscontent
    payload = json.loads((DATA / "data_dk_league_props.json").read_text())
    ev = EventMeta("e1", "baseball_mlb", "MLB",
                   datetime.now(timezone.utc) + timedelta(hours=2),
                   "ATL Braves", "COL Rockies")
    board = Board(); board.events["e1"] = ev
    stats = ingest_sportscontent(board, payload, sport_key="baseball_mlb", event=ev)
    return board, stats


@pytest.mark.parametrize("market", DFS_PITCHER_MARKETS)
def test_dfs_pitcher_market_survives_the_free_scrape(market):
    """edge/dfs.py:P_MARKETS -- the six a starter is projected from. Any one
    landing on `totals` costs DFS its whole pitcher pool, silently."""
    board, _ = _dk_league_board()
    groups = [g for g in board.groups.values() if g.key.market == market]
    assert groups, f"{market} did not survive the scrape"
    assert all(g.key.subject for g in groups), f"{market} must be keyed by player"


def test_free_scrape_feeds_project_pitcher_end_to_end():
    """The whole point: free DK data -> a real DFS pitcher projection."""
    from edge import dfs
    board, _ = _dk_league_board()
    pmkts = {}
    for g in board.groups.values():
        if g.key.subject != "Grant Holmes":
            continue
        d = {s.capitalize(): q["draftkings"].decimal
             for s, q in g.quotes.items() if "draftkings" in q}
        d["point"] = g.key.point
        pmkts[g.key.market] = d
    assert set(DFS_PITCHER_MARKETS) <= set(pmkts), \
        f"missing {sorted(set(DFS_PITCHER_MARKETS) - set(pmkts))}"
    out = dfs.project_pitcher(pmkts)
    assert out["proj"] is not None, "project_pitcher rejected the free-scrape shape"
    assert out["imputed"] == [], f"nothing should need imputing: {out['imputed']}"
    assert set(out["components"]) == {"out", "K", "ER", "hit", "bb", "win"}


def test_props_do_not_pollute_the_game_markets():
    board, _ = _dk_league_board()
    for g in board.groups.values():
        if g.key.market in ("h2h", "spreads", "totals"):
            assert g.key.subject is None, \
                f"{g.key.subject!r} landed on the game market {g.key.market}"


def test_batter_strikeouts_are_not_a_pitcher_market():
    board, _ = _dk_league_board()
    ks = {g.key.subject for g in board.groups.values()
          if g.key.market == "pitcher_strikeouts"}
    assert "Hunter Goodman" not in ks


def test_multi_player_market_is_dropped_not_guessed_at():
    """Two pitchers in one market: keyed by either, it would pair against that
    pitcher's own strikeout line and invent an arbitrage."""
    board, stats = _dk_league_board()
    assert any("multi-player" in u for u in stats["markets_unmapped"])


# --- FanDuel player props: four runner shapes, one of them parsed -----------
@pytest.mark.parametrize("name,handicap,expected", [
    ("Over 15.5 Dean Kremer",        0,   ("over",  "Dean Kremer",   15.5)),
    ("Dean Kremer Over 15.5",        0,   ("over",  "Dean Kremer",   15.5)),
    ("Luis Castillo Over",           3.5, ("over",  "Luis Castillo",  3.5)),
    ("Luis Castillo 3+ Strikeouts",  0,   ("over",  "Luis Castillo",  2.5)),
])
def test_fanduel_player_runner_shapes(name, handicap, expected):
    """FanDuel writes a player line four ways; only the first was handled, so
    the rest made the whole runner name the player -- 53 groups keyed
    'Dean Kremer 3+ Strikeouts' with no line, and Over/Under split into two
    subjects that could never meet."""
    from edge.arb.fanduel import parse_player_runner
    assert parse_player_runner(name, handicap) == expected


@pytest.mark.parametrize("market_type,expected", [
    ("PITCHER_A_OUTS_RECORDED_SB", ("pitcher_outs", None)),
    ("TO_RECORD_AN_RBI", ("batter_rbis", 0.5)),
    ("PLAYER_TO_RECORD_2+_HITS+RUNS+RBIS", ("batter_hits_runs_rbis", 1.5)),
])
def test_fanduel_classifies_the_markets_it_was_dropping(market_type, expected):
    from edge.arb.fanduel import classify
    assert classify(market_type) == expected


def test_fanduel_pitcher_outs_lands_two_sided():
    """PITCHER_*_OUTS_RECORDED_SB was unclassified, so pitcher_outs -- which
    edge/dfs.py needs -- had zero FanDuel groups."""
    from edge.arb.fanduel import FanDuelScrape
    payload = json.loads((DATA / "data_fd_player_props.json").read_text())
    board = Board()
    FanDuelScrape(state="ct").ingest_event(board, payload, "baseball_mlb",
                                           strict_match=False)
    outs = [g for g in board.groups.values() if g.key.market == "pitcher_outs"]
    assert outs
    for g in outs:
        assert {"over", "under"} <= set(g.quotes)
        assert g.key.point and " Over" not in (g.key.subject or "")


def test_dk_cap_keeps_every_market_dfs_needs():
    """At cap=12 on DraftKings' own ordering, four of the six markets
    edge/dfs.py projects a pitcher from were silently dropped."""
    from edge.arb.draftkings_league import DraftKingsLeague
    live_order = ["Walks Allowed O/U", "Strikeouts Thrown O/U", "Race to Strikeouts",
                  "Home Runs", "Hits", "Total Bases", "RBIs", "Strikeouts Thrown",
                  "Hits + Runs + RBIs O/U", "Runs O/U", "Stolen Bases O/U", "Singles O/U",
                  "Doubles O/U", "Walks (Batter) O/U", "Earned Runs Allowed O/U",
                  "Outs Recorded O/U", "Triples", "Total Bases O/U", "Hits O/U",
                  "RBIs O/U", "To Record a Win", "Hits Allowed O/U"]
    payload = {"subcategories": [{"categoryId": 1031, "id": 1000 + i, "name": n}
                                 for i, n in enumerate(live_order)]}
    subs = DraftKingsLeague(state="ct").prop_subcategories(payload)
    cap = ArbConfig().draftkings_max_prop_subcategories
    kept = {n for _c, _s, n in subs[:cap]}
    need = {"Outs Recorded O/U", "Strikeouts Thrown O/U", "Earned Runs Allowed O/U",
            "Hits Allowed O/U", "Walks Allowed O/U", "To Record a Win"}
    assert need <= kept, f"cap={cap} drops {sorted(need - kept)}"


# --- profit boosts ----------------------------------------------------------
def test_a_boost_multiplies_profit_not_the_return():
    """The whole feature rests on this. A 50% boost on +200 pays 300 profit on
    a 100 stake, i.e. decimal 4.0. Multiplying the decimal instead gives 4.5
    and overstates every boosted arb -- here by 12.5% of the payout."""
    assert om.boosted(3.0, 0.5) == pytest.approx(4.0)
    assert om.boosted(2.0, 0.5) == pytest.approx(2.5)
    assert om.boosted(1.909, 0.5) == pytest.approx(2.3635, abs=1e-4)
    assert om.boosted(2.5, 0.0) == 2.5          # no boost is a no-op


def test_a_boost_is_a_negative_commission():
    """Same shape as net_of_commission, opposite sign -- so a 10% boost and a
    10% rake cancel exactly."""
    d = 2.4
    assert om.net_of_commission(om.boosted(d, 0.1), 0.1 / 1.1) == pytest.approx(d, abs=1e-9)


def test_one_boost_clears_the_vig_on_a_fair_two_way():
    """A -110/-110 market sums to 1.048; the 4.8% is vig no shopping removes.
    One 50% boost takes it under 1.0, which is the reason this exists."""
    from edge.arb.engine import Boost           # noqa: F401  (import guard)
    plain = om.arb_sum([1.909, 1.909])
    boosted = om.arb_sum([om.boosted(1.909, 0.5), 1.909])
    assert plain > 1.0 and boosted < 1.0
    assert (1.0 / boosted - 1.0) * 100 == pytest.approx(5.6, abs=0.2)


def test_parlay_only_boost_is_never_applied():
    """Books offer the same headline boost for straight bets and for parlays.
    Only the straight-bet one can be hedged: each side of an arbitrage is its
    own single bet, so a parlay token cannot price either leg."""
    from edge.arb.engine import Boost
    straight = Boost(book="fanduel", pct=0.25, sports=["basketball_wnba"])
    parlay = Boost(book="draftkings", pct=0.25, sports=["basketball_wnba"],
                   requires_parlay=True)
    assert straight.applies_to("fanduel", "basketball_wnba", "h2h")
    assert not parlay.applies_to("draftkings", "basketball_wnba", "h2h")


def test_boost_respects_its_book_sport_and_market_filters():
    from edge.arb.engine import Boost
    b = Boost(book="fanduel", pct=0.25, sports=["basketball_wnba"], markets=["h2h"])
    assert b.applies_to("fanduel", "basketball_wnba", "h2h")
    assert not b.applies_to("draftkings", "basketball_wnba", "h2h")   # wrong book
    assert not b.applies_to("fanduel", "baseball_mlb", "h2h")         # wrong sport
    assert not b.applies_to("fanduel", "basketball_wnba", "totals")   # wrong market
    assert not Boost(book="fanduel", pct=0.0).applies_to("fanduel", "x", "h2h")


def _two_way_board(over=1.909, under=1.909, sport="basketball_wnba"):
    ev = EventMeta("e1", sport, "WNBA",
                   datetime.now(timezone.utc) + timedelta(hours=2), "Aces", "Tempo")
    b = Board()
    g = b.group(GroupKey("e1", "totals", None, 165.5), ev)
    now = datetime.now(timezone.utc)
    g.add(Quote(book="draftkings", side="over", decimal=over, point=165.5, last_update=now))
    g.add(Quote(book="fanduel", side="under", decimal=under, point=165.5, last_update=now))
    return b


def test_boost_turns_a_vig_market_into_an_arbitrage():
    from edge.arb.engine import Boost
    c = cfg()
    board = _two_way_board()
    assert find_arbitrages(board, c) == [], "a -110/-110 pair is not an arb on its own"

    c.boosts = [Boost(book="fanduel", pct=0.5, max_stake=25.0,
                      sports=["basketball_wnba"])]
    found = find_arbitrages(board, c)
    assert found, "a 50% boost on one leg must create the arbitrage"
    o = found[0]
    assert o.boost and o.profit_pct > 0
    boosted_legs = [l for l in o.legs if l.boost_pct]
    assert len(boosted_legs) == 1, "a token applies to ONE slip"
    assert boosted_legs[0].book == "fanduel"
    assert boosted_legs[0].raw_decimal == pytest.approx(1.909, abs=1e-3)
    assert boosted_legs[0].decimal > boosted_legs[0].raw_decimal
    assert any("without it this is" in w for w in o.warnings), \
        "must say what the position is worth WITHOUT the boost"


def test_boost_max_stake_caps_the_whole_position():
    """The token's cap bounds the position, not just its own leg -- the hedge
    is sized off it. A $25 boost cannot carry a $1000 bankroll."""
    from edge.arb.engine import Boost
    c = cfg()
    c.bankroll.total = 1000.0
    c.boosts = [Boost(book="fanduel", pct=0.5, max_stake=25.0)]
    o = find_arbitrages(_two_way_board(), c)[0]
    fd = next(l for l in o.legs if l.book == "fanduel")
    assert fd.stake <= 25.0, f"boosted leg staked {fd.stake} over a $25 cap"
    assert o.stake_total < 100.0, "the hedge must be sized off the capped leg"


def test_two_different_boosts_stack_on_the_same_arbitrage():
    """DraftKings' token and FanDuel's token are separate bet slips on
    separate books' legs, so both apply to the same two-leg market at once --
    "one token, one slip" bounds a single Boost to one leg, not the whole
    position to one boost. The combined result must be at least as good as
    using either token alone, since boosting an additional leg can only
    lower arb_sum further."""
    from edge.arb.engine import Boost
    c = cfg()
    c.boosts = [Boost(book="draftkings", pct=0.5, max_stake=500.0),
                Boost(book="fanduel", pct=0.5, max_stake=500.0)]
    o = find_arbitrages(_two_way_board(over=1.5, under=3.0), c)[0]
    boosted_books = {l.book for l in o.legs if l.boost_pct}
    assert boosted_books == {"draftkings", "fanduel"}, \
        "two tokens on two different books' legs should both be used"
    single_fd = om.arb_sum([1.5, om.boosted(3.0, 0.5)])
    assert (1.0 / om.arb_sum([l.decimal for l in o.legs]) - 1.0) * 100 >= (
        (1.0 / single_fd - 1.0) * 100 - 1e-9), \
        "stacking both tokens must be at least as good as using just one"
    assert o.floor_pct == pytest.approx(o.profit_pct)
    assert o.ceiling_pct >= o.floor_pct - 1e-9


def test_a_boost_that_cannot_apply_leaves_the_other_leg_alone():
    """Only one of two configured tokens actually covers this market (the
    second is scoped to a sport it is not in) -- exactly that one leg is
    boosted, not both."""
    from edge.arb.engine import Boost
    c = cfg()
    c.boosts = [Boost(book="draftkings", pct=0.5, max_stake=500.0),
                Boost(book="fanduel", pct=0.5, max_stake=500.0,
                      sports=["basketball_nba"])]
    o = find_arbitrages(_two_way_board(over=1.5, under=3.0), c)[0]
    boosted_legs = [l for l in o.legs if l.boost_pct]
    assert len(boosted_legs) == 1
    assert boosted_legs[0].book == "draftkings"


def test_top_per_sport_keeps_each_sport_represented():
    """A boosted scan can return hundreds; ranking globally buries whole
    sports under whichever prices widest."""
    from edge.arb.engine import top_per_sport
    def opp(sport, pct):
        return Opportunity(kind="arb", fingerprint=f"{sport}{pct}", sport_key=sport,
                           sport_title=sport, event_id="e", matchup="m",
                           commence_time=datetime.now(timezone.utc), market="h2h",
                           subject=None, description="d", legs=[], profit_pct=pct)
    from edge.arb.engine import Opportunity
    rows = [opp("a", p) for p in (9, 8, 7, 6, 5)] + [opp("b", p) for p in (4, 3, 2)]
    top = top_per_sport(rows, 3)
    assert len(top) == 6
    assert sorted({o.sport_key for o in top}) == ["a", "b"]
    assert [o.profit_pct for o in top if o.sport_key == "a"] == [9, 8, 7]


def test_price_candidates_agrees_with_the_scanner():
    """The slider re-prices snapshot candidates instead of re-scanning, so it
    is a second implementation of the same maths. If they disagree the number
    on screen is not the number you can place."""
    from edge.arb.engine import Boost, price_candidates
    from edge.arb.run import candidates
    c = cfg()
    c.boosts = [Boost(book="fanduel", pct=0.5, max_stake=25.0)]
    board = _two_way_board()
    scanned = find_arbitrages(board, c)[0]
    priced = price_candidates(candidates(board, c), c.boosts, c)[0]
    assert priced["profit_pct"] == pytest.approx(scanned.profit_pct, abs=1e-6)
    assert priced["stake_total"] == pytest.approx(scanned.stake_total, abs=1e-6)


def test_price_candidates_stacks_two_boosts_like_the_scanner():
    """Two different tokens on two different books' legs must agree between
    the two implementations here too, not just the single-boost case."""
    from edge.arb.engine import Boost, price_candidates
    from edge.arb.run import candidates
    c = cfg()
    c.boosts = [Boost(book="draftkings", pct=0.5, max_stake=500.0),
                Boost(book="fanduel", pct=0.5, max_stake=500.0)]
    board = _two_way_board(over=1.5, under=3.0)
    scanned = find_arbitrages(board, c)[0]
    priced = price_candidates(candidates(board, c), c.boosts, c)[0]
    assert priced["profit_pct"] == pytest.approx(scanned.profit_pct, abs=1e-6)
    assert {l["book"] for l in priced["legs"] if l["boost_pct"]} == {"draftkings", "fanduel"}
    assert priced["ceiling_pct"] >= priced["floor_pct"] - 1e-9
    assert priced["floor_pct"] == pytest.approx(priced["profit_pct"])


def test_price_middle_candidates_agrees_with_the_scanner():
    """The middle-shaped counterpart to price_candidates: re-pricing a
    snapshot's middle_candidates under a boost must land on the same numbers
    find_middles reports live, stacked boosts included."""
    from edge.arb.engine import Boost, price_middle_candidates
    from edge.arb.run import middle_candidates
    b, _ = board_with(("totals", None, 45.5, "over", "draftkings", 1.91),
                      ("totals", None, 47.5, "under", "fanduel", 1.91))
    c = cfg()
    c.boosts = [Boost(book="draftkings", pct=0.5, max_stake=500.0),
                Boost(book="fanduel", pct=0.3, max_stake=500.0)]
    scanned = find_middles(b, c)[0]
    priced = price_middle_candidates(middle_candidates(b, c), c.boosts, c)[0]
    assert priced["floor_pct"] == pytest.approx(scanned.floor_pct, abs=1e-6)
    assert priced["hit_pct"] == pytest.approx(scanned.ceiling_pct, abs=1e-6)
    assert {l["book"] for l in priced["legs"] if l["boost_pct"]} == {"draftkings", "fanduel"}
    assert priced["free_middle"] == scanned.free_middle


def _spread_board(home_point=-57.5, home_dec=1.76, away_dec=2.38):
    """A two-book spread, folded onto the home axis like the real feed."""
    ev = EventMeta("e1", "americanfootball_ncaaf", "NCAAF",
                   datetime.now(timezone.utc) + timedelta(hours=8),
                   "Missouri", "Arkansas Pine Bluff")
    b = Board()
    g = b.group(GroupKey("e1", "spreads", None, home_point), ev)
    now = datetime.now(timezone.utc)
    g.add(Quote(book="draftkings", side="home", decimal=home_dec,
                point=home_point, last_update=now))
    g.add(Quote(book="fanduel", side="away", decimal=away_dec,
                point=-home_point, last_update=now))
    return b


def test_candidate_legs_carry_their_own_signed_point():
    """Spreads are stored folded onto the home axis, so the group's OWN point
    is the same negative number for both sides. A leg dict that just copied it
    would show the away side laying the favourite's points too -- the exact
    bug already fixed once for the opportunity list's legs (`_leg_point`), but
    `run.candidates()` builds its own leg dicts and had not picked up the fix.
    The boost panel reads this `point` straight into its "Line" column."""
    from edge.arb.run import candidates
    rows = candidates(_spread_board(), cfg(), max_sum=2.0)
    assert len(rows) == 1
    by_side = {l["side"]: l["point"] for l in rows[0]["legs"]}
    assert by_side["home"] == -57.5, "Missouri (home, favourite) lays 57.5"
    assert by_side["away"] == 57.5, "Arkansas Pine Bluff (away) gets 57.5"


def test_boosted_ev_row_point_is_signed_per_side_not_the_folded_group_point():
    """price_boosted_ev used to carry the candidate's raw (home-folded) point
    straight through regardless of which side the row was FOR -- so a boosted
    away spread would have displayed the home team's negative number."""
    from edge.arb.engine import Boost, price_boosted_ev
    from edge.arb.run import candidates
    c = cfg()
    rows = price_boosted_ev(candidates(_spread_board(), c, max_sum=2.0),
                            [Boost(book="fanduel", pct=0.5, max_stake=25.0)],
                            c, min_ev_pct=-100.0)
    away_rows = [r for r in rows if r["side"] == "away"]
    assert away_rows and all(r["point"] == 57.5 for r in away_rows)


def test_skip_live_false_actually_surfaces_a_live_event():
    """min_minutes_to_start defaults to 3.0, and mins < 0 is always also
    mins < 3.0 -- so the OLD in_window fell through to that check regardless
    of skip_live and excluded every live event no matter what skip_live said.
    Setting skip_live=False must now actually change the answer."""
    from edge.arb.engine import in_window
    ev = EventMeta("e1", "baseball_mlb", "MLB",
                   datetime.now(timezone.utc) - timedelta(minutes=10), "Home", "Away")
    now = datetime.now(timezone.utc)
    c = cfg()
    c.detect.skip_live = True
    assert not in_window(ev, c, now), "default must still exclude a live event"
    c.detect.skip_live = False
    assert in_window(ev, c, now), "skip_live=False must surface it"


def test_within_date_bounds_uses_the_et_calendar_date_not_utc():
    """The whole reason for this check: a late-evening ET kickoff already
    rolled over to the next UTC calendar date. A naive UTC-date comparison
    would put this event on the wrong side of an ET-dated bound."""
    from edge.arb.engine import within_date_bounds
    # 01:30 UTC on the 4th == 21:30 ET on the 3rd (EDT, UTC-4 in September).
    commence = datetime(2026, 9, 4, 1, 30, tzinfo=timezone.utc)
    c = cfg()
    c.detect.date_from = c.detect.date_to = "2026-09-03"
    assert within_date_bounds(commence, c), \
        "21:30 ET on the 3rd must match a bound of 2026-09-03"
    c.detect.date_from = c.detect.date_to = "2026-09-04"
    assert not within_date_bounds(commence, c), \
        "the UTC calendar date (the 4th) must NOT be what this matches on"


def test_in_window_respects_date_bounds():
    """Computes the bound from the event's OWN ET date rather than assuming
    "6 hours out" stays on today's calendar date -- it doesn't, whenever
    `now` is within 6 hours of ET midnight, which made an earlier version of
    this test flaky depending on the wall clock at run time."""
    from edge.arb import engine as engine_mod
    from edge.arb.engine import in_window
    now = datetime.now(timezone.utc)
    ev = EventMeta("e1", "americanfootball_ncaaf", "NCAAF",
                   now + timedelta(hours=6), "Home", "Away")
    c = cfg()
    assert in_window(ev, c, now), "no bound set must not filter anything"
    its_own_et_date = ev.commence_time.astimezone(engine_mod.ET).date().isoformat()
    c.detect.date_from = c.detect.date_to = its_own_et_date
    assert in_window(ev, c, now), "an event must fall inside a bound built from its own date"
    c.detect.date_from = c.detect.date_to = (now + timedelta(days=10)).date().isoformat()
    assert not in_window(ev, c, now), "a 6h-out event must not match a +10-day bound"


def test_wnba_is_reachable_on_draftkings():
    """FanDuel had WNBA; DraftKings had no league id, so it was a one-book
    sport and could never arb. 94682 verified live 2026-08-28."""
    from edge.arb.draftkings_league import LEAGUE_IDS
    from edge.arb.fanduel import LEAGUE_PAGES
    assert LEAGUE_IDS["basketball_wnba"] == 94682
    assert "basketball_wnba" in LEAGUE_PAGES


def test_both_sides_plus_is_the_eyeball_screen():
    """Positive American odds means decimal >= 2.0, so 1/d <= 0.5 and two of
    them sum to <= 1.0. +100/+100 is the boundary -- exactly 1.0, which locks
    nothing -- so one leg has to be strictly longer."""
    from edge.arb.engine import both_sides_plus
    assert both_sides_plus([2.05, 2.00])          # +105 / +100
    assert both_sides_plus([2.36, 2.10])          # +136 / +110
    assert not both_sides_plus([2.00, 2.00])      # +100 / +100 is break-even
    assert not both_sides_plus([1.909, 1.909])    # -110 / -110
    assert not both_sides_plus([2.50, 1.90])      # one side negative
    assert not both_sides_plus([2.50])            # a single leg is not a market


def test_both_sides_plus_agrees_with_the_arithmetic():
    """The screen must never disagree with arb_sum, or it is a trap."""
    from edge.arb.engine import both_sides_plus
    for a in (1.7, 1.909, 2.0, 2.05, 2.4, 3.0):
        for b in (1.7, 1.909, 2.0, 2.05, 2.4, 3.0):
            if both_sides_plus([a, b]):
                assert om.arb_sum([a, b]) < 1.0, f"{a}/{b} screened in but is not an arb"


def test_a_boost_can_push_a_leg_to_plus_money():
    """The mechanism the screen relies on: 25% takes -110 to +114, 50% to +136."""
    assert om.format_american(om.boosted(1.909, 0.25)) == "+114"
    assert om.format_american(om.boosted(1.909, 0.50)) == "+136"


# --- boosted +EV: the boost you cannot hedge --------------------------------
def _cand(over=2.96, under=1.50, over_book="fanduel", dk_over=None, market="batter_hits"):
    """One candidate in snapshot shape, with per-book prices."""
    prices = {"over": {over_book: over}, "under": {"draftkings": under}}
    if dk_over is not None:
        prices["over"]["draftkings"] = dk_over
    return {
        "sport_key": "baseball_mlb", "sport_title": "MLB", "matchup": "A @ B",
        "commence_time": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
        "market": market, "subject": "Bobby Witt Jr.", "point": 1.5,
        "arb_sum": 1.0, "single_book": False,
        "legs": [{"side": "over", "book": over_book, "decimal": over, "label": "Over"},
                 {"side": "under", "book": "draftkings", "decimal": under, "label": "Under"}],
        "prices": prices,
    }


def test_boosted_ev_uses_the_books_own_price_not_the_best_one():
    """The whole reason `prices` exists. A DraftKings token is useless if the
    snapshot only kept FanDuel's better over -- the EV has to be computed on
    the price you can actually get at DraftKings."""
    from edge.arb.engine import Boost, price_boosted_ev
    c = cfg()
    b = Boost(book="draftkings", pct=0.5, max_stake=10.0, sides=["over"],
              min_decimal=1.5, markets=["batter_hits"])
    rows = price_boosted_ev([_cand(over=2.70, dk_over=2.96)], [b], c)
    assert rows and rows[0]["book"] == "draftkings"
    assert rows[0]["raw_decimal"] == 2.96          # DK's own, not FanDuel's 2.70
    assert rows[0]["boosted_decimal"] == pytest.approx(om.boosted(2.96, 0.5), abs=1e-4)


def test_boosted_ev_is_nothing_without_a_price_at_that_book():
    """FanDuel posts no under on batter markets; a DraftKings-over token has
    nothing to price if DraftKings does not post that side either."""
    from edge.arb.engine import Boost, price_boosted_ev
    b = Boost(book="draftkings", pct=0.5, sides=["over"], markets=["batter_hits"])
    assert price_boosted_ev([_cand(dk_over=None)], [b], cfg()) == []


def test_boosted_ev_respects_the_side_and_odds_terms():
    from edge.arb.engine import Boost, price_boosted_ev
    c = cfg()
    over_only = Boost(book="draftkings", pct=0.5, sides=["over"], min_decimal=1.5,
                      markets=["batter_hits"])
    # DK posts the under at 1.50 and an over at 2.96; the token is over-only
    rows = price_boosted_ev([_cand(dk_over=2.96)], [over_only], c)
    assert {r["side"] for r in rows} == {"over"}

    too_short = Boost(book="draftkings", pct=0.5, sides=["over"], min_decimal=1.5,
                      markets=["batter_hits"])
    assert price_boosted_ev([_cand(dk_over=1.40)], [too_short], c) == []


def test_the_fair_estimate_is_never_more_generous_than_the_book():
    """Devigging can hand back a probability longer than the price on offer;
    taking the book's own implied probability as a ceiling keeps the EV
    conservative rather than flattering."""
    from edge.arb.engine import Boost, price_boosted_ev
    b = Boost(book="draftkings", pct=0.5, sides=["over"], markets=["batter_hits"])
    rows = price_boosted_ev([_cand(over=2.70, under=1.50, dk_over=2.96)], [b], cfg())
    # tolerance is the stored precision: fair_prob is rounded to 5dp, which can
    # sit a hair above the cap. That is rounding, not generosity.
    assert rows[0]["fair_prob"] <= om.implied_prob(2.96) + 1e-5


def test_a_bigger_boost_is_worth_more_ev():
    from edge.arb.engine import Boost, price_boosted_ev
    c = cfg()
    got = []
    for pct in (0.25, 0.5, 1.0):
        b = Boost(book="draftkings", pct=pct, sides=["over"], markets=["batter_hits"])
        got.append(price_boosted_ev([_cand(dk_over=2.96)], [b], c)[0]["ev_pct"])
    assert got == sorted(got), f"EV must rise with the boost: {got}"


def test_a_single_book_market_is_not_an_arb_without_a_boost():
    """min_books rejects one book on both sides as a data artifact. That guard
    has to survive: only a boost makes such a pair a real position."""
    from edge.arb.engine import Boost, price_candidates
    c = cfg()
    single = _cand(over=2.20, under=2.20, over_book="draftkings")
    single["single_book"] = True
    assert price_candidates([single], [], c) == [], "priced a one-book pair with no boost"
    b = Boost(book="draftkings", pct=0.5, max_stake=10.0, sides=["over"],
              markets=["batter_hits"])
    assert price_candidates([single], [b], c), "a boost makes it real and it was still dropped"


# --- golf ------------------------------------------------------------------
def test_only_the_two_way_golf_markets_are_classified():
    """Golf is mostly fields -- Top 5 has 29-67 runners, the outright 150+ --
    and a field cannot be paired from a partial list of runners. Only the
    head-to-heads are usable."""
    from edge.arb.fanduel import classify
    assert classify("2_BALLS_IMG") == ("golf_2ball", None)
    assert classify("TOURNAMENT_MATCHBETS_IMG") == ("golf_matchup", None)
    assert classify("WHO_WILL_WIN_A_GROUP_OF_HOLES_IMG") == ("golf_hole_group", None)
    for field_market in ("TOP_5_FINISH_IMG", "TOP_10_FINISH_IMG", "ROUND_LEADER_IMG",
                         "OUTRIGHT_BETTING", "WIN_ONLY_IMG"):
        assert classify(field_market) is None, field_market


def _golf_payload(pairs, market_type="2_BALLS_IMG", event="2 Balls"):
    markets, i = {}, 0
    for names in pairs:
        i += 1
        markets[str(i)] = {
            "eventId": "9", "marketType": market_type,
            "marketName": f"2 Ball - {' / '.join(names)}", "marketStatus": "OPEN",
            "runners": [{"runnerName": n, "handicap": 0, "runnerStatus": "ACTIVE",
                         "winRunnerOdds": {"trueOdds": {"decimalOdds": {"decimalOdds": 2.0}}}}
                        for n in names],
        }
    return {"attachments": {
        "events": {"9": {"name": event, "openDate": (
            datetime.now(timezone.utc) + timedelta(hours=3)).isoformat().replace("+00:00", "Z")}},
        "markets": markets}}


def test_each_golf_pairing_gets_its_own_group():
    """All the two-balls in a tournament share one market key on one event, so
    without a subject they collapse into a single group -- 14 pairings became
    one group of 28 'sides', which is unpriceable nonsense."""
    from edge.arb.fanduel import FanDuelScrape
    board = Board()
    FanDuelScrape(state="ct").ingest_event(
        board, _golf_payload([["Tom Kim", "Alex Smalley"],
                              ["Ryan Fox", "Akshay Bhatia"]]),
        "golf_pga", strict_match=False)
    groups = [g for g in board.groups.values() if g.key.market == "golf_2ball"]
    assert len(groups) == 2, "the pairings did not separate"
    for g in groups:
        assert len(g.quotes) == 2, "a head-to-head has exactly two sides"


def test_the_golf_subject_is_built_from_the_players_not_the_label():
    """FanDuel writes '2 Ball (Round 3) - Smalley / T. Kim'; another book will
    label it differently. The pair of full runner names, sorted, is the part
    two books can agree on -- and sorting means the order they list them in
    does not matter."""
    from edge.arb.fanduel import FanDuelScrape
    board = Board()
    FanDuelScrape(state="ct").ingest_event(
        board, _golf_payload([["Tom Kim", "Alex Smalley"]]), "golf_pga",
        strict_match=False)
    g = next(g for g in board.groups.values() if g.key.market == "golf_2ball")
    assert g.key.subject == "alex_smalley|tom_kim"
    assert set(g.quotes) == {"tom_kim", "alex_smalley"}


def test_a_golf_market_that_is_not_a_head_to_head_is_dropped():
    """A three-runner market is a field, not a pairing; keying it by two of the
    three would invent a market that does not exist."""
    from edge.arb.fanduel import FanDuelScrape
    board = Board()
    st = FanDuelScrape(state="ct").ingest_event(
        board, _golf_payload([["A Player", "B Player", "C Player"]]),
        "golf_pga", strict_match=False)
    assert not [g for g in board.groups.values() if g.key.market == "golf_2ball"]
    assert any("3 runners" in u for u in st["unmapped"])


def test_a_tournament_event_resolves_without_home_and_away():
    """Golf events are tournaments, not matchups. Requiring 'away @ home'
    dropped every golf market before it reached the board."""
    from edge.arb.fanduel import FanDuelScrape
    board = Board()
    FanDuelScrape(state="ct").ingest_event(
        board, _golf_payload([["Tom Kim", "Alex Smalley"]], event="PGA Tour Championship"),
        "golf_pga", strict_match=False)
    g = next(iter(board.groups.values()))
    assert g.event.matchup == "PGA Tour Championship"


def test_a_tournament_event_is_never_matched_onto_an_existing_one():
    """There is no home/away for match_event to key on, so under strict_match
    such an event must be skipped rather than guessed at."""
    from edge.arb.fanduel import FanDuelScrape
    board = Board()
    st = FanDuelScrape(state="ct").ingest_event(
        board, _golf_payload([["Tom Kim", "Alex Smalley"]]), "golf_pga",
        strict_match=True)
    assert st["quotes"] == 0 and len(board.groups) == 0


def test_skipped_events_are_counted_before_prune_erases_them():
    """prune() deletes anything past its grace period, which is exactly the
    golf case -- so counting after it would always report nothing skipped and
    an empty board with no explanation."""
    from edge.arb import engine
    ev_live = EventMeta("g1", "golf_pga", "PGA",
                        datetime.now(timezone.utc) - timedelta(hours=4), "Championship", None)
    ev_soon = EventMeta("m1", "baseball_mlb", "MLB",
                        datetime.now(timezone.utc) + timedelta(hours=2), "H", "A")
    c = cfg()
    assert not engine.in_window(ev_live, c, datetime.now(timezone.utc))
    assert engine.in_window(ev_soon, c, datetime.now(timezone.utc))

    b = Board()
    b.events["g1"], b.events["m1"] = ev_live, ev_soon
    b.group(GroupKey("g1", "golf_2ball", "a|b", None), ev_live)
    b.group(GroupKey("m1", "h2h", None, None), ev_soon)
    before = len(b.events)
    b.prune()
    assert len(b.events) < before, "prune must drop the finished event"
    assert "g1" not in b.events, "counting after prune would see nothing to explain"


def test_golf_collapses_to_one_event_so_the_books_can_meet():
    """Golf has no fixture to join on, and the books disagree on what an event
    IS: FanDuel splits a tournament into '2 Balls', 'Hole Match Betting' and
    the tournament itself; DraftKings has a single 'Tour Championship'. Nothing
    matched, so 78 DraftKings quotes and 72 FanDuel quotes produced ZERO shared
    groups. The pairing is what both books agree on, so it carries the identity
    and the event collapses to one per tour."""
    from edge.arb.models import field_event
    a = field_event("golf_pga", datetime.now(timezone.utc), "Tour Championship 2026")
    b = field_event("golf_pga", datetime.now(timezone.utc) + timedelta(hours=6), "2 Balls")
    assert a.event_id == b.event_id, "the two books must land on one event"
    assert a.sport_title == "Golf"


def test_a_dk_and_fd_golf_pairing_land_on_the_same_group():
    """The end-to-end join: same two players, same market, one group."""
    from edge.arb.draftkings_nash import ingest_sportscontent
    from edge.arb.fanduel import FanDuelScrape
    from edge.arb.models import field_event

    when = datetime.now(timezone.utc) + timedelta(hours=4)
    board = Board()
    ev = field_event("golf_pga", when, "Tour Championship")

    FanDuelScrape(state="ct").ingest_event(board, {"attachments": {
        "events": {"1": {"name": "2 Balls",
                         "openDate": when.isoformat().replace("+00:00", "Z")}},
        "markets": {"m1": {"eventId": "1", "marketType": "2_BALLS_IMG",
                           "marketName": "2 Ball - Kim / Smalley", "marketStatus": "OPEN",
                           "runners": [
                               {"runnerName": n, "handicap": 0, "runnerStatus": "ACTIVE",
                                "winRunnerOdds": {"trueOdds": {"decimalOdds": {"decimalOdds": d}}}}
                               for n, d in (("Tom Kim", 1.61), ("Alex Smalley", 2.25))]}}}},
        "golf_pga", strict_match=False)

    ingest_sportscontent(board, {
        "markets": [{"id": "9", "eventId": "9", "name": "2 Ball - Round 4",
                     "marketType": {"name": "2 Ball - Round 4"}}],
        "selections": [
            {"marketId": "9", "label": "Tom Kim", "participants": [{"name": "Tom Kim", "type": "Team"}],
             "displayOdds": {"decimal": "1.70"}},
            {"marketId": "9", "label": "Alex Smalley", "participants": [{"name": "Alex Smalley", "type": "Team"}],
             "displayOdds": {"decimal": "2.30"}}],
    }, sport_key="golf_pga", strict_match=False, event=ev)

    g = [x for x in board.groups.values() if x.key.market == "golf_2ball"]
    assert len(g) == 1, f"the pairing split into {len(g)} groups"
    books = {b for per in g[0].quotes.values() for b in per}
    assert books == {"fanduel", "draftkings"}
    assert g[0].key.subject == "alex_smalley|tom_kim"


def test_a_three_way_golf_market_is_not_paired_with_a_two_way_one():
    """DraftKings offers a '(3 Way)' variant of every golf head-to-head, with an
    explicit Tie selection. The two-way pushes on a tie, the three-way pays a
    third outcome -- pricing one against the other is pricing two different
    bets."""
    from edge.arb.draftkings_nash import ingest_sportscontent
    from edge.arb.models import field_event
    board = Board()
    ev = field_event("golf_pga", datetime.now(timezone.utc) + timedelta(hours=4), "T")
    st = ingest_sportscontent(board, {
        "markets": [{"id": "9", "eventId": "9", "name": "2 Ball (3 Way) - Round 4",
                     "marketType": {"name": "2 Ball (3 Way) - Round 4"}}],
        "selections": [
            {"marketId": "9", "label": "Patrick Cantlay",
             "participants": [{"name": "Patrick Cantlay", "type": "Team"}],
             "displayOdds": {"decimal": "1.73"}},
            {"marketId": "9", "label": "Tie", "participants": [],
             "displayOdds": {"decimal": "8.30"}},
            {"marketId": "9", "label": "Kristoffer Reitan",
             "participants": [{"name": "Kristoffer Reitan", "type": "Team"}],
             "displayOdds": {"decimal": "2.58"}}],
    }, sport_key="golf_pga", strict_match=False, event=ev)
    assert st["quotes"] == 0
    assert not [g for g in board.groups.values() if g.key.market.startswith("golf_")]
    # Refused, and now refused EARLIER than it used to be: "(3 Way)" in the
    # name is caught by marketmap's guard before the runner count is reached,
    # because FanDuel offers the same two-way/three-way pair on rugby league
    # moneylines where there are no runners to count. Either reason is fine;
    # what matters is that it never reaches a golf_ key.
    assert any("3 Way" in u or "three-way" in u for u in st["markets_unmapped"])


# --- DraftKings league discovery -------------------------------------------
GOLF_PAGE_SNIPPET = (
    '{"sportName":"golf","hasOffers":true,"eventGroupInfos":'
    '[{"displayGroupId":12,"eventGroupId":71813,"eventGroupName":"Tour Championship"},'
    '{"displayGroupId":12,"eventGroupId":25461,"eventGroupName":"Presidents Cup"},'
    '{"displayGroupId":12,"eventGroupId":92694,"eventGroupName":"The Masters"}],'
    '"other":[{"name":"MLB","displayGroupId":"1","eventGroupId":"84240",'
    '"path":"/leagues/baseball/mlb"}]}')


def test_league_ids_are_parsed_out_of_the_page():
    """Golf is a league PER TOURNAMENT, so its id changes weekly and there is
    no API that lists them -- sportscontent has no endpoint, the v5 API is
    Akamai blocked, and pagedata offers only id -> slug. The league page's HTML
    carries them, and unlike the API on that host it is not blocked."""
    from edge.arb.draftkings_league import parse_league_page
    got = parse_league_page(GOLF_PAGE_SNIPPET, 12)
    assert got == {71813: "Tour Championship", 25461: "Presidents Cup",
                   92694: "The Masters"}


def test_discovery_ignores_other_sports_on_the_page():
    """The page carries a nav listing every sport; only this displayGroup's
    leagues are ours. Picking up MLB's 84240 as a golf tournament would send
    the golf scraper at a baseball league."""
    from edge.arb.draftkings_league import parse_league_page
    assert 84240 not in parse_league_page(GOLF_PAGE_SNIPPET, 12)
    assert parse_league_page(GOLF_PAGE_SNIPPET, 1) == {}


@pytest.mark.parametrize("html", ["", None, "<html>nothing here</html>",
                                  '{"displayGroupId":12}'])
def test_a_changed_page_yields_nothing_rather_than_garbage(html):
    """HTML scraping breaks when the page changes. It must fail soft so the
    caller falls back to configured ids -- a layout change should cost coverage,
    never the scan."""
    from edge.arb.draftkings_league import parse_league_page
    assert parse_league_page(html, 12) == {}


def test_groups_sharing_an_event_id_share_one_event():
    """Golf collapses ten tournaments onto one synthetic event id. Without
    this, groups built from the Presidents Cup carried a late-September start
    while groups from this weekend's round carried today's -- and the window
    check then dropped two thirds of the board depending on parse order."""
    from edge.arb.models import field_event
    soon = datetime.now(timezone.utc) + timedelta(hours=1)
    later = datetime.now(timezone.utc) + timedelta(days=26)
    b = Board()
    b.group(GroupKey("golf_pga:field", "golf_2ball", "a|b", None),
            field_event("golf_pga", soon, "Tour Championship"))
    g2 = b.group(GroupKey("golf_pga:field", "golf_2ball", "c|d", None),
                 field_event("golf_pga", later, "Presidents Cup"))
    assert g2.event.commence_time == soon, "the second event overrode the first"
    assert len({g.event.commence_time for g in b.groups.values()}) == 1


# --- tennis -----------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("Buffalo Bills @ Kansas City Chiefs", ("Buffalo Bills", "Kansas City Chiefs")),
    ("Lorenzo Carboni vs Pedro Martinez", ("Lorenzo Carboni", "Pedro Martinez")),
    ("A vs. B", ("A", "B")),
    ("X v Y", ("X", "Y")),
])
def test_a_fixture_is_parsed_from_either_separator(name, expected):
    """Team sports are written "Away @ Home", tennis "Player A vs Player B".
    Requiring "@" skipped every tennis event before it was looked at -- and
    tennis is a real two-player fixture, so it fits the ordinary model once
    parsed, unlike golf which needed a synthetic event."""
    from edge.arb.draftkings_league import split_fixture
    assert split_fixture(name) == expected


@pytest.mark.parametrize("name", ["Tour Championship 2026", "", "vs ", None,
                                  "Player Finishing Position"])
def test_a_non_fixture_name_is_refused(name):
    """An outright container is not a fixture; treating it as one would invent
    two competitors out of a tournament title."""
    from edge.arb.draftkings_league import split_fixture
    assert split_fixture(name) is None


def test_a_venueless_sport_reads_vs_not_at():
    """"Carboni @ Martinez" implies a home venue that tennis does not have."""
    from edge.arb.models import EventMeta
    when = datetime.now(timezone.utc)
    tennis = EventMeta("e", "tennis_atp", "Tennis", when, "Martinez", "Carboni")
    nfl = EventMeta("e", "americanfootball_nfl", "NFL", when, "Chiefs", "Bills")
    assert tennis.matchup == "Carboni vs Martinez"
    assert nfl.matchup == "Bills @ Chiefs"


def test_tennis_leagues_are_discovered_under_their_own_display_group():
    """Tennis is displayGroup 6; golf is 12. Reading the wrong group off the
    same page would point the tennis scraper at golf tournaments."""
    from edge.arb.draftkings_league import DISPLAY_GROUPS, parse_league_page
    assert DISPLAY_GROUPS["tennis"] == 6 and DISPLAY_GROUPS["golf"] == 12
    html = ('{"displayGroupId":6,"eventGroupId":205637,"eventGroupName":"Challenger - Porto"},'
            '{"displayGroupId":12,"eventGroupId":71813,"eventGroupName":"Tour Championship"}')
    assert parse_league_page(html, 6) == {205637: "Challenger - Porto"}
    assert parse_league_page(html, 12) == {71813: "Tour Championship"}


def test_a_moneyline_is_not_routed_into_the_prop_parser():
    """In a sport played by individuals the runners of an ordinary moneyline
    ARE people, and FanDuel flags them isPlayerSelection. Routing on that flag
    sent every tennis h2h into the prop parser, which found no Over/Under and
    no ladder rung and dropped it: 140 of 144 markets, silently, leaving 19
    quotes where there were 280. The market key is what says whether a line is
    a prop, not the runner."""
    from edge.arb.fanduel import FanDuelScrape
    when = datetime.now(timezone.utc) + timedelta(hours=3)
    payload = {"attachments": {
        "events": {"7": {"name": "Aoi Ito v Oksana Selekhmeteva",
                         "openDate": when.isoformat().replace("+00:00", "Z")}},
        "markets": {"m": {"eventId": "7", "marketType": "MATCH_BETTING",
                          "marketName": "Moneyline", "marketStatus": "OPEN",
                          "runners": [
                              {"runnerName": "Aoi Ito", "handicap": 0,
                               "runnerStatus": "ACTIVE", "isPlayerSelection": True,
                               "winRunnerOdds": {"trueOdds": {"decimalOdds": {"decimalOdds": 2.32}}}},
                              {"runnerName": "Oksana Selekhmeteva", "handicap": 0,
                               "runnerStatus": "ACTIVE", "isPlayerSelection": None,
                               "winRunnerOdds": {"trueOdds": {"decimalOdds": {"decimalOdds": 1.62}}}}]}}}}
    board = Board()
    st = FanDuelScrape(state="ct").ingest_event(board, payload, "tennis_atp",
                                                strict_match=False)
    assert st["quotes"] == 2, f"got {st['quotes']} quotes, the h2h was dropped"
    g = next(iter(board.groups.values()))
    assert g.key.market == "h2h"
    assert set(g.quotes) == {"home", "away"}


def test_tennis_moneyline_classifies():
    """FanDuel calls it MATCH_BETTING, not MONEY_LINE."""
    from edge.arb.fanduel import classify
    assert classify("MATCH_BETTING") == ("h2h", None)


def test_tennis_is_served_by_event_type_id_not_a_slug():
    """Tennis has no customPageId -- every slug guess 404s. page=SPORT with
    eventTypeId=2 is the shape FanDuel's own app uses."""
    from edge.arb.fanduel import EVENT_TYPE_IDS, LEAGUE_PAGES
    assert EVENT_TYPE_IDS["tennis_atp"] == 2
    assert "tennis_atp" not in LEAGUE_PAGES


# --- token expiry -----------------------------------------------------------
def test_an_event_after_the_token_expires_cannot_be_boosted():
    """Reported live: the scanner offered Vallejo vs Monfils as a boosted
    arbitrage against a DraftKings token expiring that night. The match was
    Tuesday, 55 hours out -- the odds cleared the -200 floor, but the offer is
    dated ("valid on Tennis on 8/30") and the event was outside it. A bet that
    cannot be placed."""
    from edge.arb.engine import Boost
    now = datetime.now(timezone.utc)
    tok = Boost(book="draftkings", pct=0.5, max_stake=5.0, sports=["tennis_atp"],
                min_decimal=1.5, expires_at=now + timedelta(hours=16))
    ok = dict(book="draftkings", sport_key="tennis_atp", market="h2h",
              side="away", decimal=1.76)
    assert tok.applies_to(**ok, event_start=now + timedelta(hours=1))
    assert tok.applies_to(**ok, event_start=now + timedelta(hours=15))
    assert not tok.applies_to(**ok, event_start=now + timedelta(hours=55))


def test_a_boost_with_no_expiry_is_unaffected():
    """Existing boosts carry none and must keep working."""
    from edge.arb.engine import Boost
    b = Boost(book="fanduel", pct=0.25)
    assert b.applies_to("fanduel", "x", "h2h", "over", 2.0,
                        event_start=datetime.now(timezone.utc) + timedelta(days=90))


def test_an_unreadable_start_time_does_not_block_every_boost():
    """A snapshot written before commence_time was carried should degrade to
    'no expiry rule', not to 'nothing is boostable'."""
    from edge.arb.engine import _start_of, Boost
    assert _start_of({"commence_time": "not a date"}) is None
    assert _start_of({}) is None
    tok = Boost(book="fanduel", pct=0.5,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=2))
    assert tok.applies_to("fanduel", "x", "h2h", "over", 2.0, event_start=None)
