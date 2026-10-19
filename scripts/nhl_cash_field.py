#!/usr/bin/env python3
"""Backtest the field-aware NHL cash objective against the floor objective.

    python3 scripts/nhl_cash_field.py              # every graded night
    python3 scripts/nhl_cash_field.py --fit-only   # just the cash-field ownership fit

For each graded night: rebuild the board as it stood at lock
(scripts/nhl_rebuild.py), fit the cash-field ownership on the OTHER nights'
double-ups (leave one out), and build two cash lineups -- the shipped floor
objective and edge/dfs_nhl_field.optimize_cash. Both are scored on the real DK
points in that night's exports and placed against the real cash line.

A player nobody rostered in either export has no points in the export; he
counts 0 and is flagged (`unk`).
"""
from __future__ import annotations

import argparse
import itertools
import json
import pickle
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from edge import dfs_nhl_field as field, dfs_nhl_theory as theory, dfs_opt_nhl  # noqa: E402
from edge.dfs_contest import parse_contest_file  # noqa: E402
from edge.names import norm  # noqa: E402

#: gid -> (cash export, gpp export or None). The first eight nights, as fitted
#: 2026-10-10; all_nights() finds every tagged NHL double-up since.
NIGHTS = {153977: ("195958188", None), 154305: ("196218453", "196218438"),
          154311: ("196255389", "196254552"), 154332: ("196302159", "196302148"),
          154342: ("196371527", "196304871"), 154356: ("196425573", "196425557"),
          154690: ("196480935", "196456857"), 154704: ("196531907", "196531691")}
GRID = list(itertools.product([0.5, 1.0, 1.5, 2.0, 2.5], [0.0, 0.5, 1.0, 1.5],
                              [0.0, 0.4, 0.8], [60.0, 75.0, 90.0]))


def all_nights(meta: dict) -> dict:
    """gid -> (cash export, gpp export or None) for every NHL draft group DK tagged
    with a double-up: the biggest cash field, plus the biggest GPP on the same draft
    group for the points of players no cash entry rostered."""
    by: dict = {}
    for cid, m in meta.items():
        if isinstance(m, dict) and m.get("sport") == "NHL" and m.get("draft_group") \
                and (ROOT / f"data/contest-standings-{cid}.csv").exists():
            by.setdefault(int(m["draft_group"]), []).append((m.get("type"), m.get("field") or 0, cid))
    out = {}
    for gid, cs in sorted(by.items()):
        cash = max((c for c in cs if c[0] == "cash"), key=lambda c: c[1], default=None)
        gpp = max((c for c in cs if c[0] == "gpp"), key=lambda c: c[1], default=None)
        if cash:
            out[gid] = (cash[2], gpp[2] if gpp else None)
    return out


def board(gid: int, cache: Path):
    path = cache / f"nhl_board_{gid}.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())
    import nhl_rebuild
    pool, info, sim = nhl_rebuild.rebuild(gid, None, 2000, 150)
    path.write_bytes(pickle.dumps((pool, info, sim)))
    return pool, info, sim


def entries(cid: str) -> list[float]:
    return [float(l.split(",")[4]) for l in (ROOT / f"data/contest-standings-{cid}.csv").open()
            if l.split(",")[0].isdigit()]


def own_error(pool, actual, w) -> float:
    rows = [dict(p) for p in pool]
    theory.add_ownership(rows, weights=w[:3], max_own=w[3], key="cash_own")
    return statistics.fmean((r["cash_own"] - actual.get(norm(r["name"]), 0.0)) ** 2 for r in rows)


def fit(nights: dict, skip=None, skip_date=None) -> tuple:
    """Squared-error fit over the other nights. skip_date holds out a whole DAY:
    an early slate's games are also on that day's main slate (Adam plays early,
    main and late), so leaving out one draft group alone would leak."""
    return min(GRID, key=lambda w: sum(own_error(n["pool"], n["own"], w)
                                       for g, n in nights.items()
                                       if g != skip and n["info"]["date"] != skip_date))


