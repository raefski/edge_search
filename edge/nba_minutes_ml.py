"""Gradient-boosted correction of edge/nba_minutes.project (overnight review 2026-10-30).

WHY
Twenty-three critique cycles each tested one hand-built term on the minutes model, and
the last several moved held-out RMSE by ~0.01. A gradient-boosted model given the
shipped projection plus the role pieces, recent minutes and team context finds what
the hand terms miss, mostly at once: confirmed-five RMSE 6.273 -> ~6.13 held out
2025-26 (numbers: NBA_MINUTES_CRITIQUE_LOG.md "Overnight 2026-10-30").

HOW
`features()` is called from inside nba_minutes.project with that function's own
internals (role_v2 output, the adjusted role, the shipped minutes, the history it was
given), so the features the model was trained on and the ones it sees live come from
ONE code path -- scripts/nba_minutes_ml_fit.py trains by calling project() itself on
leak-free rebuilt nights. Two models: "confirmed" (the five is known) and "inactives"
(it is not -- the starter / promoted flags are then unknown and never used).
Prediction is clipped to 0-48 and each team is rescaled back to the shipped total
(the 240 bookkeeping stays the shipped model's job).

The trees are stored as plain arrays (edge/nba_minutes_ml_trees.npz, written by the fit
script) and walked with numpy: no scikit-learn at run time, no pickle tied to a version.
Missing file -> the shipped model unchanged, and LAST_SOURCE says so.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

TREES = Path(__file__).with_name("nba_minutes_ml_trees.npz")
#: Off switch (and what the fit script uses to get the shipped baseline).
ENABLED = True
#: "ml" when the model was applied on the last call, else why not -- shown on the page.
LAST_SOURCE = "not run"
#: The fit script sets a list here: every call appends (mode, {id: features}).
CAPTURE: list | None = None

FEATURES = ("ship", "adj", "base", "srate", "ms", "mb", "ns", "nb", "starter", "promoted",
            "last1", "last1_start", "l3", "l5", "l10", "sd10", "max5", "st5",
            "season_games", "nba_games", "since_return", "pick",
            "scratch_load", "scratch_sr", "scratch_any", "act_roles", "act_sr", "n_act", "psum",
            "rk_base", "rk_ship", "pos_g", "pos_f", "pos_c")
NAN = float("nan")


def features(team: list, out: dict, roles: dict, rinfo: dict, regular_min: float) -> dict:
    """{id: [feature values in FEATURES order]} for the active players in `out`.
    `roles` = project's adjusted roles, `rinfo` = its role_v2 dicts, `out` its result."""
    confirmed = any(pl.get("starter") is not None for pl in team)
    act = [pl for pl in team if not pl["inactive"] and pl["id"] in out]
    ina = [pl for pl in team if pl["inactive"]]
    scratch_load = sum(roles.get(pl["id"], 0.0) for pl in ina)
    scratch_sr = sum(rinfo[pl["id"]]["start_rate"] for pl in ina if pl["id"] in rinfo)
    scratch_any = float(any(roles.get(pl["id"], 0.0) >= regular_min for pl in ina))
    act_roles = sum(rinfo[pl["id"]]["base"] for pl in act)
    act_sr = sum(rinfo[pl["id"]]["start_rate"] for pl in act)
    psum = sum(out[pl["id"]]["minutes"] for pl in act)
    rk_b = {pl["id"]: k for k, pl in enumerate(sorted(act, key=lambda p: -rinfo[p["id"]]["base"]))}
    rk_s = {pl["id"]: k for k, pl in enumerate(sorted(act, key=lambda p: -out[p["id"]]["minutes"]))}
    feats = {}
    for pl in act:
        i, r = pl["id"], rinfo[pl["id"]]
        n_pre = int(pl.get("n_pre") or 0)
        hist = pl["history"][: len(pl["history"]) - n_pre] if n_pre else pl["history"]
        m = [float(x[0]) for x in hist]
        s = [float(bool(x[1])) for x in hist]

        def mean(xs):
            return sum(xs) / len(xs) if xs else NAN

        sd10 = float(np.std(m[-10:])) if len(m) >= 3 else NAN
        pos = pl.get("position") or ""
        starter = (float(bool(pl.get("starter"))) if confirmed else NAN)
        promoted = (float(bool(pl.get("starter")) and r["start_rate"] < 0.5) if confirmed else NAN)
        sr = pl.get("since_return")
        feats[i] = [out[i]["minutes"], roles.get(i, 0.0), r["base"], r["start_rate"], r["ms"], r["mb"],
                    r["ns"], r["nb"], starter, promoted,
                    m[-1] if m else NAN, s[-1] if s else NAN, mean(m[-3:]), mean(m[-5:]), mean(m[-10:]), sd10,
                    max(m[-5:]) if m else NAN, mean(s[-5:]),
                    float(pl["cur_n"]) if pl.get("cur_n") is not None else float(len(m)), float(len(m)),
                    float(sr) if sr is not None else -1.0, float(pl.get("pick") or 70),
                    scratch_load, scratch_sr, scratch_any, act_roles, act_sr, float(len(act)), psum,
                    float(rk_b[i]), float(rk_s[i]),
                    float("G" in pos), float("F" in pos), float("C" in pos)]
    return feats


