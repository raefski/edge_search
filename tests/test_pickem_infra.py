"""Tests for the pick'em infrastructure added 2026-08-22:
multi-book consensus, the snapshot log, and standings strategy."""
import tempfile
from pathlib import Path

from edge.pickem_live import BOOK_WEIGHTS, _parse_events, weighted_consensus
from edge.pickem_log import Snapshot, append, cbs_offset, load
from edge.pickem_strategy import (
    GameContext, PoolState, apply, divergences_needed, mode_for,
)


# --- multi-book consensus --------------------------------------------------

def _event(home="Seattle Seahawks", away="New England Patriots", spreads=None, totals=None):
    books = []
    for bk, pt in (spreads or {}).items():
        books.append({"key": bk, "markets": [
            {"key": "spreads", "outcomes": [
                {"name": home, "point": pt}, {"name": away, "point": -pt}]}]})
    for bk, pt in (totals or {}).items():
        entry = next((b for b in books if b["key"] == bk), None)
        mk = {"key": "totals", "outcomes": [
            {"name": "Over", "point": pt}, {"name": "Under", "point": pt}]}
        if entry:
            entry["markets"].append(mk)
        else:
            books.append({"key": bk, "markets": [mk]})
    return {"home_team": home, "away_team": away,
            "commence_time": "2026-09-10T00:20:00Z", "bookmakers": books}


def test_weighted_consensus_favours_sharper_books():
    # lowvig (2.0) should pull the number harder than bovada (0.5)
    plain = weighted_consensus({"betmgm": -3.0, "williamhill_us": -3.0})
    assert plain == -3.0
    tilted = weighted_consensus({"lowvig": -4.0, "bovada": -3.0})
    # weights 2.0 vs 0.5 -> (2*-4 + 0.5*-3) / 2.5 = -3.8
    assert abs(tilted - (-3.8)) < 1e-9


def test_weighted_consensus_handles_unknown_books_at_default_weight():
    assert weighted_consensus({"some_new_book": -7.0}) == -7.0


def test_weighted_consensus_of_nothing_is_none():
    assert weighted_consensus({}) is None


def test_parse_events_extracts_spread_total_and_disagreement():
    ev = _event(spreads={"draftkings": -3.0, "fanduel": -4.0},
                totals={"draftkings": 44.5, "fanduel": 45.5})
    g = _parse_events([ev])[0]
    assert g.home_abbr == "SEA" and g.away_abbr == "NE"
    assert g.n_books == 2
    assert g.live_line_mean == -3.5           # plain mean
    assert g.book_spread == 1.0               # books disagree by a full point
    assert g.total is not None
    # both books carry weight 1.5, so weighted == mean here
    assert abs(g.live_line - (-3.5)) < 1e-9


def test_parse_events_survives_a_game_with_no_market_yet():
    g = _parse_events([_event(spreads={})])[0]
    assert g.live_line is None and g.n_books == 0
    assert g.book_spread is None              # not a crash, just unknown


def test_sharp_books_outweigh_recreational_ones():
    assert BOOK_WEIGHTS["lowvig"] > BOOK_WEIGHTS["bovada"]
    assert BOOK_WEIGHTS["pinnacle"] > BOOK_WEIGHTS["draftkings"]


# --- snapshot log ----------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp()) / "line_log.csv"


def test_append_is_idempotent_for_the_same_snapshot():
    p = _tmp()
    s = Snapshot(season=2026, week=1, snapshot="post", away_team="NE",
                 home_team="SEA", cbs_line_home=-3.5, market_line_home=-4.2)
    assert append([s], p) == 1
    assert append([s], p) == 0          # re-running a capture must not duplicate
    assert len(load(p)) == 1


def test_a_different_snapshot_label_is_a_new_row():
    p = _tmp()
    a = Snapshot(season=2026, week=1, snapshot="post", away_team="NE",
                 home_team="SEA", market_line_home=-3.5)
    b = Snapshot(season=2026, week=1, snapshot="lock", away_team="NE",
                 home_team="SEA", market_line_home=-5.0)
    append([a], p)
    assert append([b], p) == 1
    assert len(load(p)) == 2


def test_cbs_offset_is_market_at_post_minus_cbs():
    p = _tmp()
    append([Snapshot(season=2026, week=1, snapshot="post", away_team="NE",
                     home_team="SEA", cbs_line_home=-3.5, market_line_home=-4.2)], p)
    assert abs(cbs_offset(2026, 1, "SEA", p) - (-0.7)) < 1e-6


def test_cbs_offset_and_drift_add_to_the_total_edge():
    """The regression guard for the 2026-08-23 sign bug (PICKEM_MODEL.md 5j r3).

    The old version returned cbs_line - market_at_post, and the prescribed
    formula `(market_now - cbs) - bias` then reduced to
    `market_now + market_at_post - 2*cbs` -- doubling the offset rather than
    removing it, and wrong in exactly the no-drift case it existed for.
    """
    p = _tmp()
    cbs, at_post, at_lock = -3.5, -4.5, -5.5
    append([Snapshot(season=2026, week=1, snapshot="post", away_team="NE",
                     home_team="SEA", cbs_line_home=cbs, market_line_home=at_post)], p)
    offset = cbs_offset(2026, 1, "SEA", p)
    drift = at_lock - at_post
    assert abs(offset - (-1.0)) < 1e-6
    assert abs(offset + drift - (at_lock - cbs)) < 1e-6      # components must ADD

    # the specific case the old formula got wrong: offset present, zero drift
    p2 = _tmp()
    append([Snapshot(season=2026, week=1, snapshot="post", away_team="NE",
                     home_team="SEA", cbs_line_home=-3.5, market_line_home=-4.5)], p2)
    assert abs(cbs_offset(2026, 1, "SEA", p2) + 0.0 - (-4.5 - -3.5)) < 1e-6


def test_cbs_offset_is_none_until_both_numbers_exist():
    p = _tmp()
    append([Snapshot(season=2026, week=1, snapshot="post", away_team="NE",
                     home_team="SEA", cbs_line_home=-3.5)], p)   # no market reading
    assert cbs_offset(2026, 1, "SEA", p) is None
    assert cbs_offset(2026, 1, "KC", p) is None                  # unknown game


# --- standings strategy ----------------------------------------------------

def _slate():
    return [
        GameContext("EDGE@GAME", "away", 0.588, 78),   # real edge, field with us
        GameContext("FLIP@ONE", "home", 0.50, 81),     # coin flip, field heavy
        GameContext("FLIP@TWO", "home", 0.50, 79),
        GameContext("FLIP@THREE", "away", 0.50, 40),   # coin flip, field against
    ]


def test_before_week_14_it_stays_out_of_the_way():
    st = PoolState(week=8, weeks_remaining=10, my_rank=6, my_wins=70,
                   leader_wins=76, n_players=18)
    assert mode_for(st) == "neutral"
    assert all(not r.deviated for r in apply(st, _slate()))


