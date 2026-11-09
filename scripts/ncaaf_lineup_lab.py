#!/usr/bin/env python3
"""NCAAF lineups, held out: the PRODUCTION optimizer replayed on every logged pre-lock
board, scored with DK's own points against the real contest fields.

    python3 scripts/ncaaf_lineup_lab.py parity            # replayed lineups == the logged ones?
    python3 scripts/ncaaf_lineup_lab.py report [--z Z]..  # cash rate / field pctile / $ per contest
    python3 scripts/ncaaf_lineup_lab.py lodo              # cash z picked leave-one-slate-out
    python3 scripts/ncaaf_lineup_lab.py calibrate [--forward]  # cycle 7 candidate, held out
    python3 scripts/ncaaf_lineup_lab.py gate OUT          # rows for harness/critique_gate.py

Why it exists (critique cycle 7, 2026-11-09, target picked by dfs_target_scout). NCAAF
cash lost -63% [-98%, -27%] (12 entries / 9 slates, data/dfs_scorecard.json) while every
critique cycle so far scored PLAYER DK points (scripts/ncaaf_lab.py). Player MAE does not
see the lineup we enter, and the cash objective (dfs_ncaaf_theory.Z_CASH = 0.75, carried
over from the NFL unmeasured) had never been backtested. This harness scores what we enter.

What is replayed, per NCAAF Classic draft group in data/dfs_proj_log_ncaaf.csv (the last
pre-lock build, edge/dfs_forward_log.py):
  - the pool exactly as logged, in logged order (= build_pool's order; it breaks the
    optimizer's sort ties): proj, salary, dk_pos -> eligible slots, team / opp, game (DK
    event id), own and leverage (after add_ownership: they steer the GPP QB order);
  - edge.dfs_opt_ncaaf.optimize, cash and GPP, seed 0, ITERS = 700 (the autolog CLI's
    default, scripts/dfs_lineups_ncaaf.py; the page's slider defaults to 350, so a lineup
    Adam built by hand on the page may differ -- `parity` says how often).
Truth: DK's own FPTS for the draft group (contests.db player_result, max over every
exported contest on it: the number the field was scored with). A lineup player no export
lists (nobody drafted him anywhere) has no known score; he is scored 0 and COUNTED
(`report` prints it) rather than silently dropped.
Field: every exported contest on that draft group. Per contest: the lineup's percentile
among live entries, whether it reaches the cash line (points of the last paid place), and
the dollars it would have won from contests.db payout at the rank it would have taken
(entry fee subtracted, so "net" is per $ entered). Cash lineup vs cash contests, GPP
lineup vs GPP contests, never pooled. Cluster: the slate date (10/17's two draft groups
are one day). Adam's real entry in the same contest is reported beside the replay, so
hand edits are visible.

No train/test split by date: with 9 cash slates the only honest fit is leave-one-slate-out
(`lodo`): a constant is chosen on 8 slates' contests and scored on the 9th, for every slate.
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
from edge import dfs_ncaaf_theory as theory          # noqa: E402
from edge import dfs_opt_ncaaf                       # noqa: E402
from edge.names import norm                          # noqa: E402

LOG = ROOT / "data" / "dfs_proj_log_ncaaf.csv"
DB = ROOT / "data" / "contests.db"
ITERS = 700
#: the cash z grid `lodo` chooses from (production: theory.Z_CASH = 0.75)
Z_GRID = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5)


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
                "dk_pos": r["dk_pos"], "pos": dfs_opt_ncaaf.eligible_slots(r["dk_pos"]),
                "salary": int(sal), "proj": proj, "game": r.get("game") or None,
                "own": _f(r.get("own")), "leverage": _f(r.get("leverage")), "date": r["date"]})
    return dict(by)


def logged_lineups(B: dict) -> dict:
    """{(gid, mode): set of names} from data/dfs_lineups_ncaaf_<date>.csv. Files before
    the gid column existed are given to the one board of that date holding every name."""
    out = {}
    for f in sorted(ROOT.glob("data/dfs_lineups_ncaaf_*.csv")):
        date = f.stem.rsplit("_", 1)[-1]
        rows = list(csv.DictReader(f.open(newline="")))
        groups = defaultdict(set)
        for r in rows:
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


def build(ps: list[dict], mode: str, z_cash: float = theory.Z_CASH, iters: int = ITERS) -> list[str]:
    pool = [dict(p, pos=set(p["pos"])) for p in ps]
    res = dfs_opt_ncaaf.optimize(pool, mode=mode, iters=iters, seed=0, z_cash=z_cash)
    return [p["name"] for p, _ in (res or {}).get("lineup") or []]


# ---- a build is deterministic: cache on disk, keyed by the board, the arguments and
# the source of every module the build runs, so an edit to any of them misses the cache
CACHE = ROOT / "data" / "harness" / "ncaaf_lineup_lab"
SOURCES = ("edge/dfs_ncaaf_theory.py", "edge/dfs_opt_ncaaf.py", "edge/ncaaf.py",
           "scripts/ncaaf_lineup_lab.py")


def _key(ps, *args) -> str:
    h = hashlib.sha1(json.dumps(args).encode())
    for f in SOURCES:
        h.update((ROOT / f).read_bytes())
    h.update(json.dumps(ps, sort_keys=True, default=sorted).encode())
    return h.hexdigest()[:20]


def _one(args):
    gid, ps, mode, z, iters = args
    return (gid, mode, z), build(ps, mode, z, iters)


def build_all(B: dict, jobs: list[tuple], iters: int = ITERS, workers: int = 12) -> dict:
    """{(gid, mode, z): [names]} for jobs = [(gid, mode, z)], cached and in parallel."""
    from concurrent.futures import ProcessPoolExecutor
    CACHE.mkdir(parents=True, exist_ok=True)
    out, todo = {}, []
    for gid, mode, z in jobs:
        f = CACHE / f"{gid}-{mode}-{_key(B[gid], mode, z, iters)}.json"
        if f.exists():
            out[(gid, mode, z)] = json.loads(f.read_text())
        else:
            todo.append((gid, B[gid], mode, z, iters))
    if todo:
        with ProcessPoolExecutor(max_workers=min(workers, len(todo))) as ex:
            for k, lu in ex.map(_one, todo):
                out[k] = lu
                gid, mode, z = k
                (CACHE / f"{gid}-{mode}-{_key(B[gid], mode, z, iters)}.json").write_text(json.dumps(lu))
    return out


# ---- truth and fields ----
def dk_points() -> dict:
    """{gid: {norm name: DK FPTS}}, max over every export on the draft group."""
    c = sqlite3.connect(DB)
    out = defaultdict(dict)
    for dg, pn, f in c.execute(
            "select k.draft_group, pr.player_norm, max(pr.fpts) from player_result pr "
            "join contest k using(contest_id) where k.sport='NCAAF' and pr.fpts is not null "
            "group by k.draft_group, pr.player_norm"):
        out[int(dg)][pn] = float(f)
    return dict(out)


def fields() -> dict:
    """{gid: [contest dict]}: points sorted desc, cash line, payouts, fee, Adam's points."""
    c = sqlite3.connect(DB)
    out = defaultdict(list)
    for cid, dg, typ, paid, fee, date in c.execute(
            "select contest_id, draft_group, type, places_paid, entry_fee, slate_date from contest "
            "where sport='NCAAF' and places_paid > 0 and draft_group is not null"):
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
    if len(lineup) != 8 or not truth:
        return None
    tot = 0.0
    for n in lineup:
        v = truth.get(norm(n))
        if v is None:
            UNSCORED["lineup players no export lists (scored 0)"] += 1
        tot += v or 0.0
    return tot


