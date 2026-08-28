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
from edge.arb.engine import find_arbitrages, find_ev, find_middles, middle_scenarios
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