def test_leader_conforms_only_on_free_coin_flips():
    st = PoolState(week=16, weeks_remaining=3, my_rank=1, my_wins=140,
                   leader_wins=140, n_players=18)
    assert mode_for(st) == "protect"
    recs = {r.matchup: r for r in apply(st, _slate())}
    # the one coin flip where the field disagrees with us -> side with the crowd
    assert recs["FLIP@THREE"].deviated is True
    # never give away a genuine edge to follow the crowd
    assert recs["EDGE@GAME"].deviated is False


def test_chasing_spends_coin_flips_first_and_protects_real_edges():
    st = PoolState(week=15, weeks_remaining=4, my_rank=4, my_wins=131,
                   leader_wins=134, n_players=18)
    assert mode_for(st) == "chase"
    recs = {r.matchup: r for r in apply(st, _slate())}
    assert recs["EDGE@GAME"].deviated is False        # the edge survives
    flipped = [m for m, r in recs.items() if r.deviated]
    assert flipped, "a chasing pool player must actually diverge somewhere"
    assert all(recs[m].ev_cost <= 0.12 for m in flipped)


def test_divergence_count_scales_with_deficit_and_urgency():
    def need(gap, weeks):
        return divergences_needed(PoolState(
            week=15, weeks_remaining=weeks, my_rank=5, my_wins=100,
            leader_wins=100 + gap, n_players=18))
    assert need(0, 4) == 0                # level -> nothing to chase
    assert need(3, 6) < need(3, 2)        # less time -> more urgency
    assert need(2, 4) < need(6, 4)        # bigger hole -> more divergence


def test_leader_with_no_deficit_never_chases():
    st = PoolState(week=17, weeks_remaining=1, my_rank=1, my_wins=150,
                   leader_wins=150, n_players=18)
    assert divergences_needed(st) == 0


# --- transferability estimator (PICKEM_MODEL.md 5j round 6) -----------------

def test_transferability_recovers_a_known_w_from_second_moments():
    """w is a VARIANCE share, so it must be estimated with second moments.

    Regression guard for a real bug: the first version of this estimator used
    the ratio of MEAN ABSOLUTE gaps, which overestimated w by ~30% (true 0.50
    recovered as 0.66) because 19.7% of historical games do not move at all --
    a spike that drags E|M| down relative to SD(M). Second moments are exact
    regardless of distribution shape.
    """
    import math
    import random
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import pickem_transferability as PT

    for true_w in (1.0, 0.5, 0.25):
        rng = random.Random(4)
        p = _tmp()
        snaps = []
        for i in range(400):
            gap = math.sqrt(true_w) * 1.8152 * rng.gauss(0, 1)
            snaps += [
                Snapshot(season=2026, week=1 + i // 16, snapshot="post",
                         away_team=f"A{i}", home_team=f"H{i}",
                         cbs_line_home=-3.5, market_line_home=-3.5),
                Snapshot(season=2026, week=1 + i // 16, snapshot="lock",
                         away_team=f"A{i}", home_team=f"H{i}",
                         cbs_line_home=-3.5, market_line_home=-3.5 + gap),
            ]
        append(snaps, p)
        games = [g for g in PT.collect(p) if g["lock"] is not None]
        assert len(games) == 400
        sq = [(g["lock"] - g["cbs"]) ** 2 for g in games]
        w = sum(sq) / len(sq) / PT.DEV_MEAN_SQ_MOVE
        assert abs(w - true_w) < 0.10, f"w={w} vs true {true_w}"


def test_transferability_curve_is_monotone_and_bottoms_at_chalk():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import pickem_transferability as PT

    margins = [PT.interp(w)[0] for w in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]
    assert margins == sorted(margins), "edge must not increase as w falls"
    assert abs(PT.interp(0.0)[0]) < 0.5, "at w=0 the model should collapse to chalk"
    assert PT.interp(1.0)[0] > 6.0, "at w=1 it must reproduce the backtested margin"


# --- free feed slate filter (architecture review fix 1) --------------------

def test_free_feed_rejects_a_board_spanning_two_slates():
    """Regression guard for a live-affecting bug found 2026-08-24.

    The books' league endpoints return their ENTIRE visible board, not one
    week. Unfiltered, that was 44 events from preseason through Christmas with
    12 home teams appearing 2-4 times. Every consumer keys by home_abbr, so
    duplicates silently collapsed to whichever landed last -- often a PRESEASON
    game -- and the model then emitted maximum-confidence picks off a
    fabricated edge (CLE +7.5 at a claimed +9.00; five of six STRONG picks
    were garbage).
    """
    from datetime import datetime, timezone
    import pytest
    from edge.pickem_free import filter_to_slate
    from edge.pickem_live import LiveGame

    def g(home, away, kickoff, line):
        return LiveGame(away=away, home=home, away_abbr=away, home_abbr=home,
                        kickoff=kickoff, live_line=line, live_line_mean=line,
                        live_line_median=line, total=44.0, n_books=2)

    preseason = g("SEA", "GB", "2026-08-28T23:00:00Z", 1.5)
    week1 = g("SEA", "NE", "2026-09-10T00:20:00Z", -3.5)
    week3 = g("SEA", "DAL", "2026-09-24T00:20:00Z", -2.5)
    board = [preseason, week1, week3]

    # a window catching two slates must RAISE, not silently pick one
    with pytest.raises(ValueError, match="more than once"):
        filter_to_slate(board,
                        window_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                        window_end=datetime(2026, 12, 1, tzinfo=timezone.utc))

    # a correct window yields exactly the Week 1 game, with the Week 1 line
    only = filter_to_slate(board,
                           window_start=datetime(2026, 9, 9, tzinfo=timezone.utc),
                           window_end=datetime(2026, 9, 16, tzinfo=timezone.utc))
    assert len(only) == 1
    assert only[0].live_line == -3.5, "must be the Week 1 line, not preseason's"

    # the escape hatch still works, but only when asked for explicitly
    raw = filter_to_slate(board,
                          window_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                          window_end=datetime(2026, 12, 1, tzinfo=timezone.utc),
                          allow_duplicates=True)
    assert len(raw) == 3


# --- weekly capture: joining two team vocabularies, and one week at a time ---
# All four guard bugs found 2026-09-08, when the log was still empty and the
# first live capture of the season had not yet run.

def test_capture_joins_cbs_and_market_across_team_vocabularies():
    """The two sides spell two teams differently, and an unaliased join is silent.

    The market side names teams via edge/nfl.py (nflverse spelling: LA, WAS);
    the pool side via edge/pickem_cbs.py (CBS spelling: LAR, WSH). Joined raw,
    the Rams and Commanders match nothing and are logged with a CBS line and no
    market reading beside it -- which is precisely the pairing a snapshot exists
    to create. Same failure shape as dfs_lineups_nfl.py's DK_ALIAS.
    """
    from scripts.pickem_capture import _cbs_abbr

    assert _cbs_abbr("LA") == "LAR"
    assert _cbs_abbr("WAS") == "WSH"
    assert _cbs_abbr("SEA") == "SEA"          # everything else passes through


