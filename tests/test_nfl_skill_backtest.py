"""scripts/nfl_skill_backtest.py -- the properties that make it a backtest.

A backtest whose leak-freeness is not pinned is not evidence, it is a number.
These tests do not check the RESULT (that is data, and it will move as seasons
are added); they check the three things that would make any result meaningless:

  1. a prediction for week w must not move when week w's outcome changes,
  2. the baselines must read UNDECAYED history, or the candidate silently
     becomes its own baseline,
  3. the means must be scored by the same assembler the props path uses.

(2) is not hypothetical. The first version of the script shared one accumulator
between candidate and baseline, so `skill` and `player_mean` came out
byte-identical for every decay value and the recency term's entire
contribution was invisible.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from scripts import nfl_skill_backtest as bt


def _row(pid, pos, season, week, **stats):
    base = {c: 0.0 for c in bt.CATEGORIES}
    base.update(stats)
    row = {"player_id": pid, "name": pid, "position": pos,
           "season": season, "week": week, **base}
    # actual is DK points off the same categories, so a synthetic row is
    # internally consistent the way a real one is.
    row["actual"] = (0.04 * row["passing_yards"] + 4 * row["passing_tds"]
                     + 0.1 * row["rushing_yards"] + 6 * row["rushing_tds"]
                     + 0.1 * row["receiving_yards"] + 6 * row["receiving_tds"]
                     + 1 * row["receptions"] - 1 * row["passing_interceptions"])
    return row


def _season(pid="p1", pos="WR", rec_yds=50.0, weeks=range(1, 19), season=2023):
    return [_row(pid, pos, season, w, receiving_yards=rec_yds, receptions=4.0)
            for w in weeks]


def test_the_week_being_predicted_cannot_influence_its_own_prediction():
    """THE property. Everything else here is bookkeeping."""
    quiet = _season() + _season(season=2024, weeks=range(1, 19))
    loud = _season() + [
        _row("p1", "WR", 2024, w, receiving_yards=(900.0 if w >= 10 else 50.0),
             receptions=(20.0 if w >= 10 else 4.0))
        for w in range(1, 19)]

    got_quiet = bt.run_backtest(quiet, range(10, 11), k=0.0, d=1.0)
    got_loud = bt.run_backtest(loud, range(10, 11), k=0.0, d=1.0)

    assert got_quiet and got_loud
    assert got_quiet[0]["skill"] == got_loud[0]["skill"], (
        "week 10's own outcome changed week 10's prediction -- the harness "
        "is leaking the future into the past")
    # ...and the outcome it is graded against DID change, which is what makes
    # the assertion above meaningful rather than vacuous.
    assert got_quiet[0]["actual"] != got_loud[0]["actual"]


def test_a_later_week_does_move_an_earlier_ones_history():
    """The other direction must work, or the test above would pass on a model
    that ignores its inputs entirely."""
    rows = _season() + _season(season=2024, weeks=range(1, 19))
    hot = _season() + [
        _row("p1", "WR", 2024, w, receiving_yards=(900.0 if w < 10 else 50.0),
             receptions=(20.0 if w < 10 else 4.0))
        for w in range(1, 19)]

    base = bt.run_backtest(rows, range(10, 11), k=0.0, d=1.0)[0]["skill"]
    after = bt.run_backtest(hot, range(10, 11), k=0.0, d=1.0)[0]["skill"]

    assert after > base, "weeks 1-9 should raise the week-10 projection"


def test_the_baseline_reads_undecayed_history():
    """The bug that made the first run's table degenerate: with one shared
    accumulator, `player_mean` IS `skill` for every decay."""
    rows = _season() + [
        _row("p1", "WR", 2024, w, receiving_yards=(20.0 if w < 9 else 200.0),
             receptions=(2.0 if w < 9 else 12.0))
        for w in range(1, 19)]

    graded = bt.run_backtest(rows, range(12, 13), k=0.0, d=0.80)[0]

    assert graded["skill"] != graded["player_mean"], (
        "a strong decay produced the same number as the flat baseline -- they "
        "are sharing an accumulator again")
    assert graded["skill"] > graded["player_mean"], (
        "recency should weight the recent hot weeks more than a flat average")


def test_a_decay_of_one_is_exactly_the_flat_baseline():
    """d=1.0 with no shrinkage is the player mean by definition. If these ever
    disagree, one of the two paths has grown a term the other has not."""
    rows = _season() + _season(season=2024, weeks=range(1, 19), rec_yds=80.0)

    g = bt.run_backtest(rows, range(12, 13), k=0.0, d=1.0)[0]

    assert g["skill"] == pytest.approx(g["player_mean"])


def test_means_are_scored_by_the_shipped_assembler():
    """Not a second implementation of DK scoring. If the props path changes how
    means become points, this model changes with it."""
    import inspect
    from edge import dfs_project

    src = inspect.getsource(bt)
    assert "points_from_means" in src
    assert not any(tok in src for tok in ("0.04 *", "* 0.04", "6 * ")), (
        "the backtest is doing its own DK arithmetic instead of using the "
        "assembler")
    assert callable(dfs_project.points_from_means)


def test_only_skill_positions_and_regular_season_are_loaded():
    """DST has no player rows and is projected from the market's implied team
    total, so it is not a skill-model question; POST is a different population
    from the slates this model is for."""
    assert set(bt.POSITIONS) == {"QB", "RB", "WR", "TE"}
    src = Path(bt.__file__).read_text()
    assert '"REG"' in src
