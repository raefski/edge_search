#!/usr/bin/env python3
"""Fit edge/nba_minutes_ml.py: the gradient-boosted minutes correction (overnight 2026-10-30).

    python3 scripts/nba_minutes_ml_fit.py            # fit 2024-25, report 2025-26 / 2026-27
    python3 scripts/nba_minutes_ml_fit.py --write    # ...and write edge/nba_minutes_ml_trees.npz

Every team-game comes from the minutes lab (scripts/nba_minutes_lab.py: histories from
earlier games only, tonight's inactives and starters from NBA.com's lineups), turned into
the dicts the LIVE build hands edge/nba_minutes.project (history [(min, started)] with
trailing preseason entries for a player with < 5 real games, cur_n, n_pre, since_return,
pick). project() itself runs with the model off and nba_minutes_ml.CAPTURE on, so the
training features are exactly what production computes. Two states: "confirmed" (the
five is known) and "inactives" (starter = None). Fit on 2024-25 only; hyperparameters
from a time-ordered split inside 2024-25 (the ML-minutes study: confirmed lr 0.03 /
15 leaves / min leaf 50 / 486 iters, inactives lr 0.03 / 31 / 200 / 384). Scored as
production scores it: clipped 0-48, each team rescaled to the shipped total.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import nba_minutes_lab as L                        # noqa: E402
from edge import nba_minutes as NM                 # noqa: E402
from edge import nba_minutes_ml as ML              # noqa: E402

HP = {"confirmed": dict(learning_rate=0.03, max_leaf_nodes=15, min_samples_leaf=50, l2_regularization=1.0,
                        max_iter=486),
      "inactives": dict(learning_rate=0.03, max_leaf_nodes=31, min_samples_leaf=200, l2_regularization=1.0,
                        max_iter=384)}
PRESEASON_IF = 5


def team_input(D: dict, g: dict, mode: str) -> list:
    rows, M, S = D["rows"], D["M"], D["S"]
    team = []
    for i in g["idx"]:
        r = rows[i]
        ok = ~np.isnan(M[i])
        hist = [(float(m), bool(s)) for m, s in zip(M[i][ok], S[i][ok])]
        n_pre = 0
        if r["real_pos_games"] < PRESEASON_IF and r["pre"]:
            hist += [(float(m) * NM.__dict__.get("PRESEASON_MIN_RATIO", 0.66), False) for m in r["pre"]]
            n_pre = len(r["pre"])
        team.append({"id": i, "position": r["pos"], "inactive": r["inactive"],
                     "starter": bool(r["starter"]) if mode == "confirmed" else None,
                     "history": hist, "cur_n": r["season_games"], "n_pre": n_pre,
                     "since_return": r["since_return"], "pick": r["pick"]})
    return team


def collect(D: dict) -> dict:
    """{mode: list of (season, game key, ids, X, actual)}"""
    out = defaultdict(list)
    rows = D["rows"]
    for k, g in enumerate(D["games"]):
        for mode in ("confirmed", "inactives"):
            ML.CAPTURE = []
            NM.project(team_input(D, g, mode), {"ML": True})
            cap = ML.CAPTURE
            ML.CAPTURE = None
            # the five-blend calls project twice (hard / soft): the hard call is the one
            # whose state matches the mode; take the last capture of that mode
            F = next((f for m, f in reversed(cap) if m == mode), None)
            if not F:
                continue
            ids = [i for i in F if rows[i]["actual"] is not None]
            if ids:
                out[mode].append((g["season"], k, ids, np.array([F[i] for i in ids]),
                                  np.array([rows[i]["actual"] for i in ids], float)))
    return out


def stack(parts, season):
    P = [p for p in parts if p[0] == season]
    return P, (np.vstack([p[3] for p in P]) if P else None), (np.concatenate([p[4] for p in P]) if P else None)


def score(P, model=None):
    e = []
    for _, _, ids, X, y in P:
        ship = X[:, 0]
        if model is None:
            p = ship
        else:
            p = np.clip(model.predict(X), 0, 48)
            p = p * ship.sum() / p.sum() if p.sum() > 0 else ship
        e.append(p - y)
    e = np.concatenate(e)
    return f"{np.sqrt((e ** 2).mean()):.4f} / {np.abs(e).mean():.3f} / {e.mean():+.2f} (n {len(e):,})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    from sklearn.ensemble import HistGradientBoostingRegressor as HGB
    ML.ENABLED = False
    D = L.load(verbose=False)
    parts = collect(D)
    models = {}
    print("| state | season | shipped RMSE / MAE / bias | + ML |\n|---|---|---|---|")
    for mode in ("confirmed", "inactives"):
        _, X, y = stack(parts[mode], "2024-25")
        m = HGB(random_state=0, early_stopping=False, **HP[mode]).fit(X, y)
        models[mode] = m
        for s in ("2024-25", "2025-26", "2026-27"):
            P, _, _ = stack(parts[mode], s)
            if P:
                print(f"| {mode} | {s}{' (fit)' if s == '2024-25' else ''} | {score(P)} | {score(P, m)} |", flush=True)
    if a.write:
        ML.export(models)
        # parity: production project() with the written trees == this script's prediction
        ML.ENABLED = True
        bad = 0
        for season, k, ids, X, y in parts["confirmed"][-200:]:
            g = D["games"][k]
            live = NM.project(team_input(D, g, "confirmed"))
            p = np.clip(models["confirmed"].predict(X), 0, 48)
            p = p * X[:, 0].sum() / p.sum()
            bad += sum(abs(live[i]["minutes"] - round(v, 1)) > 0.11 for i, v in zip(ids, p))
        print(f"wrote {ML.TREES.relative_to(ROOT)}; parity check (last 200 confirmed team-games): "
              f"{bad} players differ by > 0.1 min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