def test_week_window_keeps_monday_night_in_its_own_week():
    """A Monday night kickoff is Tuesday in UTC.

    Bucketing the slate by UTC date pushed DEN@KC (Mon 2026-09-14 8:15pm ET =
    Tue 00:15 UTC) into week 2, so week 1 came back as 15 games instead of 16.
    The window is therefore built in ET, the timezone the pool actually runs on.
    """
    import datetime
    from scripts.pickem_capture import week_window

    start, end = week_window(1)
    mnf = datetime.datetime(2026, 9, 15, 0, 15, tzinfo=datetime.timezone.utc)
    assert start <= mnf < end

    # ...and it belongs to week 1 only.
    start2, _ = week_window(2)
    assert not (start2 <= mnf)


def test_transferability_counts_every_per_deadline_lock_label():
    """The pool's deadline is per DAY, so a week holds several lock readings.

    edge.pickem_log.append de-dupes on (season, week, snapshot, home_team), so
    reusing a bare 'lock' for all four deadlines keeps the FIRST reading of the
    week and silently discards the rest. Each deadline gets its own label, and
    the analysis has to recognise them -- otherwise the extra captures are
    written and then ignored, which is the worst of both.
    """
    import tempfile
    from pathlib import Path
    from edge.pickem_log import Snapshot, append
    from scripts.pickem_transferability import collect

    log = Path(tempfile.mkdtemp()) / "log.csv"

    def snap(label, market, captured):
        return Snapshot(season=2026, week=1, snapshot=label, captured_at=captured,
                        away_team="DEN", home_team="KC",
                        kickoff_utc="2026-09-15T00:15:00+00:00",
                        cbs_line_home=-2.5, market_line_home=market)

    append([snap("post", -2.5, "2026-09-08T18:00:00Z")], path=log)
    append([snap("lock-sun", -3.0, "2026-09-13T16:00:00Z")], path=log)
    append([snap("lock-mon", -4.0, "2026-09-14T22:00:00Z")], path=log)

    games = collect(log)
    assert len(games) == 1
    # the LAST lock before kickoff wins -- Monday's, not Sunday's
    assert games[0]["lock"] == -4.0
    assert games[0]["post"] == -2.5


def test_transferability_ignores_a_lock_captured_after_kickoff():
    """A Monday capture still returns Sunday's games.

    Without a kickoff guard, that late reading overwrites the good Sunday one
    and the measured gap silently includes movement Adam could never have acted
    on -- which biases w, the one parameter the whole capture habit exists to
    measure.
    """
    import tempfile
    from pathlib import Path
    from edge.pickem_log import Snapshot, append
    from scripts.pickem_transferability import collect

    log = Path(tempfile.mkdtemp()) / "log.csv"

    def snap(label, market, captured):
        return Snapshot(season=2026, week=1, snapshot=label, captured_at=captured,
                        away_team="TB", home_team="CIN",
                        kickoff_utc="2026-09-13T17:00:00+00:00",   # Sun 1pm ET
                        cbs_line_home=-3.5, market_line_home=market)

    append([snap("lock-sun", -3.0, "2026-09-13T16:00:00Z")], path=log)   # in time
    append([snap("lock-mon", -9.9, "2026-09-14T22:00:00Z")], path=log)   # too late

    games = collect(log)
    assert games[0]["lock"] == -3.0, "a post-kickoff reading must not win"


# --- the two-halves workflow: bank the market now, add CBS later -----------
# All found 2026-09-09, with the log still empty and the timers never installed.

def test_complete_fills_a_market_only_row_with_cbs_numbers():
    """REGRESSION for the bug that made the timers pointless.

    PICKEM_WEEKLY.md promises "capture the market now, fill CBS in afterwards
    with the same --snapshot label". `append` de-dupes on
    (season, week, snapshot, home_team) and DROPS the colliding row, so that
    second run wrote nothing at all -- and since
    scripts/pickem_transferability.py skips any game with no CBS line, every
    row the timers banked was discarded on read. The project's only
    accumulating dataset was silently half-empty.
    """
    import tempfile
    from pathlib import Path
    from edge.pickem_log import Snapshot, append, complete, load

    log = Path(tempfile.mkdtemp()) / "log.csv"

    market_half = Snapshot(season=2026, week=1, snapshot="post",
                           captured_at="2026-09-08T17:11:20Z",
                           away_team="BUF", home_team="HOU",
                           kickoff_utc="2026-09-13T17:00:00Z",
                           market_line_home=1.375, market_total=44.625,
                           n_books=3)
    assert append([market_half], path=log) == 1
    assert load(log)[0]["cbs_line_home"] == ""

    cbs_half = Snapshot(season=2026, week=1, snapshot="post",
                        captured_at="2026-09-08T17:11:20Z",
                        away_team="BUF", home_team="HOU",
                        kickoff_utc="2026-09-13T17:00:00Z",
                        cbs_line_home=-1.5, comm_pct_away=78, comm_pct_home=22)

    # append alone is a no-op -- that is the whole bug
    assert append([cbs_half], path=log) == 0
    assert load(log)[0]["cbs_line_home"] == ""

    assert complete([cbs_half], path=log).changed == 1
    rows = load(log)
    assert len(rows) == 1, "completing must not add a second row"
    assert float(rows[0]["cbs_line_home"]) == -1.5
    assert float(rows[0]["comm_pct_away"]) == 78
    # ...and the market half it was joined onto is untouched
    assert float(rows[0]["market_line_home"]) == 1.375
    assert rows[0]["captured_at"] == "2026-09-08T17:11:20Z"


def test_complete_never_overwrites_a_recorded_measurement():
    """The log is append-only because a snapshot is a claim about one instant.

    `complete` is the single narrow exception and has to stay inside that rule:
    it fills columns never measured, and refuses to touch ones that were. Two
    runs of the same label can complete each other but can never disagree.
    """
    import tempfile
    from pathlib import Path
    from edge.pickem_log import Snapshot, append, complete, load

    log = Path(tempfile.mkdtemp()) / "log.csv"
    original = Snapshot(season=2026, week=1, snapshot="post",
                        captured_at="2026-09-08T17:11:20Z",
                        away_team="BUF", home_team="HOU",
                        cbs_line_home=-1.5, market_line_home=1.375, n_books=3)
    append([original], path=log)

    contradicting = Snapshot(season=2026, week=1, snapshot="post",
                             captured_at="2026-09-09T23:00:00Z",
                             away_team="BUF", home_team="HOU",
                             cbs_line_home=-7.5, market_line_home=-9.9,
                             market_total=44.0, n_books=1)
    # Nothing here is fillable: the CBS line was already recorded, and
    # market_total -- blank though it is -- belongs to the market half, which
    # cannot be dated with someone else's instant (see CBS_FILLABLE).
    assert complete([contradicting], path=log) == (0, 0)

    row = load(log)[0]
    assert float(row["cbs_line_home"]) == -1.5, "a recorded CBS line is final"
    assert float(row["market_line_home"]) == 1.375, "so is a recorded market line"
    assert row["captured_at"] == "2026-09-08T17:11:20Z", "and so is the instant"
    assert row["market_total"] == "", (
        "a blank MARKET column stays blank: filling it from a later run would "
        "put a Wednesday total in a row stamped Tuesday")
    assert int(row["n_books"]) == 3, "0 is blank, 3 is a measurement"


