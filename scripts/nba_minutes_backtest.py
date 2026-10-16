#!/usr/bin/env python3
"""Fit and test edge/nba_minutes.py: who absorbs a scratched player's minutes.

Every team-game of a season is rebuilt from what was known before tip: each
player's role from his EARLIER games only, tonight's inactive list and (in the
"confirmed" mode) tonight's starting five, from NBA.com's daily lineups
(edge/nba_lineups.py, cached in data/nba_lineups/). Projected minutes are scored
against the box score (data/nba_ground_truth/player_gamelog_<season>.json; an
active player missing from it played 0).

Fit on 2024-25, report on 2025-26, the slate (team-game) as the unit for the
paired comparisons. Three groups matter:
  * all active players;
  * teammates of a scratched regular (>= REGULAR_MIN minutes) -- the redistribution;
  * PROMOTED starters (start tonight, started < half their last games) -- the
    cheap player Adam wants to catch.

    python3 scripts/nba_minutes_backtest.py            # fit 2024-25, test 2025-26
    python3 scripts/nba_minutes_backtest.py --quick    # smaller grid
"""
from __future__ import annotations

import argparse
import itertools
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import nba_minutes as NM                    # noqa: E402
from edge.nba_lineups import CACHE, parse             # noqa: E402

GT = ROOT / "data" / "nba_ground_truth"


def build(season: str) -> list[dict]:
    """[{date, team, players: [{id, position, inactive, starter, history, actual}]}]
    in date order, histories leak-free."""
    logs = json.load(open(GT / f"player_gamelog_{season}.json"))
    pos = json.load(open(GT / "positions.json"))
    mins = {(str(r["PLAYER_ID"]), r["GAME_ID"]): float(r["MIN"] or 0) for r in logs}
    dates = sorted({r["GAME_DATE"] for r in logs})
    hist: dict = defaultdict(list)
    games = []
    for d in dates:
        path = CACHE / f"{d.replace('-', '')}.json"
        if not path.exists():
            continue
        tonight = []
        for g in parse(json.loads(path.read_text())):
            gid = g["game_id"]
            by_team = defaultdict(list)
            for p in g["players"]:
                by_team[p["team"]].append(p)
            for team, ps in by_team.items():
                rows = []
                for p in ps:
                    pid = str(p["id"])
                    actual = None if p["inactive"] else mins.get((pid, gid), 0.0)
                    rows.append({"id": pid, "position": pos.get(pid), "inactive": p["inactive"],
                                 "starter": p["starter"], "history": list(hist[pid]),
                                 "actual": actual})
                if sum(1 for r in rows if r["starter"]) != 5:
                    continue                                  # lineup not recorded properly
                tonight.append({"date": d, "team": team, "game": gid, "players": rows})
        games += tonight
        for t in tonight:                                     # history only after the day
            for r in t["players"]:
                if not r["inactive"]:
                    hist[r["id"]].append((r["actual"], r["starter"]))
    return games


def score(games: list[dict], params: dict, starters_known: bool = True) -> dict:
    err = defaultdict(list)
    for g in games:
        team = [{**p, "starter": p["starter"] if starters_known else None} for p in g["players"]]
        proj = NM.project(team, params)
        out = any(p["inactive"] and NM.role(p["history"])["base"] >= NM.REGULAR_MIN
                  for p in g["players"])
        for p in g["players"]:
            if p["inactive"] or len(p["history"]) < 5:
                continue
            e = proj[p["id"]]["minutes"] - p["actual"]
            err["all"].append(e)
            if out:
                err["teammates of a scratch"].append(e)
            r = NM.role(p["history"])
            if p["starter"] and r["start_rate"] < 0.5:
                err["promoted starters"].append(e)
                err["promoted base"].append(r["base"] - p["actual"])
    return {k: (st.mean(map(abs, v)), st.mean(v), len(v)) for k, v in err.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="2024-25")
    ap.add_argument("--test", default="2025-26")
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    train, test = build(a.train), build(a.test)
    print(f"team-games with a recorded lineup: train {len(train)}, test {len(test)}")
    grid = {"ROLE_POW": [0.5, 1.0, 1.5, 2.0], "POS_W": [0.0, 0.5, 1.0, 2.0],
            "PROMO_W": [0.0, 1.0, 3.0], "PROMO_LIFT": [0.0, 0.25, 0.5, 0.75]}
    if a.quick:
        grid = {"ROLE_POW": [1.0, 1.5], "POS_W": [0.0, 1.0], "PROMO_W": [0.0, 2.0],
                "PROMO_LIFT": [0.0, 0.5]}
    best = None
    for combo in itertools.product(*grid.values()):
        params = dict(zip(grid, combo))
        s = score(train, params)
        # The objective is the news Adam acts on: scratches and promotions.
        obj = s["teammates of a scratch"][0] + s["promoted starters"][0]
        if best is None or obj < best[0]:
            best = (obj, params)
    params = best[1]
    print("fitted on", a.train, params)
    models = {"no change (recent minutes)": {"ROLE_POW": 1.0, "POS_W": 0, "PROMO_W": 0,
                                             "PROMO_LIFT": 0, "_none": True},
              "proportional share": {"ROLE_POW": 1.0, "POS_W": 0, "PROMO_W": 0, "PROMO_LIFT": 0},
              "fitted": params}
    for mode in (True, False):
        print(f"\n== {a.test}, {'starters confirmed (30 min before tip)' if mode else 'inactives only (earlier in the day)'}")
        print(f"{'model':28} {'all':>16} {'teammates of a scratch':>24} {'promoted starters':>20}")
        for name, prm in models.items():
            if prm.get("_none"):
                # 'no change' = each player's recent minutes, nothing redistributed:
                s2 = defaultdict(list)
                for g in test:
                    out = any(p["inactive"] and NM.role(p["history"])["base"] >= NM.REGULAR_MIN
                              for p in g["players"])
                    for p in g["players"]:
                        if p["inactive"] or len(p["history"]) < 5:
                            continue
                        r = NM.role(p["history"])
                        e = r["base"] - p["actual"]
                        s2["all"].append(e)
                        if out:
                            s2["teammates of a scratch"].append(e)
                        if p["starter"] and r["start_rate"] < 0.5:
                            s2["promoted starters"].append(e)
                s = {k: (st.mean(map(abs, v)), st.mean(v), len(v)) for k, v in s2.items()}
            else:
                s = score(test, prm, starters_known=mode)
            cells = [f"{s[k][0]:5.2f} ({s[k][1]:+.1f}) n={s[k][2]}" for k in
                     ("all", "teammates of a scratch", "promoted starters")]
            print(f"{name:28} {cells[0]:>16} {cells[1]:>24} {cells[2]:>20}")
    print("\nmean |error| in minutes (bias: + = projected too many). n = player-games.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
