#!/usr/bin/env python3
"""Fit NBA GPP ownership (edge/dfs_nba_theory.add_ownership) on DK contest exports.

    python3 scripts/nba_ownership_fit.py            # rebuild every NBA GPP night, fit, hold out nights
    python3 scripts/nba_ownership_fit.py --show     # also print the worst-missed players per night

No NBA contest night had a logged projection: the build shipped 2026-10-15 and the
exports in data/contests.db are from 2025-26 (12 GPPs, 11 nights). So each night is
rebuilt as it stood:

  salaries   DK's draftables for the contest's own draft group (api.draftkings.com
             still serves past groups), cached in data/cache/nba_draftables/<gid>.json,
             one call 1.5 s apart, once
  history    scripts/nba_history_publish.build(before=<date>): only games before
             that night count
  news       NBA.com's daily lineups for that date (data/nba_lineups/): the day's
             FINAL file, so starters for late games are as confirmed at their tip
             -- mild hindsight for a build made at the first lock
  lines      none (no 2025-26 NBA odds were collected): implied totals off

-- then add_ownership is scored against the export's real %Drafted (summed over
roster slots, 800% a contest). Weights are fitted by grid search, leave one NIGHT
out (fit on the rest, score the held-out night), on mean absolute error. GPP only:
cash and GPP ownership are never pooled (CLAUDE.md).
"""
from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs, dfs_nba_theory as T, dfs_run_nba as R, nba_lineups  # noqa: E402
from edge.names import norm  # noqa: E402
from scripts import nba_history_publish as H  # noqa: E402
from scripts.contest_stats import spearman  # noqa: E402

CACHE = ROOT / "data" / "cache" / "nba_draftables"
GRID_V = (0.4, 0.7, 1.0, 1.3, 1.6, 2.0)
GRID_S = (0.0, 0.2, 0.4, 0.6, 0.9, 1.2)
GRID_P = (0.0, 0.6, 1.2)


def season_of(d: str) -> str:
    y = int(d[:4])
    start = y if d[5:7] >= "08" else y - 1
    return f"{start}-{str(start + 1)[2:]}"


def draftables(gid: int) -> list:
    path = CACHE / f"{gid}.json"
    if path.exists():
        return json.loads(path.read_text())
    CACHE.mkdir(parents=True, exist_ok=True)
    time.sleep(1.5)                       # DRAFTKINGS_ACCESS.md: >= 1 s between ad-hoc lookups
    rows = dfs._get(f"https://api.draftkings.com/draftgroups/v1/draftgroups/{gid}/draftables").get("draftables", [])
    path.write_text(json.dumps(rows))
    return rows


def rebuild(gid: int, date: str) -> list:
    rows = draftables(gid)
    hist = H.build(before=date, current=season_of(date))
    raw = nba_lineups.fetch(date)                       # cached day file
    status = {}
    for g in nba_lineups.parse(raw):
        for p in g["players"]:
            status[(norm(p["name"]), p["team"])] = {"starter": p["starter"], "confirmed": p["confirmed"],
                                                    "inactive": p["inactive"], "id": str(p["id"])}
    real = (dfs._draftables_raw, R.load_history, R.tonight_status)
    dfs._draftables_raw = lambda g: rows
    R.load_history = lambda: hist
    R.tonight_status = lambda d: status
    try:
        starts = sorted(r["competition"]["startTime"] for r in rows if r.get("competition"))
        pool, _ = R.build_board(gid, {"start": starts[0] if starts else None}, None)
    finally:
        dfs._draftables_raw, R.load_history, R.tonight_status = real
    return pool


def score(pool: list, actual: dict, w: tuple) -> tuple[float, float, int]:
    T.add_ownership(pool, weights=w[:2]) if len(w) == 2 else _own(pool, w)
    pairs = [(p["own"], actual[norm(p["name"])]) for p in pool if norm(p["name"]) in actual]
    if not pairs:
        return float("nan"), float("nan"), 0
    pred, act = zip(*pairs)
    return statistics.fmean(abs(a - b) for a, b in pairs), spearman(pred, act), len(pairs)