def test_complete_leaves_other_games_and_snapshots_alone():
    """It rewrites the whole CSV, so every untouched row must survive verbatim."""
    import tempfile
    from pathlib import Path
    from edge.pickem_log import Snapshot, append, complete, load

    log = Path(tempfile.mkdtemp()) / "log.csv"
    append([Snapshot(season=2026, week=1, snapshot="post", away_team="BUF",
                     home_team="HOU", market_line_home=1.375),
            Snapshot(season=2026, week=1, snapshot="post", away_team="DEN",
                     home_team="KC", market_line_home=-2.5),
            Snapshot(season=2026, week=1, snapshot="lock-sun", away_team="BUF",
                     home_team="HOU", market_line_home=2.0)], path=log)

    n = complete([Snapshot(season=2026, week=1, snapshot="post", away_team="BUF",
                           home_team="HOU", cbs_line_home=-1.5)], path=log)
    assert n.changed == 1

    rows = {(r["snapshot"], r["home_team"]): r for r in load(log)}
    assert len(rows) == 3
    assert float(rows[("post", "HOU")]["cbs_line_home"]) == -1.5
    assert rows[("post", "KC")]["cbs_line_home"] == "", "a different game"
    assert rows[("lock-sun", "HOU")]["cbs_line_home"] == "", "a different label"


def test_market_only_suppresses_cbs_lines_even_when_the_csv_has_them():
    """--market-only must SUPPRESS CBS's numbers, not merely tolerate absence.

    It was only consulted when data/pickem_current_week.csv came back empty,
    so the six timers -- which all pass --market-only -- would have banked the
    CSV's *provisional* transcription as though it were a verified reading,
    into an append-only file with no provenance column to tell them apart
    afterwards. Every row of the Week 1 CSV was tagged provisional.
    """
    import inspect
    from scripts import pickem_capture

    src = inspect.getsource(pickem_capture.main)
    for field in ("cbs_line_home", "comm_pct_away", "comm_pct_home"):
        assert f'{field}=None if args.market_only else' in src, (
            f"--market-only must force {field} to None")
    # ...and it must drive off the live slate rather than the CSV transcription
    assert "if args.market_only:" in src


def test_a_pinned_scan_serves_that_scan_and_its_real_timestamp(tmp_path):
    """BACKFILL. A capture missed at its deadline is recoverable; a lie is not.

    data/odds.db keeps 400 days of scans, so the Tuesday `post` reading nobody
    took on 2026-09-08 was still sitting in scan 224. But a recovered row MUST
    carry the scan's own finish time: stamp it utcnow() and the log claims a
    Tuesday-1pm reading was taken on Wednesday evening, and
    scripts/pickem_transferability.py's before-kickoff guard then decides
    whether it was actionable from a moment that never happened.
    """
    import pytest
    from edge.arb.models import Board, EventMeta, GroupKey, Quote
    from edge.odds import OddsStore, board_to_rows
    from edge.odds.source import ScrapedOddsClient, StaleOdds
    from datetime import datetime, timedelta, timezone

    kickoff = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)

    def write(store, point, when):
        board = Board()
        ev = EventMeta(event_id="e1", sport_key="americanfootball_nfl",
                       sport_title="NFL", commence_time=kickoff,
                       home_team="Houston Texans", away_team="Buffalo Bills")
        for side, dec in (("home", 1.91), ("away", 1.91)):
            g = board.group(GroupKey("e1", "spreads", None, point), ev)
            g.add(Quote(book="draftkings", side=side, decimal=dec, point=point,
                        last_update=when))
        events, quotes = board_to_rows(board)
        scan_id = store.begin_scan("pickem_nfl")
        store.write_events(events)
        store.write_quotes(scan_id, quotes)
        store.finish_scan(scan_id, {})
        store.conn.execute("UPDATE scan SET finished_at=? WHERE id=?",
                           (when.isoformat(), scan_id))
        store.conn.commit()
        return scan_id

    with OddsStore(tmp_path / "odds.db") as store:
        tuesday = datetime(2026, 9, 8, 17, 11, 20, tzinfo=timezone.utc)
        old_scan = write(store, 1.5, tuesday)
        write(store, -6.5, tuesday + timedelta(days=1))     # the newest scan

        def home_point(client):
            ev = client.get_featured_odds("americanfootball_nfl", ["spreads"])[0]
            outcomes = ev["bookmakers"][0]["markets"][0]["outcomes"]
            return next(o["point"] for o in outcomes
                        if o["name"] == "Houston Texans")

        # unpinned: the newest scan, i.e. the wrong moment for a backfill
        assert home_point(ScrapedOddsClient(store, profile="pickem_nfl")) == -6.5

        pinned = ScrapedOddsClient(store, profile="pickem_nfl", scan_id=old_scan)
        assert home_point(pinned) == 1.5
        assert pinned.scan_finished_at() == tuesday.isoformat()

        # a scan that does not exist must fail loudly, not fall back to newest
        with pytest.raises(StaleOdds, match="not a committed scan"):
            ScrapedOddsClient(store, profile="pickem_nfl",
                              scan_id=9999).get_featured_odds(
                                  "americanfootball_nfl", ["spreads"])
        # ...and neither may a scan from a different profile's coverage
        with pytest.raises(StaleOdds, match="belongs to profile"):
            ScrapedOddsClient(store, profile="dfs_nfl",
                              scan_id=old_scan).get_featured_odds(
                                  "americanfootball_nfl", ["spreads"])


