"""The shared simulation-percentile search (edge/dfs_opt_climb.py), driven
through the NBA roster: slot matching, late tips in the flex slots, late-swap
locks and the GPP portfolio. Network-free; synthetic pool."""
from __future__ import annotations

import pytest

from edge import dfs_nba_theory as T, dfs_opt_climb as C, dfs_opt_nba as O

POS = ["PG", "SG", "SF", "PF", "C", "PG/SG", "SF/PF", "PF/C", "SG", "C"]
TEAMS = [("NYK", "PHI", "2099-01-01T00:00:00Z"), ("BOS", "DET", "2099-01-01T01:00:00Z"),
         ("LAL", "GSW", "2099-01-01T03:30:00Z")]


@pytest.fixture
def board():
    pool = []
    for g, (home, away, start) in enumerate(TEAMS):
        for t in (home, away):
            for i, pos in enumerate(POS):
                pool.append({"name": f"{t}{i}", "team": t, "game": f"g{g}", "start": start,
                             "dk_pos": pos, "salary": 3000 + 700 * ((i * 7 + g) % 10),
                             "proj": 12 + 3 * ((i * 5 + g) % 9), "own": 10.0})
    sim = T.simulate(pool, n_sims=800, seed=1)
    T.summarise(sim, pool)
    return pool, sim


def test_matching_is_exact():
    m = lambda pos: C.mask(O.NBA, O.slots_for(O.eligible(pos)))   # noqa: E731
    ok = [m(p) for p in ("PG", "SG", "SF", "PF", "C", "PG", "SF", "C")]
    bad = [m(p) for p in ("PG", "PG", "PG", "PF", "C", "SF", "SF", "C")]    # three PGs, no SG
    assert C._matchable(C._key(ok), 8)
    assert not C._matchable(C._key(bad), 8)


def test_late_tips_go_to_the_flex_slots(board):
    pool, sim = board
    res = O.optimize(pool, sim, mode="gpp", iters=15, seed=0)
    slots = res["slots"]
    # no strict-slot player tips later than a flex player he could have swapped with
    for s, p in slots:
        if s in ("G", "F", "UTIL"):
            continue
        for s2, q in slots:
            if s2 in ("G", "F", "UTIL") and s in q["slots"] and s2 in p["slots"]:
                assert str(p["start"]) <= str(q["start"])


def test_locked_players_stay(board):
    pool, sim = board
    for mode in ("cash", "gpp"):
        res = O.optimize(pool, sim, mode=mode, iters=15, seed=0, locked={"GSW6"})
        assert "GSW6" in {r["name"] for r in res["lineup"]}


def test_every_lineup_is_legal_and_under_the_cap(board):
    pool, sim = board
    for mode in ("cash", "gpp"):
        res = O.optimize(pool, sim, mode=mode, iters=15, seed=0)
        assert O.valid(pool, res["idx"]) and res["salary"] <= O.SALARY_CAP
        assert [s for s, _ in res["slots"]] == list(O.SLOTS)


def test_portfolio_lineups_differ(board):
    pool, sim = board
    lus = O.portfolio(pool, sim, 3, iters=8)
    assert len(lus) == 3
    sets = [{r["name"] for r in lu["lineup"]} for lu in lus]
    assert all(len(a & b) <= 5 for i, a in enumerate(sets) for b in sets[i + 1:])