def _own(pool: list, w: tuple) -> None:
    saved = T.PROMOTED_BONUS
    T.PROMOTED_BONUS = w[2]
    try:
        T.add_ownership(pool, weights=w[:2])
    finally:
        T.PROMOTED_BONUS = saved


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    conn = sqlite3.connect(ROOT / "data" / "contests.db")
    cons = conn.execute("SELECT contest_id, draft_group, slate_date, live_entries FROM contest "
                        "WHERE sport='NBA' AND type='gpp' AND final=1 ORDER BY slate_date").fetchall()
    nights = []
    for cid, gid, d, field in cons:
        try:
            pool = rebuild(int(gid), d)
        except Exception as exc:  # noqa: BLE001 -- one night that cannot be rebuilt is reported, not fatal
            print(f"{cid} {d}: rebuild failed ({exc})", file=sys.stderr)
            continue
        actual = dict(conn.execute("SELECT player_norm, pct_drafted FROM player_result WHERE contest_id=?", (cid,)))
        matched = sum(1 for p in pool if norm(p["name"]) in actual)
        own_cov = sum(actual.get(norm(p["name"]), 0) for p in pool) / 8
        print(f"{cid} {d} gid {gid}: {len(pool)} projected, {matched} matched, "
              f"{own_cov:.0f}% of the field's ownership covered, field {field:,}", file=sys.stderr)
        nights.append({"cid": cid, "date": d, "field": field, "pool": pool, "actual": actual})
    if len(nights) < 2:
        print("not enough nights")
        return 1
    prior = (T.VALUE_WEIGHT, T.SALARY_WEIGHT, T.PROMOTED_BONUS)
    grid = list(itertools.product(GRID_V, GRID_S, GRID_P))
    table = {w: [score(n["pool"], n["actual"], w) for n in nights] for w in grid + [prior]}
    by_date = sorted({n["date"] for n in nights})
    held, picks = [], []
    for d in by_date:
        train = [i for i, n in enumerate(nights) if n["date"] != d]
        best = min(grid, key=lambda w: statistics.fmean(table[w][i][0] for i in train))
        picks.append(best)
        for i, n in enumerate(nights):
            if n["date"] == d:
                held.append((i, best))
    out = [f"# NBA GPP ownership fit: {len(nights)} contests, {len(by_date)} nights "
           f"({nights[0]['date']} .. {nights[-1]['date']}), leave one night out\n",
           "| | MAE | rank corr | contests better than prior |", "|---|---|---|---|"]
    pm = [table[prior][i][0] for i in range(len(nights))]
    pr = [table[prior][i][1] for i in range(len(nights))]
    hm = [table[w][i][0] for i, w in held]
    hr = [table[w][i][1] for i, w in held]
    out.append(f"| prior {prior} | {statistics.fmean(pm):.3f} | {statistics.fmean(pr):+.3f} | |")
    out.append(f"| held-out fit | **{statistics.fmean(hm):.3f}** | **{statistics.fmean(hr):+.3f}** | "
               f"{sum(1 for (i, _), h in zip(held, hm) if h < pm[i])}/{len(held)} |")
    full = min(grid, key=lambda w: statistics.fmean(table[w][i][0] for i in range(len(nights))))
    from collections import Counter
    out.append(f"\nFolds' picks: {Counter(picks).most_common(3)}; all nights: {full} "
               f"(MAE {statistics.fmean(table[full][i][0] for i in range(len(nights))):.3f})\n")
    out += ["| night | contest | field | prior MAE | held-out MAE | held-out weights |", "|---|---|---|---|---|---|"]
    for (i, w), h in zip(held, hm):
        n = nights[i]
        out.append(f"| {n['date']} | {n['cid']} | {n['field']:,} | {pm[i]:.2f} | {h:.2f} | {w} |")
    if a.show:
        for n in nights:
            _own(n["pool"], full)
            miss = sorted(((p["own"] - n["actual"].get(norm(p["name"]), 0), p["name"], p["own"],
                            n["actual"].get(norm(p["name"]), 0)) for p in n["pool"]), key=lambda z: -abs(z[0]))[:5]
            out.append(f"\n{n['date']}: " + "; ".join(f"{nm} {o:.0f}% vs {r:.0f}%" for _, nm, o, r in miss))
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