def test_the_week_calendar_has_exactly_one_implementation():
    """After the 2026-08-21 fallback-drift incident: one implementation.

    scripts/pickem_capture.py was fixed on 2026-09-08 to trim the board to one
    pool week and alias the two team names CBS and the market spell
    differently. pages/4_🎯_Pickem.py was not, and eighteen hours later printed
    the wrong side of BUF@HOU off a Week 2 line. Both now import from
    edge/pickem_week.py; this fails if either grows a private copy again.
    """
    from pathlib import Path
    from edge import pickem_week
    from scripts import pickem_capture

    assert pickem_capture.week_window is pickem_week.week_window
    assert pickem_capture._cbs_abbr is pickem_week._cbs_abbr
    assert pickem_capture.MARKET_TO_CBS_ABBR is pickem_week.MARKET_TO_CBS_ABBR

    root = Path(pickem_week.__file__).resolve().parents[1]
    page = (root / "pages" / "4_🎯_Pickem.py").read_text()
    assert "from edge.pickem_week import" in page
    assert "MARKET_TO_CBS_ABBR = {" not in page, "no private alias table"
    assert "SEASON_START = " not in page, "no private season anchor"


# --- FINDING 3: complete() must not fill a MARKET half from another moment --
# Found 2026-09-09 (iteration 2). complete() was added the same day and filled
# any blank column, which reopened the exact hole scripts/pickem_capture.py's
# backfill branch was written to close.

def test_complete_refuses_to_backfill_a_market_reading_from_another_moment():
    """A row's captured_at must remain true of EVERY number in that row.

    The demonstrated failure: a market-only capture on Tuesday finds no board
    (n_books=0, market_line_home blank) and banks a row stamped Tuesday 17:11.
    On Friday the same label is re-run, the board is up, and `complete` --
    which filled any blank column -- writes Friday's -7.0 into the Tuesday row
    while leaving captured_at at Tuesday 17:11. The row then claims a Friday
    price was observed on Tuesday afternoon.

    That is not a cosmetic error. `cbs_offset` (PICKEM_MODEL.md 5f) is only
    meaningful because market_line_home and cbs_line_home share one instant,
    and scripts/pickem_transferability.py's `_before_kickoff` decides whether
    a reading was actionable from captured_at. Both silently consume the lie.

    The CBS half is different in kind and stays fillable: CBS's number is
    FROZEN for the week, so transcribing it later does not make it a different
    measurement. The market moves by the minute.
    """
    import tempfile
    from pathlib import Path
    from edge.pickem_log import Snapshot, append, complete, load

    log = Path(tempfile.mkdtemp()) / "log.csv"

    # Tuesday: the capture ran but the board was empty.
    tuesday = Snapshot(season=2026, week=1, snapshot="lock-sun",
                       captured_at="2026-09-08T17:11:20Z",
                       away_team="BUF", home_team="HOU",
                       kickoff_utc="2026-09-13T17:00:00Z",
                       market_line_home=None, n_books=0)
    assert append([tuesday], path=log) == 1
    assert load(log)[0]["market_line_home"] == ""

    # Friday: same label, board up. This must NOT be merged in.
    friday = Snapshot(season=2026, week=1, snapshot="lock-sun",
                      captured_at="2026-09-11T17:00:00Z",
                      away_team="BUF", home_team="HOU",
                      kickoff_utc="2026-09-13T17:00:00Z",
                      market_line_home=-7.0, market_line_mean=-7.0,
                      market_line_median=-7.0, market_total=44.0, n_books=3,
                      book_disagreement=0.5, book_lines={"draftkings": -7.0})

    res = complete([friday], path=log)
    assert res.changed == 0, "a market half may never arrive from another moment"
    assert res.skipped == 1, "and the caller must be told the row is still empty"

    row = load(log)[0]
    assert row["market_line_home"] == "", "still no market reading"
    assert row["n_books"] in ("0", ""), "and still no books"
    assert row["captured_at"] == "2026-09-08T17:11:20Z"


def test_complete_still_fills_the_cbs_half_and_the_kickoff():
    """The two-halves workflow PICKEM_WEEKLY.md promises must survive the fix.

    CBS's line is frozen for the week, so transcribing it on Thursday records
    the same number that existed on Tuesday. Those four columns stay fillable;
    everything the market touches does not.
    """
    import tempfile
    from pathlib import Path
    from edge.pickem_log import Snapshot, append, complete, load

    log = Path(tempfile.mkdtemp()) / "log.csv"
    append([Snapshot(season=2026, week=1, snapshot="post",
                     captured_at="2026-09-08T17:11:20Z",
                     away_team="BUF", home_team="HOU",
                     market_line_home=1.375, market_total=44.625, n_books=3)],
           path=log)

    res = complete([Snapshot(season=2026, week=1, snapshot="post",
                             captured_at="2026-09-10T12:00:00Z",
                             away_team="BUF", home_team="HOU",
                             kickoff_utc="2026-09-13T17:00:00Z",
                             cbs_line_home=-1.5, comm_pct_away=78,
                             comm_pct_home=22)], path=log)
    assert (res.changed, res.skipped) == (1, 0)

    row = load(log)[0]
    assert float(row["cbs_line_home"]) == -1.5
    assert float(row["comm_pct_away"]) == 78
    assert row["kickoff_utc"] == "2026-09-13T17:00:00Z"
    assert row["captured_at"] == "2026-09-08T17:11:20Z", "the instant is final"
    assert float(row["market_line_home"]) == 1.375, "market half untouched"


# --- FINDING 1: the board is older than the row claims -----------------------

def test_captured_at_is_the_boards_own_moment_not_the_wall_clock():
    """Every lock/midweek capture was recording prices 6-13 hours old as `now`.

    The collection timer runs 12:20 and 23:20; `pickem-capture-lock-sun` fired
    at 12:00, twenty minutes BEFORE it, so it read Saturday 23:20 prices and
    stamped them Sunday noon. The profile's max_age_seconds is a full day, so
    the freshness contract never objected. `scan_finished_at()` already existed
    and was consulted only on the --scan-id backfill path.

    Nothing downstream can detect this, and it is fatal to the one thing the
    `midweek` snapshot exists for (the TIME PROFILE of drift, 5j round 6d):
    every timestamp offset backwards by an unrecorded amount.
    """
    import datetime
    from scripts.pickem_capture import _board_instant

    eight_hours_ago = (datetime.datetime.now(datetime.timezone.utc)
                       - datetime.timedelta(hours=8))

    class Scraped:
        scan_id = None

        def scan_finished_at(self):
            return eight_hours_ago.isoformat()

    captured, age = _board_instant(Scraped())
    assert captured == eight_hours_ago.strftime("%Y-%m-%dT%H:%M:%SZ"), (
        "the row must carry the moment the PRICES were observed")
    assert 8 * 3600 - 60 <= age <= 8 * 3600 + 60, (
        "and board_age_seconds must record how stale they already were")


def test_a_client_with_no_scan_falls_back_to_now_with_no_age():
    """The paid Odds API has no scan, so there is nothing to be honest about."""
    from scripts.pickem_capture import _board_instant

    class Paid:
        pass

    captured, age = _board_instant(Paid())
    assert captured.endswith("Z") and len(captured) == 20
    assert age is None, "a blank column, not a fabricated zero"


