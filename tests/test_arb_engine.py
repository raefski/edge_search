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


def test_the_better_leg_is_the_one_boosted():
    """A boost is worth more on the leg carrying more of the stake, so both
    have to be tried rather than assuming the longer price."""
    from edge.arb.engine import Boost
    c = cfg()
    c.boosts = [Boost(book="draftkings", pct=0.5, max_stake=500.0),
                Boost(book="fanduel", pct=0.5, max_stake=500.0)]
    o = find_arbitrages(_two_way_board(over=1.5, under=3.0), c)[0]
    assert len([l for l in o.legs if l.boost_pct]) == 1
    alt = om.arb_sum([1.5, om.boosted(3.0, 0.5)])
    chosen = om.arb_sum([l.decimal for l in o.legs])
    assert chosen <= alt + 1e-9, "picked the worse leg to boost"


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
