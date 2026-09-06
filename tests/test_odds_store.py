"""The market data layer: store round-trip and Odds-API shape fidelity.

The tests that matter here are the SHAPE ones. `project_pitcher` and
`_parse_events` are unchanged code with backtested behaviour behind them; if
ScrapedOddsClient hands them a payload that differs from the Odds API's in any
way those functions read, the models break quietly rather than loudly. So the
assertions are against what those consumers actually index into, not against a
payload that merely looks plausible.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from edge import dfs
from edge.arb.models import Board, EventMeta, GroupKey, Quote
from edge.odds import OddsStore, ScrapedOddsClient, board_to_rows, group_key_str
from edge.odds.parity import compare_keyed
from edge.odds.source import StaleOdds
from edge.pickem_live import _parse_events

NOW = datetime(2026, 9, 6, 18, 0, tzinfo=timezone.utc)
SOON = NOW + timedelta(hours=3)


def _board() -> Board:
    """A board with the three shapes every consumer depends on: a game spread,
    a game total, and a two-sided player prop with an alternate ladder."""
    board = Board()
    ev = EventMeta(event_id="e1", sport_key="baseball_mlb", sport_title="MLB",
                   commence_time=SOON, home_team="New York Yankees",
                   away_team="Boston Red Sox")

    def add(market, subject, point, side, book, dec):
        g = board.group(GroupKey("e1", market, subject, point), ev)
        g.add(Quote(book=book, side=side, decimal=dec, point=point, last_update=NOW))

    add("spreads", None, -1.5, "home", "draftkings", 2.10)
    add("spreads", None, -1.5, "away", "draftkings", 1.76)
    add("totals", None, 8.5, "over", "draftkings", 1.91)
    add("totals", None, 8.5, "under", "draftkings", 1.91)
    add("totals", None, 8.5, "over", "fanduel", 1.95)
    add("totals", None, 8.5, "under", "fanduel", 1.87)

    # main rung (nearly even) plus two lopsided alternates -- the ladder that
    # would otherwise mix a price from one rung with the line from another
    add("pitcher_strikeouts", "Gerrit Cole", 6.5, "over", "draftkings", 1.95)
    add("pitcher_strikeouts", "Gerrit Cole", 6.5, "under", "draftkings", 1.87)
    add("pitcher_strikeouts", "Gerrit Cole", 4.5, "over", "draftkings", 1.30)
    add("pitcher_strikeouts", "Gerrit Cole", 4.5, "under", "draftkings", 3.40)
    add("pitcher_strikeouts", "Gerrit Cole", 9.5, "over", "draftkings", 4.20)
    add("pitcher_strikeouts", "Gerrit Cole", 9.5, "under", "draftkings", 1.24)
    add("pitcher_outs", "Gerrit Cole", 17.5, "over", "draftkings", 1.90)
    add("pitcher_outs", "Gerrit Cole", 17.5, "under", "draftkings", 1.90)
    return board


@pytest.fixture()
def store(tmp_path):
    with OddsStore(tmp_path / "odds.db") as s:
        yield s


@pytest.fixture()
def loaded(store):
    events, quotes = board_to_rows(_board())
    scan_id = store.begin_scan("dfs_mlb")
    store.write_events(events)
    store.write_quotes(scan_id, quotes)
    store.finish_scan(scan_id, {"price_conflicts": 0})
    return store, scan_id


# --- store ------------------------------------------------------------------

def test_group_key_is_stable_across_null_subject_and_point():
    a = group_key_str(GroupKey("e1", "totals", None, 8.5))
    b = group_key_str(GroupKey("e1", "totals", None, 8.5))
    assert a == b
    assert a != group_key_str(GroupKey("e1", "totals", None, 9.5))
    # A NULL subject must not make two rows distinct -- SQLite treats NULLs in
    # a composite PRIMARY KEY as different from each other, which would let the
    # same total persist twice in one scan.
    assert "||" in a


def test_uncommitted_scan_is_invisible(store):
    events, quotes = board_to_rows(_board())
    scan_id = store.begin_scan("dfs_mlb")
    store.write_events(events)
    store.write_quotes(scan_id, quotes)
    assert store.latest_scan("dfs_mlb") is None   # not finished
    store.finish_scan(scan_id, {})
    assert store.latest_scan("dfs_mlb")["id"] == scan_id


def test_abandoned_scan_leaves_nothing_behind(store):
    events, quotes = board_to_rows(_board())
    scan_id = store.begin_scan("dfs_mlb")
    store.write_events(events)
    store.write_quotes(scan_id, quotes)
    store.abandon_scan(scan_id)
    assert store.latest_scan("dfs_mlb") is None
    assert store.conn.execute("SELECT COUNT(*) c FROM quote").fetchone()["c"] == 0


def test_history_accumulates_across_scans(store):
    board = _board()
    events, quotes = board_to_rows(board)
    for _ in range(2):
        sid = store.begin_scan("dfs_mlb")
        store.write_events(events)
        store.write_quotes(sid, quotes)
        store.finish_scan(sid, {})
    gk = group_key_str(GroupKey("e1", "totals", None, 8.5))
    assert len(store.history(gk, side="over", book="draftkings")) == 2


def test_reads_are_scoped_to_one_scan(loaded):
    store, scan_id = loaded
    later = store.begin_scan("dfs_mlb")
    store.finish_scan(later, {})
    # the newest scan is empty; it must not inherit the older one's quotes
    assert store.quotes(later, "baseball_mlb") == []
    assert store.quotes(scan_id, "baseball_mlb")


# --- Odds API shape fidelity ------------------------------------------------

def test_get_events_uses_the_key_consumers_index(loaded):
    store, _ = loaded
    client = ScrapedOddsClient(store, "dfs_mlb")
    events = client.get_events("baseball_mlb")
    assert len(events) == 1
    # build_slate does ev["id"]; the store's column is event_id
    assert events[0]["id"] == "e1"
    assert events[0]["home_team"] == "New York Yankees"


def test_ladder_collapses_to_its_main_rung(loaded):
    """The bug this guards: dfs.player_markets keeps ONE point per market and
    the last outcome wins it, so a whole ladder mixes a price from one rung
    with the line from another."""
    store, _ = loaded
    client = ScrapedOddsClient(store, "dfs_mlb")
    payload = client.get_event_odds("baseball_mlb", "e1", dfs.P_MARKETS)
    dk = next(b for b in payload["bookmakers"] if b["key"] == "draftkings")
    ks = next(m for m in dk["markets"] if m["key"] == "pitcher_strikeouts")
    assert {o["point"] for o in ks["outcomes"]} == {6.5}     # not 4.5 or 9.5
    assert {o["name"] for o in ks["outcomes"]} == {"Over", "Under"}
    assert {o["description"] for o in ks["outcomes"]} == {"Gerrit Cole"}


def test_project_pitcher_runs_unchanged_on_the_payload(loaded):
    store, _ = loaded
    client = ScrapedOddsClient(store, "dfs_mlb")
    payload = client.get_event_odds("baseball_mlb", "e1", dfs.P_MARKETS)
    dk = next(b for b in payload["bookmakers"] if b["key"] == "draftkings")
    res = dfs.project_pitcher(dfs.player_markets(dk, "Gerrit Cole"))
    assert res["proj"] is not None
    # both core markets present, so nothing in the core is imputed
    assert "ER" in res["imputed"] and "out" not in res["imputed"]
    assert 4.0 < res["k_mean"] < 9.0


def test_spread_away_side_carries_its_own_posted_line(loaded):
    """Spreads are stored folded onto the home axis. Emitting the raw stored
    number is how a spread once showed BOTH teams laying points."""
    store, _ = loaded
    client = ScrapedOddsClient(store, "dfs_mlb")
    payload = client.get_event_odds("baseball_mlb", "e1", ["spreads"])
    dk = next(b for b in payload["bookmakers"] if b["key"] == "draftkings")
    sp = next(m for m in dk["markets"] if m["key"] == "spreads")
    by_name = {o["name"]: o["point"] for o in sp["outcomes"]}
    assert by_name["New York Yankees"] == -1.5
    assert by_name["Boston Red Sox"] == 1.5


def test_pickem_parses_the_payload_into_a_consensus(loaded):
    store, _ = loaded
    client = ScrapedOddsClient(store, "dfs_mlb")
    games = _parse_events(client.get_featured_odds("baseball_mlb",
                                                   ["spreads", "totals"]))
    assert len(games) == 1
    g = games[0]
    assert g.live_line == -1.5
    assert g.total == 8.5
    assert g.n_books == 1              # only DraftKings posts the spread here
    assert set(g.book_totals) == {"draftkings", "fanduel"}


def test_missing_event_returns_an_empty_payload_not_a_crash(loaded):
    store, _ = loaded
    client = ScrapedOddsClient(store, "dfs_mlb")
    assert client.get_event_odds("baseball_mlb", "nope", ["totals"])["bookmakers"] == []


# --- freshness contract -----------------------------------------------------

def test_stale_scan_is_refused_rather_than_served(loaded):
    store, _ = loaded
    fresh = ScrapedOddsClient(store, "dfs_mlb", max_age_seconds=3600)
    assert fresh.get_events("baseball_mlb")
    strict = ScrapedOddsClient(store, "dfs_mlb", max_age_seconds=0.0)
    with pytest.raises(StaleOdds):
        strict.get_events("baseball_mlb")


def test_unknown_profile_does_not_fall_back_to_another(loaded):
    """A DFS client must not silently read the wide arb scan, whose
    prop_events_per_league cap covers part of a slate."""
    store, _ = loaded
    with pytest.raises(StaleOdds):
        ScrapedOddsClient(store, "arb").get_events("baseball_mlb")


def test_client_reports_no_credit_spend(loaded):
    store, _ = loaded
    client = ScrapedOddsClient(store, "dfs_mlb")
    assert client.spent_this_session == 0
    assert client.remaining_credits() is None


# --- parity -----------------------------------------------------------------

def test_coverage_counts_what_was_dropped_not_what_matched():
    d = compare_keyed({"a": 1.0, "b": 2.0, "c": 3.0}, {"a": 1.0, "b": 2.0}, "pts")
    assert d.coverage == pytest.approx(2 / 3)
    assert d.mae == 0.0        # perfect on the matched set, and still 67% cover
    assert d.n_paid == 3 and d.n_free == 2


def test_parity_separates_bias_from_noise():
    d = compare_keyed({"a": 0.0, "b": 0.0}, {"a": 1.0, "b": -1.0}, "pts")
    assert d.bias == 0.0       # cancels -- pick'em reads a move
    assert d.mae == 1.0        # does not cancel