def test_board_age_seconds_is_the_last_column_and_old_rows_read_back_blank():
    """Appending a column at the END keeps every already-written row readable.

    data/pickem_line_log.csv is committed and append-only; git is its database.
    A column inserted in the middle would silently re-key every historical row.
    """
    import tempfile
    from pathlib import Path
    from edge.pickem_log import FIELDS, Snapshot, append, load

    assert FIELDS[-1] == "board_age_seconds"

    log = Path(tempfile.mkdtemp()) / "log.csv"
    log.write_text(
        ",".join(FIELDS[:-1]) + "\n"
        "2026,1,post,2026-09-08T17:11:20Z,BUF,HOU,,,,,1.375,,,,3,,\n")
    assert load(log)[0].get("board_age_seconds") in (None, "")

    append([Snapshot(season=2026, week=1, snapshot="lock-sun",
                     away_team="BUF", home_team="HOU",
                     market_line_home=-7.0, n_books=3,
                     board_age_seconds=45640)], path=log)
    rows = load(log)
    assert rows[0]["board_age_seconds"] in (None, "")
    assert rows[1]["board_age_seconds"] == "45640"


# --- FINDING 2: --at was inert in a dry run ---------------------------------

def test_at_resolves_to_a_scan_even_in_a_dry_run(monkeypatch, capsys):
    """DRY RUN BY DEFAULT is this project's convention, so a flag that only
    works with --confirm cannot be rehearsed. `--at` describes a backfill the
    way it is actually remembered ("the reading that should have been taken
    just after 1pm Tuesday"), and the whole value of printing which scan that
    resolves to is being able to check it BEFORE writing to an append-only log.
    Resolution lived inside _client(), which runs after the dry-run return.
    """
    import sys as _sys
    from scripts import pickem_capture

    class _Row(dict):
        pass

    class _Conn:
        def execute(self, _sql, _params):
            class _Cur:
                @staticmethod
                def fetchone():
                    return _Row(id=7, finished_at="2026-09-08T17:11:20+00:00")
            return _Cur()

    class _Store:
        conn = _Conn()

    monkeypatch.setattr("edge.odds.store.OddsStore", lambda *a, **k: _Store())
    monkeypatch.setattr(_sys, "argv", [
        "pickem_capture.py", "--snapshot", "post", "--week", "1",
        "--market-only", "--at", "2026-09-08T17:15:00Z"])
    pickem_capture.main()

    out = capsys.readouterr().out
    assert "-> scan 7" in out, "the resolution must be visible without --confirm"
    assert "DRY RUN" in out, "and it must still write nothing"


def test_every_log_column_is_classified_as_fillable_or_not():
    """A new column must be a deliberate choice, not a default.

    `complete` used to fill "whatever is blank", which is how a market reading
    from Friday ended up eligible for a row stamped Tuesday. The fillable set
    is explicit now, so this fails the moment a column is added to FIELDS
    without someone deciding which half it belongs to.
    """
    from edge.pickem_log import (
        CBS_FILLABLE, FIELDS, KEY_FIELDS, MARKET_FIELDS,
    )

    # away_team is identity that KEY_FIELDS does not need (home_team already
    # pins the game); captured_at is the row's instant and is never fillable.
    accounted = set(KEY_FIELDS) | set(CBS_FILLABLE) | set(MARKET_FIELDS) | {
        "away_team", "captured_at"}
    assert accounted == set(FIELDS), (
        f"unclassified log columns: {set(FIELDS) ^ accounted}")
    assert not set(CBS_FILLABLE) & set(MARKET_FIELDS)


# ===========================================================================
# A CAPTURE THAT BANKS NOTHING MUST FAIL (iteration 3, finding 3)
#
# Iteration 2 built a whole alerting chain -- OnFailure=, a failures log, a
# banner on the Pick'em page, Restart=on-failure -- and every link of it is
# armed by a NON-ZERO EXIT CODE. The most likely miss exited 0:
#
#     live slate for week 18 (Tue Jan 05 -> Tue Jan 12 ET): 0 of 29 board events
#     Nothing on the live slate falls in week 18. Nothing written.
#     >>> exit code 0
#
# Same shape when `--week auto` resolves to None. SEASON_START = 2026-09-08
# makes current_week() return None from 2027-01-12 onward, so through the
# whole 2027 season all six timers would print "Outside the season", exit 0,
# and say nothing for a year:
#
#     2027-01-10 -> 18      2027-01-12 -> None      2027-09-14 -> None
#
# A market reading missed at its deadline is gone forever -- data/odds.db can
# recover a scan, but only if a scan happened. Silence is the one response
# that cannot be right.
# ===========================================================================

def _cap_harness(monkeypatch, argv, games=(), written=0, completed=0,
                 skipped=0, cbs_rows=(), logged=0):
    """Run scripts.pickem_capture.main() with the world stubbed out."""
    import sys as _sys

    from scripts import pickem_capture

    monkeypatch.setattr(pickem_capture, "_client", lambda _a: object())
    monkeypatch.setattr(pickem_capture, "fetch_week",
                        lambda *_a, **_k: list(games))
    monkeypatch.setattr(pickem_capture, "load_cbs", lambda _w: list(cbs_rows))
    monkeypatch.setattr(pickem_capture, "append", lambda _s: written)
    monkeypatch.setattr(pickem_capture, "complete",
                        lambda _s: (completed, skipped))
    monkeypatch.setattr(pickem_capture, "_already_logged",
                        lambda *_a, **_k: logged)
    monkeypatch.setattr(_sys, "argv", ["pickem_capture.py"] + argv)
    return pickem_capture


def _live(home="HOU", away="BUF", kickoff="2026-09-13T17:00:00Z", line=-1.5):
    from edge.pickem_live import LiveGame
    return LiveGame(away=away, home=home, away_abbr=away, home_abbr=home,
                    kickoff=kickoff, live_line=line, n_books=3)


def test_an_empty_slate_at_a_deadline_exits_non_zero(monkeypatch, capsys):
    """`0 of N board events` at a deadline is the permanent miss.

    The board had games; none of them fell in this pool week. That is either a
    wrong week or a board that never got collected, and both mean this
    deadline's reading does not exist. Exit 0 told systemd everything was fine.
    """
    import pytest as _pytest

    cap = _cap_harness(monkeypatch,
                       ["--snapshot", "lock-sun", "--week", "1",
                        "--market-only", "--confirm"],
                       games=[_live(kickoff="2026-11-01T17:00:00Z")])
    with _pytest.raises(SystemExit) as e:
        cap.main()
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "Nothing" in out and "week 1" in out


