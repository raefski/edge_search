#!/usr/bin/env python3
"""Fit and test edge/nba_project.py: DK points = tonight's minutes x points per minute.

Every team-game is rebuilt leak-free (histories from EARLIER games only, tonight's
starters and inactives from NBA.com's daily lineups, data/nba_lineups/). Fit the
rate decay and the opponent power on 2024-25; report on 2025-26 against two naive
baselines -- his last-10 average and his season-to-date average -- and against the
same rate with the minutes he ACTUALLY played (the ceiling a perfect minutes model
would reach), so the two halves of the error are visible.

    python3 scripts/nba_projection_backtest.py
"""
from __future__ import annotations

import itertools
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import nba_minutes as NM, nba_project as NP     # noqa: E402
from edge.nba import actual_points                        # noqa: E402
from edge.nba_lineups import CACHE, parse                 # noqa: E402

GT = ROOT / "data" / "nba_ground_truth"


def build(season: str) -> list[dict]:
    logs = json.load(open(GT / f"player_gamelog_{season}.json"))
    pos = json.load(open(GT / "positions.json"))
    box = {(str(r["PLAYER_ID"]), r["GAME_ID"]): r for r in logs}
    hist: dict = defaultdict(list)
    allowed: dict = defaultdict(list)        # team -> [(opp dk pts, opp minutes)] per game
    out = []
    for d in sorted({r["GAME_DATE"] for r in logs}):
        path = CACHE / f"{d.replace('-', '')}.json"
        if not path.exists():
            continue
        tonight = []
        for g in parse(json.loads(path.read_text())):
            teams = defaultdict(list)
            for p in g["players"]:
                teams[p["team"]].append(p)
            if len(teams) != 2:
                continue
            league = [x for v in allowed.values() for x in v[-NP.OPP_WINDOW:]]
            league_pm = sum(a for a, _ in league) / max(1, sum(m for _, m in league)) if league else None
            for team, ps in teams.items():
                opp = next(t for t in teams if t != team)
                if sum(1 for p in ps if p["starter"]) != 5:
                    continue
                al = allowed[opp][-NP.OPP_WINDOW:]
                opp_pm = sum(a for a, _ in al) / max(1, sum(m for _, m in al)) if len(al) >= 5 else None
                rows = []
                for p in ps:
                    pid = str(p["id"])
                    r = box.get((pid, g["game_id"]))
                    rows.append({"id": pid, "name": p["name"], "position": pos.get(pid), "inactive": p["inactive"],
                                 "starter": p["starter"], "history": list(hist[pid]),
                                 "min": float(r["MIN"] or 0) if r else 0.0,
                                 "pts": actual_points(r) if r else 0.0})
                tonight.append({"team": team, "opp": opp, "opp_pm": opp_pm, "league_pm": league_pm,
                                "rows": rows, "game": g["game_id"], "date": d})
        out += tonight
        for t in tonight:
            got = [(r["pts"], r["min"]) for r in t["rows"] if not r["inactive"]]
            allowed[t["opp"]].append((sum(a for a, _ in got), sum(m for _, m in got)))
            for r in t["rows"]:
                if not r["inactive"]:
                    hist[r["id"]].append((r["min"], r["starter"], r["pts"]))
    return out


def evaluate(games, decay, opp_pow, use_actual_minutes=False):
    pred, act, last10, season = [], [], [], []
    for t in games:
        team = [{"id": r["id"], "position": r["position"], "inactive": r["inactive"],
                 "starter": r["starter"], "history": [(m, s) for m, s, _ in r["history"]]}
                for r in t["rows"]]
        mins = NM.project(team)
        of = NP.opponent_factor(t["opp_pm"], t["league_pm"], opp_pow)
        for r in t["rows"]:
            if r["inactive"] or len(r["history"]) < 5:
                continue
            m = r["min"] if use_actual_minutes else mins[r["id"]]["minutes"]
            pred.append(m * NP.rate(r["history"], r["position"], decay) * of)
            act.append(r["pts"])
            h = [p for _, _, p in r["history"]]
            last10.append(st.mean(h[-10:]))
            season.append(st.mean(h))
    a = np.array(act)
    def m(x):
        x = np.array(x)
        return {"mae": round(float(np.abs(x - a).mean()), 3), "corr": round(float(np.corrcoef(x, a)[0, 1]), 4),
                "bias": round(float((x - a).mean()), 2)}
    return {"model": m(pred), "last10": m(last10), "season": m(season), "n": len(a)}


def main() -> int:
    train, test = build("2024-25"), build("2025-26")
    print(f"team-games: train {len(train)}, test {len(test)}")
    best = None
    for decay, opp_pow in itertools.product((0.85, 0.9, 0.95, 0.98, 1.0), (0.0, 0.5, 1.0)):
        r = evaluate(train, decay, opp_pow)["model"]
        print(f"  train decay={decay} opp_pow={opp_pow}: MAE {r['mae']} corr {r['corr']}")
        if best is None or r["mae"] < best[0]:
            best = (r["mae"], decay, opp_pow)
    _, decay, opp_pow = best
    print(f"\nfitted on 2024-25: decay={decay} opp_pow={opp_pow}")
    r = evaluate(test, decay, opp_pow)
    ra = evaluate(test, decay, opp_pow, use_actual_minutes=True)
    print(f"2025-26 (n={r['n']} player-games):")
    for k in ("model", "last10", "season"):
        print(f"  {k:24} MAE {r[k]['mae']:.3f}  corr {r[k]['corr']:.4f}  bias {r[k]['bias']:+.2f}")
    print(f"  {'rate x ACTUAL minutes':24} MAE {ra['model']['mae']:.3f}  corr {ra['model']['corr']:.4f}"
          "   (the ceiling a perfect minutes model reaches)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
