#!/usr/bin/env python3
"""Actual-vs-predicted calibration data for DK points AND ownership, pitchers
and hitters separately -- the closed loop this project didn't have: contest
exports give DK's own real %Drafted + FPTS, dfs_proj_log.csv has our own
predicted own/proj for the same date. This script joins them.

Each DK contest-standings export interleaves two unrelated tables in one CSV:
per-entry leaderboard rows (Rank,EntryId,EntryName,...,Points,Lineup) AND, in
the same rows' trailing columns, a full field ownership board
(Player,Roster Position,%Drafted,FPTS) -- one player per row, unrelated to
that row's entry. We only want the second table.

Contest filenames are bare numeric IDs with no date. An early version of this
script guessed the date by overlap against dfs_proj_log's player pool, which
was WRONG on 3 of 8 files (that pool is incomplete -- only that day's built
slate, and MLB rosters overlap heavily day to day, so "most overlap" often
picks a neighboring date instead of the true one). This version matches
against GROUND TRUTH instead: each candidate date's REAL boxscore-derived DK
points (via scripts.dfs_grade.actuals_for_date, cached to disk) should be
near-identical to the contest file's own FPTS column for the true date, and
wildly different for every wrong date -- a much stronger signal.

Usage: python3 scripts/dfs_calibration.py   (writes data/dfs_calibration.json)
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
from scripts.dfs_grade import actuals_for_date  # noqa: E402

ACTUALS_CACHE_DIR = ROOT / "data/actuals_cache"
CONTEST_META_PATH = ROOT / "data/contest_meta.json"


def load_contest_type(path) -> str:
    """'cash' | 'gpp' | 'unknown', from data/contest_meta.json keyed by the
    numeric contest id in the filename (contest-standings-<id>.csv). No
    programmatic way to detect this from the export itself (DK's standings
    CSV carries no contest-type/entry-fee field -- see dfs_roi_backtest.py's
    docstring on the same limitation), so this is a small manually-maintained
    manifest. Added 2026-07-11 per user request: cash-game fields concentrate
    ownership differently than GPP fields (no incentive to differentiate), so
    ownership-gamma fitting (dfs_ownership_gamma_sweep.py) should only use GPP
    dates. Untagged files default to 'unknown' and are excluded from gamma
    fitting rather than silently assumed to be GPP."""
    meta = json.loads(CONTEST_META_PATH.read_text()) if CONTEST_META_PATH.exists() else {}
    cid = Path(path).stem.replace("contest-standings-", "")
    return meta.get(cid, "unknown")


def games_for_date(pool_rows: list[dict]) -> int | None:
    """Slate size (game count) for one date's logged pool. Prefers the 'games'
    column log_forward_test started writing 2026-07-11 (DK's own declared
    GameCount for the resolved draft group -- exact); falls back to distinct
    team count / 2 for rows logged before that column existed (a same-slate
    proxy, off by one per doubleheader and undercounts a known-partial pool
    like 7/2's -- good enough to bucket by slate size, not exact)."""
    for r in pool_rows:
        g = r.get("games")
        if g:
            try:
                return int(g)
            except ValueError:
                pass
    teams = {r["team"] for r in pool_rows if r.get("team")}
    return len(teams) // 2 if teams else None


def parse_contest_file(path):
    """-> {norm_name: {"name":, "pct_drafted":, "fpts":}} from the ownership
    board embedded in a DK contest-standings export (ignores leaderboard rows
    with no Player/%Drafted/FPTS)."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("Player") or "").strip()
            pct = (row.get("%Drafted") or "").strip()
            fpts = (row.get("FPTS") or "").strip()
            if not name or not pct.endswith("%"):
                continue
            try:
                pct_val = float(pct.rstrip("%"))
                fpts_val = float(fpts)
            except ValueError:
                continue
            out[norm(name)] = {"name": name, "pct_drafted": pct_val, "fpts": fpts_val}
    return out


def load_proj_log():
    """-> {date: {norm_name: row_dict}}"""
    by_date = defaultdict(dict)
    with open(ROOT / "data/dfs_proj_log.csv", newline="") as fh:
        for row in csv.DictReader(fh):
            by_date[row["date"]][norm(row["player"])] = row
    return by_date


def cached_actuals(date):
    ACTUALS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = ACTUALS_CACHE_DIR / f"{date}.json"
    if p.exists():
        return json.loads(p.read_text())
    act, finals = actuals_for_date(date)
    # only persist a COMPLETE slate -- a cache written mid-slate (see the
    # 2026-07-08 stub: 1 final game, 25 players) would silently serve partial
    # actuals forever after. Incomplete slates are returned but not cached.
    from scripts.dfs_grade import date_all_final
    if date_all_final(date):
        p.write_text(json.dumps(act))
    return act


def infer_date_by_ground_truth(contest, candidate_dates, tol=0.3):
    """Score each candidate date by how many contest-board players have a
    near-identical REAL actual DK score on that date. The true date should
    score near 100%; wrong dates should collapse fast (different games,
    different outcomes)."""
    scores = {}
    for date in candidate_dates:
        act = cached_actuals(date)
        matched = checked = 0
        for key, row in contest.items():
            real = act.get(key)
            if real is None:
                continue
            checked += 1
            if abs(real - row["fpts"]) < tol:
                matched += 1
        scores[date] = (matched / checked if checked else 0.0, checked)
    best_date = max(scores, key=lambda d: scores[d][0])
    return best_date, scores[best_date][0], scores


def main():
    by_date = load_proj_log()
    candidate_dates = sorted(by_date.keys())
    files = sorted(glob.glob(str(ROOT / "data/contest-standings-*.csv")))
    print(f"{len(files)} contest files, candidate dates: {candidate_dates}")

    rows = []
    for f in files:
        contest = parse_contest_file(f)
        if not contest:
            print(f"  {Path(f).name}: no ownership board found, skipping")
            continue
        date, match_frac, scores = infer_date_by_ground_truth(contest, candidate_dates)
        runner_up = sorted(scores.items(), key=lambda kv: -kv[1][0])[1] if len(scores) > 1 else (None, (0, 0))
        if match_frac < 0.85:
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
            pos = proj_row["pos"]
            is_pitcher = pos.split("/")[0] == "P" or "P" in pos.split("/")
            try:
                pred_proj = float(proj_row["proj"])
                pred_own = float(proj_row["own"]) if proj_row["own"] not in ("", None) else None
            except ValueError:
                continue
            rows.append({
                "date": date, "player": act["name"], "team": proj_row["team"], "is_pitcher": is_pitcher,
                "pred_proj": pred_proj, "actual_pts": act["fpts"],
                "pred_own": pred_own, "actual_own": act["pct_drafted"],
                "games": games, "contest_type": contest_type,
            })
            n += 1
        type_flag = "" if contest_type != "unknown" else "  ⚠ untagged in data/contest_meta.json"
        print(f"  {Path(f).name}: date {date} ({match_frac:.0%} ground-truth match, "
              f"runner-up {runner_up[0]} @ {runner_up[1][0]:.0%}), {n} joined players, "
              f"{games} games, type={contest_type}{type_flag}")

    out_path = ROOT / "data/dfs_calibration.json"
    out_path.write_text(json.dumps(rows))
    print(f"\n{len(rows)} total joined rows -> {out_path}")
    print(f"dates covered: {sorted(set(r['date'] for r in rows))}")


if __name__ == "__main__":
    main()