def rows(z_cash: float = theory.Z_CASH, modes=("cash", "gpp"), B=None, F=None, T=None) -> list[dict]:
    """One row per (lineup mode, contest of that type) on a board with an export."""
    B = B if B is not None else boards()
    F = F if F is not None else fields()
    T = T if T is not None else dk_points()
    gids = [g for g in B if g in F and g in T]
    built = build_all(B, [(g, m, z_cash if m == "cash" else theory.Z_CASH) for g in gids for m in modes])
    out = []
    for gid in sorted(gids, key=lambda g: B[g][0]["date"]):
        for mode in modes:
            lu = built[(gid, mode, z_cash if mode == "cash" else theory.Z_CASH)]
            pts = score(T[gid], lu)
            if pts is None:
                continue
            for k in F[gid]:
                if k["type"] != mode:
                    continue
                net = won(k, pts) - k["fee"]
                out.append({"gid": gid, "contest": k["contest"], "date": B[gid][0]["date"],
                            "mode": mode, "points": pts, "pctile": float((k["pts"] < pts).mean()),
                            "cashed": bool(pts >= k["line"]), "line": k["line"], "fee": k["fee"],
                            "net": net, "field_n": len(k["pts"]), "lineup": lu,
                            "adam": max(k["adam"]) if k["adam"] else None,
                            "adam_pctile": float((k["pts"] < max(k["adam"])).mean()) if k["adam"] else None,
                            "adam_net": (won(k, max(k["adam"])) - k["fee"]) if k["adam"] else None})
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
        for g, m, z in jobs:
            same[m][1] += 1
            same[m][0] += set(built[(g, m, z)]) == L[(g, m)]
        print(f"iters {iters}: " + "; ".join(f"{m} replay == logged lineup on {a} of {n} boards"
                                             for m, (a, n) in sorted(same.items())))
    return 0


