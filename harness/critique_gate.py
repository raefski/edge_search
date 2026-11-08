"""The unattended critique loop's ship gate: does a candidate checkout beat main, held out?

    python3 -m harness critique-gate nba <candidate checkout>     # verdict + table

The loop (harness/critique_loop.py) never takes the agent's word for an improvement.
This module runs the SAME fixed per-sport lab command in a fresh worktree of main and
in the candidate's (both built by critique_loop.worktree: identical copies of main's
untracked data), and compares the two row sets:

  - every row is one held-out unit (player-game / player-slate) with a key, a cluster
    (the night or slate: rows in one night share one realisation, CLAUDE.md), a split
    (primary = the held-out period the change is judged on; guard = a later, still
    growing period it must not hurt), an information state, `pred` and `actual`;
  - the gate computes the loss itself (squared or absolute error, per sport) and
    insists the candidate scored the SAME rows against the SAME truth: >= 99% of main's
    keys present, actuals identical. A candidate can't win by changing what it is
    scored on;
  - candidate minus main, per (split, state): mean loss difference per row with a 90%
    interval from a bootstrap over clusters (2,000 draws, seed 0).

Pass = at least one primary state better beyond noise (its interval wholly below 0, with
>= min_clusters clusters: "not enough data" below ~5 slates, CLAUDE.md) AND no state
worse -- not beyond noise (interval wholly above 0), not even in its point estimate
(an exactly unchanged state is fine: a change aimed at one information state leaves
the others' rows identical) -- AND no guard state worse by more than 1% of main's loss.
Cycle 25 (2026-11-07) is why it is not "every primary state better": the Expected-five
hedge left 2025-26 untouched and helped only the live state. Requiring every OTHER
point estimate <= 0 keeps a pure-noise change from passing on one lucky state; the
7-day forward re-gate (critique_loop) is the second check. A candidate that edited a lab's gate()
function is never auto-passed: the numbers are shown and the verdict is "review".
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

#: per sport: the command that writes gate rows to {out} (run from the checkout's root),
#: an optional `prepare` run first in BOTH checkouts (NHL's lab reads a pre-simulation
#: board cache that a build_board change only reaches after a rebuild), the file holding
#: its gate() function, the loss, and the minimum clusters per state.
#: Runtimes on the desktop (2026-11-07): nba ~10-15 min per checkout (lab load rebuilds
#: when the model source changes) + the live replay ~5 min, ncaab < 1 min, nhl a few
#: minutes (4 seeds), ncaaf ~2 min (uncached slates replay the odds store), nascar ~5 s.
#: NFL / MMA / MLB have no held-out lab with a gate() yet: their model changes go to Adam.
SPECS = {
    # 2025-26 / 2026-27 with the TRUE five or none (the lab's states), plus the live
    # state replayed: NBA.com's five + the report as of tip-45 / tip-90 (2026-27 only)
    "nba": {"cmd": [["scripts/nba_minutes_ml_fit.py", "--gate", "{out}"],
                    ["scripts/nba_expected_replay.py", "gate", "{out}"]],
            "judge": "scripts/nba_minutes_ml_fit.py", "loss": "sq", "min_clusters": 5},
    "ncaab": {"cmd": [["scripts/ncaab_lab.py", "gate", "{out}"]],
              "judge": "scripts/ncaab_lab.py", "loss": "abs", "min_clusters": 5},
    # held-out slates (on/after 10/11) rebuilt from their raw ladders by today's code;
    # truth = DK FPTS from the contest exports, by draft group
    "ncaaf": {"cmd": [["scripts/ncaaf_lab.py", "gate", "{out}"]],
              "judge": "scripts/ncaaf_lab.py", "loss": "abs", "min_clusters": 5},
    # 2025-26 Cup races, production simulate() with 2022-24-fitted tables, DK points
    "nascar": {"cmd": [["scripts/nascar_lab.py", "gate", "{out}"]],
               "judge": "scripts/nascar_lab.py", "loss": "sq", "min_clusters": 5},
    "nhl": {"cmd": [["scripts/nhl_skater_lab.py", "gate", "{out}"]],
            "prepare": ["scripts/nhl_skater_lab.py", "cache"],
            "judge": "scripts/nhl_skater_lab.py", "loss": "sq", "min_clusters": 5},
}
COVERAGE = 0.99
GUARD_REL = 0.01
N_BOOT = 2000
TIMEOUT = 3 * 3600


def rows(checkout: Path, sport: str) -> list[dict]:
    spec = SPECS[sport]
    if spec.get("prepare"):
        r = subprocess.run([sys.executable, *spec["prepare"]], cwd=checkout, capture_output=True,
                           text=True, timeout=TIMEOUT)
        if r.returncode != 0:
            raise RuntimeError(f"{sport} prepare failed in {checkout}: {(r.stderr or r.stdout)[-800:]}")
    got = []
    with tempfile.TemporaryDirectory() as d:
        for k, c in enumerate(spec["cmd"]):
            out = Path(d) / f"gate{k}.jsonl"
            cmd = [sys.executable] + [x.replace("{out}", str(out)) for x in c]
            r = subprocess.run(cmd, cwd=checkout, capture_output=True, text=True, timeout=TIMEOUT)
            if r.returncode != 0 or not out.exists():
                raise RuntimeError(f"{sport} gate rows failed in {checkout} ({c[0]}, rc {r.returncode}): "
                                   f"{(r.stderr or r.stdout)[-800:]}")
            got += [json.loads(x) for x in out.read_text().splitlines() if x.strip()]
    return got


def _gate_source(path: Path) -> str | None:
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "gate":
            return ast.dump(node)
    return None


def judge_changed(sport: str, base: Path, cand: Path) -> bool:
    if sport not in SPECS:
        return False            # no gate yet: critique_loop.judge sends model changes to Adam
    j = SPECS[sport]["judge"]
    return _gate_source(base / j) != _gate_source(cand / j)


def _loss(kind: str, pred: np.ndarray, actual: np.ndarray) -> np.ndarray:
    e = pred - actual
    return e * e if kind == "sq" else np.abs(e)


def _boot(cl: np.ndarray, d: np.ndarray, seed: int = 0) -> tuple[float, float, float]:
    """Mean of d per row, with a 90% interval resampling whole clusters."""
    ids, inv = np.unique(cl, return_inverse=True)
    s = np.bincount(inv, weights=d, minlength=len(ids))
    c = np.bincount(inv, minlength=len(ids)).astype(float)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(ids), size=(N_BOOT, len(ids)))
    est = s.sum() / c.sum()
    boots = s[pick].sum(axis=1) / c[pick].sum(axis=1)
    lo, hi = np.percentile(boots, [5, 95])
    return float(est), float(lo), float(hi)


def compare(base: list[dict], cand: list[dict], loss: str = "sq", min_clusters: int = 5) -> dict:
    """{"ok": bool, "reasons": [...], "groups": [{split, state, n, clusters, base, cand, d, lo, hi, verdict}]}"""
    b = {r["key"]: r for r in base}
    c = {r["key"]: r for r in cand}
    shared = [k for k in b if k in c]
    reasons = []
    cov = len(shared) / len(b) if b else 0.0
    if cov < COVERAGE:
        reasons.append(f"candidate scored {cov:.1%} of main's rows (< {COVERAGE:.0%})")
    bad_truth = sum(abs(b[k]["actual"] - c[k]["actual"]) > 1e-9 or b[k]["cluster"] != c[k]["cluster"]
                    or b[k]["split"] != c[k]["split"] or b[k]["state"] != c[k]["state"] for k in shared)
    if bad_truth:
        reasons.append(f"{bad_truth} rows changed truth / cluster / split / state")
    groups = []
    by = {}
    for k in shared:
        by.setdefault((b[k]["split"], b[k]["state"]), []).append(k)
    for (split, state), keys in sorted(by.items()):
        act = np.array([b[k]["actual"] for k in keys])
        lb = _loss(loss, np.array([b[k]["pred"] for k in keys]), act)
        lc = _loss(loss, np.array([c[k]["pred"] for k in keys]), act)
        cl = np.array([str(b[k]["cluster"]) for k in keys])
        d, lo, hi = _boot(cl, lc - lb)
        n_cl = len(set(cl))
        if split == "primary":
            if not np.any(lc != lb):
                verdict = "unchanged"
            elif lo > 0:
                verdict = "worse"
            elif hi < 0:
                verdict = "better" if n_cl >= min_clusters else "not enough data"
            else:
                verdict = "noise"
            if verdict == "worse" or (verdict in ("noise", "not enough data") and d > 0):
                reasons.append(f"{split} {state}: {verdict}, point estimate worse "
                               f"({d:+.4f} [{lo:+.4f}, {hi:+.4f}], {n_cl} clusters)")
        else:
            worse = lo > 0 or d > GUARD_REL * lb.mean()
            verdict = "worse" if worse else "holds"
            if worse:
                reasons.append(f"guard {state}: worse ({d:+.4f} [{lo:+.4f}, {hi:+.4f}])")
        groups.append({"split": split, "state": state, "n": len(keys), "clusters": n_cl,
                       "base": float(lb.mean()), "cand": float(lc.mean()), "d": d, "lo": lo, "hi": hi,
                       "verdict": verdict})
    if not any(g["split"] == "primary" and g["verdict"] == "better" for g in groups):
        reasons.append("no primary state better beyond noise")
    return {"ok": not reasons, "reasons": reasons, "groups": groups, "coverage": cov}


def table(res: dict, loss: str) -> str:
    unit = "RMSE" if loss == "sq" else "MAE"
    out = [f"| split | state | n | clusters | main {unit} | candidate {unit} | loss diff [90%] | verdict |",
           "|---|---|---|---|---|---|---|---|"]
    f = np.sqrt if loss == "sq" else (lambda x: x)
    for g in res["groups"]:
        out.append(f"| {g['split']} | {g['state']} | {g['n']:,} | {g['clusters']} | {f(g['base']):.4f} | "
                   f"{f(g['cand']):.4f} | {g['d']:+.4f} [{g['lo']:+.4f}, {g['hi']:+.4f}] | {g['verdict']} |")
    return "\n".join(out)


def run(sport: str, cand: Path, base: Path = ROOT) -> dict:
    """Measure both checkouts; {"verdict": "pass"|"fail"|"review", "table", "reasons", ...}."""
    spec = SPECS[sport]
    res = compare(rows(base, sport), rows(cand, sport), spec["loss"], spec["min_clusters"])
    verdict = "pass" if res["ok"] else "fail"
    if judge_changed(sport, base, cand):
        res["reasons"].append(f"candidate edited gate() in {spec['judge']}: a person decides")
        verdict = "review" if res["ok"] else "fail"
    return {**res, "verdict": verdict, "table": table(res, spec["loss"])}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in SPECS:
        print(__doc__)
        return 2
    r = run(argv[0], Path(argv[1]).resolve())
    print(r["table"])
    print(f"\nverdict: {r['verdict']}" + "".join(f"\n- {x}" for x in r["reasons"]))
    return 0 if r["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
