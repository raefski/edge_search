#!/usr/bin/env python3
"""Does the simulated NFL GPP field look like the real one? Weekly check.

    python3 scripts/nfl_field_calibration.py            # every graded NFL GPP export
    python3 scripts/nfl_field_calibration.py --last 4   # the 4 most recent

WHY
edge/dfs_nfl_contest.py ranks our GPP lineups against a SIMULATED field
(ownership draws, FIELD_STACK_1/2 stacking, a SHARP_SHARE tilt toward the
projected top) and turns finishes into dollars with a fitted tail. A sim can be
internally consistent and still systematically wrong about the field, so each
real export is compared, with the PRODUCTION constants, on what the sim claims:

  * stack1 / stack2 -- share of lineups whose QB has >= 1 / >= 2 own WR/TE;
  * proj p50 / p99  -- the field's lineup projections (OUR logged projections,
    so this measures construction, not projection error);
  * dup             -- share of lineups that exactly duplicate another, both
    measured on a 2,000-lineup sample so the sizes match;
  * PIT p50 / p99 / max -- where the REAL field's realised score quantile (and
    winning score) falls among 3,000 simulated worlds of the sim's field, the
    max extrapolated to the real field size by the same tail the ranking uses.
    Over many slates a calibrated sim gives PITs spread evenly over 0-1; values
    stuck near 0 or 1 mean the sim's field is too strong or too weak.

Built 2026-11-15 after an independent review asked for exactly this loop. The
first run found the 24-32% duplicate field (real 3-7%) fixed that day.
Read with the slate as the unit: 8 slates cannot calibrate a tail, but a
quantity that is off in the same direction every week is a bug, not noise.
Writes data/nfl_field_calibration.json.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import random
import re
import sys
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np  # noqa: E402

from edge import dfs_leverage as lev  # noqa: E402
from edge import dfs_nfl_contest as con  # noqa: E402
from edge import dfs_opt_nfl as opt  # noqa: E402
from edge.names import norm  # noqa: E402

LOG = ROOT / "data/dfs_proj_log_nfl.csv"
META = ROOT / "data/contest_meta.json"
OUT = ROOT / "data/nfl_field_calibration.json"
MIN_FIELD = 1000          # smaller fields are too few lineups to read a top tail
SAMPLE = 2000
N_WORLDS = 3000
SLOT = re.compile(r"\b(QB|RB|WR|TE|FLEX|DST)\s")


def contests(meta: dict, logged: set) -> list[tuple]:
    out = []
    for cid, m in meta.items():
        if (m.get("sport") == "NFL" and m.get("type") == "gpp" and (m.get("field") or 0) >= MIN_FIELD
                and str(m.get("draft_group")) in logged
                and (ROOT / f"data/contest-standings-{cid}.csv").exists()):
            out.append((str(m.get("slate", ""))[:10], str(m["draft_group"]), cid, m.get("name", "")))
    return sorted(out)


def pool_for(gid: str) -> list:
    pool = []
    for r in csv.DictReader(LOG.open()):
        if r["gid"] != gid:
            continue
        proj = float(r["proj_gpp"] or r["proj"] or 0)
        if proj <= 0.3:
            continue
        pool.append({"name": r["player"], "team": r["team"], "opp_team": r["opp_team"],
                     "dk_pos": r["dk_pos"], "pos": opt.eligible_slots(r["dk_pos"]),
                     "salary": int(r["salary"]), "proj": proj, "own": float(r["own"] or 0),
                     "game": r["game"]})
    return pool


def read_export(cid: str, pool: list) -> tuple[list, np.ndarray, dict]:
    """(real lineups as pool indices -- only those fully on our board --,
    every entry's score, {norm name: DK points})."""
    ix = {norm(p["name"]): i for i, p in enumerate(pool)}
    lus, scores, pts = [], [], {}
    with open(ROOT / f"data/contest-standings-{cid}.csv", encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if r[0] == "Rank":
                continue
            if len(r) > 10 and r[7]:
                pts[norm(r[7])] = float(r[10] or 0)
            if not r[5]:
                continue
            scores.append(float(r[4] or 0))
            parts = SLOT.split(" " + r[5] + " ")
            idx = [ix.get(norm(parts[k + 1].strip())) for k in range(1, len(parts) - 1, 2)]
            if len(idx) == 9 and None not in idx:
                lus.append(idx)
    return lus, np.sort(np.array(scores)), pts


def shape(pool: list, lus: list) -> dict:
    s1 = s2 = 0
    for lu in lus:
        qb = next(i for i in lu if pool[i]["dk_pos"].startswith("QB"))
        k = sum(1 for i in lu if i != qb and pool[i]["team"] == pool[qb]["team"]
                and pool[i]["dk_pos"].split("/")[0] in ("WR", "TE"))
        s1 += k >= 1
        s2 += k >= 2
    proj = np.array([sum(pool[i]["proj"] for i in lu) for lu in lus])
    keys = collections.Counter(tuple(sorted(lu)) for lu in lus)
    return {"stack1": round(s1 / len(lus), 3), "stack2": round(s2 / len(lus), 3),
            "proj_p50": round(float(np.quantile(proj, .5)), 1),
            "proj_p99": round(float(np.quantile(proj, .99)), 1),
            "dup": round(sum(v for v in keys.values() if v > 1) / len(lus), 3)}


def check(item: tuple) -> dict:
    date, gid, cid, name = item
    pool = pool_for(gid)
    real_lus, real_scores, _ = read_export(cid, pool)
    if len(real_lus) < SAMPLE or len(real_scores) < MIN_FIELD:
        return {"date": date, "contest": cid, "skipped": f"{len(real_lus)} lineups on our board"}
    rng = random.Random(int(cid))
    real = shape(pool, rng.sample(real_lus, SAMPLE))
    field = con.sample_field(pool, SAMPLE, seed=17)
    sim = shape(pool, field)
    worlds = con.scores(pool, n_sims=N_WORLDS, seed=99)
    srt = np.sort(np.stack([worlds[:, lu].sum(1) for lu in field], 1), axis=1)
    m, k, n = srt.shape[1], lev.TAIL_K, len(real_scores)
    x_k = srt[:, m - k]
    excess = np.maximum((srt[:, m - k + 1:] - x_k[:, None]).mean(1), 1e-6)
    sim_max = x_k + excess * np.log(k / m * n)
    pit = lambda sim_v, real_v: round(float(np.mean(sim_v < real_v)), 2)  # noqa: E731
    return {"date": date, "contest": cid, "name": name, "field": n, "real": real, "sim": sim,
            "pit": {"p50": pit(np.quantile(srt, .5, axis=1), np.quantile(real_scores, .5)),
                    "p99": pit(np.quantile(srt, .99, axis=1), np.quantile(real_scores, .99)),
                    "max": pit(sim_max, real_scores.max())},
            "real_max": round(float(real_scores.max()), 1),
            "sim_max_median": round(float(np.median(sim_max)), 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--last", type=int, default=0, help="only the N most recent contests")
    args = ap.parse_args()
    logged = {r["gid"] for r in csv.DictReader(LOG.open())}
    items = contests(json.loads(META.read_text()), logged)
    if args.last:
        items = items[-args.last:]
    if not items:
        print("no graded NFL GPP export of 1,000+ entries with a logged draft group")
        return 0
    from multiprocessing import Pool
    with Pool(min(len(items), os.cpu_count() or 4)) as p:
        rows = p.map(check, items)
    rows = [r for r in rows if "skipped" not in r]
    print(f"NFL field calibration, production constants (FIELD_STACK_1 {con.FIELD_STACK_1}, "
          f"FIELD_STACK_2 {con.FIELD_STACK_2}, SHARP_SHARE {con.SHARP_SHARE}, SHARP_BETA {con.SHARP_BETA})")
    print("real/sim per contest; PIT 0.5 = calibrated, near 0 = sim field too strong, near 1 = too weak\n")
    print(f"{'date':10s} {'contest':>10s} {'field':>6s}  {'stack1':>11s} {'stack2':>11s} "
          f"{'proj p50':>11s} {'proj p99':>11s} {'dup':>11s}  PIT p50 p99 max")
    for r in rows:
        a, b = r["real"], r["sim"]
        cell = lambda k: f"{a[k]:>5}/{b[k]:<5}"  # noqa: E731
        print(f"{r['date']:10s} {r['contest']:>10s} {r['field']:>6d}  {cell('stack1')} {cell('stack2')} "
              f"{cell('proj_p50')} {cell('proj_p99')} {cell('dup')}  "
              f"    {r['pit']['p50']:.2f} {r['pit']['p99']:.2f} {r['pit']['max']:.2f}")
    summary = {}
    if rows:
        for k in ("stack1", "stack2", "proj_p50", "proj_p99", "dup"):
            summary[k + "_gap"] = round(float(np.mean([r["sim"][k] - r["real"][k] for r in rows])), 3)
        for k in ("p50", "p99", "max"):
            summary["pit_" + k] = round(float(np.mean([r["pit"][k] for r in rows])), 2)
        summary["n_contests"] = len(rows)
        summary["enough_data"] = len({r["date"] for r in rows}) >= 5
        print("\nmean sim-minus-real gap:", {k: v for k, v in summary.items() if k.endswith("_gap")})
        print("mean PIT:", {k: v for k, v in summary.items() if k.startswith("pit")},
              f"over {len(rows)} contests" + ("" if summary["enough_data"] else " (not enough data)"))
    OUT.write_text(json.dumps({"constants": {"FIELD_STACK_1": con.FIELD_STACK_1,
                                             "FIELD_STACK_2": con.FIELD_STACK_2,
                                             "SHARP_SHARE": con.SHARP_SHARE,
                                             "SHARP_BETA": con.SHARP_BETA},
                               "summary": summary, "contests": rows}, indent=1))
    print(f"-> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
