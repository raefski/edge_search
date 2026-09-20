#!/usr/bin/env python3
"""Actual-vs-predicted calibration data for NFL DK points AND ownership, by
DK position -- the NFL analog of scripts/dfs_calibration.py (see that file's
docstring for the shared parsing approach; parse_contest_file/load_contest_meta/
load_contest_type/games_for_date are reused unchanged from it, since none of
them are actually MLB-specific).

The one piece that ISN'T reused is date inference. MLB needs ground-truth
boxscore actuals to pick a contest file's true date because its roster pool
overlaps heavily day to day (scripts/dfs_calibration.py::infer_date_by_ground_truth).
NFL is weekly, so a much cheaper check suffices: match by name-overlap
against each candidate date's logged pool (a bye-week slate's pool differs
enough week to week to discriminate) and skip a file below 85% overlap
rather than guess -- this also protects against data/'s MLB contest files
matching an NFL date (or vice versa) by accident, which a shortcut that
trusted a single candidate date outright would not have caught.

Usage: python3 scripts/dfs_calibration_nfl.py   (writes data/dfs_calibration_nfl.json)
"""
import csv
import glob
import json
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.dfs import norm  # noqa: E402
from scripts.dfs_calibration import (  # noqa: E402
    parse_contest_file, load_contest_meta, load_contest_type, games_for_date,
)


def load_proj_log_nfl() -> dict:
    """-> {date: {norm_name: row_dict}} from data/dfs_proj_log_nfl.csv."""
    by_date = defaultdict(dict)
    with open(ROOT / "data/dfs_proj_log_nfl.csv", newline="") as fh:
        for row in csv.DictReader(fh):
            by_date[row["date"]][norm(row["player"])] = row
    return by_date


def infer_date_by_overlap(contest: dict, candidate_dates: list, by_date: dict):
    """Best-matching logged date for a contest file with no date of its own,
    scored by how much of the contest board's names appear in that date's
    pool. Always computed for real, even with one candidate date -- an
    unrelated (e.g. MLB) contest file must still score low and get skipped,
    not be assumed to match by default."""
    scores = {}
    for date in candidate_dates:
        pool = by_date[date]
        matched = sum(1 for key in contest if key in pool)
        scores[date] = matched / len(contest) if contest else 0.0
    best_date = max(scores, key=scores.get)
    return best_date, scores[best_date]


def main():
    by_date = load_proj_log_nfl()
    candidate_dates = sorted(by_date.keys())
    files = sorted(glob.glob(str(ROOT / "data/contest-standings-*.csv")))
    print(f"{len(files)} contest files in data/, candidate NFL dates: {candidate_dates}")

    rows = []
    for f in files:
        contest = parse_contest_file(f)
        if not contest:
            continue
        date, match_frac = infer_date_by_overlap(contest, candidate_dates, by_date)
        # 0.3 not 0.85: a big-field GPP board lists every player anyone
        # rostered, including deep bench/inactive names our built pool never
        # carried (304 board names vs 184 in the pool for 2026-09-13, 61%
        # overlap) -- that's a legitimately-matched date with an incomplete
        # pool, not a wrong date. An unrelated sport/date scores ~0% (all 21
        # MLB files here do), so 0.3 still separates the two cleanly.
        if match_frac < 0.3:
            print(f"  {Path(f).name}: no confident date match (best={date} @ {match_frac:.0%}), skipping")
            continue
        pool = by_date[date]
        games = games_for_date(list(pool.values()))
        contest_type = load_contest_type(f)
        n = 0
        for key, act in contest.items():
            proj_row = pool.get(key)
            if not proj_row:
                continue
            try:
                pred_proj = float(proj_row["proj"])
                pred_own = float(proj_row["own"]) if proj_row["own"] not in ("", None) else None
            except ValueError:
                continue
            rows.append({
                "date": date, "player": act["name"], "team": proj_row["team"],
                "dk_pos": proj_row["dk_pos"],
                "pred_proj": pred_proj, "actual_pts": act["fpts"],
                "pred_own": pred_own, "actual_own": act["pct_drafted"],
                "games": games, "contest_type": contest_type,
            })
            n += 1
        type_flag = "" if contest_type != "unknown" else "  ⚠ untagged in data/contest_meta.json"
        print(f"  {Path(f).name}: date {date} ({match_frac:.0%} overlap), {n} joined players, "
              f"{games} games, type={contest_type}{type_flag}")

    out_path = ROOT / "data/dfs_calibration_nfl.json"
    out_path.write_text(json.dumps(rows))
    print(f"\n{len(rows)} total joined rows -> {out_path}")
    print(f"dates covered: {sorted(set(r['date'] for r in rows))}")


if __name__ == "__main__":
    main()