def test_an_exploratory_label_may_still_come_back_empty(monkeypatch, capsys):
    """Only the six timer labels are deadlines.

    A free-form mid-week probe is something a human typed at a keyboard and is
    watching; making it fatal would train the reflex that a red capture is
    normal, which is exactly what stops the real one being noticed.
    """
    cap = _cap_harness(monkeypatch,
                       ["--snapshot", "probe-idea-7", "--week", "1",
                        "--market-only", "--confirm"],
                       games=[_live(kickoff="2026-11-01T17:00:00Z")])
    cap.main()                                     # must not raise
    assert "Nothing" in capsys.readouterr().out


def test_week_auto_outside_the_season_fails_during_the_season(monkeypatch, capsys):
    """SEASON_START's own consequence, made loud instead of silent.

    current_week() returns None from 2027-01-12 onward. In September 2027 all
    six timers would print "Outside the season" and exit 0. The anchor itself
    is verified correct for THIS season and is not being touched -- but a timer
    that says "no season" in the middle of one has to fail.
    """
    import pytest as _pytest

    cap = _cap_harness(monkeypatch,
                       ["--snapshot", "lock-sun", "--week", "auto", "--confirm"])
    monkeypatch.setattr(cap, "current_week", lambda *_a, **_k: None)
    monkeypatch.setattr(cap, "_in_season_months", lambda *_a, **_k: True)
    with _pytest.raises(SystemExit) as e:
        cap.main()
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "SEASON_START" in out, "the report has to name the thing to look at"


def test_week_auto_in_the_real_offseason_is_a_quiet_no_op(monkeypatch, capsys):
    """June has no NFL. That one really is nothing to capture."""
    cap = _cap_harness(monkeypatch,
                       ["--snapshot", "lock-sun", "--week", "auto", "--confirm"])
    monkeypatch.setattr(cap, "current_week", lambda *_a, **_k: None)
    monkeypatch.setattr(cap, "_in_season_months", lambda *_a, **_k: False)
    cap.main()                                     # must not raise
    assert "Outside the season" in capsys.readouterr().out


def test_writing_nothing_at_a_deadline_exits_non_zero(monkeypatch, capsys):
    """wrote 0, completed 0, skipped 0, and no such row on file.

    Every counter zero means the log is unchanged, so if the label is not
    already there this run banked nothing at all.
    """
    import pytest as _pytest

    cap = _cap_harness(monkeypatch,
                       ["--snapshot", "post", "--week", "1", "--confirm"],
                       games=[_live()],
                       cbs_rows=[{"away_abbr": "BUF", "home_abbr": "HOU",
                                  "cbs_line_home": "-1.5",
                                  "kickoff_utc": "2026-09-13T17:00:00Z"}],
                       written=0, completed=0, skipped=0, logged=0)
    with _pytest.raises(SystemExit) as e:
        cap.main()
    assert e.value.code == 1


def test_a_label_already_recorded_in_full_still_exits_zero(monkeypatch, capsys):
    """The genuine no-op: the second run of a two-half capture.

    PICKEM_WEEKLY.md's documented workflow re-runs the SAME label after the
    CBS numbers arrive, and 'wrote 0 new, completed 16' is success. Re-running
    once more is 'nothing to do' -- and must stay green, or the timers cry
    wolf every week.
    """
    cap = _cap_harness(monkeypatch,
                       ["--snapshot", "post", "--week", "1", "--confirm"],
                       games=[_live()],
                       cbs_rows=[{"away_abbr": "BUF", "home_abbr": "HOU",
                                  "cbs_line_home": "-1.5",
                                  "kickoff_utc": "2026-09-13T17:00:00Z"}],
                       written=0, completed=0, skipped=0, logged=16)
    cap.main()                                     # must not raise
    assert "already recorded in full" in capsys.readouterr().out


def test_a_normal_capture_still_exits_zero(monkeypatch, capsys):
    cap = _cap_harness(monkeypatch,
                       ["--snapshot", "lock-sun", "--week", "1",
                        "--market-only", "--confirm"],
                       games=[_live()], written=1)
    cap.main()
    assert "wrote 1 new" in capsys.readouterr().out


def test_every_timer_label_is_recognised_as_a_deadline():
    """The failure path keys off the label, so the six the timers pass must
    all be in it. A typo here is a timer that fails silently again."""
    from scripts.pickem_capture import _is_deadline

    for label in ("post", "lock-wed", "lock-thu", "lock-sun", "lock-mon",
                  "midweek"):
        assert _is_deadline(label), label
    assert not _is_deadline("probe-idea-7")
    assert not _is_deadline("scratch")


# ===========================================================================
# ONE CLIENT, ONE SCAN (iteration 3, finding 5a)
#
# fetch_week() and _board_instant() resolve the scan INDEPENDENTLY:
# get_featured_odds -> _scan_id(), then scan_finished_at() -> _scan_id() again,
# and each call re-runs store.latest_scan(). Instrumented on the real client:
#
#     _scan_id resolved 1 times   ->   after scan_finished_at: 2
#
# scripts/arb_agent.py publishes a scan on phone demand -- i.e. exactly when
# Adam is looking at his phone on a Sunday at noon, which is when the lock
# timers fire. A scan committing between those two calls writes scan N's
# PRICES stamped with scan N+1's FINISH TIME: a row that claims prices it
# never observed, at a moment it never observed them, with nothing downstream
# able to detect it.
# ===========================================================================

def _mini_store(tmp_path):
    """A store with two committed pickem_nfl scans, newest last."""
    from datetime import datetime, timedelta, timezone

    from edge.arb.models import Board, EventMeta, GroupKey, Quote
    from edge.odds import OddsStore, board_to_rows

    kickoff = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)
    store = OddsStore(tmp_path / "odds.db")

    def write(point, when):
        board = Board()
        ev = EventMeta(event_id="e1", sport_key="americanfootball_nfl",
                       sport_title="NFL", commence_time=kickoff,
                       home_team="Houston Texans", away_team="Buffalo Bills")
        for side in ("home", "away"):
            g = board.group(GroupKey("e1", "spreads", None, point), ev)
            g.add(Quote(book="draftkings", side=side, decimal=1.91, point=point,
                        last_update=when))
        events, quotes = board_to_rows(board)
        sid = store.begin_scan("pickem_nfl")
        store.write_events(events)
        store.write_quotes(sid, quotes)
        store.finish_scan(sid, {})
        store.conn.execute("UPDATE scan SET finished_at=? WHERE id=?",
                           (when.isoformat(), sid))
        store.conn.commit()
        return sid

    t0 = datetime(2026, 9, 13, 16, 20, tzinfo=timezone.utc)
    return store, write, t0, timedelta


