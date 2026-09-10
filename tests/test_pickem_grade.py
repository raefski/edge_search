"""scripts/pickem_grade.py -- the model graded against its own backtest.

WHY THIS FILE EXISTS
PICKEM_STATUS.md's "Immediate next step" was to run a week live and grade it,
and nothing implemented it: data/pickem/tracker.csv had 24 columns and zero
writers, and `edge.pickem.ats_result` was called only by the backtest.

The grader is the one place where a second copy of the model would be
completely invisible -- it would report a plausible-looking number about a
model nobody is playing. So the assertions here are mostly about PROVENANCE:
that the shipped make_pick is the thing being graded, that "covered" means what
the backtest means by it, and that a reading taken after kickoff can never be
counted as if it had been available.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pickem_log import Snapshot, append  # noqa: E402
from scripts import pickem_grade  # noqa: E402


def _log(tmp_path, snaps):
    path = Path(tmp_path) / "line_log.csv"
    append(snaps, path=path)
    return path


def _snap(snapshot, captured, **kw):
    base = dict(season=2026, week=1, snapshot=snapshot, captured_at=captured,
                away_team="BUF", home_team="HOU",
                kickoff_utc="2026-09-13T17:00:00Z")
    base.update(kw)
    return Snapshot(**base)


def _sched(home="HOU", away_score=20, home_score=17, week=1):
    return {(2026, week, home): {"season": "2026", "week": str(week),
                                 "home_team": home, "away_team": "BUF",
                                 "home_score": str(home_score),
                                 "away_score": str(away_score),
                                 "game_type": "REG"}}


def test_it_grades_with_the_shipped_model_not_a_copy(tmp_path):
    """The 2026-08-21 incident was a second copy of the fallback rule drifting.

    A grader with its own copy would produce a number that looks like the
    model's record and is not. This pins the identity: the Pick attached to a
    graded row must be exactly what edge.pickem.make_pick returns for the same
    inputs, and the verdict exactly what edge.pickem.ats_result returns.
    """
    from edge.pickem import ats_result, make_pick

    log = _log(tmp_path, [
        _snap("post", "2026-09-08T17:11:20Z", cbs_line_home=-1.5,
              market_line_home=-1.5, market_total=44.5, n_books=3),
        _snap("lock-sun", "2026-09-13T16:35:00Z", market_line_home=1.375,
              market_total=45.0, n_books=3),
    ])
    graded = pickem_grade.grade(pickem_grade.readings(log), _sched())
    assert len(graded) == 1
    g = graded[0]

    expected = make_pick("BUF", "HOU", -1.5, 1.375, 44.5, 45.0)
    assert g["pick"] == expected, "the grader must call the shipped model"
    # BUF won 20-17, so home margin is -3 against a pool line of -1.5.
    assert g["result"] == ats_result(-3.0, -1.5, expected.side)
    assert g["result"] == "W"


def test_a_lock_taken_after_kickoff_is_never_counted(tmp_path):
    """A Monday capture still returns Sunday's games.

    Counting it would grade the model on a number it could not have had, which
    inflates the live record with hindsight -- the exact thing this project
    exists to avoid measuring. The guard is imported from
    scripts/pickem_transferability, not re-implemented.
    """
    log = _log(tmp_path, [
        _snap("post", "2026-09-08T17:11:20Z", cbs_line_home=-1.5,
              market_line_home=-1.5, n_books=3),
        _snap("lock-sun", "2026-09-13T16:35:00Z", market_line_home=1.375,
              n_books=3),
        # Monday, hours after this Sunday game finished
        _snap("lock-mon", "2026-09-14T23:00:00Z", market_line_home=-9.5,
              n_books=3),
    ])
    games = pickem_grade.readings(log)
    assert games[0]["lock"] == 1.375
    assert games[0]["lock_label"] == "lock-sun"


def test_the_latest_lock_before_kickoff_wins_regardless_of_file_order(tmp_path):
    """Rows are ordered by captured_at, which is the board's own finish time.

    Before 2026-09-09 captured_at was utcnow() and file order was the only
    usable proxy. Now that the column means what it says, "the last reading
    before kickoff" can be taken literally.
    """
    log = _log(tmp_path, [
        _snap("post", "2026-09-08T17:11:20Z", cbs_line_home=-1.5,
              market_line_home=-1.5, n_books=3),
        _snap("lock-thu", "2026-09-13T16:40:00Z", market_line_home=2.0,
              n_books=3),
        _snap("midweek", "2026-09-11T16:40:00Z", market_line_home=99.0,
              n_books=3),
        _snap("lock-wed", "2026-09-09T22:56:20Z", market_line_home=0.5,
              n_books=3),
    ])
    g = pickem_grade.readings(log)[0]
    assert g["lock"] == 2.0, "the 16:40 reading, not the last line in the file"
    assert g["post"] == -1.5


def test_the_rams_are_joined_to_nflverses_spelling(tmp_path):
    """CBS says LAR, nflverse says LA. An unaliased join drops the game.

    Same failure shape as the market/CBS alias in edge/pickem_week.py, and the
    map here is that table INVERTED rather than a second copy of it.
    """
    log = _log(tmp_path, [
        Snapshot(season=2026, week=1, snapshot="post",
                 captured_at="2026-09-08T17:11:20Z", away_team="SF",
                 home_team="LAR", kickoff_utc="2026-09-11T00:35:00Z",
                 cbs_line_home=-3.5, market_line_home=-3.5, n_books=3),
        Snapshot(season=2026, week=1, snapshot="lock-thu",
                 captured_at="2026-09-11T00:20:00Z", away_team="SF",
                 home_team="LAR", kickoff_utc="2026-09-11T00:35:00Z",
                 market_line_home=-5.5, n_books=3),
    ])
    sched = {(2026, 1, "LA"): {"home_team": "LA", "away_team": "SF",
                               "home_score": "27", "away_score": "20",
                               "game_type": "REG", "season": "2026",
                               "week": "1"}}
    graded = pickem_grade.grade(pickem_grade.readings(log), sched)
    assert len(graded) == 1, "LAR must resolve to nflverse's LA"
    assert graded[0]["result"] == "W"      # market moved to LA, LA covered -3.5


def test_a_game_with_no_score_yet_is_skipped_not_crashed(tmp_path):
    """The normal state on a Saturday. An empty join must be quiet."""
    log = _log(tmp_path, [
        _snap("post", "2026-09-08T17:11:20Z", cbs_line_home=-1.5,
              market_line_home=-1.5, n_books=3),
        _snap("lock-sun", "2026-09-13T16:35:00Z", market_line_home=1.375,
              n_books=3),
    ])
    sched = {(2026, 1, "HOU"): {"home_team": "HOU", "away_team": "BUF",
                                "home_score": "", "away_score": "",
                                "game_type": "REG", "season": "2026",
                                "week": "1"}}
    assert pickem_grade.grade(pickem_grade.readings(log), sched) == []
    assert pickem_grade.grade([], {}) == []


def test_a_market_only_row_with_no_cbs_line_cannot_be_graded(tmp_path):
    """You are graded against CBS's number. Without it there is no grade.

    Every row the timers bank starts this way (--market-only), so this is the
    common case, not an edge case.
    """
    log = _log(tmp_path, [
        _snap("lock-sun", "2026-09-13T16:35:00Z", market_line_home=1.375,
              n_books=3)])
    assert pickem_grade.grade(pickem_grade.readings(log), _sched()) == []


def test_wilson_is_wide_at_sixteen_games_and_excludes_pushes():
    """The interval is the point of this script at n=16.

    9-7 is 56.3% -- indistinguishable from the backtested 55.9% and from a coin
    flip at the same time. Reporting the point estimate alone would invite
    exactly the conclusion the data cannot support.
    """
    lo, hi = pickem_grade.wilson(9, 7)
    assert lo < 0.35 and hi > 0.75
    assert lo < 0.5 < hi, "16 games cannot distinguish the model from chance"
    # a decided-game denominator: pushes are not losses
    assert pickem_grade.wilson(1, 0) == pickem_grade.wilson(1, 0)
    assert pickem_grade.wilson(0, 0) == (0.0, 1.0)


def test_write_tracker_never_overwrites_a_hand_typed_cell(tmp_path):
    """data/pickem/tracker.csv is Adam's, and gitignored on purpose.

    my_pick, confidence and notes are his; the graded columns are the
    script's. Filling only blanks means running the grader twice, or after he
    has corrected something, cannot destroy his entry.

    AMENDED 2026-09-10: `ats_result` is his too. It records whether ADAM
    covered, and the script was writing the MODEL's verdict into it -- which on
    the real Week-1 opener booked a loss on a game he won. The model's verdict
    now goes to `model_ats_result`; see MODEL_RESULT_COL.
    """
    from edge.pickem import make_pick

    path = Path(tmp_path) / "tracker.csv"
    fields = ["week", "away_team", "home_team", "cbs_line_home",
              "live_line_at_lock_home", "my_pick", "final_home_score",
              "final_away_score", "ats_result", "notes"]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({"week": "1", "away_team": "Bills", "home_team": "Texans",
                    "cbs_line_home": "", "live_line_at_lock_home": "",
                    "my_pick": "Bills (mine)", "final_home_score": "99",
                    "final_away_score": "", "ats_result": "",
                    "notes": "hand-written"})

    pk = make_pick("BUF", "HOU", -1.5, 1.375, None, None)
    graded = [{"season": 2026, "week": 1, "home": "HOU", "away": "BUF",
               "cbs": -1.5, "post": -1.5, "lock": 1.375,
               "lock_at": "2026-09-13T16:35:00Z", "pick": pk,
               "home_score": 17.0, "away_score": 20.0, "result": "W"}]
    assert pickem_grade.write_tracker(graded, path) == 1

    row = list(csv.DictReader(path.open()))[0]
    assert row["model_ats_result"] == "W"
    assert row["ats_result"] == "", "Adam's own result column stays his"
    assert float(row["live_line_at_lock_home"]) == 1.375
    assert row["final_away_score"] == "20"
    assert row["final_home_score"] == "99", "a filled cell is his, not ours"
    assert row["my_pick"] == "Bills (mine)"
    assert row["notes"] == "hand-written"

    # ...and a second run is a no-op rather than a rewrite
    assert pickem_grade.write_tracker(graded, path) == 0


def test_it_reads_the_frozen_backtest_numbers_it_is_compared_against():
    """55.9 / 56.7 / 50.6 are the once-evaluated holdout results.

    They are constants here, never recomputed: the 2023-24 seasons are spent,
    and anything that re-derived them would be a second look at a holdout.
    """
    assert pickem_grade.BACKTEST["overall"][0] == 0.559
    assert pickem_grade.BACKTEST["signal"][0] == 0.567
    assert pickem_grade.BACKTEST["fallback"][0] == 0.506
    src = Path(pickem_grade.__file__).read_text()
    for banned in ("pickem_odds_history", "2023", "2024"):
        assert banned not in src.replace("2023-24", "").replace("2024)", ""), (
            f"the grader must not reach for holdout data ({banned})")


# ===========================================================================
# THE TRACKER IS ADAM'S, NOT THE MODEL'S (iteration 3, finding 4)
#
# `--write` grafted the MODEL's verdict onto Adam's own row. Verified on a copy
# of the real data/pickem/tracker.csv, Week 1 NE@SEA:
#
#     my_pick     'Patriots (Adam pre-pick)'   (unchanged)
#     ats_result  ''  ->  'L'
#
# The model played Seahawks -3.5 and lost. Adam played Patriots +3.5 and won.
# His permanent record would have booked a loss on a game he won -- in a
# gitignored file with no history to recover it from.
#
# Two more, same family:
#   * the blank-only rule PRESERVES FABRICATIONS. live_line_at_post_home holds
#     the 2026-08-20 provisional -3.5 and stays; live_line_at_lock_home gets
#     the real -3.188; stale_gap stays 0.0 while the true edge is +0.312. The
#     row then contradicts itself and nothing says so.
#   * write_tracker's key was (week, home) with no season, so week 1 SEA of
#     2027 would silently take 2026's numbers.
# ===========================================================================

TRACKER_HEADER = [
    "week", "game_date", "away_team", "home_team", "cbs_line_home",
    "cbs_captured_at", "live_line_at_post_home", "live_post_captured_at",
    "live_line_at_lock_home", "live_lock_captured_at", "stale_gap",
    "baseline_adjusted_gap", "key_number_crossed", "ticket_pct_home",
    "money_pct_home", "sharp_money", "injury_trigger", "model_score",
    "recommendation", "my_pick", "confidence_1to3", "final_away_score",
    "final_home_score", "ats_result", "notes",
]


def _tracker(tmp_path, rows, header=None):
    path = Path(tmp_path) / "tracker.csv"
    fields = list(header or TRACKER_HEADER)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    return path


def _read(path):
    with Path(path).open() as f:
        return list(csv.DictReader(f))


def _ne_at_sea(season=2026, week=1, post=-3.188, lock=-3.188):
    """The real Week-1 opener, graded. NE 10 SEA 13.

    Home margin +3 against a pool line of -3.5, so Seahawks -3.5 did NOT cover:
    the model's side loses, Adam's Patriots +3.5 wins.
    """
    from edge.pickem import ats_result, make_pick

    pk = make_pick("Patriots", "Seahawks", -3.5, lock, 44.5, 44.5)
    return {
        "season": season, "week": week, "home": "SEA", "away": "Patriots",
        "cbs": -3.5, "post": post, "lock": lock,
        "lock_at": "2026-09-09T23:20:00Z", "lock_label": "lock-wed",
        "total_open": 44.5, "total_close": 44.5, "pick": pk,
        "home_score": 13.0, "away_score": 10.0,
        "result": ats_result(13 - 10, -3.5, pk.side),
    }


ADAMS_ROW = {
    "week": "1", "away_team": "Patriots", "home_team": "Seahawks",
    "cbs_line_home": "-3.5", "cbs_captured_at": "2026-08-20 (provisional)",
    "live_line_at_post_home": "-3.5", "live_post_captured_at": "2026-08-20",
    "stale_gap": "0.0", "key_number_crossed": "no",
    "recommendation": "COINFLIP - live fav Seahawks",
    "my_pick": "Patriots (Adam pre-pick)", "confidence_1to3": "2",
    "notes": "comm 30/70; Wed 8:20p NBC; re-verify Tue 9/8",
}


def test_the_models_verdict_never_lands_in_adams_result_column(tmp_path, capsys):
    """REGRESSION. The model lost this game; Adam won it.

    `ats_result` is the column that says whether ADAM covered. The model's
    verdict is a different fact about a different pick and belongs in its own
    column.
    """
    g = _ne_at_sea()
    assert g["result"] == "L", "sanity: the model's Seahawks -3.5 lost"

    path = _tracker(tmp_path, [dict(ADAMS_ROW)])
    pickem_grade.write_tracker([g], path=path)
    row = _read(path)[0]

    assert row["ats_result"] == "", "Adam's own result column must stay his"
    assert row["model_ats_result"] == "L"
    assert row["my_pick"] == "Patriots (Adam pre-pick)", "never touched"


def test_the_model_result_column_is_appended_when_the_file_lacks_it(tmp_path):
    """The real tracker has 25 hand-made columns and none of them is this one."""
    path = _tracker(tmp_path, [dict(ADAMS_ROW)])
    assert "model_ats_result" not in _read(path)[0]

    pickem_grade.write_tracker([_ne_at_sea()], path=path)
    with path.open() as f:
        header = next(csv.reader(f))
    assert header[-1] == "model_ats_result", "appended, never inserted"
    assert header[:len(TRACKER_HEADER)] == TRACKER_HEADER, \
        "and every existing column keeps its position"


def test_adams_own_columns_are_never_touched(tmp_path):
    path = _tracker(tmp_path, [dict(ADAMS_ROW)])
    pickem_grade.write_tracker([_ne_at_sea()], path=path)
    row = _read(path)[0]
    for col in ("my_pick", "confidence_1to3", "notes"):
        assert row[col] == ADAMS_ROW[col], col


# ------------------------------------------------------- the silent contradiction
def test_a_populated_cell_that_disagrees_is_reported(tmp_path, capsys):
    """The blank-only rule is right, and on its own it preserves fabrications.

    live_line_at_post_home holds a provisional -3.5 typed on Aug 20; the log
    says the market was -3.188 at the freeze. stale_gap says 0.0; the real edge
    is +0.312. Keeping the old value silently leaves a row that contradicts
    itself, so the disagreement has to be named.
    """
    path = _tracker(tmp_path, [dict(ADAMS_ROW)])
    pickem_grade.write_tracker([_ne_at_sea()], path=path)
    out = capsys.readouterr().out

    assert "MISMATCH" in out
    assert "live_line_at_post_home" in out and "-3.188" in out
    assert "stale_gap" in out and "0.312" in out
    # ...and the file itself is unchanged on those cells: blank-only stands
    row = _read(path)[0]
    assert row["live_line_at_post_home"] == "-3.5"
    assert row["stale_gap"] == "0.0"


def test_a_populated_cell_that_agrees_is_not_reported(tmp_path, capsys):
    """cbs_line_home is -3.5 in both places. Numeric equality, not string:
    '-3.5' and -3.5 are the same number and must not cry wolf."""
    path = _tracker(tmp_path, [dict(ADAMS_ROW)])
    pickem_grade.write_tracker([_ne_at_sea()], path=path)
    out = capsys.readouterr().out
    for line in out.splitlines():
        if "MISMATCH" in line:
            assert "cbs_line_home" not in line, line


def test_the_mismatch_report_runs_without_writing(tmp_path, capsys):
    """DRY RUN BY DEFAULT. Seeing the disagreements is the reason to look
    before writing, so it cannot require writing first."""
    path = _tracker(tmp_path, [dict(ADAMS_ROW)])
    before = path.read_text()
    pickem_grade.write_tracker([_ne_at_sea()], path=path, dry_run=True)
    assert path.read_text() == before, "a dry run must not touch the file"
    assert "MISMATCH" in capsys.readouterr().out


# --------------------------------------------------------------- the season key
def test_two_seasons_do_not_collide(tmp_path):
    """(week, home) repeats every year. Week 1 at Seattle is not one game."""
    rows = [
        dict(ADAMS_ROW, season="2026"),
        dict(ADAMS_ROW, season="2027", my_pick="Seahawks", notes="next year"),
    ]
    path = _tracker(tmp_path, rows, header=["season"] + TRACKER_HEADER)
    pickem_grade.write_tracker([_ne_at_sea(season=2026)], path=path)

    out = _read(path)
    assert out[0]["model_ats_result"] == "L", "2026 is the graded season"
    assert out[1]["model_ats_result"] == "", "2027 must not inherit it"


def test_a_tracker_without_a_season_column_still_matches(tmp_path, capsys):
    """The real file has no season column. One season in the log is
    unambiguous, so fall back to it rather than refusing to grade."""
    path = _tracker(tmp_path, [dict(ADAMS_ROW)])
    pickem_grade.write_tracker([_ne_at_sea(season=2026)], path=path)
    assert _read(path)[0]["model_ats_result"] == "L"


def test_a_seasonless_tracker_is_refused_when_the_log_spans_seasons(tmp_path, capsys):
    """Two seasons in the log and no season column in the file is genuinely
    ambiguous. Guessing is how the wrong year's numbers get written."""
    path = _tracker(tmp_path, [dict(ADAMS_ROW)])
    changed = pickem_grade.write_tracker(
        [_ne_at_sea(season=2026), _ne_at_sea(season=2027)], path=path)
    assert changed == 0
    out = capsys.readouterr().out.lower()
    assert "season" in out
    assert _read(path)[0].get("model_ats_result", "") == ""