_MODELS: dict | None = None


def _load() -> dict:
    global _MODELS
    if _MODELS is None:
        _MODELS = {}
        if TREES.exists():
            z = np.load(TREES)
            for mode in ("confirmed", "inactives"):
                if f"{mode}_feature" in z:
                    _MODELS[mode] = {k: z[f"{mode}_{k}"] for k in
                                     ("feature", "threshold", "left", "right", "leaf", "value", "nan_left",
                                      "roots", "baseline")}
    return _MODELS


def predict(mode: str, X: np.ndarray) -> np.ndarray | None:
    """Walk every tree for every row of X (rows = players). None if no model."""
    t = _load().get(mode)
    if t is None:
        return None
    pred = np.full(len(X), float(t["baseline"]))
    rows = np.arange(len(X))
    for root in t["roots"]:
        node = np.full(len(X), root)
        while True:
            live = ~t["leaf"][node]
            if not live.any():
                break
            n = node[live]
            x = X[rows[live], t["feature"][n]]
            go_left = np.where(np.isnan(x), t["nan_left"][n], x <= t["threshold"][n])
            node[live] = np.where(go_left, t["left"][n], t["right"][n])
        pred += t["value"][node]
    return pred


def adjust(team: list, out: dict, roles: dict, rinfo: dict, regular_min: float) -> dict:
    """The shipped `out` with each active player's minutes replaced by the model's,
    the team rescaled to the shipped total. Unchanged when disabled or no model."""
    global LAST_SOURCE
    if not ENABLED and CAPTURE is None:
        LAST_SOURCE = "off"
        return out
    mode = "confirmed" if any(pl.get("starter") is not None for pl in team) else "inactives"
    F = features(team, out, roles, rinfo, regular_min)
    if CAPTURE is not None:
        CAPTURE.append((mode, F))
    if not ENABLED:
        LAST_SOURCE = "off"
        return out
    if not F:
        return out
    ids = list(F)
    p = predict(mode, np.array([F[i] for i in ids], dtype=float))
    if p is None:
        LAST_SOURCE = f"no model file ({TREES.name}): shipped minutes"
        return out
    p = np.clip(p, 0.0, 48.0)
    ship = sum(out[i]["minutes"] for i in ids)
    k = ship / p.sum() if p.sum() > 0 else 1.0
    LAST_SOURCE = "ml"
    new = dict(out)
    for i, v in zip(ids, p):
        new[i] = {**out[i], "minutes": round(min(48.0, float(v) * k), 1), "ship_minutes": out[i]["minutes"]}
    return new


def export(models: dict, path: Path = TREES) -> None:
    """Write fitted sklearn HistGradientBoostingRegressors ({mode: model}) as arrays."""
    arrays = {}
    for mode, m in models.items():
        feat, thr, left, right, leaf, val, nanl, roots = [], [], [], [], [], [], [], []
        off = 0
        for it in m._predictors:
            nodes = it[0].nodes
            roots.append(off)
            feat += nodes["feature_idx"].tolist()
            thr += nodes["num_threshold"].tolist()
            left += (nodes["left"] + off).tolist()
            right += (nodes["right"] + off).tolist()
            leaf += nodes["is_leaf"].astype(bool).tolist()
            val += nodes["value"].tolist()
            nanl += nodes["missing_go_to_left"].astype(bool).tolist()
            off += len(nodes)
        arrays.update({f"{mode}_feature": np.array(feat, np.int32), f"{mode}_threshold": np.array(thr),
                       f"{mode}_left": np.array(left, np.int32), f"{mode}_right": np.array(right, np.int32),
                       f"{mode}_leaf": np.array(leaf, bool), f"{mode}_value": np.array(val),
                       f"{mode}_nan_left": np.array(nanl, bool), f"{mode}_roots": np.array(roots, np.int64),
                       f"{mode}_baseline": np.array(float(np.ravel(m._baseline_prediction)[0]))})
    np.savez_compressed(path, **arrays)
    global _MODELS
    _MODELS = None