def report(zs: list[float]) -> int:
    B, F, T = boards(), fields(), dk_points()
    base_rows = rows(theory.Z_CASH, B=B, F=F, T=T)
    base = {r["contest"]: r for r in base_rows}
    no_board = sorted({k["date"] + f" dg {g}" for g, ks in F.items() if g not in B for k in ks})
    print("# NCAAF lineups replayed (production optimizer), scored vs the real field\n")
    print(f"exports with no logged board (excluded): {', '.join(no_board) or 'none'}")
    print("unscored: " + (", ".join(f"{k} {v}" for k, v in UNSCORED.items()) or "none") + "\n")
    for mode in ("cash", "gpp"):
        print(f"## {mode.upper()}\n\n{HEAD}")
        s = [r for r in base_rows if r["mode"] == mode]
        print(summary(s, f"shipped (z_cash {theory.Z_CASH})"))
        a = [dict(r, points=r["adam"], pctile=r["adam_pctile"], net=r["adam_net"],
                  cashed=r["adam"] >= r["line"]) for r in s if r["adam"] is not None]
        print(summary(a, "Adam's real entry", base))
        if mode == "cash":
            for z in zs:
                if z != theory.Z_CASH:
                    print(summary(rows(z, ("cash",), B, F, T), f"z_cash {z}", base))
        print()
    print("## per contest (shipped)\n\n| date | dg | mode | contest | field | line | replay pts | "
          "pctile | cashed | Adam pts | Adam pctile |\n|---|---|---|---|---|---|---|---|---|---|---|")
    def opt(v, fmt):
        return "" if v is None else format(v, fmt)

    for r in base_rows:
        print(f"| {r['date']} | {r['gid']} | {r['mode']} | {r['contest']} | {r['field_n']} | "
              f"{r['line']:.1f} | {r['points']:.1f} | {r['pctile']:.2f} | {'Y' if r['cashed'] else '-'} | "
              f"{opt(r['adam'], '.1f')} | {opt(r['adam_pctile'], '.2f')} |")
    return 0


def lodo() -> int:
    """For each cash slate: choose z_cash from Z_GRID on the OTHER slates' contests (mean
    field percentile, ties to production), score it on this slate. The honest estimate of
    what tuning the cash objective is worth with 9 slates."""
    B, F, T = boards(), fields(), dk_points()
    by_z = {z: [r for r in rows(z, ("cash",), B, F, T)] for z in Z_GRID}
    dates = sorted({r["date"] for r in by_z[theory.Z_CASH]})
    print("# cash z_cash chosen leave-one-slate-out\n")
    print("| held-out slate | z chosen on the rest | contests | shipped pctile | chosen pctile | "
          "shipped cashed | chosen cashed |\n|---|---|---|---|---|---|---|")
    held, shipped = [], []
    for d in dates:
        def fit(z):
            rs = [r for r in by_z[z] if r["date"] != d]
            return (np.mean([r["pctile"] for r in rs]), z == theory.Z_CASH)
        zbest = max(Z_GRID, key=fit)
        h = [r for r in by_z[zbest] if r["date"] == d]
        s = [r for r in by_z[theory.Z_CASH] if r["date"] == d]
        held += h
        shipped += s
        print(f"| {d} | {zbest} | {len(h)} | {np.mean([r['pctile'] for r in s]):.3f} | "
              f"{np.mean([r['pctile'] for r in h]):.3f} | {sum(r['cashed'] for r in s)} | "
              f"{sum(r['cashed'] for r in h)} |")
    base = {r["contest"]: r for r in shipped}
    print(f"\n{HEAD}")
    print(summary(shipped, "shipped"))
    print(summary(held, "z chosen leave-one-slate-out", base))
    print("\nIn-sample by z (every slate; NOT held out, for reading only):\n")
    print(HEAD)
    for z in Z_GRID:
        print(summary(by_z[z], f"z_cash {z}", base))
    return 0


