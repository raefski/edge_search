#!/usr/bin/env python3
"""ROI/rank backtest: where would OUR built lineups actually have finished in
the REAL contest, and how do they compare to the lineup the user actually
entered? This is the test the methodology doc was missing entirely --
projection accuracy and ownership calibration say nothing about whether
construction has ever beaten the field or the rake.

Reuses the ground-truth date-matching from dfs_calibration.py so contest
files map to dates the same way for both pipelines.

HONEST LIMITATION: DK's standings export has Rank + Points per entry but NOT
payout amounts or entry fee -- dollar ROI isn't computable from this file
alone. This reports rank/percentile in the real field instead, which is the
part that's actually verifiable from what we have.

Usage: python3 scripts/dfs_roi_backtest.py
"""
import csv
import glob
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.dfs import norm  # noqa: E402
from scripts.dfs_calibration import parse_contest_file, load_proj_log, infer_date_by_ground_truth  # noqa: E402


def parse_leaderboard(path):
    """-> [(rank:int, entry_name:str, points:float)], full field, every entry."""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            rank, pts = row.get("Rank"), row.get("Points")
            if not rank or not pts:
                continue
            try:
                out.append((int(rank), row.get("EntryName", ""), float(pts)))
            except ValueError:
                continue
    return out


def load_our_lineup(date):
    """-> {"cash": [(player, salary)], "gpp": [...]} from data/dfs_lineups_<date>.csv"""
    p = ROOT / f"data/dfs_lineups_{date}.csv"
    if not p.exists():
        return None
    out = {"cash": [], "gpp": []}
    for row in csv.DictReader(open(p)):
        mode = row.get("mode")
        if mode in out:
            out[mode].append(row["player"])
    return out


def rank_for_score(score, leaderboard):
    """Where would this score place? (rank, percentile, field_size)"""
    field = len(leaderboard)
    beat_by = sum(1 for _, _, pts in leaderboard if pts > score)
    rank = beat_by + 1
    percentile = 100 * (1 - beat_by / field) if field else None
    return rank, percentile, field


def main():
    by_date = load_proj_log()
    candidate_dates = sorted(by_date.keys())
    files = sorted(glob.glob(str(ROOT / "data/contest-standings-*.csv")))

    print(f"{'date':12} {'mode':5} {'our score':>10} {'user score':>11} {'our rank':>10} {'our pctile':>11} {'field':>7}")
    print("-" * 75)

    for f in files:
        contest = parse_contest_file(f)
        if not contest:
            continue
        date, match_frac, _ = infer_date_by_ground_truth(contest, candidate_dates)
        if match_frac < 0.85:
            continue

        leaderboard = parse_leaderboard(f)
        our = load_our_lineup(date)
        if not our:
            print(f"{date:12} -- no logged lineup file for this date, skipping")
            continue

        # the user's own actual entry: find their real score among the leaderboard.
        # DK appends " (n/m)" multi-entry suffixes to EntryName -- match by prefix.
        user_scores = [pts for rank, name, pts in leaderboard if name.split(" (")[0] == "gorillabiscuit"]
        user_best = max(user_scores) if user_scores else None

        for mode in ("cash", "gpp"):
            names = our[mode]
            if not names:
                continue
            act = load_proj_log_actuals(date)
            score = sum(act.get(norm(n), 0.0) for n in names)
            rank, pctile, field = rank_for_score(score, leaderboard)
            user_str = f"{user_best:.2f}" if user_best is not None else "n/a"
            print(f"{date:12} {mode:5} {score:>10.2f} {user_str:>11} {rank:>10} {pctile:>10.1f}% {field:>7}")


_actuals_cache = {}


def load_proj_log_actuals(date):
    """Real DK points per player for `date`, via the same cached actuals used
    by the calibration pipeline (data/actuals_cache/<date>.json)."""
    if date not in _actuals_cache:
        import json
        p = ROOT / f"data/actuals_cache/{date}.json"
        if p.exists():
            raw = json.loads(p.read_text())
        else:
            from scripts.dfs_grade import actuals_for_date
            raw, _ = actuals_for_date(date)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(raw))
        _actuals_cache[date] = raw
    return _actuals_cache[date]


if __name__ == "__main__":
    main()
