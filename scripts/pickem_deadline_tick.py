#!/usr/bin/env python3
"""Fire pick'em captures from the schedule, not from fixed clock times.

WHY (2026-10-11)
The four lock timers fired at fixed times: Wed/Thu/Mon 19:00 and Sun 12:35.
That assumed one deadline per day at the day's first kickoff, and that the
first Sunday kickoff is 1pm. Weeks 4 and 5 opened Sunday with a 9:30am London
game, so the Sunday reading landed three hours after the slate locked, and
the newest board before 9:30 was Saturday 23:20. Four more 9:30 Sundays remain
(weeks 6, 7, 9, 10), plus Thanksgiving (1:00pm Thursday), Black Friday, two
week-15 Saturdays and a Christmas-Friday triple-header that no timer covered.
On top of that, the lock rule itself is unsettled (edge/pickem_deadline.py):
under "2 hours before kickoff" every one of those fixed times is late.

WHAT IT DOES -- run every 5 minutes by deploy/pickem-deadline-tick.timer
For the current pool week it takes every kickoff in the line log (falling back
to nflverse's schedule before Tuesday's post capture exists), computes every
deadline EITHER rule could produce, and for each one:

  * CAPTURE_LEAD before it: starts pickem-capture@<label>.service, the same
    unit the fixed timers ran (collect the board, read CBS, bank the reading,
    push). Labels are lock-<day><HHMM>, e.g. lock-sun1100, so each deadline
    gets its own row in the append-only log.
  * just after it: runs scripts/pickem_pool_picks.py, which records whose
    picks have become visible -- the observation that settles the lock rule --
    and every entry's picks.
  * Tuesday from 10:00 ET: one final pool-picks fetch of LAST week, so the
    finished week's results are on file.

Each action fires once; data/pickem/deadline_tick_state.json remembers which.

    python3 scripts/pickem_deadline_tick.py --plan      # this week's schedule
    python3 scripts/pickem_deadline_tick.py --dry-run   # what would fire now
"""
from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pickem_deadline import (  # noqa: E402
    FIRST_GAME_OF_DAY, KICKOFF_MINUS_2H, RULE, candidate_deadlines,
    game_deadlines, label_for, parse_utc)
from edge.pickem_log import load as load_line_log  # noqa: E402
from edge.pickem_week import EASTERN, SEASON_START, current_week  # noqa: E402

STATE = ROOT / "data" / "pickem" / "deadline_tick_state.json"
SCHEDULE_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
SCHEDULE_CACHE = ROOT / "data" / "cache" / "nflverse_games.csv"

#: Capture this long before a deadline. The unit collects the board, fetches
#: CBS, captures and pushes in ~1-2 minutes; 25 minutes leaves room for its
#: one 5-minute retry and still lands inside the window.
CAPTURE_LEAD = datetime.timedelta(minutes=25)
#: Observe lock state this long after a deadline (CBS needs a moment), and
#: give up if the machine was asleep for longer than OBSERVE_WINDOW.
OBSERVE_DELAY = datetime.timedelta(minutes=3)
OBSERVE_WINDOW = datetime.timedelta(minutes=45)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def kickoffs_from_log(season: int, week: int) -> dict[str, datetime.datetime]:
    out = {}
    for r in sorted(load_line_log(), key=lambda r: r.get("captured_at") or ""):
        if str(r.get("season")) == str(season) and str(r.get("week")) == str(week):
            k = parse_utc(r.get("kickoff_utc"))
            if k is not None:
                out[r["home_team"]] = k          # newest wins: flexed games
    return out


