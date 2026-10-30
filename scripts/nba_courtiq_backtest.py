#!/usr/bin/env python3
"""Does the CourtIQ with/without table improve the minutes projection? (held out)

    python3 scripts/nba_courtiq_backtest.py

The shipped minutes model (edge/nba_minutes.project, lab terms: scripts/nba_minutes_lab.py)
re-shares a scratched regular's minutes by ROLE RANK; cycle 21 found a same-POSITION
transfer on top of it worse at every weight. This tests what pages/14 CourtIQ shows: what
THIS team actually did. For a teammate Y of a regular X who is out tonight,
    delta(Y | X) = mean minutes of Y in this team's earlier games X missed
                 - mean minutes of Y in its earlier games X dressed for
using only games BEFORE tonight (both on the team's list; Y inactive -> not counted;
X's absence = he was on the inactive list), shrunk by n_without / (n_without + K) and
summed over tonight's scratches. Projection = shipped + ALPHA x delta, then the team is
put back to its shipped total (the shift is taken from everyone in proportion to minutes),
nobody outside 0-48. ALPHA and K are chosen on 2024-25 only; 2025-26 and 2026-27 are held
out. Both information states: starters confirmed, and inactives only.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import nba_minutes_lab as L          # noqa: E402
from edge import nba_minutes as NM   # noqa: E402

P = {"alpha": 0.0, "K": 5.0}
GRID_K = (3.0, 10.0, 20.0, 40.0)
GRID_A = (0.25, 0.5, 0.75, 1.0)
if "--fine" in sys.argv:            # the second pass: heavier shrinkage, smaller weights
    GRID_K, GRID_A = (10.0, 20.0, 40.0), (0.15, 0.25, 0.4)


def stats(D: dict, R: dict, value=None) -> dict:
    """(game index, Y) -> {X: (sum_w, n_w, sum_wo, n_wo)} for tonight's scratched regulars X."""
    rows = D["rows"]
    past = defaultdict(list)                     # team -> [(date, {pid: minutes or None if inactive})]
    out = {}
    order = sorted(range(len(D["games"])), key=lambda k: D["games"][k]["date"])
    k = 0
    while k < len(order):                        # a day's games become history after the whole day
        d = D["games"][order[k]]["date"]
        day = []
        while k < len(order) and D["games"][order[k]]["date"] == d:
            day.append(order[k])
            k += 1
        for gi in day:
            g = D["games"][gi]
            scr = [rows[i]["pid"] for i in g["idx"] if rows[i]["inactive"] and R["base"][i] >= NM.REGULAR_MIN]
            if not scr:
                continue
            act = [rows[i]["pid"] for i in g["idx"] if not rows[i]["inactive"]]
            hist = past[g["team"]]
            for y in act:
                per = {}
                for x in scr:
                    sw = nw = so = no = 0.0
                    for _, m in hist:
                        if x not in m or y not in m or m[y] is None:
                            continue
                        if m[x] is None:
                            so += m[y]; no += 1
                        else:
                            sw += m[y]; nw += 1
                    if no and nw:
                        per[x] = (sw, nw, so, no)
                if per:
                    out[(gi, y)] = per
        for gi in day:
            g = D["games"][gi]
            past[g["team"]].append((d, {rows[i]["pid"]: (None if rows[i]["inactive"] else
                                                         (value(i) if value else (rows[i]["actual"] or 0.0)))
                                        for i in g["idx"]}))
    return out


def main() -> int:
    D = L.load(verbose=False)
    prm = L.final_params()
    R = L.roles(D, prm)
    # RESID=1: the table holds the shipped model's past ERRORS (actual - projected), so the
    # term only adds what the role-rank re-share has missed for this team, not the minutes
    # it already moves (raw minutes double counted them: worse at every alpha, 2026-10-29)
    if "--raw" in sys.argv:
        S = stats(D, R)
    else:
        mode0 = "inactives" if "--inactives" in sys.argv else "confirmed"
        proj = {}
        for g in D["games"]:
            proj.update(L.project_game(D, g, R, prm, mode0))
        S = stats(D, R, value=lambda i: (D["rows"][i]["actual"] or 0.0) - proj.get(i, 0.0))
    print(f"teammate-of-scratch rows with a with/without history: {len(S):,}", flush=True)
    gidx = {id(g): k for k, g in enumerate(D["games"])}
    orig = L.project_game

    def patched(D_, g, R_, prm_, mode):
        base = orig(D_, g, R_, prm_, mode)
        a = P["alpha"]
        if not a:
            return base
        gi = gidx[id(g)]
        pid = {i: D_["rows"][i]["pid"] for i in g["idx"]}
        adj = {}
        for i, m in base.items():
            per = S.get((gi, pid[i]))
            if per:
                adj[i] = a * sum((so / no - sw / nw) * no / (no + P["K"]) for sw, nw, so, no in per.values())
        if not adj:
            return base
        new = {i: min(48.0, max(0.0, m + adj.get(i, 0.0))) for i, m in base.items()}
        diff = sum(new.values()) - sum(base.values())
        tot = sum(new.values()) or 1.0
        return {i: min(48.0, max(0.0, m - diff * m / tot)) for i, m in new.items()}

    L.project_game = patched
    seasons = ("2024-25", "2025-26", "2026-27")
    sl = "teammate of a scratch"
    for mode in (("inactives",) if "--inactives" in sys.argv else ("confirmed",)) if "--raw" not in sys.argv \
            else ("confirmed", "inactives"):
        for a, K in [(0.0, 5.0)] + [(a, K) for K in GRID_K for a in GRID_A]:
            P.update(alpha=a, K=K)
            r = L.evaluate(D, prm, mode, seasons=seasons)
            cells = " | ".join(f"{r[s]['all']['rmse']:.4f} / {r[s][sl]['rmse']:.4f} ({r[s][sl]['bias']:+.2f})"
                               for s in seasons)
            print(f"| {mode} | {a} | {K:g} | {cells} |", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
