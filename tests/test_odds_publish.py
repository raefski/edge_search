"""The cloud path: a published snapshot must serve what the store serves.

WHY THIS MATTERS MORE THAN IT LOOKS
Streamlit Cloud deploys from git and cannot scrape, and data/odds.db is
gitignored on purpose. So the snapshot is the ONLY free price source the
phone-facing app has. If it drifts from what the desktop serves, the cloud app
does not break loudly -- it falls back to the paid Odds API and quietly starts
spending credits again, which is the exact thing this whole layer removed.

So the property under test is equivalence: the same consumer code, over the
store and over a snapshot exported from it, must produce the same answers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from edge import dfs
from edge.arb.models import Board, EventMeta, GroupKey, Quote
from edge.odds import OddsStore, ScrapedOddsClient, board_to_rows
from edge.odds.publish import SnapshotOddsClient, export
from edge.odds.source import StaleOdds
from edge.pickem_live import _parse_events

NOW = datetime(2026, 9, 6, 18, 0, tzinfo=timezone.utc)
SOON = NOW + timedelta(hours=3)


def _board() -> Board:
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
    add("pitcher_strikeouts", "Gerrit Cole", 6.5, "over", "draftkings", 1.95)
    add("pitcher_strikeouts", "Gerrit Cole", 6.5, "under", "draftkings", 1.87)
    add("pitcher_outs", "Gerrit Cole", 17.5, "over", "draftkings", 1.90)
    add("pitcher_outs", "Gerrit Cole", 17.5, "under", "draftkings", 1.90)
    return board


@pytest.fixture()
def loaded(tmp_path):
    store = OddsStore(tmp_path / "odds.db")
    events, quotes = board_to_rows(_board())
    scan_id = store.begin_scan("dfs_mlb")
    store.write_events(events)
    store.write_quotes(scan_id, quotes)
    store.finish_scan(scan_id, {"price_conflicts": 0})
    yield store, tmp_path
    store.close()


def test_snapshot_serves_what_the_store_serves(loaded):
    """The equivalence that keeps the cloud app off the paid feed."""
    store, tmp = loaded
    path = tmp / "snap.json"
    export(store, "dfs_mlb", None, path)

    live = ScrapedOddsClient(store, "dfs_mlb")
    snap = SnapshotOddsClient(path)

    assert ([e["id"] for e in live.get_events("baseball_mlb")]
            == [e["id"] for e in snap.get_events("baseball_mlb")])

    a = live.get_event_odds("baseball_mlb", "e1", dfs.P_MARKETS)
    b = snap.get_event_odds("baseball_mlb", "e1", dfs.P_MARKETS)
    ma = {bk["key"]: {m["key"] for m in bk["markets"]} for bk in a["bookmakers"]}
    mb = {bk["key"]: {m["key"] for m in bk["markets"]} for bk in b["bookmakers"]}
    assert ma == mb


def test_projection_is_identical_across_both_sources(loaded):
    store, tmp = loaded
    path = tmp / "snap.json"
    export(store, "dfs_mlb", None, path)

    def project(client):
        p = client.get_event_odds("baseball_mlb", "e1", dfs.P_MARKETS)
        dk = next(b for b in p["bookmakers"] if b["key"] == "draftkings")
        return dfs.project_pitcher(dfs.player_markets(dk, "Gerrit Cole"))["proj"]

    assert project(SnapshotOddsClient(path)) == project(ScrapedOddsClient(store, "dfs_mlb"))


def test_pickem_consensus_is_identical_across_both_sources(loaded):
    store, tmp = loaded
    path = tmp / "snap.json"
    export(store, "dfs_mlb", None, path)
    markets = ["spreads", "totals"]

    def consensus(client):
        g = _parse_events(client.get_featured_odds("baseball_mlb", markets))[0]
        return g.live_line, g.total, g.n_books

    assert consensus(SnapshotOddsClient(path)) == consensus(ScrapedOddsClient(store, "dfs_mlb"))


def test_publish_markets_trim_the_file(loaded):
    """The trim is what keeps a per-collection commit small enough to live in
    git history at all."""
    store, tmp = loaded
    full = export(store, "dfs_mlb", None, tmp / "full.json")
    trimmed = export(store, "dfs_mlb", ["spreads", "totals"], tmp / "trim.json")
    assert trimmed["bytes"] < full["bytes"]

    snap = SnapshotOddsClient(tmp / "trim.json")
    keys = {m["key"] for e in snap.get_featured_odds("baseball_mlb", ["spreads", "totals"])
            for bk in e["bookmakers"] for m in bk["markets"]}
    assert keys == {"spreads", "totals"}
    assert "pitcher_outs" not in keys


def test_a_stale_snapshot_is_refused_not_served(loaded):
    """A snapshot reaches the cloud through a git push, so it is always somewhat
    behind. The bound is looser than the store's -- but it is real, because
    serving yesterday's prices as today's is the failure that matters."""
    store, tmp = loaded
    path = tmp / "snap.json"
    export(store, "dfs_mlb", None, path)
    assert SnapshotOddsClient(path, max_age_seconds=3600).get_events("baseball_mlb")
    with pytest.raises(StaleOdds):
        SnapshotOddsClient(path, max_age_seconds=-1)


def test_snapshot_reports_no_credit_spend(loaded):
    store, tmp = loaded
    path = tmp / "snap.json"
    export(store, "dfs_mlb", None, path)
    snap = SnapshotOddsClient(path)
    assert snap.spent_this_session == 0
    assert snap.remaining_credits() is None


def test_missing_event_returns_empty_not_a_crash(loaded):
    store, tmp = loaded
    path = tmp / "snap.json"
    export(store, "dfs_mlb", None, path)
    snap = SnapshotOddsClient(path)
    assert snap.get_event_odds("baseball_mlb", "nope", ["totals"])["bookmakers"] == []


def test_export_refuses_a_profile_with_no_committed_scan(loaded):
    store, tmp = loaded
    with pytest.raises(ValueError):
        export(store, "pickem_nfl", None, tmp / "x.json")
