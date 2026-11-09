#!/usr/bin/env python3
"""NFL lineups, held out: the PRODUCTION optimizer replayed on every logged pre-lock
board, scored with DK's own points against the real contest fields.

    python3 scripts/nfl_lineup_lab.py parity            # replayed lineups == the logged ones?
    python3 scripts/nfl_lineup_lab.py report [--z Z].. [--seeds 8]  # cash rate / field pctile / $
    python3 scripts/nfl_lineup_lab.py lodo [--seeds 8]  # cash z picked leave-one-slate-out
    python3 scripts/nfl_lineup_lab.py gate OUT [--seeds 8]  # rows for harness/critique_gate.py

SEED NOISE FIRST (critic, cycle 1): the optimizer does not converge at 700 iterations, so
seed 0's lineup is one random draw -- field pctile 0.507 for the shipped cash lineup with
seed 0, 0.648 averaged over 8 seeds. Every comparison here is made on the seed mean.

Why it exists (NFL critique cycle 1, 2026-11-09, target picked by dfs_target_scout). NFL
had no held-out harness that scores what we enter: model cash +29% [-33%, +92%] on 13
entries / 8 slates (sign unknown), GPP +251% on 23 entries / 9 slates but $102 of the $116
from one slate (11/01, overlay contests). scripts/nfl_lineup_backtest.py replays 2020-21
seasons against a simulated field with no ROI, and model_accuracy.py scores player MAE,
which does not see the lineup. The cash objective (dfs_nfl_theory.Z_CASH = 0.75, a
percentile argument, never backtested on a real field) is the first constant this tests.

What is replayed, per NFL Classic draft group in data/dfs_proj_log_nfl.csv (the last
pre-lock build, edge/dfs_forward_log.py, written by build_slate):
  - the pool exactly as logged, in logged order (= build_pool's order after attach_intel
    removed ruled-out players; it breaks the optimizer's sort ties): proj, salary,
    dk_pos -> eligible slots, team / opp_team, game (DK competition id), own and
    leverage (after add_ownership: leverage orders the GPP quarterbacks);
  - edge.dfs_opt_nfl.optimize exactly as build_slate calls it: cash, and GPP with
    stack_n 2, bring_back 1, own_weight 0; seed 0, ITERS = 700 (the autolog CLI's
    default, scripts/dfs_lineups_nfl.py; the page's slider defaults to 350, so a lineup
    Adam built by hand on the page may differ -- `parity` says how often). The player
    sd is NOT read from the log: optimize() recomputes it from proj + position
    (theory.player_sd), as production does.
Truth: DK's own FPTS for the draft group (contests.db player_result, max over every
exported contest on it: the number the field was scored with). A lineup player no export
lists (nobody drafted him anywhere) has no known score; he is scored 0 and COUNTED
(`report` prints it) rather than silently dropped.
Field: every exported contest on that draft group. Per contest: the lineup's percentile
among live entries, whether it reaches the cash line (points of the last paid place), and
the dollars it would have won from contests.db payout at the rank it would have taken
(entry fee subtracted). Cash lineup vs cash contests, GPP lineup vs GPP contests, never
pooled. Cluster: the slate date (one Sunday's main + early-only groups are one day: the
same games, one realisation). Adam's real entry in the same contest is shown beside it.

No train/test split by date: with 7 cash slates the only honest fit is leave-one-slate-out
(`lodo`): a constant is chosen on the other slates' contests and scored on the held-out one.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
from edge import dfs_nfl_theory as theory            # noqa: E402
from edge import dfs_opt_nfl                         # noqa: E402
from edge.names import norm                          # noqa: E402

LOG = ROOT / "data" / "dfs_proj_log_nfl.csv"
DB = ROOT / "data" / "contests.db"
ITERS = 700
ROSTER = len(dfs_opt_nfl.SLOTS)                      # 9
#: GPP construction exactly as build_slate passes it
STACK_N, BRING_BACK, OWN_WEIGHT = 2, 1, 0.0
#: the cash z grid `lodo` chooses from (production: theory.Z_CASH = 0.75)
#: 1.25 / 2.0 / 2.5 added after the single-seed run put 1.5 at the old grid's edge on every
#: fold (a judgment call made on all slates; lodo still picks within the grid held out)
Z_GRID = (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5)


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def boards() -> dict:
    """{gid: [pool rows]} from the projection log, in logged order."""
    by = defaultdict(list)
    with LOG.open(newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                gid = int(r["gid"])
            except (TypeError, ValueError):
                continue
            proj, sal = _f(r.get("proj"), None), _f(r.get("salary"), None)
            if proj is None or not sal:
                continue
            by[gid].append({
                "name": r["player"], "team": r["team"], "opp_team": r.get("opp_team") or None,
                "dk_pos": r["dk_pos"], "pos": sorted(dfs_opt_nfl.eligible_slots(r["dk_pos"])),
                "salary": int(sal), "proj": proj, "game": r.get("game") or None,
                "own": _f(r.get("own")), "leverage": _f(r.get("leverage")), "date": r["date"]})
    return dict(by)


def logged_lineups(B: dict) -> dict:
    """{(gid, mode): set of names} from data/dfs_lineups_nfl_<date>.csv. Files before the
    gid column existed are given to the one board of that date holding every name."""
    out = {}
    for f in sorted(ROOT.glob("data/dfs_lineups_nfl_*.csv")):
        date = f.stem.rsplit("_", 1)[-1]
        groups = defaultdict(set)
        for r in csv.DictReader(f.open(newline="")):
            groups[(r.get("gid") or "", r["mode"])].add(r["player"])
        for (g, mode), names in groups.items():
            if g:
                out[(int(g), mode)] = names
                continue
            hits = [gid for gid, ps in B.items() if ps[0]["date"] == date
                    and names <= {p["name"] for p in ps}]
            if len(hits) == 1:
                out[(hits[0], mode)] = names
    return out


def build(ps: list[dict], mode: str, z_cash: float = theory.Z_CASH, iters: int = ITERS,
          seed: int = 0) -> list[str]:
    pool = [dict(p, pos=set(p["pos"])) for p in ps]
    res = dfs_opt_nfl.optimize(pool, mode=mode, iters=iters, seed=seed, z_cash=z_cash,
                               stack_n=STACK_N, bring_back=BRING_BACK, own_weight=OWN_WEIGHT)
    return [p["name"] for p, _ in (res or {}).get("lineup") or []]


# ---- a build is deterministic: cache on disk, keyed by the board, the arguments and
# the source of every module the build runs, so an edit to any of them misses the cache
CACHE = ROOT / "data" / "harness" / "nfl_lineup_lab"
SOURCES = ("edge/dfs_nfl_theory.py", "edge/dfs_opt_nfl.py", "edge/dfs_opt.py",
           "scripts/nfl_lineup_lab.py")


def _key(ps, *args) -> str:
    h = hashlib.sha1(json.dumps(args).encode())
    for f in SOURCES:
        p = ROOT / f
        if p.exists():
            h.update(p.read_bytes())
    h.update(json.dumps(ps, sort_keys=True, default=sorted).encode())
    return h.hexdigest()[:20]


def _one(args):
    gid, ps, mode, z, iters, seed = args
    return (gid, mode, z, seed), build(ps, mode, z, iters, seed)


def _kargs(mode, z, iters, seed):
    # seed 0 keeps the key it had before seeds existed, so the cache stays valid
    return (mode, z, iters) if seed == 0 else (mode, z, iters, seed)


def build_all(B: dict, jobs: list[tuple], iters: int = ITERS, workers: int = 12) -> dict:
    """{(gid, mode, z[, seed]): [names]} for jobs = [(gid, mode, z[, seed])], cached and in
    parallel. A job without a seed is seed 0 (production)."""
    from concurrent.futures import ProcessPoolExecutor
    CACHE.mkdir(parents=True, exist_ok=True)
    out, todo = {}, []
    for job in jobs:
        gid, mode, z = job[:3]
        seed = job[3] if len(job) > 3 else 0
        f = CACHE / f"{gid}-{mode}-{_key(B[gid], *_kargs(mode, z, iters, seed))}.json"
        if f.exists():
            out[job] = json.loads(f.read_text())
        else:
            todo.append((gid, B[gid], mode, z, iters, seed))
    if todo:
        with ProcessPoolExecutor(max_workers=min(workers, len(todo))) as ex:
            for (gid, mode, z, seed), lu in ex.map(_one, todo):
                out[(gid, mode, z, seed) if seed else (gid, mode, z)] = lu
                (CACHE / f"{gid}-{mode}-{_key(B[gid], *_kargs(mode, z, iters, seed))}.json"
                 ).write_text(json.dumps(lu))
    for job in jobs:                       # (gid, mode, z, 0) and (gid, mode, z) are one build
        if job not in out:
            out[job] = out[job[:3]]
    return out


# ---- truth and fields ----
def dk_points() -> dict:
    """{gid: {norm name: DK FPTS}}, max over every export on the draft group."""
    c = sqlite3.connect(DB)
    out = defaultdict(dict)
    for dg, pn, f in c.execute(
            "select k.draft_group, pr.player_norm, max(pr.fpts) from player_result pr "
            "join contest k using(contest_id) where k.sport='NFL' and pr.fpts is not null "
            "group by k.draft_group, pr.player_norm"):
        out[int(dg)][pn] = float(f)
    return dict(out)


def fields() -> dict:
    """{gid: [contest dict]}: points sorted desc, cash line, payouts, fee, Adam's points."""
    c = sqlite3.connect(DB)
    out = defaultdict(list)
    for cid, dg, typ, paid, fee, date in c.execute(
            "select contest_id, draft_group, type, places_paid, entry_fee, slate_date from contest "
            "where sport='NFL' and places_paid > 0 and draft_group is not null"):
        pts = np.array(sorted((p for (p,) in c.execute(
            "select points from entry where contest_id=? and points is not null and n_players > 0",
            (cid,))), reverse=True))
        if not len(pts):
            continue
        pay = c.execute("select rank_lo, rank_hi, amount from payout where contest_id=?", (cid,)).fetchall()
        adam = [p for (p,) in c.execute(
            "select points from entry where contest_id=? and is_adam=1 and points is not null", (cid,))]
        out[int(dg)].append({"contest": cid, "type": typ, "pts": pts, "fee": float(fee or 0),
                             "line": float(pts[min(paid, len(pts)) - 1]), "payout": pay,
                             "adam": adam, "date": date})
    return dict(out)


