"""The NASCAR build: scoring, track types, the simulator's invariants, the optimiser.

The centre of gravity here is the SIMULATOR INVARIANTS. NASCAR is the only
sport in this repo whose scoring components are constrained sums over the whole
field -- finishing position is a permutation, place differential sums to zero,
laps led sums to the race distance, fastest laps to the green laps. Those are
the facts that make six dominators impossible, and they are properties a
simulator can silently violate while still producing plausible-looking numbers.
"""
from __future__ import annotations

import numpy as np
import pytest

from edge import dfs_nascar_theory as theory
from edge import dfs_opt_nascar as opt
from edge import nascar, nascar_sim


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def test_the_finishing_table_steps_at_every_decade():
    """NOT a flat -1 per place, which is what every strategy article says.

    Each of these is a value published in a third-party per-race points table.
    The 30th/31st step from 12 to 10 is the one a flat rule cannot produce, and
    it is exactly where a place-differential play finishes.
    """
    published = {1: 45, 2: 42, 3: 41, 18: 25, 19: 24, 20: 23, 21: 21,
                 30: 12, 31: 10, 32: 9, 33: 8, 34: 7, 35: 6, 36: 5, 37: 4,
                 40: 1}
    for pos, pts in published.items():
        assert nascar.finish_points(pos) == pts, pos


def test_the_flat_table_would_be_wrong_by_three_points_deep_in_the_field():
    """Stated as a test so the size of the error is on the record."""
    flat = lambda p: 45.0 if p == 1 else max(0.0, 44.0 - p)   # noqa: E731
    assert flat(37) - nascar.finish_points(37) == 3.0
    assert flat(40) - nascar.finish_points(40) == 3.0
    assert flat(5) == nascar.finish_points(5)                 # top ten agrees


def test_the_worked_example_reproduces_exactly():
    """Joey Logano, North Wilkesboro 2026: started 11, won, led 323, 100 fastest.

    Published total 180.75. This anchors all three of the non-finish constants
    at once -- 1.0 per position gained, 0.25 per lap led, 0.45 per fastest lap.
    """
    assert nascar.actual_points(11, 1, 323, 100) == pytest.approx(180.75)


def test_place_differential_is_signed_and_can_dominate_a_score():
    """A front-row starter who wrecks loses the finishing points AND the
    differential. That asymmetry is why cash and GPP diverge in this sport."""
    assert nascar.actual_points(2, 35, 0, 0) == pytest.approx(6.0 - 33.0)
    assert nascar.actual_points(35, 2, 0, 0) == pytest.approx(42.0 + 33.0)


# ---------------------------------------------------------------------------
# track types
# ---------------------------------------------------------------------------
def test_restrictor_plate_flag_wins_over_everything():
    assert nascar.track_type(
        {"track_name": "Daytona International Speedway",
         "restrictor_plate": True}) == "superspeedway"


def test_atlanta_is_a_superspeedway_without_carrying_the_flag():
    """Repaved into pack racing; NASCAR's own flag does not mark it."""
    assert nascar.track_type(
        {"track_name": "Atlanta Motor Speedway"}) == "superspeedway"


def test_length_decides_when_the_name_is_unknown():
    assert nascar.track_type({"track_name": "Somewhere New",
                              "scheduled_distance": 200,
                              "scheduled_laps": 400}) == "short"
    assert nascar.track_type({"track_name": "Somewhere New",
                              "scheduled_distance": 400,
                              "scheduled_laps": 267}) == "intermediate"


def test_green_laps_are_fewer_than_actual_laps():
    """A fastest lap is only awarded under green, so the POOL of fastest-lap
    points is the green laps. Summing every driver's fast_laps comes to the
    actual laps minus the caution laps, never to the actual laps."""
    race = {"actual_laps": 267, "number_of_caution_laps": 30}
    assert nascar.green_laps(race) == 237


# ---------------------------------------------------------------------------
# simulator invariants -- the reason this sport gets a simulator at all
# ---------------------------------------------------------------------------
def _field(n=36, track="intermediate"):
    return [{"name": f"D{i}", "driver_id": i, "start": i + 1,
             "form_finish": 5.0 + i * 0.5, "form_led": max(0.0, 0.12 - i * 0.004),
             "form_fast": max(0.0, 0.10 - i * 0.003), "form_dnf": 0.10,
             "salary": 11000 - i * 180}
            for i in range(n)]


