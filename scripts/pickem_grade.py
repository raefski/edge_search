#!/usr/bin/env python3
"""Grade the live model against its own backtest. The step PICKEM_STATUS.md
calls "Immediate next step" and nothing implemented.

WHY THIS HAD TO EXIST
The whole project rests on one out-of-sample number: 298-235-10 = 55.9% ATS on
the 2023-24 held-out seasons, with signal games (83% of the slate) at 56.7% and
fallback/unmoved games at 50.6%. Those seasons are SPENT -- they have been
evaluated, twice, and looking again would turn a held-out estimate into a
fitted one. So the only honest way to learn anything more about this model is
forward: pick a week, wait for the games, and grade what actually happened.

Nothing did that. data/pickem/tracker.csv has 24 columns and zero writers.
`edge.pickem.ats_result` existed and was called only by the backtest simulator.

WHAT IT DOES
For each (season, week, home_team) in data/pickem_line_log.csv:

  * pool_line  = cbs_line_home, the number the pool grades you against
  * live_line  = the LAST `lock*` reading taken BEFORE that game kicked off
                 (the pool's deadline is per day, so a week has up to four
                 lock labels; a Monday capture still returns Sunday's games and
                 must not be allowed to overwrite their good Sunday reading --
                 the guard is scripts/pickem_transferability._before_kickoff,
                 imported rather than re-implemented)
  * the totals tiebreak gets market_total at `post` and at that lock, exactly
    as pages/4_🎯_Pickem.py hands them over

then calls the SHIPPED `edge.pickem.make_pick` -- never a local
reimplementation. That rule is not stylistic: the 2026-08-21 incident was a
second copy of the fallback rule drifting from the first, and it was caught
only because one implementation existed to compare against. A grader with its
own copy of the model would report on a model nobody is playing.

Scores come from nflverse (the same file scripts/pickem_situational_collect.py
already uses) and the verdict from `edge.pickem.ats_result`, so "covered" means
here exactly what it means in the backtest.

WHAT IT IS NOT
Not a holdout look. 2026 is new forward data that did not exist when the model
was frozen, so grading it costs nothing and settles nothing about 2023-24.
And it is not a verdict for a long time: sixteen games is worth roughly +-12
percentage points, which is why every rate below is printed with an interval
and why the interval, not the point estimate, is the number to read.

DRY RUN BY DEFAULT, like every other script here. `--write` fills the graded
columns of data/pickem/tracker.csv; that file is gitignored real pool data and
stays that way. Only blank cells are filled -- anything typed by hand (my_pick,
confidence, notes) is never touched.

    python3 scripts/pickem_grade.py
    python3 scripts/pickem_grade.py --week 1 --write
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pickem import ats_result, make_pick  # noqa: E402
from edge.pickem_cbs import NICK_TO_ABBR  # noqa: E402
from edge.pickem_log import LINE_LOG, load  # noqa: E402
from edge.pickem_week import MARKET_TO_CBS_ABBR  # noqa: E402
# The before-kickoff guard, imported from the one place it lives. A second copy
# of "was this reading actionable?" is the same class of bug as a second copy
# of the model.
from scripts.pickem_transferability import _before_kickoff  # noqa: E402

SCHEDULE_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
TRACKER = ROOT / "data" / "pickem" / "tracker.csv"

#: nflverse spells two teams differently from CBS. Derived by INVERTING the
#: shared table rather than by writing a second one -- edge/pickem_week.py's
#: docstring records what an unaliased join costs (a silently dropped game,
#: which on the page became a fabricated zero edge).
CBS_TO_NFLVERSE = {v: k for k, v in MARKET_TO_CBS_ABBR.items()}

#: The frozen out-of-sample benchmarks these live numbers are read against.
#: PICKEM_MODEL.md section 2; PICKEM_STATUS.md lines 51-52.
BACKTEST = {
    "overall": (0.559, "298-235-10, 2023-24 held out"),
    "signal": (0.567, "253-193, games where the line moved (83% of slate)"),
    "fallback": (0.506, "unmoved games -- the market-favourite fallback"),
}
TIERS = ("STRONG", "SOLID", "LEAN", "COIN FLIP")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def wilson(wins: int, losses: int, z: float = 1.96) -> tuple[float, float]:
    """Score interval for a win rate. Pushes are excluded, as in the backtest.

    Wilson rather than the normal approximation because at n=16 the latter is
    badly wrong near the tails -- and n=16 is exactly where this script starts.
    """
    n = wins + losses
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - margin) / d), min(1.0, (centre + margin) / d))


def readings(path=LINE_LOG) -> list[dict]:
    """One row per game: CBS's frozen line, and the market at post and at lock.

    Deliberately close in shape to scripts/pickem_transferability.collect, but
    it keeps the extra columns grading needs (kickoff, totals, the lock's own
    timestamp) rather than only the numbers w is estimated from.
    """
    games: dict[tuple, dict] = {}
    # Sorted by the moment the prices were observed, so "the last lock before
    # kickoff" means the latest one, not the one furthest down the file. Since
    # 2026-09-09 captured_at is the board's own finish time, which is what
    # makes this ordering meaningful at all.
    for r in sorted(load(path), key=lambda r: r.get("captured_at") or ""):
        key = (int(r["season"]), int(r["week"]), r["home_team"])
        g = games.setdefault(key, {
            "season": key[0], "week": key[1], "home": key[2],
            "away": r.get("away_team", ""), "kickoff": "", "cbs": None,
            "post": None, "total_open": None,
            "lock": None, "total_close": None, "lock_at": "", "lock_label": "",
        })
        if r.get("kickoff_utc"):
            g["kickoff"] = r["kickoff_utc"]
        if _f(r.get("cbs_line_home")) is not None:
            g["cbs"] = _f(r["cbs_line_home"])
        mkt = _f(r.get("market_line_home"))
        if mkt is None:
            continue
        snap = r["snapshot"]
        if snap == "post":
            g["post"] = mkt
            g["total_open"] = _f(r.get("market_total"))
        elif snap.startswith("lock"):
            # The kickoff on THIS row may be blank (a market-only capture that
            # found no board), so check against the best kickoff known for the
            # game rather than against the row alone.
            if _before_kickoff({"captured_at": r.get("captured_at"),
                                "kickoff_utc": g["kickoff"]}):
                g["lock"] = mkt
                g["total_close"] = _f(r.get("market_total"))
                g["lock_at"] = r.get("captured_at", "")
                g["lock_label"] = snap
    return list(games.values())


def fetch_schedule(seasons: set[int]) -> dict[tuple, dict]:
    """{(season, week, home_abbr): row} from nflverse, regular season only."""
    print(f"fetching {SCHEDULE_URL}")
    with urllib.request.urlopen(SCHEDULE_URL, timeout=120) as r:
        text = r.read().decode("utf-8", "replace")
    out = {}
    for row in csv.DictReader(text.splitlines()):
        if row.get("game_type") != "REG":
            continue
        try:
            season, week = int(row["season"]), int(row["week"])
        except (TypeError, ValueError):
            continue
        if season in seasons:
            out[(season, week, row["home_team"])] = row
    return out


def grade(games: list[dict], sched: dict[tuple, dict]) -> list[dict]:
    """Score every game that has a pool line, a lock reading, and a result."""
    out = []
    for g in games:
        if g["cbs"] is None or g["lock"] is None:
            continue
        key = (g["season"], g["week"],
               CBS_TO_NFLVERSE.get(g["home"], g["home"]))
        row = sched.get(key)
        if row is None:
            continue
        home, away = _f(row.get("home_score")), _f(row.get("away_score"))
        if home is None or away is None:
            continue                       # not played yet -- not a failure
        pk = make_pick(g["away"], g["home"], g["cbs"], g["lock"],
                       g["total_open"], g["total_close"])
        out.append(dict(g, pick=pk, home_score=home, away_score=away,
                        result=ats_result(home - away, g["cbs"], pk.side)))
    return out


def _line(label: str, graded: list[dict], bench: tuple[float, str] | None) -> None:
    w = sum(1 for g in graded if g["result"] == "W")
    l = sum(1 for g in graded if g["result"] == "L")
    p = sum(1 for g in graded if g["result"] == "P")
    if w + l == 0:
        print(f"  {label:<12} {w}-{l}-{p}      (no decided games)")
        return
    rate = w / (w + l)
    lo, hi = wilson(w, l)
    tail = ""
    if bench:
        tail = f"   vs backtest {bench[0]:.1%}  ({bench[1]})"
    print(f"  {label:<12} {w}-{l}-{p}   {rate:6.1%}   "
          f"95% CI [{lo:.1%}, {hi:.1%}]{tail}")


def summarise(games: list[dict], graded: list[dict]) -> None:
    print("=" * 78)
    print("LIVE GRADE: how the shipped model is actually doing")
    print("=" * 78)
    print(f"log: {LINE_LOG}")
    have_cbs = [g for g in games if g["cbs"] is not None]
    have_lock = [g for g in have_cbs if g["lock"] is not None]
    print(f"games in the log:                    {len(games)}")
    print(f"  ...with CBS's frozen line:         {len(have_cbs)}")
    print(f"  ...and a lock reading before kick: {len(have_lock)}")
    print(f"  ...and a final score:              {len(graded)}")

    if not graded:
        print("\nNo results yet -- nothing to grade. That is the expected output "
              "until a week's games have been played.")
        print("\nWhat each missing piece needs:")
        if not have_cbs:
            print("  * CBS's frozen line. Transcribe the pool screenshot into")
            print("    data/pickem_current_week.csv (or paste the Picks page into")
            print("    scripts/pickem_pool_import.py), then re-run the SAME capture")
            print("    label: `python3 scripts/pickem_capture.py --snapshot post")
            print("    --week N --confirm` -> 'wrote 0 new, completed 16'.")
        if not have_lock:
            print("  * A lock reading taken before kickoff. The six timers do this")
            print("    (deploy/pickem-capture-*.timer); check them with")
            print("    `systemctl --user list-timers 'pickem-capture*'`.")
        print("  * Final scores. nflverse publishes them within a day of the game;")
        print("    Week 1 completes Monday night.")
        return

    print("\n" + "-" * 78)
    print("OVERALL                W-L-P     rate     interval")
    print("-" * 78)
    _line("all games", graded, BACKTEST["overall"])
    signal = [g for g in graded if g["pick"].tier != "COIN FLIP"]
    fallback = [g for g in graded if g["pick"].tier == "COIN FLIP"]
    _line("signal", signal, BACKTEST["signal"])
    _line("fallback", fallback, BACKTEST["fallback"])

    print("\nBY TIER")
    print("-" * 78)
    for tier in TIERS:
        rows = [g for g in graded if g["pick"].tier == tier]
        if rows:
            _line(tier, rows, None)

    w = sum(1 for g in graded if g["result"] == "W")
    l = sum(1 for g in graded if g["result"] == "L")
    if w + l < 100:
        lo, hi = wilson(w, l)
        print(f"\n  READ THE INTERVAL, NOT THE RATE. n={w + l} decided games gives "
              f"+-{(hi - lo) / 2:.0%}, which spans")
        print("  both 'better than the backtest' and 'worse than a coin flip'. "
              "PICKEM_MODEL.md's own")
        print("  55.9% took 543 games. A season is 272; two are needed before "
              "this line means much.")

    print("\nWORST MISSES (biggest edge that lost)")
    print("-" * 78)
    losses = sorted((g for g in graded if g["result"] == "L"),
                    key=lambda g: -abs(g["pick"].edge_pts))[:5]
    for g in losses:
        pk = g["pick"]
        print(f"  wk{g['week']:>2} {g['away']:>3}@{g['home']:<3} "
              f"{pk.tier:<9} CBS {pk.pool_line:+5.1f} -> market {pk.live_line:+6.2f} "
              f"({pk.edge_pts:+.2f})  final {g['away_score']:.0f}-{g['home_score']:.0f}")
    if not losses:
        print("  (none)")


#: The column the MODEL's verdict goes in. NOT `ats_result`.
#:
#: THE BUG THIS FIXES, found 2026-09-10, verified on a copy of the real file.
#: `--write` grafted the model's result onto Adam's own row, Week 1 NE@SEA:
#:
#:     my_pick     'Patriots (Adam pre-pick)'   (unchanged)
#:     ats_result  ''  ->  'L'
#:
#: The model played Seahawks -3.5 and lost; Adam played Patriots +3.5 and won.
#: `ats_result` is the column that records whether ADAM covered -- his
#: permanent pool record, in a gitignored file with no history to recover from.
#: The model's verdict is a fact about a different pick and gets its own
#: column, appended to the header if the file has not seen it before.
MODEL_RESULT_COL = "model_ats_result"

#: Only these tracker columns are ever written, and only when blank. Everything
#: else in that file is Adam's -- his pick, his confidence, his notes, and his
#: result.
TRACKER_COLS = ("cbs_line_home", "live_line_at_post_home",
                "live_line_at_lock_home", "live_lock_captured_at",
                "stale_gap", "key_number_crossed", "model_score",
                "recommendation", "final_away_score", "final_home_score",
                MODEL_RESULT_COL)


def _agrees(existing: str, computed) -> bool:
    """Is a cell already holding what we would have written?

    Numeric where both sides are numeric -- '-3.5' and -3.5 are the same
    number, and a string comparison there would cry wolf on every row.
    """
    existing = (existing or "").strip()
    if computed is None:
        return True
    try:
        return abs(float(existing) - float(computed)) < 1e-9
    except (TypeError, ValueError):
        return existing == str(computed).strip()


def write_tracker(graded: list[dict], path: Path = TRACKER,
                  dry_run: bool = False) -> int:
    """Fill the graded columns of the real pool tracker. Blank cells only.

    THE SECOND HALF OF THE FIX. Blank-only is the right rule -- it is what
    keeps a hand-typed cell safe -- but on its own it PRESERVES FABRICATIONS.
    On the real Week-1 row, `live_line_at_post_home` holds the 2026-08-20
    provisional -3.5 while the log says the market was -3.188 at the freeze,
    and `stale_gap` says 0.0 while the true edge is +0.312. Keeping both leaves
    a row that contradicts itself with nothing pointing at it. So every
    already-populated cell is COMPARED and every disagreement is printed;
    the file is still left alone.
    """
    if not graded:
        return 0
    if not path.exists():
        print(f"\n{path} does not exist -- nothing written. It is gitignored "
              f"real pool data and this script will not create it.")
        return 0
    with path.open() as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys()) if rows else []

    # (season, week, home). Without the season, week 1 at Seattle is the same
    # key every year and the second season silently takes the first's numbers.
    by_key = {(g["season"], g["week"], g["home"]): g for g in graded}
    seasons = {g["season"] for g in graded}
    # The real tracker has 25 hand-made columns and no season among them. One
    # season in the log is unambiguous, so fall back to it; more than one is
    # genuinely ambiguous and guessing is how the wrong year gets written.
    has_season_col = any("season" in (r or {}) for r in rows)
    fallback_season = next(iter(seasons)) if len(seasons) == 1 else None
    if not has_season_col and fallback_season is None:
        print(f"\n{path.name} has no `season` column and the log spans "
              f"{sorted(seasons)}. Refusing to guess which year a row belongs "
              "to -- add a `season` column, or grade one season at a time with "
              "`--season`.")
        return 0

    mismatches: list[str] = []
    changed = 0
    for r in rows:
        home = NICK_TO_ABBR.get((r.get("home_team") or "").strip())
        try:
            week = int(r.get("week") or 0)
        except ValueError:
            continue
        raw_season = (r.get("season") or "").strip()
        try:
            season = int(raw_season) if raw_season else fallback_season
        except ValueError:
            season = fallback_season
        if season is None:
            continue
        g = by_key.get((season, week, home))
        if g is None:
            continue
        pk = g["pick"]
        new = {
            "cbs_line_home": g["cbs"],
            "live_line_at_post_home": g["post"],
            "live_line_at_lock_home": g["lock"],
            "live_lock_captured_at": g["lock_at"],
            "stale_gap": round(pk.edge_pts, 3),
            "key_number_crossed": "yes" if pk.key_number else "no",
            "model_score": round(pk.prob, 4),
            "recommendation": f"{pk.tier} {pk.side}",
            "final_away_score": int(g["away_score"]),
            "final_home_score": int(g["home_score"]),
            MODEL_RESULT_COL: g["result"],
        }
        touched = False
        for col in TRACKER_COLS:
            # A column the file does not have is not ours to invent -- except
            # MODEL_RESULT_COL, which is deliberately appended below.
            if col not in r and col != MODEL_RESULT_COL:
                continue
            if new[col] in (None, ""):
                continue
            current = (r.get(col) or "").strip()
            if not current:
                r[col] = new[col]
                touched = True
            elif not _agrees(current, new[col]):
                mismatches.append(
                    f"  MISMATCH  {season} wk{week} {g['away']}@{g['home']:<3} "
                    f"{col:<24} on file {current!r} != computed "
                    f"{new[col]!r}  (kept {current!r})")
        changed += touched

    if mismatches:
        print(f"\n{len(mismatches)} populated cell(s) disagree with what the "
              "log and the shipped model compute. Blank-only stands, so the "
              "file keeps what it has -- but a row that contradicts itself is "
              "worse than a blank one, so here they are:")
        for line in mismatches:
            print(line)
        print("  A provisional number typed before the freeze is the usual "
              "cause. Clear the cell to let the graded value in.")

    if dry_run:
        print(f"\nDRY RUN -- {path.name} untouched. {changed} row(s) would "
              "gain at least one graded cell.")
        return 0
    if not changed:
        print("\ntracker.csv already had every graded cell filled.")
        return 0
    if MODEL_RESULT_COL not in fields:
        # Appended, never inserted: the file is read by eye and by hand-written
        # spreadsheet formulas, and a column in the middle re-keys both.
        fields = fields + [MODEL_RESULT_COL]
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)
    print(f"\nwrote {changed} graded row(s) -> {path}")
    print(f"  the model's verdict went to `{MODEL_RESULT_COL}`; `ats_result` "
          "is yours and was not touched.")
    return changed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None,
                    help="grade one season only (default: every season logged)")
    ap.add_argument("--week", type=int, default=None,
                    help="grade one week only")
    ap.add_argument("--write", action="store_true",
                    help="fill the graded columns of data/pickem/tracker.csv. "
                         "Blank cells only -- your own picks and notes are "
                         "never touched. That file is gitignored real pool "
                         "data and stays out of the repo.")
    args = ap.parse_args()

    games = readings()
    if args.season is not None:
        games = [g for g in games if g["season"] == args.season]
    if args.week is not None:
        games = [g for g in games if g["week"] == args.week]

    # Only reach for the network if something could actually be graded. An
    # empty log is the normal state before the first week completes, and it
    # should print in a second without a download.
    gradeable = [g for g in games if g["cbs"] is not None and g["lock"] is not None]
    sched: dict[tuple, dict] = {}
    if gradeable:
        try:
            sched = fetch_schedule({g["season"] for g in gradeable})
        except Exception as e:                        # noqa: BLE001
            print(f"\ncould not fetch scores ({type(e).__name__}: {e}). "
                  "Nothing graded; the log is untouched.")
            return

    graded = grade(games, sched)
    summarise(games, graded)

    if graded and args.write:
        write_tracker(graded)
    elif graded:
        # Report-only pass: the disagreements are the reason to look BEFORE
        # writing, so they cannot require having written first.
        write_tracker(graded, dry_run=True)
        print("Re-run with --write to fill its graded columns.")


if __name__ == "__main__":
    main()