def won(contest: dict, points: float) -> float:
    """Dollars a lineup scoring `points` would win: the rank it takes (ties to the better
    place: DK splits a tie's prizes, so this is an upper bound by at most one place)."""
    rank = int((contest["pts"] > points).sum()) + 1
    return float(sum(a for lo, hi, a in contest["payout"] if lo <= rank <= hi))


UNSCORED: dict = defaultdict(int)


def score(truth: dict, lineup: list[str]) -> float | None:
    if len(lineup) != ROSTER or not truth:
        return None
    tot = 0.0
    for n in lineup:
        v = truth.get(norm(n))
        if v is None:
            UNSCORED["lineup players no export lists (scored 0)"] += 1
        tot += v or 0.0
    return tot


def rows(z_cash: float = theory.Z_CASH, modes=("cash", "gpp"), B=None, F=None, T=None,
         seed: int = 0) -> list[dict]:
    """One row per (lineup mode, contest of that type) on a board with an export, for the
    lineup the optimizer returns with `seed` (production: 0)."""
    B = B if B is not None else boards()
    F = F if F is not None else fields()
    T = T if T is not None else dk_points()
    gids = [g for g in B if g in F and g in T]
    zz = {m: (z_cash if m == "cash" else theory.Z_CASH) for m in modes}
    built = build_all(B, [(g, m, zz[m], seed) for g in gids for m in modes])
    out = []
    for gid in sorted(gids, key=lambda g: (B[g][0]["date"], g)):
        for mode in modes:
            lu = built[(gid, mode, zz[mode], seed)]
            pts = score(T[gid], lu)
            if pts is None:
                continue
            for k in F[gid]:
                if k["type"] != mode:
                    continue
                a = max(k["adam"]) if k["adam"] else None
                out.append({"gid": gid, "contest": k["contest"], "date": B[gid][0]["date"],
                            "mode": mode, "points": pts, "pctile": float((k["pts"] < pts).mean()),
                            "cashed": bool(pts >= k["line"]), "line": k["line"], "fee": k["fee"],
                            "net": won(k, pts) - k["fee"], "field_n": len(k["pts"]), "lineup": lu,
                            "adam": a,
                            "adam_pctile": float((k["pts"] < a).mean()) if a is not None else None,
                            "adam_net": (won(k, a) - k["fee"]) if a is not None else None})
    return out


