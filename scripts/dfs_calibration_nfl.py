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
# edge.names.norm, NOT edge.dfs.norm: the contest board is keyed with the
# suffix-free one (edge/dfs_contest.py), and keying the log with the other
# silently dropped every "Jr."/"III" player from calibration -- 11 on the
# 2026-09-13 GPP alone, up to 9.8% owned (Luther Burden III).
from edge.names import norm  # noqa: E402
from scripts.dfs_calibration import (  # noqa: E402
    parse_contest_file, load_contest_meta, load_contest_type, games_for_date,
)


def load_proj_log_nfl() -> tuple[dict, dict]:
    """-> ({date: {norm_name: row}}, {gid: {norm_name: row}}) from
    data/dfs_proj_log_nfl.csv. Several slates share a date (10/11 logged Main,
    Early, Turbo, Afternoon and Sun-Mon), so the by-date view mixes them; it is
    only the fallback for an export DK has not tagged with its draft group."""
    by_date, by_gid = defaultdict(dict), defaultdict(dict)
    with open(ROOT / "data/dfs_proj_log_nfl.csv", newline="") as fh:
        for row in csv.DictReader(fh):
            by_date[row["date"]][norm(row["player"])] = row
            if row.get("gid"):
                by_gid[str(row["gid"])][norm(row["player"])] = row
    return by_date, by_gid


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
    by_date, by_gid = load_proj_log_nfl()
    candidate_dates = sorted(by_date.keys())
    meta = json.loads((ROOT / "data/contest_meta.json").read_text())
    meta = {k: v for k, v in meta.items() if isinstance(v, dict)}
    date_of_gid = {g: next(iter(rows.values()))["date"] for g, rows in by_gid.items()}
    files = sorted(glob.glob(str(ROOT / "data/contest-standings-*.csv")))
    print(f"{len(files)} contest files in data/, candidate NFL dates: {candidate_dates}")

    rows = []
    for f in files:
        contest = parse_contest_file(f)
        if not contest:
            continue
        tag = meta.get(Path(f).stem.replace("contest-standings-", "")) or {}
        if tag.get("sport") and tag["sport"] != "NFL":
            continue
        gid = str(tag.get("draft_group") or "")
        if gid:
            # Tagged by DK: grade against exactly that slate's log, or not at
            # all. Name overlap put 9/27 Primetime and 10/4 exports on 10/11.
            if gid not in by_gid:
                print(f"  {Path(f).name}: draft group {gid} has no pre-lock log, skipping")
                continue
            pool = by_gid[gid]
            date = date_of_gid[gid]
            match_frac = sum(1 for k in contest if k in pool) / len(contest)
            print(f"  {Path(f).name}: matched by draft group {gid}")
        else:
            date, match_frac = infer_date_by_overlap(contest, candidate_dates, by_date)
            print(f"  {Path(f).name}: UNTAGGED -- matched by name overlap; "
                  "run `python3 -m harness run import-contests` to tag it")
        # 0.3 not 0.85: a big-field GPP board lists every player anyone
        # rostered, including deep bench/inactive names our built pool never
        # carried (304 board names vs 184 in the pool for 2026-09-13, 61%
        # overlap) -- that's a legitimately-matched date with an incomplete
        # pool, not a wrong date. An unrelated sport/date scores ~0% (all 21
        # MLB files here do), so 0.3 still separates the two cleanly.
        if match_frac < 0.3:
            print(f"  {Path(f).name}: no confident date match (best={date} @ {match_frac:.0%}), skipping")
            continue
        if not gid:
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