# ---- critique cycle 7 candidate: projection calibrated on salary, per position --------
# The critic found the ladder projection compressed against salary: within position,
# (DK FPTS - proj) rises with salary (QB +1.80 per $1k, positive on 7 of 7 dates; RB +0.35,
# WR +0.14), so the cash climb buys cheap "value" that is not there. Candidate:
#     proj' = max(0, a_pos + b_pos * proj + c_pos * salary/$1k)
# least squares on PLAYER rows (every logged board with an export, cash or GPP: no lineup
# outcome is fitted), excluding the held-out date. --forward fits on EARLIER dates only
# (the live-faithful version; the first dates have too little to fit and stay unchanged).
CAL_MIN_ROWS = 30          # per position, else that position is left as projected


def player_rows(B: dict, T: dict) -> list[tuple]:
    from edge.ncaaf import base_position
    out = []
    for gid, ps in B.items():
        for p in ps:
            a = T.get(gid, {}).get(norm(p["name"]))
            if a is not None:
                out.append((p["date"], base_position(p["dk_pos"]), p["proj"], p["salary"] / 1000.0, a))
    return out


def fit_calibration(data: list[tuple]) -> dict:
    co = {}
    for pos in ("QB", "RB", "WR"):
        rs = [r for r in data if r[1] == pos]
        if len(rs) >= CAL_MIN_ROWS:
            X = np.array([[1.0, r[2], r[3]] for r in rs])
            co[pos] = np.linalg.lstsq(X, np.array([r[4] for r in rs]), rcond=None)[0].tolist()
    return co


def calibrate(ps: list[dict], co: dict) -> list[dict]:
    from edge.ncaaf import base_position
    out = []
    for p in ps:
        c = co.get(base_position(p["dk_pos"]))
        proj = p["proj"] if c is None else max(0.0, c[0] + c[1] * p["proj"] + c[2] * p["salary"] / 1000.0)
        out.append(dict(p, proj=round(proj, 2)))
    return out


def calibration_report(forward: bool) -> int:
    B, F, T = boards(), fields(), dk_points()
    data = player_rows(B, T)
    base_rows = rows(theory.Z_CASH, ("cash",), B, F, T)
    base = {r["contest"]: r for r in base_rows}
    held = []
    how = "earlier dates only" if forward else "every other date"
    print(f"# cash, projection calibrated on (proj, salary) per position, fit on {how}\n")
    print("| held-out slate | fit rows | QB (a, b, c) | contests | shipped pctile | calibrated pctile |"
          "\n|---|---|---|---|---|---|")
    for d in sorted({r["date"] for r in base_rows}):
        train = [r for r in data if (r[0] < d if forward else r[0] != d)]
        co = fit_calibration(train)
        Bd = {g: calibrate(ps, co) for g, ps in B.items() if ps[0]["date"] == d}
        h = rows(theory.Z_CASH, ("cash",), Bd, F, T)
        held += h
        qb = ", ".join(f"{x:.2f}" for x in co["QB"]) if "QB" in co else "unfit"
        print(f"| {d} | {len(train)} | {qb} | {len(h)} | "
              f"{np.mean([base[r['contest']]['pctile'] for r in h]):.3f} | {np.mean([r['pctile'] for r in h]):.3f} |")
    print(f"\n{HEAD}")
    print(summary(base_rows, "shipped"))
    print(summary(held, f"calibrated ({how})", base))
    return 0


def gate(path: str) -> int:
    """critique_gate rows: one per (mode, contest); pred = 1 - field percentile (a loss:
    0 = beat everyone), actual = 0, so lower = better (loss "abs"). State = mode, cluster =
    slate date, split primary for both (no constant here is fitted on these rows)."""
    n = 0
    with open(path, "w") as fh:
        for r in rows(theory.Z_CASH):
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
    sub.add_parser("lodo")
    c = sub.add_parser("calibrate", help="cycle 7 candidate: proj calibrated on salary, held out")
    c.add_argument("--forward", action="store_true", help="fit on earlier dates only")
    g = sub.add_parser("gate")
    g.add_argument("out")
    a = ap.parse_args()
    if a.cmd == "parity":
        return parity()
    if a.cmd == "lodo":
        return lodo()
    if a.cmd == "calibrate":
        return calibration_report(a.forward)
    if a.cmd == "gate":
        return gate(a.out)
    return report(a.z or [])


if __name__ == "__main__":
    raise SystemExit(main())