#: Seeds the averaged metric runs over. Why (cycle 1 critic, 2026-11-09): the optimizer is
#: a random search that does not converge at 700 iterations -- seeds 0..7 return 3-7
#: different cash lineups per board, within 1-2.5 projected points of each other, and the
#: shipped z=0.75 cash replay scored field pctile 0.507 with seed 0 but 0.507-0.822 across
#: seeds 0..7 (mean 0.648, 11 contests / 7 slates). One seed is one random draw; every
#: comparison between constructions is made on the mean over SEEDS.
SEEDS = 8


def rows_avg(z_cash: float = theory.Z_CASH, modes=("cash", "gpp"), B=None, F=None, T=None,
             seeds: int = SEEDS) -> list[dict]:
    """rows() averaged per contest over seeds 0..seeds-1: pctile, cash rate (`cashed` is
    then a fraction), points and net are means. `lineup` is seed 0's (production's)."""
    B = B if B is not None else boards()
    F = F if F is not None else fields()
    T = T if T is not None else dk_points()
    gids = [g for g in B if g in F and g in T]
    zz = {m: (z_cash if m == "cash" else theory.Z_CASH) for m in modes}
    build_all(B, [(g, m, zz[m], sd) for g in gids for m in modes for sd in range(seeds)])
    per = [rows(z_cash, modes, B, F, T, seed=sd) for sd in range(seeds)]
    out = []
    for i, r in enumerate(per[0]):
        group = [p[i] for p in per]
        assert all(g["contest"] == r["contest"] and g["mode"] == r["mode"] for g in group)
        out.append(dict(r, **{k: float(np.mean([g[k] for g in group]))
                              for k in ("pctile", "cashed", "points", "net")},
                        pctile_lo=min(g["pctile"] for g in group),
                        pctile_hi=max(g["pctile"] for g in group)))
    return out


