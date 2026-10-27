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
from edge import nba_injury_report as IR                 # noqa: E402
from edge.names import norm                              # noqa: E402

GT = ROOT / "data" / "nba_ground_truth"


def build(season: str) -> list[dict]:
    logs = json.load(open(GT / f"player_gamelog_{season}.json"))
    pos = json.load(open(GT / "positions.json"))
    box = {(str(r["PLAYER_ID"]), r["GAME_ID"]): r for r in logs}
    hist: dict = defaultdict(list)
    allowed: dict = defaultdict(list)        # team -> [(opp dk pts, opp minutes)] per game
    played_last: dict = {}                   # team -> ids who played its previous game
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
            # Live fidelity (critique P1, 2026-10-27): the build reads the official
            # injury report; a non-starter listed Out at tip is inactive even when
            # NBA.com's list calls him active (840-1,060 player-games a season, all
            # 0 minutes). Without this the harness under-projected the teammates of
            # exactly the absences this target is about. His 0-minute games stay out
            # of history (the cycle 8 rule).
            rep = IR.out_at_tip(g["game_id"])
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
                    phantom = bool(rep) and not p["inactive"] and not p["starter"] and norm(p["name"]) in rep
                    rows.append({"id": pid, "name": p["name"], "position": pos.get(pid),
                                 "inactive": p["inactive"] or phantom, "phantom": phantom,
                                 "starter": p["starter"], "history": list(hist[pid]),
                                 "min": float(r["MIN"] or 0) if r else 0.0,
                                 "pts": actual_points(r) if r else 0.0})
                prev = played_last.get(team, set())
                for x in rows:
                    x["new_out"] = x["inactive"] and x["id"] in prev
                tonight.append({"team": team, "opp": opp, "opp_pm": opp_pm, "league_pm": league_pm,
                                "rows": rows, "game": g["game_id"], "date": d})
        out += tonight
        for t in tonight:
            got = [(r["pts"], r["min"]) for r in t["rows"]
                   if not r["inactive"] or (r["phantom"] and r["min"] > 0)]
            allowed[t["opp"]].append((sum(a for a, _ in got), sum(m for _, m in got)))
            played_last[t["team"]] = {r["id"] for r in t["rows"] if r["min"] > 0}
            for r in t["rows"]:
                if not r["inactive"] or (r["phantom"] and r["min"] > 0):
                    hist[r["id"]].append((r["min"], r["starter"], r["pts"]))
    return out


#: Vacated production (critique target 2026-10-27, "promoted starters"): the sum of
#: the last-10 mean DK points of each teammate who is inactive TONIGHT but played the
#: team's previous game -- the news, not the long-term injured the minutes history
#: already reflects. From games before tonight only. The
#: slices are the nights a rotation player (or two) is out -- 10/26 OKC lost Jalen
#: Williams, Holmgren and Wallace (~80 pts/game) and Ajay Mitchell scored 42 on a
#: 26.6 projection.
VACATED_SLICES = ((None, None, "a. none new out"), (None, 20, "b. new out <20"), (20, None, "c. new out 20+"))


def vacated(rows) -> float:
    return sum(st.mean([p for _, _, p in r["history"][-10:]]) for r in rows
               if r.get("new_out") and len(r["history"]) >= 5)


def evaluate(games, decay, opp_pow, use_actual_minutes=False, slices=False):
    pred, act, last10, season = [], [], [], []
    tags = []
    for t in games:
        team = [{"id": r["id"], "position": r["position"], "inactive": r["inactive"],
                 "starter": r["starter"], "history": [(m, s) for m, s, _ in r["history"]]}
                for r in t["rows"]]
        mins = NM.project(team)
        of = NP.opponent_factor(t["opp_pm"], t["league_pm"], opp_pow)
        vac = vacated(t["rows"])
        vtag = (VACATED_SLICES[0][2] if not any(r.get("new_out") for r in t["rows"])
                else VACATED_SLICES[1][2] if vac < 20 else VACATED_SLICES[2][2])
        for r in t["rows"]:
            if r["inactive"] or len(r["history"]) < 5:
                continue
            m = r["min"] if use_actual_minutes else mins[r["id"]]["minutes"]
            pred.append(m * NP.rate(r["history"], r["position"], decay) * of)
            act.append(r["pts"])
            h = [p for _, _, p in r["history"]]
            last10.append(st.mean(h[-10:]))
            season.append(st.mean(h))
            tags.append((vtag, "starter" if r["starter"] else "bench"))
    a = np.array(act)
    def m(x):
        x = np.array(x)
        return {"mae": round(float(np.abs(x - a).mean()), 3), "corr": round(float(np.corrcoef(x, a)[0, 1]), 4),
                "bias": round(float((x - a).mean()), 2)}
    out = {"model": m(pred), "last10": m(last10), "season": m(season), "n": len(a)}
    if slices:
        P = np.array(pred)
        out["slices"] = {}
        for key in sorted(set(tags)) + [(lab, "all") for _, _, lab in VACATED_SLICES]:
            sel = np.array([t == key or (key[1] == "all" and t[0] == key[0]) for t in tags])
            e = P[sel] - a[sel]
            out["slices"][key] = {"n": int(sel.sum()), "mae": round(float(np.abs(e).mean()), 3),
                                  "rmse": round(float(np.sqrt((e ** 2).mean())), 3),
                                  "bias": round(float(e.mean()), 2)}
    return out


def slice_table(r, ra) -> str:
    lines = ["| slice | role | n | MAE | RMSE | bias | rate x ACTUAL min: MAE | bias |",
             "|---|---|---|---|---|---|---|---|"]
    for key, v in sorted(r["slices"].items()):
        w = ra["slices"][key]
        lines.append(f"| {key[0]} | {key[1]} | {v['n']} | {v['mae']:.3f} | {v['rmse']:.3f} | {v['bias']:+.2f} | "
                     f"{w['mae']:.3f} | {w['bias']:+.2f} |")
    return "\n".join(lines)


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
    r = evaluate(test, decay, opp_pow, slices=True)
    ra = evaluate(test, decay, opp_pow, use_actual_minutes=True, slices=True)
    print(f"2025-26 (n={r['n']} player-games):")
    for k in ("model", "last10", "season"):
        print(f"  {k:24} MAE {r[k]['mae']:.3f}  corr {r[k]['corr']:.4f}  bias {r[k]['bias']:+.2f}")
    print(f"  {'rate x ACTUAL minutes':24} MAE {ra['model']['mae']:.3f}  corr {ra['model']['corr']:.4f}"
          "   (the ceiling a perfect minutes model reaches)")
    print("\nBy vacated production (inactive teammates' last-10 DK pts) and role, 2025-26:")
    print(slice_table(r, ra))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
