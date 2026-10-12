"""Deadlines, the schedule-driven capture tick, and the pool-picks parser.

The lock rule is unsettled (edge/pickem_deadline.py): Adam says 2 hours before
kickoff, the pool's Settings page says the day's first kickoff. These tests pin
what each rule means on the slates that broke the fixed timers -- a 9:30 London
Sunday above all -- and that the scheduler covers both.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import pickem_deadline as D  # noqa: E402
from edge.pickem_week import EASTERN  # noqa: E402

UTC = datetime.timezone.utc


def et(y, mo, d, h, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=EASTERN).astimezone(UTC)


# Week 6 2026: TNF, a 9:30 London game, the 1pm/4pm/SNF Sunday, MNF.
SLATE = {
    "DEN": et(2026, 10, 15, 20, 15),
    "JAX": et(2026, 10, 18, 9, 30),
    "ATL": et(2026, 10, 18, 13, 0),
    "KC": et(2026, 10, 18, 16, 25),
    "GB": et(2026, 10, 18, 20, 20),
    "SF": et(2026, 10, 19, 20, 15),
}


def test_two_hours_before_is_per_game():
    dl = D.game_deadlines(SLATE, D.KICKOFF_MINUS_2H)
    assert dl["ATL"] == et(2026, 10, 18, 11, 0)
    assert dl["KC"] == et(2026, 10, 18, 14, 25)
    assert dl["JAX"] == et(2026, 10, 18, 7, 30)


def test_first_game_of_the_day_follows_the_london_kickoff():
    """The failure that started this: on a London Sunday the whole Sunday
    slate locks at 9:30, three hours before the old 12:35 capture."""
    dl = D.game_deadlines(SLATE, D.FIRST_GAME_OF_DAY)
    for home in ("JAX", "ATL", "KC", "GB"):
        assert dl[home] == et(2026, 10, 18, 9, 30), home
    assert dl["DEN"] == et(2026, 10, 15, 20, 15)
    assert dl["SF"] == et(2026, 10, 19, 20, 15)


def test_the_day_is_the_eastern_day_not_the_utc_one():
    """SNF kicks off after midnight UTC. Bucketed by UTC date it would be
    Monday's first game and lock at its own kickoff."""
    dl = D.game_deadlines(SLATE, D.FIRST_GAME_OF_DAY)
    assert dl["GB"] == et(2026, 10, 18, 9, 30)


def test_earliest_never_trusts_a_reading_either_rule_forbids():
    dl = D.game_deadlines(SLATE, D.EARLIEST)
    assert dl["ATL"] == et(2026, 10, 18, 9, 30)      # per-day is earlier
    assert dl["DEN"] == et(2026, 10, 15, 18, 15)     # 2h-before is earlier


def test_candidates_cover_both_rules_and_labels_are_unique():
    c = D.candidate_deadlines(SLATE)
    for rule in (D.KICKOFF_MINUS_2H, D.FIRST_GAME_OF_DAY):
        assert set(D.game_deadlines(SLATE, rule).values()) <= set(c)
    labels = [D.label_for(d) for d in c]
    assert len(labels) == len(set(labels))
    assert all(l.startswith("lock") for l in labels), "the lock* family"
    assert D.label_for(et(2026, 10, 18, 9, 30)) == "lock-sun0930"


def test_infer_rule_scores_only_observations_the_rules_disagree_on():
    day_first = et(2026, 10, 18, 9, 30)
    obs = [
        # 9:35, a 1pm game: per-day says locked, 2h says open. It was open.
        dict(observed_at=et(2026, 10, 18, 9, 35), kickoff=SLATE["ATL"],
             day_first=day_first, locked=False),
        # 11:05, same game: both say locked -- no information.
        dict(observed_at=et(2026, 10, 18, 11, 5), kickoff=SLATE["ATL"],
             day_first=day_first, locked=True),
    ]
    s = D.infer_rule(obs)
    assert s[D.KICKOFF_MINUS_2H] == {"agree": 1, "disagree": 0}
    assert s[D.FIRST_GAME_OF_DAY] == {"agree": 0, "disagree": 1}


def test_the_grader_drops_a_reading_taken_after_a_2h_lock(tmp_path):
    """Before kickoff is no longer enough: a 12:35 reading for a 1pm game
    came after an 11:00 lock, so under the default rule it does not count."""
    from edge.pickem_log import Snapshot, append
    from scripts import pickem_grade

    def snap(label, at, mkt):
        return Snapshot(season=2026, week=1, snapshot=label, captured_at=at,
                        away_team="BUF", home_team="HOU",
                        kickoff_utc="2026-09-13T17:00:00Z", cbs_line_home=-1.5,
                        market_line_home=mkt, n_books=3)
    log = tmp_path / "log.csv"
    append([snap("midweek", "2026-09-11T16:35:00Z", -2.0),
            snap("lock-sun", "2026-09-13T16:35:00Z", 1.0)], path=log)
    g = pickem_grade.readings(log)[0]
    assert g["lock"] == -2.0 and g["lock_label"] == "midweek"