def test_one_client_serves_one_scan_for_its_whole_lifetime(tmp_path):
    """REGRESSION. The prices and the timestamp must come from ONE scan.

    A scan committed between get_featured_odds() and scan_finished_at() used
    to move the timestamp while leaving the prices behind.
    """
    from edge.odds.source import ScrapedOddsClient

    store, write, t0, td = _mini_store(tmp_path)
    first = write(1.5, t0)
    client = ScrapedOddsClient(store, profile="pickem_nfl",
                               max_age_seconds=10 ** 9)

    ev = client.get_featured_odds("americanfootball_nfl", ["spreads"])[0]
    outcomes = ev["bookmakers"][0]["markets"][0]["outcomes"]
    point = next(o["point"] for o in outcomes if o["name"] == "Houston Texans")
    assert point == 1.5

    # arb_agent publishes a newer scan, mid-capture
    write(-6.5, t0 + td(minutes=20))

    assert client.scan_finished_at() == t0.isoformat(), \
        "the timestamp moved to a scan whose prices were never read"
    again = client.get_featured_odds("americanfootball_nfl", ["spreads"])[0]
    outs = again["bookmakers"][0]["markets"][0]["outcomes"]
    assert next(o["point"] for o in outs
                if o["name"] == "Houston Texans") == 1.5, \
        "and the prices must not move either"
    assert client.resolved_scan_id == first
    store.close()


def test_a_fresh_client_still_sees_the_newer_scan(tmp_path):
    """Memoising is per INSTANCE. The next run must not be pinned to the past.

    scraped_client() builds a new client per call and the page builds one per
    render, so this is the ordinary path -- if it broke, the board would
    freeze at whatever scan was current the first time the process started.
    """
    from edge.odds.source import ScrapedOddsClient

    store, write, t0, td = _mini_store(tmp_path)
    write(1.5, t0)
    c1 = ScrapedOddsClient(store, profile="pickem_nfl", max_age_seconds=10 ** 9)
    c1.get_featured_odds("americanfootball_nfl", ["spreads"])

    write(-6.5, t0 + td(minutes=20))
    c2 = ScrapedOddsClient(store, profile="pickem_nfl", max_age_seconds=10 ** 9)
    ev = c2.get_featured_odds("americanfootball_nfl", ["spreads"])[0]
    outs = ev["bookmakers"][0]["markets"][0]["outcomes"]
    assert next(o["point"] for o in outs if o["name"] == "Houston Texans") == -6.5
    store.close()


def test_the_store_is_consulted_once_per_client(tmp_path):
    """The measurement from the report, pinned: one resolution, not two."""
    from edge.odds.source import ScrapedOddsClient

    store, write, t0, _td = _mini_store(tmp_path)
    write(1.5, t0)
    client = ScrapedOddsClient(store, profile="pickem_nfl",
                               max_age_seconds=10 ** 9)

    calls = []
    real = store.latest_scan
    store.latest_scan = lambda *a, **k: (calls.append(1), real(*a, **k))[1]

    client.get_featured_odds("americanfootball_nfl", ["spreads"])
    client.scan_finished_at()
    client.get_featured_odds("americanfootball_nfl", ["spreads"])
    assert len(calls) == 1, f"resolved the scan {len(calls)} times"
    store.close()


# ===========================================================================
# board_age_seconds IS MEANINGLESS ON A BACKFILL (iteration 3, finding 5b)
#
# The page's failure banner tells Adam to recover a missed deadline with
# `--at ... --confirm`. That wrote board_age_seconds = 127928 -- 35 hours --
# for a perfectly-timed Tuesday-1:11pm backfill, because the column measures
# wall-clock-now minus board time and the recovery ran on Wednesday night.
#
# The column is documented as "how stale the board already was when the
# capture ran", and PICKEM_MODEL.md section 6 reads it to answer "how late can
# we legally capture?". A recovered deadline reading scoring 35h of staleness
# is not a stale reading -- it is a measurement of when the RECOVERY ran, and
# it would make every backfilled deadline look hopeless.
# ===========================================================================

def test_a_backfill_records_no_wall_clock_staleness():
    """A pinned scan IS the board at that instant, by construction."""
    from scripts.pickem_capture import _board_instant

    class _Pinned:
        scan_id = 224

        @staticmethod
        def scan_finished_at():
            return "2026-09-08T17:11:20+00:00"

    captured, age = _board_instant(_Pinned())
    assert captured == "2026-09-08T17:11:20Z"
    assert age == 0, "35 hours of 'staleness' is when the RECOVERY ran"


def test_an_ordinary_run_still_measures_real_staleness():
    """The live path is unchanged: a 12h40m-old board still says so."""
    import datetime as _dt

    from scripts.pickem_capture import _board_instant

    ago = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=3))

    class _Live:
        scan_id = None

        @staticmethod
        def scan_finished_at():
            return ago.isoformat()

    _captured, age = _board_instant(_Live())
    assert 3 * 3600 - 60 <= age <= 3 * 3600 + 60


def test_a_deadline_that_finds_no_market_at_all_refuses_to_bank(monkeypatch, capsys):
    """The other shape of the same miss, and the worst one.

    With CBS rows on file but an empty board, every snapshot is built with
    `market_line_home=None`. append() writes all sixteen, so `written` is 16,
    the run exits 0 -- and those rows now OWN the (season, week, snapshot) key.
    complete() fills the CBS half only (CBS_FILLABLE); a market reading is a
    claim about one instant and can never be merged into a row stamped with
    another. So the label is permanently occupied by rows that will never carry
    the number the snapshot exists to record.

    The CBS half is re-derivable from data/pickem_current_week.csv at any time.
    The market half is not. So this refuses to write, leaving the label free for
    a re-run once the board is collected.
    """
    import pytest as _pytest

    cap = _cap_harness(monkeypatch,
                       ["--snapshot", "lock-sun", "--week", "1", "--confirm"],
                       games=[],
                       cbs_rows=[{"away_abbr": "BUF", "home_abbr": "HOU",
                                  "cbs_line_home": "-1.5",
                                  "kickoff_utc": "2026-09-13T17:00:00Z"}],
                       written=99)
    with _pytest.raises(SystemExit) as e:
        cap.main()
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "wrote 99" not in out, "it must refuse BEFORE writing"


def test_a_partial_board_at_a_deadline_still_banks(monkeypatch, capsys):
    """One game missing from the board is not an outage.

    Fifteen readings banked beats zero, and the sixteenth is reported by the
    existing `no live market found for:` line.
    """
    cap = _cap_harness(monkeypatch,
                       ["--snapshot", "lock-sun", "--week", "1", "--confirm"],
                       games=[_live()],
                       cbs_rows=[{"away_abbr": "BUF", "home_abbr": "HOU",
                                  "cbs_line_home": "-1.5",
                                  "kickoff_utc": "2026-09-13T17:00:00Z"},
                                 {"away_abbr": "SF", "home_abbr": "LAR",
                                  "cbs_line_home": "-3.5",
                                  "kickoff_utc": "2026-09-11T00:35:00Z"}],
                       written=2)
    cap.main()
    out = capsys.readouterr().out
    assert "wrote 2 new" in out
    assert "no live market found for: SF@LAR" in out