def _boot(cl, d, n=2000, seed=0):
    """Mean with a 90% interval, resampling clusters (slate dates)."""
    ids, inv = np.unique(cl, return_inverse=True)
    s = np.bincount(inv, weights=d)
    c = np.bincount(inv).astype(float)
    k = np.random.default_rng(seed).integers(0, len(ids), (n, len(ids)))
    b = s[k].sum(1) / c[k].sum(1)
    return float(d.mean()), float(np.percentile(b, 5)), float(np.percentile(b, 95))


def summary(rs: list[dict], label: str, base: dict | None = None) -> str:
    if not rs:
        return f"| {label} | 0 | | | | | | |"
    cl = np.array([r["date"] for r in rs])
    fee = sum(r["fee"] for r in rs)
    roi = sum(r["net"] for r in rs) / fee if fee else float("nan")
    diff = ""
    if base:
        d = np.array([r["pctile"] - base[r["contest"]]["pctile"] for r in rs])
        m, lo, hi = _boot(cl, d)
        diff = f"{m:+.3f} [{lo:+.3f}, {hi:+.3f}]"
    return (f"| {label} | {len(rs)} | {len(set(cl))} | {np.mean([r['cashed'] for r in rs]):.0%} | "
            f"{np.mean([r['pctile'] for r in rs]):.3f} | {np.mean([r['points'] for r in rs]):.1f} | "
            f"{roi:+.0%} | {diff} |")


