"""The NFL optimizer: roster legality, and the correlation rules it exists for.

The lineup-shaped tests here are deliberately about CONSTRAINTS rather than
about which lineup comes back. A heuristic search is allowed to return a
different good lineup tomorrow; it is never allowed to return an illegal one,
and it is never allowed to quietly stop stacking.
"""
from __future__ import annotations

import pytest

from edge import dfs_opt_nfl as opt


def mk(name, pos, salary, proj, team, opp, **kw):
    return {"name": name, "pos": opt.eligible_slots(pos), "salary": salary,
            "proj": proj, "team": team, "opp_team": opp,
            "game": "-".join(sorted((team, opp))), **kw}


def pool():
    """Two games, enough depth at every slot to build many legal lineups."""
    ps = []
    for team, opp in (("BUF", "MIA"), ("MIA", "BUF"), ("KC", "DEN"), ("DEN", "KC")):
        ps.append(mk(f"{team} QB", "QB", 6000, 20.0, team, opp))
        for i in range(3):
            ps.append(mk(f"{team} RB{i}", "RB", 5500 - 500 * i, 14.0 - i, team, opp))
        for i in range(4):
            ps.append(mk(f"{team} WR{i}", "WR", 6500 - 700 * i, 16.0 - 1.5 * i, team, opp))
        for i in range(2):
            ps.append(mk(f"{team} TE{i}", "TE", 4000 - 500 * i, 9.0 - i, team, opp))
        ps.append(mk(f"{team} DST", "DST", 2800, 7.0, team, opp))
    return ps


# --- eligibility -------------------------------------------------------------

def test_flex_eligibility_is_derived_not_demanded_of_the_caller():
    """Forgetting FLEX is invisible: the lineup still builds, it just never
    puts a fourth receiver in the FLEX and quietly returns a worse one."""
    assert opt.eligible_slots("WR") == {"WR", "FLEX"}
    assert opt.eligible_slots("RB") == {"RB", "FLEX"}
    assert opt.eligible_slots("TE") == {"TE", "FLEX"}
    assert opt.eligible_slots("QB") == {"QB"}          # QB is NOT flex-eligible
    assert opt.eligible_slots("DST") == {"DST"}


def test_dk_multi_slot_and_fullback_strings_parse():
    assert opt.eligible_slots("RB/FLEX") == {"RB", "FLEX"}
    assert opt.eligible_slots("FB") == {"RB", "FLEX"}   # DK lists fullbacks as RB
    assert opt.eligible_slots("") == set()


# --- roster legality ---------------------------------------------------------

def test_cash_lineup_is_legal():
    r = opt.optimize(pool(), mode="cash", iters=250, seed=1)
    assert r is not None
    assert len(r["lineup"]) == 9
    assert r["salary"] <= opt.CAP
    assert sorted(s for _, s in r["lineup"]) == sorted(opt.SLOTS)
    assert len({p["name"] for p, _ in r["lineup"]}) == 9


def test_a_player_is_never_used_twice():
    for seed in range(4):
        r = opt.optimize(pool(), mode="cash", iters=150, seed=seed)
        names = [p["name"] for p, _ in r["lineup"]]
        assert len(names) == len(set(names))


def test_lineup_spans_at_least_two_games():
    r = opt.optimize(pool(), mode="cash", iters=250, seed=2)
    assert len({p["game"] for p, _ in r["lineup"]}) >= 2


def test_every_player_is_assigned_a_slot_they_are_eligible_for():
    r = opt.optimize(pool(), mode="cash", iters=250, seed=3)
    for p, slot in r["lineup"]:
        assert slot in p["pos"], f"{p['name']} put in {slot}"


def test_the_cap_is_actually_binding():
    """A pool of stars must still come in under $50k -- otherwise the optimizer
    is just picking the top of the board and the cap check is untested."""
    rich = [dict(p, salary=9000) for p in pool()]
    r = opt.optimize(rich, mode="cash", iters=400, seed=4)
    assert r is None or r["salary"] <= opt.CAP


# --- the measured correlation rules ------------------------------------------

def test_dst_never_faces_its_own_lineup():
    """Measured at r=-0.351 against the opposing quarterback -- a stronger
    relationship than the +0.249 stack it would be cancelling. Hard rule."""
    for seed in range(6):
        for mode in ("cash", "gpp"):
            r = opt.optimize(pool(), mode=mode, iters=200, seed=seed)
            if r is None:
                continue
            picked = [p for p, _ in r["lineup"]]
            dst = [p for p in picked if "DST" in p["pos"]]
            for d in dst:
                for o in picked:
                    if "DST" in o["pos"]:
                        continue
                    assert o["team"] != d["opp_team"], \
                        f"{d['name']} is facing {o['name']}"


def test_gpp_forces_the_quarterback_stack():
    r = opt.optimize(pool(), mode="gpp", stack_n=2, bring_back=1,
                     iters=300, seed=5)
    assert r is not None and r["stack"] is not None
    qb = next(p for p, s in r["lineup"] if s == "QB")
    assert r["stack"]["qb"] == qb["name"]
    assert len(r["stack"]["with"]) >= 2
    for name in r["stack"]["with"]:
        mate = next(p for p, _ in r["lineup"] if p["name"] == name)
        assert mate["team"] == qb["team"]
        assert mate["pos"] & opt.CATCHERS