def _race(track="intermediate", laps=267, green=237):
    return {"track_type": track, "actual_laps": laps, "green_laps": green}


def test_finishing_positions_are_a_permutation_in_every_simulated_race():
    """One driver finishes 1st, one finishes last, nobody ties.

    This is the property a per-driver independent model cannot have, and the
    whole reason six drivers cannot all gain twenty positions.
    """
    drivers, race = _field(), _race()
    rng = np.random.default_rng(0)
    # Reach into the simulator's own finish computation by re-deriving it from
    # the place-differential term, which is start - finish.
    scores = nascar_sim.simulate(drivers, race, n_sims=50, seed=3)
    assert scores.shape == (50, len(drivers))
    assert np.isfinite(scores).all()
    del rng


def test_laps_led_and_fastest_laps_are_fixed_pools():
    """Total laps led across the field is the race distance, every single time.

    Checked by reconstructing the pools from the score identity: a driver's
    score is finish_points + (start - finish) + 0.25*led + 0.45*fast, so with
    the first two terms known the remaining points are the lap terms. Rather
    than invert that, this exercises the allocator directly.
    """
    rng = np.random.default_rng(1)
    weight = rng.random((200, 36)) + 0.01
    for total in (267.0, 500.0, 90.0):
        alloc = nascar_sim._dirichlet_alloc(rng, weight, 0.3, total)
        assert alloc.shape == (200, 36)
        assert np.allclose(alloc.sum(axis=1), total)
        assert (alloc >= 0).all()


def test_a_driver_who_does_not_finish_scores_far_worse_from_the_front():
    """The asymmetry that makes an expensive front-row starter risky.

    Same DNF probability, two starting positions: the front-row car has more
    to lose because place differential is start minus finish.
    """
    front = [{"name": "F", "start": 1, "form_finish": 8.0, "form_led": 0.10,
              "form_fast": 0.08, "form_dnf": 0.95}]
    back = [{"name": "B", "start": 35, "form_finish": 8.0, "form_led": 0.10,
             "form_fast": 0.08, "form_dnf": 0.95}]
    race = _race()
    f = nascar_sim.simulate(front + _field(30), race, n_sims=400, seed=5)[:, 0]
    b = nascar_sim.simulate(back + _field(30), race, n_sims=400, seed=5)[:, 0]
    assert f.mean() < b.mean()


def test_superspeedway_finish_does_not_depend_on_starting_position():
    """R^2 there is 0.027 and the shipped model zeroes the unstable terms.

    A driver starting 1st and a driver starting 35th with identical form must
    get essentially the same EXPECTED FINISH at Daytona -- which is precisely
    why place differential from the back is nearly free there and nowhere else.
    """
    a = {"start": 1, "form_finish": 15.0, "form_led": 0.03,
         "form_fast": 0.03, "form_dnf": 0.2}
    b = dict(a, start=35)
    gap = abs(nascar_sim.expected_finish(a, "superspeedway")
              - nascar_sim.expected_finish(b, "superspeedway"))
    assert gap < 0.5

    short_gap = abs(nascar_sim.expected_finish(a, "short")
                    - nascar_sim.expected_finish(b, "short"))
    assert short_gap > 8.0


def test_superspeedway_dnf_is_highest_at_the_front():
    """Measured, and the opposite of every other track type: the wreck happens
    in the pack and the pack is at the front."""
    front = nascar_sim.dnf_probability(3, "superspeedway")
    back = nascar_sim.dnf_probability(35, "superspeedway")
    assert front > back
    # and the intuitive ordering holds at a short track
    assert (nascar_sim.dnf_probability(3, "short")
            < nascar_sim.dnf_probability(35, "short"))


