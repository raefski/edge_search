#!/usr/bin/env python3
"""Does YouTube buzz improve the NFL ownership model? Held-out test.

    python3 scripts/buzz_fit_nfl.py [--type gpp|cash] [--feature mentions]

The field model in edge/dfs_nfl_theory.py::add_ownership is a softmax over
points per dollar within each position. This rebuilds it from the logged
pool (data/dfs_proj_log_nfl.csv), adds buzz as a second term

    weight = exp(gamma * z(value) + beta * z(log1p(buzz)))

and scores both against real contest ownership (data/dfs_calibration_nfl.json,
from scripts/dfs_calibration_nfl.py). Each slate is scored with gamma and beta
fitted on the OTHER slates only, so a win here is out of sample. Cash and GPP
are fitted separately and never pooled (small-field cash concentrates far
harder; see DFS_STATUS.md).

A pool player missing from a contest's ownership board was rostered by nobody
and counts as 0%.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.names import norm  # noqa: E402
from edge import dfs_nfl_theory as theory  # noqa: E402
from edge.dfs_nfl_theory import OWNERSHIP_GAMMA, SLOTS_BY_POSITION  # noqa: E402

FEATURES = ("mentions", "videos", "channels", "reach")
GAMMAS = [round(0.1 * i, 1) for i in range(2, 31)]
BETAS = [round(0.1 * i, 1) for i in range(0, 26)]


def load_slates(contest_type: str, feature: str) -> dict:
    """{date: {position: [player dict with value, buzz, actual]}} for every
    date that has a pool, a contest of this type, and buzz."""
    pools = defaultdict(list)
    with (ROOT / "data/dfs_proj_log_nfl.csv").open(newline="") as fh:
        for r in csv.DictReader(fh):
            pools[r["date"]].append(r)
    actual = defaultdict(dict)
    for r in json.loads((ROOT / "data/dfs_calibration_nfl.json").read_text()):
        if r["contest_type"] == contest_type:
            actual[r["date"]][norm(r["player"])] = r["actual_own"]
    buzz = defaultdict(dict)
    buzz_path = ROOT / "data/buzz_nfl.csv"
    if buzz_path.exists():
        with buzz_path.open(newline="") as fh:
            for r in csv.DictReader(fh):
                buzz[r["date"]][norm(r["player"])] = float(r[feature])

    slates = {}
    for date in sorted(set(pools) & set(actual) & set(buzz)):
        by_pos = defaultdict(list)
        for r in pools[date]:
            salary = float(r["salary"] or 0)
            if not salary:
                continue
            k = norm(r["player"])
            by_pos[r["dk_pos"]].append({
                "player": r["player"], "pos": r["dk_pos"],
                "salary": salary, "proj": float(r["proj"] or 0),
                "logged_own": float(r["own"]) if r["own"] else None,
                "buzz": buzz[date].get(k, 0.0),
                "actual": actual[date].get(k, 0.0),
            })
        slates[date] = dict(by_pos)
    return slates


def predict(slate: dict, gamma: float, beta: float) -> list[tuple[dict, float]]:
    """The shipped model (theory.add_ownership) at these constants."""
    out = []
    for players in slate.values():
        pool = [{"dk_pos": p["pos"], "salary": p["salary"], "proj": p["proj"],
                 "buzz": p["buzz"]} for p in players]
        theory.add_ownership(pool, gamma=gamma, buzz_beta=beta)
        out += [(p, q["own"]) for p, q in zip(players, pool)]
    return out


def mae(pairs) -> float:
    return sum(abs(p["actual"] - own) for p, own in pairs) / len(pairs)


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2
        i = j + 1
    return ranks


def corr(a, b) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a) ** 0.5
    vb = sum((y - mb) ** 2 for y in b) ** 0.5
    return cov / (va * vb) if va and vb else 0.0


def spearman(pairs) -> float:
    return corr(_ranks([p["actual"] for p, _ in pairs]), _ranks([o for _, o in pairs]))


def fit(slates: list[dict], with_buzz: bool) -> tuple[float, float]:
    best = None
    for g in GAMMAS:
        for b in (BETAS if with_buzz else [0.0]):
            err = sum(mae(predict(s, g, b)) for s in slates)
            if best is None or err < best[0]:
                best = (err, g, b)
    return best[1], best[2]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--type", default="gpp", choices=("gpp", "cash"))
    ap.add_argument("--feature", default="mentions", choices=FEATURES)
    args = ap.parse_args()

    slates = load_slates(args.type, args.feature)
    if len(slates) < 2:
        raise SystemExit(f"need 2+ slates with a {args.type} contest AND buzz; have {sorted(slates)}")
    print(f"{args.type.upper()} ownership, buzz feature = {args.feature}, slates {sorted(slates)}\n")

    for date, s in slates.items():
        pairs = predict(s, OWNERSHIP_GAMMA, 0.0)
        logged = [(p, own) for p, own in pairs if p["logged_own"] is not None]
        drift = sum(abs(p["logged_own"] - own) for p, own in logged) / max(len(logged), 1)
        print(f"  {date}: rebuilt production model vs logged own, mean |diff| {drift:.2f} pts")

    print(f"\n{'held out':12} {'model':22} {'gamma':>5} {'beta':>5} {'MAE':>6} {'rank corr':>9}")
    totals = defaultdict(list)
    by_pos = defaultdict(lambda: defaultdict(list))
    for held in slates:
        train = [s for d, s in slates.items() if d != held]
        runs = [("production (unfitted)", OWNERSHIP_GAMMA, 0.0),
                ("value only, refit", *fit(train, False)),
                ("value + buzz, refit", *fit(train, True))]
        for label, g, b in runs:
            pairs = predict(slates[held], g, b)
            m, rc = mae(pairs), spearman(pairs)
            totals[label].append((m, rc))
            for p, own in pairs:
                by_pos[label][p["pos"]].append(abs(p["actual"] - own))
            print(f"{held:12} {label:22} {g:5.1f} {b:5.1f} {m:6.2f} {rc:9.3f}")

    print(f"\n{'average':12} {'model':22} {'MAE':>6} {'rank corr':>9}   MAE by position")
    positions = sorted(SLOTS_BY_POSITION)
    for label, rows in totals.items():
        m = sum(r[0] for r in rows) / len(rows)
        rc = sum(r[1] for r in rows) / len(rows)
        pos_txt = "  ".join(f"{p} {sum(v) / len(v):.2f}" for p in positions
                            if (v := by_pos[label][p]))
        print(f"{'':12} {label:22} {m:6.2f} {rc:9.3f}   {pos_txt}")

    all_slates = list(slates.values())
    g, b = fit(all_slates, True)
    print(f"\nfitted on all {len(all_slates)} slates: gamma {g}, beta {b}")
    last = sorted(slates)[-1]
    base = {p["player"]: own for p, own in predict(slates[last], *fit(all_slates, False))}
    moved = sorted(((p, own, base[p["player"]]) for p, own in predict(slates[last], g, b)),
                   key=lambda t: -abs(t[1] - t[2]))[:15]
    print(f"\nbiggest moves from buzz on {last}:")
    print(f"  {'player':24} {'pos':4} {args.feature:>8} {'value-only':>10} {'+buzz':>6} {'actual':>7}")
    for p, own, b0 in moved:
        print(f"  {p['player'][:24]:24} {p['pos']:4} {p['buzz']:8.0f} {b0:10.1f} {own:6.1f} {p['actual']:7.1f}")


if __name__ == "__main__":
    main()