HEAD = ("| lineup | contests | slates | cashed | mean field pctile | mean points | ROI ($ at the "
        "rank taken) | pctile diff vs shipped [90%, by slate] |\n|---|---|---|---|---|---|---|---|")


def parity() -> int:
    B = boards()
    L = logged_lineups(B)
    jobs = [(g, m, theory.Z_CASH) for (g, m) in L if g in B]
    for iters in (ITERS, 350):
        built = build_all(B, jobs, iters=iters)
        same = defaultdict(lambda: [0, 0])
        miss = []
        for g, m, z in jobs:
            same[m][1] += 1
            ok = set(built[(g, m, z)]) == L[(g, m)]
            same[m][0] += ok
            if not ok:
                miss.append(f"{g}/{m}")
        print(f"iters {iters}: " + "; ".join(f"{m} replay == logged lineup on {a} of {n} boards"
                                             for m, (a, n) in sorted(same.items()))
              + (f"  (differ: {', '.join(miss)})" if miss else ""))
    return 0


def report(zs: list[float], seeds: int = SEEDS) -> int:
    B, F, T = boards(), fields(), dk_points()
    base_rows = rows(theory.Z_CASH, B=B, F=F, T=T)
    base = {r["contest"]: r for r in base_rows}
    avg = rows_avg(theory.Z_CASH, B=B, F=F, T=T, seeds=seeds)
    avg_base = {r["contest"]: r for r in avg}
    no_board = sorted({k["date"] + f" dg {g}" for g, ks in F.items() if g not in B for k in ks})
    print("# NFL lineups replayed (production optimizer), scored vs the real field\n")
    print(f"exports with no logged board (excluded): {', '.join(no_board) or 'none'}")
    print("unscored: " + (", ".join(f"{k} {v}" for k, v in UNSCORED.items()) or "none") + "\n")
    print(f"'seed 0' = the lineup production built; 'mean of {seeds} seeds' = the same construction "
          "averaged over optimizer seeds (cashed = mean rate). Compare constructions on the mean.\n")
    for mode in ("cash", "gpp"):
        print(f"## {mode.upper()}\n\n{HEAD}")
        tag = f"z_cash {theory.Z_CASH}" if mode == "cash" else "shipped"
        s = [r for r in base_rows if r["mode"] == mode]
        print(summary(s, f"{tag}, seed 0"))
        sa = [r for r in avg if r["mode"] == mode]
        print(summary(sa, f"{tag}, mean of {seeds} seeds", base))
        a = [dict(r, points=r["adam"], pctile=r["adam_pctile"], net=r["adam_net"],
                  cashed=r["adam"] >= r["line"]) for r in s if r["adam"] is not None]
        print(summary(a, "Adam's real entry (diff vs seed mean)", avg_base))
        if mode == "cash":
            for z in zs:
                if z != theory.Z_CASH:
                    print(summary(rows_avg(z, ("cash",), B, F, T, seeds=seeds),
                                  f"z_cash {z}, mean of {seeds} seeds", avg_base))
        print()

    def opt(v, fmt):
        return "" if v is None else format(v, fmt)

    print("## per contest (shipped)\n\n| date | dg | mode | contest | field | fee | line | seed-0 pts | "
          "seed-0 pctile | seed pctile mean [min, max] | seed cash rate | Adam pts | Adam pctile |\n"
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in base_rows:
        m = avg_base[r["contest"]]
        print(f"| {r['date']} | {r['gid']} | {r['mode']} | {r['contest']} | {r['field_n']} | "
              f"{r['fee']:g} | {r['line']:.1f} | {r['points']:.1f} | {r['pctile']:.2f} | "
              f"{m['pctile']:.2f} [{m['pctile_lo']:.2f}, {m['pctile_hi']:.2f}] | {m['cashed']:.0%} | "
              f"{opt(r['adam'], '.1f')} | {opt(r['adam_pctile'], '.2f')} |")
    return 0


def lodo(seeds: int = SEEDS) -> int:
    """For each cash slate: choose z_cash from Z_GRID on the OTHER slates' contests (mean
    field percentile averaged over `seeds` optimizer seeds, ties to production), score it on
    this slate (same seed average)."""
    B, F, T = boards(), fields(), dk_points()
    by_z = {z: rows_avg(z, ("cash",), B, F, T, seeds=seeds) for z in Z_GRID}
    dates = sorted({r["date"] for r in by_z[theory.Z_CASH]})
    print(f"# cash z_cash chosen leave-one-slate-out (every number a mean over {seeds} seeds)\n")
    print("| held-out slate | z chosen on the rest | contests | shipped pctile | chosen pctile | "
          "shipped cash rate | chosen cash rate |\n|---|---|---|---|---|---|---|")
    held, shipped = [], []
    for d in dates:
        def fit(z):
            rs = [r for r in by_z[z] if r["date"] != d]
            return (np.mean([r["pctile"] for r in rs]), z == theory.Z_CASH)
        zbest = max(Z_GRID, key=fit)
        h = [r for r in by_z[zbest] if r["date"] == d]
        s_ = [r for r in by_z[theory.Z_CASH] if r["date"] == d]
        held += h
        shipped += s_
        print(f"| {d} | {zbest} | {len(h)} | {np.mean([r['pctile'] for r in s_]):.3f} | "
              f"{np.mean([r['pctile'] for r in h]):.3f} | {np.mean([r['cashed'] for r in s_]):.0%} | "
              f"{np.mean([r['cashed'] for r in h]):.0%} |")
    base = {r["contest"]: r for r in shipped}
    print(f"\n{HEAD}")
    print(summary(shipped, "shipped"))
    print(summary(held, "z chosen leave-one-slate-out", base))
    print("\nIn-sample by z (every slate; NOT held out, for reading only):\n")
    print(HEAD)
    for z in Z_GRID:
        print(summary(by_z[z], f"z_cash {z}", base))
    return 0


def gate(path: str, seeds: int = SEEDS) -> int:
    """critique_gate rows: one per (mode, contest); pred = 1 - field percentile averaged
    over `seeds` optimizer seeds (a loss: 0 = beat everyone), actual = 0, so lower = better
    (loss "abs"). Seed-averaged because one seed is one random draw of a search that does
    not converge (SEEDS): a branch that changes anything upstream re-draws every lineup,
    and the gate would be judging the draw. State = mode, cluster = slate date, split
    primary for both (no constant here is fitted on these rows)."""
    n = 0
    with open(path, "w") as fh:
        for r in rows_avg(theory.Z_CASH, seeds=seeds):
            fh.write(json.dumps({"key": f"{r['mode']}|{r['contest']}", "cluster": r["date"],
                                 "split": "primary", "state": r["mode"],
                                 "pred": 1.0 - r["pctile"], "actual": 0.0}) + "\n")
            n += 1
    print(f"{n} gate rows -> {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("parity")
    r = sub.add_parser("report")
    r.add_argument("--z", type=float, action="append", help="extra cash z values to compare")
    r.add_argument("--seeds", type=int, default=SEEDS)
    lo = sub.add_parser("lodo")
    lo.add_argument("--seeds", type=int, default=SEEDS)
    g = sub.add_parser("gate")
    g.add_argument("out")
    g.add_argument("--seeds", type=int, default=SEEDS)
    a = ap.parse_args()
    if a.cmd == "parity":
        return parity()
    if a.cmd == "lodo":
        return lodo(a.seeds)
    if a.cmd == "gate":
        return gate(a.out, a.seeds)
    return report(a.z or [], a.seeds)


if __name__ == "__main__":
    raise SystemExit(main())