def test_gpp_brings_one_back_from_the_opposing_side():
    r = opt.optimize(pool(), mode="gpp", stack_n=2, bring_back=1,
                     iters=300, seed=6)
    qb = next(p for p, s in r["lineup"] if s == "QB")
    assert len(r["stack"]["bring_back"]) >= 1
    for name in r["stack"]["bring_back"]:
        p = next(x for x, _ in r["lineup"] if x["name"] == name)
        assert p["team"] == qb["opp_team"]


def test_a_running_back_is_never_counted_as_a_stack_partner():
    """Measured at +0.065 with his own quarterback -- essentially nothing.
    Backs earn a roster spot on projection, not on correlation."""
    r = opt.optimize(pool(), mode="gpp", stack_n=2, iters=300, seed=7)
    for name in r["stack"]["with"]:
        p = next(x for x, _ in r["lineup"] if x["name"] == name)
        assert "RB" not in p["pos"] or p["pos"] & opt.CATCHERS


def test_stack_candidates_stay_slot_assignable():
    """The MLB lesson (edge/dfs_opt.py::_secondary_stack): choosing a slice and
    trimming later silently returns a smaller stack than asked for. Asking for
    four catchers must not produce a group that cannot be rostered."""
    ps = pool()
    qb = next(p for p in ps if p["name"] == "BUF QB")
    got = opt.stack_candidates(ps, qb, 4)
    assert opt.assign_slots([qb] + got, opt.SLOTS) is not None


def test_ceiling_prefers_the_correlated_lineup_at_equal_projection():
    """The tie-break that makes a stack worth forcing: two lineups projecting
    the same must not rank the same when one of them is correlated."""
    stacked = opt.optimize(pool(), mode="gpp", stack_n=2, bring_back=1,
                           iters=300, seed=8)
    flat = opt.optimize(pool(), mode="cash", iters=300, seed=8)
    assert stacked["ceil"] - stacked["proj"] > flat["ceil"] - flat["proj"]


def test_an_impossible_stack_returns_nothing_rather_than_an_unstacked_lineup():
    """A silent downgrade is the failure this repo keeps recording. If the
    requested stack cannot be built, say so."""
    ps = [p for p in pool() if not (p["team"] == "BUF" and p["pos"] & opt.CATCHERS)]
    r = opt.optimize(ps, mode="gpp", stack_qb="BUF QB", stack_n=2, iters=100, seed=9)
    assert r is None


def test_empty_pool_returns_none():
    assert opt.optimize([], mode="cash", iters=10, seed=0) is None


# --- portfolio ----------------------------------------------------------------

def test_a_portfolio_does_not_return_the_same_lineup_repeatedly():
    """Varying only the seed converges to the same optimum. Asking for three
    lineups and getting one lineup three times is the silent-uselessness
    failure: it looks like it worked."""
    got = opt.portfolio(pool(), 3, max_overlap=6, mode="gpp", iters=200, seed=0)
    assert len(got) >= 2
    sigs = [frozenset(p["name"] for p, _ in r["lineup"]) for r in got]
    assert len(set(sigs)) == len(sigs)


def test_portfolio_lineups_respect_the_overlap_cap():
    got = opt.portfolio(pool(), 3, max_overlap=5, mode="gpp", iters=200, seed=0)
    sigs = [{p["name"] for p, _ in r["lineup"]} for r in got]
    for i, a in enumerate(sigs):
        for b in sigs[i + 1:]:
            assert len(a & b) <= 5


def test_portfolio_uses_a_different_quarterback_each_time():
    """A portfolio built on one quarterback is one bet with extra entry fees."""
    got = opt.portfolio(pool(), 3, mode="gpp", iters=200, seed=0)
    qbs = [r["stack"]["qb"] for r in got if r["stack"]]
    assert len(set(qbs)) == len(qbs)


def test_portfolio_returns_short_rather_than_padding():
    """Four teams cannot support twenty distinct lineups; say so by returning
    fewer, never by repeating one."""
    got = opt.portfolio(pool(), 20, max_overlap=4, mode="gpp", iters=120, seed=0)
    assert len(got) < 20
    sigs = [frozenset(p["name"] for p, _ in r["lineup"]) for r in got]
    assert len(set(sigs)) == len(sigs)


def test_teammate_pass_catchers_are_charged_against_the_ceiling():
    """Measured at -0.029: two catchers on one team share one football. Without
    this term the ceiling piles a whole receiving corps onto one cheap passing
    game, because each of them collects the full QB bonus and nothing charges
    for the overlap between them."""
    wr1 = mk("BUF WR0", "WR", 6000, 15.0, "BUF", "MIA")
    wr2 = mk("BUF WR1", "WR", 6000, 15.0, "BUF", "MIA")
    other = mk("KC WR0", "WR", 6000, 15.0, "KC", "DEN")
    # isolate the term: same two projections, differing only in being teammates
    teammates = opt._ceiling([wr1, wr2])
    apart = opt._ceiling([wr1, other])
    assert teammates < apart
    assert teammates == pytest.approx(apart + opt.R_CATCHER_TEAMMATE * 15.0, abs=1e-6)

    # and in a full stack it must not cancel the QB pairing it comes with
    qb = mk("BUF QB", "QB", 6000, 20.0, "BUF", "MIA")
    assert opt._ceiling([qb, wr1, wr2]) > opt._ceiling([qb, wr1, other])