def kickoffs_from_schedule(season: int, week: int) -> dict[str, datetime.datetime]:
    """nflverse's schedule, cached for a day. Only used before the week's
    first capture exists, so its team spellings never meet the log's."""
    fresh = (SCHEDULE_CACHE.exists() and
             _now().timestamp() - SCHEDULE_CACHE.stat().st_mtime < 86400)
    if not fresh:
        try:
            with urllib.request.urlopen(SCHEDULE_URL, timeout=60) as r:
                SCHEDULE_CACHE.parent.mkdir(parents=True, exist_ok=True)
                SCHEDULE_CACHE.write_bytes(r.read())
        except OSError as e:
            if not SCHEDULE_CACHE.exists():
                print(f"no schedule: {e}")
                return {}
    out = {}
    for row in csv.DictReader(io.StringIO(SCHEDULE_CACHE.read_text())):
        if row.get("season") != str(season) or row.get("week") != str(week) \
                or row.get("game_type") != "REG":
            continue
        try:
            local = datetime.datetime.strptime(
                f"{row['gameday']} {row['gametime']}", "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            continue
        out[row["home_team"]] = local.replace(tzinfo=EASTERN).astimezone(
            datetime.timezone.utc)
    return out


def week_kickoffs(season: int, week: int) -> dict[str, datetime.datetime]:
    return kickoffs_from_log(season, week) or kickoffs_from_schedule(season, week)


def due_actions(kicks: dict, now: datetime.datetime, fired: set[str],
                season: int, week: int) -> list[tuple[str, str]]:
    """[(state_key, action)] that should run at `now`. Pure, for the tests."""
    out = []
    for d in candidate_deadlines(kicks):
        label = label_for(d)
        key = f"{season}-{week}-{label}"
        if d - CAPTURE_LEAD <= now < d and f"capture|{key}" not in fired:
            out.append((f"capture|{key}", f"capture:{label}"))
        if d + OBSERVE_DELAY <= now < d + OBSERVE_WINDOW and f"observe|{key}" not in fired:
            out.append((f"observe|{key}", f"observe:{week}"))
    et = now.astimezone(EASTERN)
    if et.weekday() == 1 and et.hour >= 10 and week > 1:
        key = f"final|{season}-{week - 1}"
        if key not in fired:
            out.append((key, f"observe-final:{week - 1}"))
    return out


def run(action: str, dry: bool) -> bool:
    kind, _, arg = action.partition(":")
    if kind == "capture":
        cmd = ["systemctl", "--user", "start", "--no-block",
               f"pickem-capture@{arg}.service"]
    elif kind == "observe":
        cmd = [sys.executable, "scripts/pickem_pool_picks.py", "--weeks", arg]
    else:  # observe-final: a past week's visibility says nothing about locks
        cmd = [sys.executable, "scripts/pickem_pool_picks.py", "--weeks", arg, "--no-obs"]
    print(("DRY  " if dry else "RUN  ") + " ".join(cmd))
    if dry:
        return True
    r = subprocess.run(cmd, cwd=ROOT, timeout=300)
    return r.returncode == 0


def plan(kicks: dict) -> None:
    by_rule = {r: game_deadlines(kicks, r) for r in (KICKOFF_MINUS_2H, FIRST_GAME_OF_DAY)}
    print(f"{'home':<5}{'kickoff ET':<17}{'2h-before lock':<17}{'first-of-day lock':<19}")
    for home, k in sorted(kicks.items(), key=lambda kv: kv[1]):
        f = lambda d: d.astimezone(EASTERN).strftime("%a %m/%d %H:%M")  # noqa: E731
        print(f"{home:<5}{f(k):<17}{f(by_rule[KICKOFF_MINUS_2H][home]):<17}"
              f"{f(by_rule[FIRST_GAME_OF_DAY][home]):<19}")
    print(f"\ncaptures ({CAPTURE_LEAD.seconds // 60} min before each candidate "
          f"deadline; grading/page rule = {RULE!r}):")
    for d in candidate_deadlines(kicks):
        c = (d - CAPTURE_LEAD).astimezone(EASTERN)
        print(f"  {label_for(d):<15} capture {c:%a %H:%M} ET -> deadline "
              f"{d.astimezone(EASTERN):%H:%M}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", action="store_true", help="print this week's schedule")
    ap.add_argument("--dry-run", action="store_true", help="print, do not act")
    ap.add_argument("--now", help="pretend it is this ISO instant (testing)")
    args = ap.parse_args()

    now = parse_utc(args.now) if args.now else _now()
    week = current_week(now.astimezone(EASTERN).date())
    if week is None:
        print("Outside the season.")
        return
    season = SEASON_START.year
    kicks = week_kickoffs(season, week)
    if not kicks:
        print(f"week {week}: no kickoffs known yet")
        return
    if args.plan:
        print(f"season {season} week {week}")
        plan(kicks)
        return

    state = json.loads(STATE.read_text()) if STATE.exists() else {"fired": []}
    fired = set(state["fired"])
    for key, action in due_actions(kicks, now, fired, season, week):
        ok = run(action, args.dry_run)
        # A capture is marked fired once systemd has accepted it: the unit's
        # own Restart= and OnFailure= own retries from there. An observation
        # that failed (expired session) is retried next tick inside its window.
        if ok and not args.dry_run:
            fired.add(key)
    if not args.dry_run:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"fired": sorted(fired)}, indent=1))


if __name__ == "__main__":
    main()