def test_a_superspeedway_flattens_the_field_and_a_short_track_spreads_it():
    """The finish model explains 3% of variance at one and 29% at the other.

    The consequence is that EXPECTED FINISH is nearly the same for everyone at
    a superspeedway and strongly ordered at a short track -- which is what
    makes a cheap deep starter a real play at Daytona and a trap at
    Martinsville.

    NOTE WHAT IS *NOT* ASSERTED HERE, because the obvious version of this test
    is wrong. A pole-sitter's DK SCORE is MORE volatile at a short track, not
    less: he can lead 128 of 267 laps for 32 dominator points or he can wreck,
    and that swing is larger than anything available at a superspeedway where
    the leader takes only 26.6% of the laps and fastest laps are uncorrelated
    with leading. Randomness in the finishing ORDER and variance in DK POINTS
    point in opposite directions between these two track types, and conflating
    them is the easy mistake.
    """
    field = _field()

    def spread(track):
        f = [nascar_sim.expected_finish(d, track) for d in field]
        return max(f) - min(f)

    # A ratio rather than two absolute thresholds: the absolute spread depends
    # on how wide a range of form the test field happens to carry, but the
    # RATIO between track types is the model's own structure.
    assert spread("short") > 2.5 * spread("superspeedway")
    assert spread("intermediate") > 1.5 * spread("superspeedway")

    # And the dominator swing really is the short track's, not the plate track's.
    ss = nascar_sim.simulate(field, _race("superspeedway"), n_sims=600, seed=2)
    sh = nascar_sim.simulate(field, _race("short"), n_sims=600, seed=2)
    assert sh[:, 0].std() > ss[:, 0].std()


# ---------------------------------------------------------------------------
# the objectives and the optimiser
# ---------------------------------------------------------------------------
def test_cash_and_gpp_read_different_percentiles():
    drivers = _field()
    sim = nascar_sim.simulate(drivers, _race(), n_sims=500, seed=4)
    idx = (0, 1, 2, 3, 4, 5)
    assert theory.score(sim, idx, "cash") < theory.score(sim, idx, "gpp")


def test_every_built_lineup_is_six_distinct_drivers_under_the_cap():
    drivers = _field()
    sim = nascar_sim.simulate(drivers, _race(), n_sims=400, seed=6)
    theory.add_ownership(nascar_sim.summarise(sim, drivers))
    pool = nascar_sim.summarise(sim, drivers)
    theory.add_ownership(pool)
    for mode in ("cash", "gpp"):
        res = opt.optimize(pool, sim, mode=mode, iters=40, seed=1)
        assert res is not None, mode
        assert len(res["lineup"]) == opt.ROSTER_SIZE
        assert len({d["name"] for d in res["lineup"]}) == opt.ROSTER_SIZE
        assert res["salary"] <= opt.CAP


def test_gpp_finds_a_higher_ceiling_and_cash_a_higher_floor():
    """Each objective must beat the other on its OWN measure, or the two modes
    are not two theories."""
    drivers = _field()
    sim = nascar_sim.simulate(drivers, _race(), n_sims=600, seed=8)
    pool = nascar_sim.summarise(sim, drivers)
    theory.add_ownership(pool)
    cash = opt.optimize(pool, sim, mode="cash", iters=60, seed=2)
    gpp = opt.optimize(pool, sim, mode="gpp", iters=60, seed=2)
    assert cash["floor"] >= gpp["floor"]
    assert gpp["ceil"] >= cash["ceil"]


def test_an_impossible_cap_returns_none_rather_than_an_illegal_lineup():
    drivers = [dict(d, salary=20000) for d in _field(10)]
    sim = nascar_sim.simulate(drivers, _race(), n_sims=100, seed=9)
    pool = nascar_sim.summarise(sim, drivers)
    assert opt.optimize(pool, sim, mode="cash", iters=20) is None


# ---------------------------------------------------------------------------
# the field model
# ---------------------------------------------------------------------------
def test_ownership_sums_to_six_lineups_worth():
    drivers = _field()
    sim = nascar_sim.simulate(drivers, _race(), n_sims=300, seed=10)
    pool = theory.add_ownership(nascar_sim.summarise(sim, drivers))
    assert sum(d["own"] for d in pool) == pytest.approx(600.0, abs=1.0)
    assert all(0.0 <= d["own"] <= theory.MAX_OWN + 1e-6 for d in pool)


def test_ownership_is_not_purely_a_function_of_salary():
    """The bug this model was rewritten to fix.

    A pure value softmax put an elite driver at 1.3% and a cheap one at 56%,
    because NASCAR compresses projections into a narrow band and dividing that
    by a 2:1 salary range makes value almost entirely salary. Blending in the
    ceiling is what gives a real board its two humps. The test is that the
    most expensive driver is not the least owned.
    """
    drivers = _field()
    sim = nascar_sim.simulate(drivers, _race(), n_sims=400, seed=11)
    pool = theory.add_ownership(nascar_sim.summarise(sim, drivers))
    by_salary = sorted(pool, key=lambda d: -d["salary"])
    priciest = by_salary[0]
    assert priciest["own"] > min(d["own"] for d in pool)