# --- the tick ---------------------------------------------------------------

def test_tick_fires_a_capture_inside_the_lead_window_once():
    from scripts.pickem_deadline_tick import due_actions
    now = et(2026, 10, 18, 9, 8)
    acts = due_actions(SLATE, now, set(), 2026, 6)
    assert ("capture|2026-6-lock-sun0930", "capture:lock-sun0930") in acts
    fired = {k for k, _ in acts}
    assert not [a for a in due_actions(SLATE, now, fired, 2026, 6)
                if a[1].startswith("capture")]


def test_tick_observes_just_after_a_deadline_not_before():
    from scripts.pickem_deadline_tick import due_actions
    before = due_actions(SLATE, et(2026, 10, 18, 9, 31), set(), 2026, 6)
    after = due_actions(SLATE, et(2026, 10, 18, 9, 34), set(), 2026, 6)
    assert not [a for a in before if a[1].startswith("observe")]
    assert ("observe|2026-6-lock-sun0930", "observe:6") in after


def test_tick_fetches_last_weeks_final_picks_on_tuesday():
    from scripts.pickem_deadline_tick import due_actions
    acts = due_actions({}, et(2026, 10, 20, 10, 5), set(), 2026, 7)
    assert ("final|2026-6", "observe-final:6") in acts


# --- the pool picks parser --------------------------------------------------

PAGE = (
    "Standings\nWeekly\nWeek 6\nPLAYERS\n\n\t\n\nPTS\n\n\t\n\nYTD\n\n\t\n"
    "FINAL\nPIT\nCLE\n24\n27\n(+2.5)\n\t\n"
    "SUN 1:00 PM\nIND\nWAS\n(+3.5)\n\n\n"
    "1st\nAlice Example\n\t1\t40\t\nCLE\n\t\nIND\n\n\n"
    "2nd\nBob Example\n\t0\t38\t\nPIT\n\t\n\n\n"
    "2nd\nCarol Example\n\t0\t0\t\n-\n\t\n-\n"
    "More\nPick'em Games\nFantasy Sports\n"
)


def test_parser_keeps_hidden_cells_aligned_and_ignores_the_footer():
    from scripts.pickem_pool_picks import HIDDEN, parse_standings_text
    games, entries = parse_standings_text(PAGE)
    assert [(g["away"], g["home"], g["line"]) for g in games] == [
        ("PIT", "CLE", 2.5), ("IND", "WAS", 3.5)]
    assert games[0]["home_score"] == 27 and games[1]["home_score"] is None
    by = {e["player"]: e["picks"] for e in entries}
    assert by["Alice Example"] == ["CLE", "IND"]
    assert by["Bob Example"] == ["PIT", HIDDEN], "unlocked pick stays in its column"
    assert by["Carol Example"] == ["-", "-"], "footer text is not picks"


def test_a_hidden_cell_never_erases_a_pick_already_seen(tmp_path):
    from scripts.pickem_pool_picks import HIDDEN, upsert_picks, _read
    games = [dict(away="IND", home="WAS", line=3.5, away_score=None,
                  home_score=None, status="")]
    path = tmp_path / "picks.csv"
    upsert_picks(2026, 6, games, [dict(player="Bob", picks=["IND"])], None,
                 "2026-10-18T15:00:00Z", path)
    upsert_picks(2026, 6, games, [dict(player="Bob", picks=[HIDDEN])], None,
                 "2026-10-18T16:00:00Z", path)
    (row,) = _read(path)
    assert row["pick"] == "IND" and row["home"] == "WSH"


# --- the media backtest's helpers ------------------------------------------

def test_media_age_parser_reads_youtubes_relative_dates():
    from scripts.pickem_media_backtest import _age_days
    assert _age_days("1 year ago") == 365
    assert _age_days("Streamed 3 weeks ago") == 21
    assert _age_days("") is None


def test_media_chunks_split_run_on_captions_but_keep_short_sentences():
    """A 300-word unpunctuated caption is one 'sentence'; one 'over' anywhere
    in it made extract_pick reject the whole thing."""
    from scripts.pickem_media_backtest import _chunks
    run_on = " ".join(["word"] * 100)
    assert _chunks(run_on).count(".") >= 3
    assert _chunks("Give me the Bears plus three.") == "Give me the Bears plus three."
