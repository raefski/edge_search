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
