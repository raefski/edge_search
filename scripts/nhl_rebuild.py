#!/usr/bin/env python3
"""Rebuild a past NHL slate as it stood at lock, and log it for calibration.

    python3 scripts/nhl_rebuild.py 154305 154311          # log each slate
    python3 scripts/nhl_rebuild.py 154690 --at 2026-10-07T23:21 --no-log

Phone builds on Streamlit Cloud never reach data/dfs_proj_log_nhl.csv, so a
night built only on the phone leaves nothing for scripts/nhl_calibration.py to
grade -- it then grades against whatever other night IS logged, silently.
Every input the build reads survives, so the night can be reconstructed:

    DK draftables            data/draftables_snapshot/<gid>.json, last commit before lock
    odds                     data/odds.db, last ok dfs_nhl scan before lock
    lines + starting goalies data/nhl_lines_snapshot.json, last commit before lock

Logged rows carry source=rebuild. Same seed as the live build (0).
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs, dfs_opt_nhl, nhl  # noqa: E402
from edge import dfs_run_nhl as R  # noqa: E402
from edge.odds.profiles import for_sport  # noqa: E402
from edge.odds.source import ScrapedOddsClient  # noqa: E402
from edge.odds.store import OddsStore  # noqa: E402


def utc(t: str) -> str:
    """'2026-10-07T23:21' or '...Z' -> '2026-10-07T23:21:00Z'; git reads a bare time as LOCAL."""
    t = t.rstrip("Z")
    return (t + ":00" if len(t) == 16 else t) + "Z"


def git_json(path: str, before: str | None):
    """(commit, parsed file) from the last commit touching `path` before `before` (UTC)."""
    cut = [f"--before={utc(before)}"] if before else []
    h = subprocess.run(["git", "log", "--all", "-1", "--diff-filter=AM", *cut, "--pretty=%h %cI",
                        "--", path],
                       cwd=ROOT, capture_output=True, text=True).stdout.split()
    if not h:
        raise SystemExit(f"no commit of {path} before {before}")
    raw = subprocess.run(["git", "show", f"{h[0]}:{path}"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    return h, json.loads(raw)


def lock_time(gid: int) -> str:
    _, rows = git_json(f"data/draftables_snapshot/{gid}.json", None)
    starts = sorted(r["competition"]["startTime"] for r in rows if r.get("competition"))
    return starts[0][:19] + "Z"


def scan_before(at: str, profile: str) -> tuple[int, str]:
    db = sqlite3.connect(ROOT / "data" / "odds.db")
    row = db.execute("SELECT id, finished_at FROM scan WHERE ok=1 AND profile=? AND finished_at < ?"
                     " ORDER BY finished_at DESC LIMIT 1", (profile, at.rstrip("Z"))).fetchone()
    if not row:
        raise SystemExit(f"no {profile} scan before {at}")
    return row


def rebuild(gid: int, at: str | None, n_sims: int, iters: int):
    lock = lock_time(gid)
    at = utc(at or lock)
    d_commit, draftables = git_json(f"data/draftables_snapshot/{gid}.json", at)
    l_commit, lines = git_json("data/nhl_lines_snapshot.json", at)
    prof = for_sport(R.SPORT, "dfs")
    scan_id, scan_at = scan_before(at, prof.name)

    def offline(url):
        raise RuntimeError("rebuild: DailyFaceoff pinned to the snapshot")
    dfs._draftables_raw = lambda g: draftables
    nhl._next_data = offline
    nhl._snapshot = lambda: lines

    client = ScrapedOddsClient(OddsStore(), prof.name, scan_id=scan_id,
                               main_line_only=not prof.full_ladders)
    meta = {"gid": gid, "start": lock, "label": "rebuild"}
    pool, sim, info = R.build_board(gid, meta, client, n_sims=n_sims, seed=0)
    cash = dfs_opt_nhl.optimize(pool, sim, mode="cash", iters=iters, seed=0)
    gpp = dfs_opt_nhl.optimize(pool, sim, mode="gpp", iters=iters, seed=0)
    print(f"{gid} {info['date']} lock {lock}: {info['games']} games, {len(pool)} players, "
          f"{info['imputed']} estimated | scan {scan_id} ({scan_at[:16]}) | draftables "
          f"{d_commit[0]} ({d_commit[1][:16]}) | lines {l_commit[0]} ({l_commit[1][:16]})")
    if info["missing_lines"]:
        print(f"   no game line for {info['missing_lines']}")
    missing_g = sorted({p["team"] for p in pool} - {p["team"] for p in pool if p["pos"] == "G"})
    if missing_g:
        print(f"   no starting goalie for {missing_g}")
    for mode, res in (("cash", cash), ("gpp", gpp)):
        names = ", ".join(p["name"] for _, p in res.get("slots", []))
        print(f"   {mode:4}: {names}")
    return pool, info


def log(pool, gid, date):
    plog = ROOT / "data" / "dfs_proj_log_nhl.csv"
    prior = list(csv.DictReader(plog.open())) if plog.exists() else []
    live = [r for r in prior if r["date"] == date and str(r["gid"]) == str(gid)
            and r.get("source", "live") != "rebuild"]
    if live:
        print(f"   {date} {gid} already has a live build in the log; not replacing it")
        return
    rows = [r for r in prior if not (r["date"] == date and str(r["gid"]) == str(gid))]
    rows += [{"date": date, "gid": gid, "player": p["name"], "team": p["team"], "opp": p.get("opp"),
              "dk_pos": p.get("dk_pos"), "line": p.get("line") or "", "pp": p.get("pp") or "",
              "salary": p["salary"], "proj": p["proj"], "sd": p["sd"], "floor": p["floor"],
              "ceil": p["ceil"], "own": p.get("own", ""), "imputed": "|".join(p.get("imputed", [])),
              "source": "rebuild"} for p in pool]
    with plog.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=R.PROJ_LOG_COLS, restval="")
        w.writeheader()
        w.writerows(rows)
    print(f"   logged {len(pool)} players as source=rebuild")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("gids", nargs="+", type=int)
    ap.add_argument("--at", help="pin inputs before this UTC time instead of lock (YYYY-MM-DDTHH:MM)")
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--iters", type=int, default=150)
    ap.add_argument("--no-log", action="store_true")
    args = ap.parse_args()
    for gid in args.gids:
        pool, info = rebuild(gid, args.at, args.sims, args.iters)
        if not args.no_log:
            log(pool, gid, info["date"])


if __name__ == "__main__":
    main()
