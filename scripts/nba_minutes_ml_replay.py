#!/usr/bin/env python3
"""python3 scripts/nba_minutes_ml_replay.py -- official-report replay (production's information state) through the PRODUCTION
nba_minutes.project, ML correction off vs on, 2025-26 held out."""
import sys
sys.path[:0] = ["/home/asr/edge_search", "/home/asr/edge_search/scripts"]
import nba_minutes_lab as L
import nba_minutes_ml_fit as FIT
from edge import nba_minutes as NM, nba_minutes_ml as ML

def prod(D, g, R, prm, mode):
    out = NM.project(FIT.team_input(D, g, mode))
    return {i: v["minutes"] for i, v in out.items()}

D = L.load(verbose=False)
F = L.final_params()
status = L.report_status(D)
L.project_game = prod
for mode in ("confirmed", "inactives"):
    ML.ENABLED = False
    p, _ = L.report_fit(L.report_preds(D, F, "2024-25", mode, status))
    a = L.report_score(L.report_preds(D, F, "2025-26", mode, status), p)
    ML.ENABLED = True
    b = L.report_score(L.report_preds(D, F, "2025-26", mode, status), p)
    print(f"official report, {mode}, 2025-26: board {a['fixed']:.3f} -> {b['fixed']:.3f}, "
          f"active {a['active']:.3f} -> {b['active']:.3f} (n {b['n_active']:,})", flush=True)
