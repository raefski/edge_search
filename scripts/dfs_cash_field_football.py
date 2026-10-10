#!/usr/bin/env python3
"""Backtest field-aware cash for NFL / NCAAF against the shipped mean - z*sd cash.

    python3 scripts/dfs_cash_field_football.py --sport nfl
    python3 scripts/dfs_cash_field_football.py --sport ncaaf

Pools are the LOGGED builds (data/dfs_proj_log_<sport>.csv: proj, sd, own as
shipped). Joint outcomes come from edge/dfs_field.simulate on the model's own
sd and rho. The field is drawn from cash ownership (own**k, k fitted on the
OTHER slates). Real points are the union of each night's cash and GPP export
(players nobody rostered count 0 and are flagged).

Each (cash, gpp) pair is checked to be the same week -- every shared player
must have identical points -- and 90%+ of the cash field's ownership must be on
players in the logged board.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs_field, dfs_sim  # noqa: E402
from edge.dfs_contest import parse_contest_file  # noqa: E402
from edge.names import norm  # noqa: E402

SPORTS = {
    "nfl": {"log": "data/dfs_proj_log_nfl.csv", "theory": "dfs_nfl_theory", "opt": "dfs_opt_nfl",
            "slates": [("2026-09-13", "151307", "195614469", "193028246"),
                       ("2026-09-20", "153428", "195659767", "195661382"),
                       ("2026-09-27", "153769", "195920333", "195921991")]},
    "ncaaf": {"log": "data/dfs_proj_log_ncaaf.csv", "theory": "dfs_ncaaf_theory",
              "opt": "dfs_opt_ncaaf",
              "slates": [("2026-09-26", "153951", "195999273", "195958567"),
                         ("2026-10-03", "154161", "196178850", "196178010")]},
}
KS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]


def entries(cid):
    return [float(l.split(",")[4]) for l in (ROOT / f"data/contest-standings-{cid}.csv").open()
            if l.split(",")[0].isdigit()]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sport", choices=SPORTS, required=True)
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--restarts", type=int, default=0,
                    help="extra random starting lineups for the field-aware search")
    args = ap.parse_args()
    cfg = SPORTS[args.sport]
    theory = __import__(f"edge.{cfg['theory']}", fromlist=["x"])
    opt = __import__(f"edge.{cfg['opt']}", fromlist=["x"])
    meta = json.loads((ROOT / "data/contest_meta.json").read_text())
    rows = list(csv.DictReader((ROOT / cfg["log"]).open()))

    def group(p):
        return theory.base_position(p) if args.sport == "nfl" else theory.base_position(p.get("dk_pos"))

    slates = []
    for date, gid, cash, gpp in cfg["slates"]:
        cc = parse_contest_file(str(ROOT / f"data/contest-standings-{cash}.csv"))
        gc = parse_contest_file(str(ROOT / f"data/contest-standings-{gpp}.csv"))
        shared = [k for k in cc if k in gc]
        same = sum(abs(cc[k]["fpts"] - gc[k]["fpts"]) < 0.01 for k in shared) / max(len(shared), 1)
        pool = []
        for r in rows:
            if r["date"] != date or r["gid"] != gid or not r["salary"]:
                continue
            pos = {"DST"} if r["dk_pos"] == "DST" else opt.eligible_slots(r["dk_pos"])
            pool.append({"name": r["player"], "team": r["team"], "opp_team": r["opp_team"],
                         "dk_pos": r["dk_pos"], "pos": pos, "salary": int(float(r["salary"])),
                         "proj": float(r["proj"]), "sd": float(r["sd"]),
                         "own": float(r["own"] or 0), "game": r["game"]})
        names = {norm(p["name"]) for p in pool}
        # Share of the field's OWNERSHIP inside the logged board, not of its
        # players: NCAAF only projects players with DK ladders, so a few
        # low-owned names are always missing; a broken build (9/26, the suffix
        # bug that dropped a 91%-owned QB) loses the chalk and fails this.
        fit = (sum(v["pct_drafted"] for k, v in cc.items() if norm(k) in names)
               / sum(v["pct_drafted"] for v in cc.values()))
        pts = {k: v["fpts"] for k, v in gc.items()}
        pts.update({k: v["fpts"] for k, v in cc.items()})
        real = sorted(entries(cash), reverse=True)
        paid = meta.get(cash, {}).get("places_paid") or int(len(real) * dfs_field.PAY_FRAC)
        print(f"{date} cash {cash} / gpp {gpp}: same week {same:.0%} of {len(shared)} shared, "
              f"field ownership inside the log {fit:.0%}, pool {len(pool)}")
        if same < 0.99 or fit < 0.9:
            print("   SKIPPED: not confirmed as this logged slate")
            continue
        slates.append({"date": date, "pool": pool, "pts": pts, "real": real, "line": real[paid - 1],
                       "own": {k: v["pct_drafted"] for k, v in cc.items()}})

    def err(s, k):
        p = [dict(x) for x in s["pool"]]
        dfs_field.sharpen(p, k, group)
        return statistics.fmean((x["own_cash"] - s["own"].get(norm(x["name"]), 0.0)) ** 2 for x in p)

    print(f"\n{'date':10} {'line':>6} | {'z-sd':>6} {'pct':>4} {'cash':4} {'unk':>3} | "
          f"{'field':>6} {'pct':>4} {'cash':4} {'unk':>3} | model P(clear) z-sd/field  k")
    tally = {"z": [], "field": []}
    for s in slates:
        others = [o for o in slates if o is not s] or slates
        k = min(KS, key=lambda kk: sum(err(o, kk) for o in others))
        pool = [dict(p) for p in s["pool"]]
        w = dfs_field.sharpen(pool, k, group)
        base = opt.optimize(pool, mode="cash", iters=2000, seed=0)
        scores = dfs_field.simulate(pool, lambda p: p["sd"], theory.rho, n_sims=args.sims, seed=1,
                                    normal=lambda p: p["dk_pos"] == "DST")
        field = dfs_field.sample_field(pool, w, opt.SLOTS, opt._valid, n=300, seed=11)
        line = dfs_field.cash_line(scores, field)
        ix = {p["name"]: i for i, p in enumerate(pool)}
        start = [ix[p["name"]] for p, _ in base["lineup"]]
        import random
        rng = random.Random(5)
        starts = [start]
        for _ in range(args.restarts * 5):
            if len(starts) > args.restarts:
                break
            lu = opt._fill(pool, rng)
            if lu:
                starts.append([ix[p["name"]] for p in lu])
        best, p_field = dfs_sim.optimize_cash_vs_field(pool, scores, line, starts, opt._valid)
        p_z = float((scores[:, start].sum(1) >= line).mean())
        row = f"{s['date']:10} {s['line']:6.1f} |"
        for label, names in (("z", [p["name"] for p, _ in base["lineup"]]),
                             ("field", [pool[i]["name"] for i in best])):
            got = sum(s["pts"].get(norm(n), 0.0) for n in names)
            unk = sum(norm(n) not in s["pts"] for n in names)
            pct = sum(x < got for x in s["real"]) / len(s["real"])
            tally[label].append((got >= s["line"], pct, got))
            row += f" {got:6.1f} {pct:4.0%} {'YES' if got >= s['line'] else 'no':4} {unk:3} |"
        print(row + f" {p_z:7.0%} / {p_field:.0%}  {k}   field n={len(field)}")
        print(f"{'':11} z-sd : {', '.join(p['name'] for p, _ in base['lineup'])}")
        print(f"{'':11} field: {', '.join(pool[i]['name'] for i in best)}")
    print()
    for label, rs in tally.items():
        if rs:
            print(f"{label:6} cashed {sum(r[0] for r in rs)}/{len(rs)}  mean percentile "
                  f"{statistics.fmean(r[1] for r in rs):.0%}  mean points {statistics.fmean(r[2] for r in rs):.1f}")


if __name__ == "__main__":
    main()