def score(res, pts) -> tuple[float, int]:
    names = [r["name"] for r in res["lineup"]]
    return sum(pts.get(norm(n), 0.0) for n in names), sum(norm(n) not in pts for n in names)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fit-only", action="store_true")
    ap.add_argument("--all", action="store_true", help="every tagged NHL double-up, not the first 8")
    ap.add_argument("--compare", action="store_true",
                    help="field-aware cash lineups: shipped CASH_WEIGHTS vs a refit holding out each day")
    ap.add_argument("--iters", type=int, default=150)
    ap.add_argument("--cache", default=str(ROOT / "data" / "cache"))
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    meta = json.loads((ROOT / "data/contest_meta.json").read_text())

    nights = {}
    for gid, (cash, gpp) in (all_nights(meta) if args.all else NIGHTS).items():
        try:
            pool, info, sim = board(gid, cache)
        except (Exception, SystemExit) as exc:              # noqa: BLE001  rebuild exits on missing history
            print(f"  {gid}: no rebuild ({exc}) -- skipped")
            continue
        con = parse_contest_file(str(ROOT / f"data/contest-standings-{cash}.csv"))
        pts = {k: v["fpts"] for k, v in con.items()}
        if gpp:
            pts.update({k: v["fpts"] for k, v in
                        parse_contest_file(str(ROOT / f"data/contest-standings-{gpp}.csv")).items()})
        scores = sorted(entries(cash), reverse=True)
        paid = meta.get(cash, {}).get("places_paid") or int(len(scores) * field.PAY_FRAC)
        nights[gid] = {"pool": pool, "sim": sim, "info": info, "pts": pts, "scores": scores,
                       "line": scores[paid - 1], "own": {k: v["pct_drafted"] for k, v in con.items()}}

    w_all = fit(nights)
    base = statistics.fmean(own_error(n["pool"], n["own"], (theory.VALUE_WEIGHT, theory.SALARY_WEIGHT,
                                                            theory.PP1_BONUS, theory.MAX_OWN))
                            for n in nights.values())
    best = statistics.fmean(own_error(n["pool"], n["own"], w_all) for n in nights.values())
    print(f"cash-field ownership, all nights: value {w_all[0]} salary {w_all[1]} pp1 {w_all[2]} "
          f"cap {w_all[3]:.0f} -> RMSE {best ** .5:.2f} (GPP model as shipped {base ** .5:.2f})")
    if args.fit_only:
        return
    if args.compare:
        return compare(nights, args.iters)

    print(f"\n{'night':10} {'g':>2} {'line':>6} | {'floor':>6} {'pct':>4} {'cash':4} {'unk':>3} | "
          f"{'field':>6} {'pct':>4} {'cash':4} {'unk':>3} | {'p_cash floor/field (model)':>26}")
    tally = {"floor": [], "field": []}
    for gid, n in nights.items():
        w = fit(nights, skip=gid)
        floor = dfs_opt_nhl.optimize(n["pool"], n["sim"], mode="cash", iters=args.iters, seed=0)
        fa = field.optimize_cash(n["pool"], n["sim"], iters=args.iters, seed=0,
                                 weights=w[:3], max_own=w[3])
        own = field.cash_ownership(n["pool"], w[:3], w[3])
        line = field.cash_line(n["sim"], field.sample_field(n["pool"], own, seed=1))
        p_floor = float((n["sim"][:, [n["pool"].index(r) for r in floor["lineup"]]].sum(axis=1)
                         >= line).mean())
        row = f"{n['info']['date']:10} {n['info']['games']:2} {n['line']:6.1f} |"
        for label, res in (("floor", floor), ("field", fa)):
            s, unk = score(res, n["pts"])
            pct = sum(x < s for x in n["scores"]) / len(n["scores"])
            tally[label].append((s >= n["line"], pct, s))
            row += f" {s:6.1f} {pct:4.0%} {'YES' if s >= n['line'] else 'no':4} {unk:3} |"
        row += f" {p_floor:12.0%} / {fa['p_cash']:.0%}"
        print(row)
        print(f"{'':13} floor: {', '.join(r['name'] for r in floor['lineup'])}")
        print(f"{'':13} field: {', '.join(r['name'] for r in fa['lineup'])}  (w {w})")
    print()
    for label, rows in tally.items():
        print(f"{label:6} cashed {sum(r[0] for r in rows)}/{len(rows)}  mean percentile "
              f"{statistics.fmean(r[1] for r in rows):.0%}  mean points {statistics.fmean(r[2] for r in rows):.1f}")


def compare(nights: dict, iters: int) -> None:
    """Shipped cash-field weights vs a refit, each night's weights fitted with
    that whole DAY held out; both build the field-aware cash lineup and are
    scored on the real points against the real cash line."""
    shipped = (*field.CASH_WEIGHTS, field.CASH_MAX_OWN)
    print(f"\n{'night':10} {'gid':>6} {'line':>6} | {'shipped':>7} {'pct':>4} {'cash':4} | "
          f"{'refit':>7} {'pct':>4} {'cash':4} | own RMSE shipped/refit  refit w")
    tally = {"shipped": [], "refit": []}
    for gid, n in nights.items():
        w = fit(nights, skip_date=n["info"]["date"])
        row = f"{n['info']['date']:10} {gid:6} {n['line']:6.1f} |"
        for label, wt in (("shipped", shipped), ("refit", w)):
            res = field.optimize_cash(n["pool"], n["sim"], iters=iters, seed=0,
                                      weights=wt[:3], max_own=wt[3])
            s_, _unk = score(res, n["pts"])
            pct = sum(x < s_ for x in n["scores"]) / len(n["scores"])
            tally[label].append((s_ >= n["line"], pct, s_, own_error(n["pool"], n["own"], wt)))
            row += f" {s_:7.1f} {pct:4.0%} {'YES' if s_ >= n['line'] else 'no':4} |"
        row += (f" {tally['shipped'][-1][3] ** .5:5.2f} / {tally['refit'][-1][3] ** .5:5.2f}  {w}")
        print(row, flush=True)
    print()
    for label, rows in tally.items():
        print(f"{label:8} cashed {sum(r[0] for r in rows)}/{len(rows)}  mean percentile "
              f"{statistics.fmean(r[1] for r in rows):.0%}  mean points "
              f"{statistics.fmean(r[2] for r in rows):.1f}  ownership RMSE "
              f"{statistics.fmean(r[3] for r in rows) ** .5:.2f}")


if __name__ == "__main__":
    main()
