#!/usr/bin/env python3
"""Log Polymarket's NFL spread beside every pick'em line reading, and grade it.

    python3 scripts/pickem_polymarket.py log [--push]   # backfill missing readings
    python3 scripts/pickem_polymarket.py report         # shipped make_pick: books vs Polymarket

`log` adds one row to data/pickem_polymarket_log.csv for every
(season, week, home, snapshot) in data/pickem_line_log.csv that lacks one,
read at that row's own captured_at -- the same instant the books were read,
so the two are comparable -- plus a `deadline-1m` row once the game's pick
deadline (edge.pickem_deadline) has passed. Polymarket keeps its full price
history, so a missed run loses nothing: the next one backfills. That is why
this is a separate file and not a column in the append-only line log.

Not used by any pick. Why, and the decision rule: edge/pickem_polymarket.py.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from edge import pickem_polymarket as pm                      # noqa: E402
from edge.pickem import ats_result, make_pick                 # noqa: E402

LOG = ROOT / "data" / "pickem_polymarket_log.csv"
FIELDS = ["season", "week", "snapshot", "read_at", "away_team", "home_team",
          "pm_line_home", "n_rungs", "logged_at"]
DEADLINE_LABEL = "deadline-1m"


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_log(path: Path = LOG) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def wanted_readings() -> list[dict]:
    """Every (game, instant) the books were read at, plus each passed deadline."""
    import pickem_grade as pg
    from edge.pickem_log import load
    from edge.pickem_deadline import deadlines_from_rows
    rows = load(pg.LINE_LOG)
    deadlines = deadlines_from_rows(rows)
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    out, seen = [], set()
    for r in rows:
        if not r.get("captured_at") or not r.get("kickoff_utc"):
            continue
        base = {"season": r["season"], "week": r["week"], "away_team": r["away_team"],
                "home_team": r["home_team"], "kickoff": pm.parse_ts(r["kickoff_utc"])}
        out.append(dict(base, snapshot=r["snapshot"], read_at=pm.parse_ts(r["captured_at"])))
        key = (int(r["season"]), int(r["week"]), r["home_team"])
        dl = deadlines.get(key)
        if dl is not None and key not in seen and dl.timestamp() < now:
            seen.add(key)
            out.append(dict(base, snapshot=DEADLINE_LABEL, read_at=dl.timestamp() - 60))
    return out


def cmd_log(push: bool) -> int:
    have = {(r["season"], r["week"], r["home_team"], r["snapshot"]) for r in load_log()}
    todo = [w for w in wanted_readings()
            if (w["season"], w["week"], w["home_team"], w["snapshot"]) not in have]
    if not todo:
        print("polymarket: nothing to backfill")
        return 0
    # Gamma's start_date is when the MARKET opened, not kickoff: week 1 2026
    # games were listed 2026-08-04, a month early. Look back well past that.
    since = _iso(min(w["kickoff"] for w in todo) - 120 * 86400)
    # Gamma's default listing omits closed games; past readings need them.
    events = pm.list_games(since, closed=True) + pm.list_games(since, closed=False)
    games = {}
    for e in events:
        teams, kick, rungs = pm.game_rungs(e)
        if kick and rungs and None not in teams:
            games.setdefault(frozenset(teams), []).append((kick, e.get("closed", False), rungs))
    new, misses = [], 0
    for w in todo:
        cands = [g for g in games.get(frozenset({w["home_team"], w["away_team"]}), [])
                 if abs(g[0] - w["kickoff"]) < 86400]
        if not cands:
            misses += 1
            continue
        kick, closed, rungs = cands[0]
        for r in rungs:
            if "hist" not in r:
                r["hist"] = pm.history(r["token"], kick, closed)
        line, n = pm.fair_home_line(w["home_team"], rungs, w["read_at"])
        if line is None and w["read_at"] > kick - 3600 and not closed:
            continue            # live game with no quote yet -- try again next run
        new.append({"season": w["season"], "week": w["week"], "snapshot": w["snapshot"],
                    "read_at": _iso(w["read_at"]), "away_team": w["away_team"],
                    "home_team": w["home_team"],
                    "pm_line_home": "" if line is None else line, "n_rungs": n,
                    "logged_at": _iso(dt.datetime.now(dt.timezone.utc).timestamp())})
    fresh = not LOG.exists()
    with LOG.open("a", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=FIELDS)
        if fresh:
            wr.writeheader()
        wr.writerows(new)
    print(f"polymarket: logged {len(new)} reading(s), {sum(r['pm_line_home'] == '' for r in new)} "
          f"without a line; {misses} with no Polymarket game -> {LOG.name}")
    if push and new:
        from edge.data_branch import publish_one
        if not publish_one(LOG, "pickem polymarket readings"):
            return 1
    return 0


def cmd_report() -> int:
    import pickem_grade as pg
    log = {(int(r["season"]), int(r["week"]), r["home_team"], r["snapshot"]): r for r in load_log()}
    games = pg.readings()
    sched = pg.fetch_schedule({g["season"] for g in games})
    rec = {k: [0, 0, 0] for k in ("book", "pm_same", "pm_deadline")}
    changed = {k: [0, 0, 0] for k in ("pm_same", "pm_deadline")}
    diffs = []
    for g in pg.grade(games, sched):
        key = (g["season"], g["week"], g["home"])
        same = log.get(key + (g["lock_label"],))
        dead = log.get(key + (DEADLINE_LABEL,))
        lines = {"book": g["lock"],
                 "pm_same": pg._f(same["pm_line_home"]) if same else None,
                 "pm_deadline": pg._f(dead["pm_line_home"]) if dead else None}
        if lines["pm_same"] is None:
            continue            # score only games every column can read
        diffs.append(lines["pm_same"] - g["lock"])
        margin = g["home_score"] - g["away_score"]
        book_side = g["pick"].side
        for col, live in lines.items():
            if live is None:
                continue
            side = make_pick(g["away"], g["home"], g["cbs"], live,
                             g["total_open"], g["total_close"]).side
            res = ats_result(margin, g["cbs"], side)
            i = "WLP".index(res)
            rec[col][i] += 1
            if col != "book" and side != book_side:
                changed[col][i] += 1
    if not diffs:
        print("no graded game has a Polymarket reading yet -- run `log` first")
        return 0
    print(f"{len(diffs)} graded games with a Polymarket reading at the lock instant")
    print(f"PM - books at that instant: mean {sum(diffs)/len(diffs):+.3f}, "
          f"mean |diff| {sum(map(abs, diffs))/len(diffs):.3f}, >=0.5 pt: {sum(abs(d) >= 0.5 for d in diffs)}")
    for col, (w, l, p) in rec.items():
        lo, hi = pg.wilson(w, l)
        extra = ""
        if col in changed:
            cw, cl, cp = changed[col]
            extra = f"   picks that differ from books: {cw}-{cl}-{cp}"
        print(f"  {col:12s} {w}-{l}-{p}  {w / max(w + l, 1):.1%}  [{lo:.1%}, {hi:.1%}]{extra}")
    print("Decision rule (edge/pickem_polymarket.py): 30+ differing picks before acting.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("log", "report"))
    ap.add_argument("--push", action="store_true", help="publish the log to the data branch")
    a = ap.parse_args()
    return cmd_log(a.push) if a.cmd == "log" else cmd_report()


if __name__ == "__main__":
    sys.exit(main())
